"""Union dedup-aware sync between two media directories.

Strategy:
1. Hash all files in the destination to build a known-content set.
2. For each source file, hash it and check:
   - Hash already in destination → skip (identical content exists).
   - Hash not in destination, but filename collision → rename with _<hash8> suffix.
   - Hash not in destination, no collision → copy/move as-is.

This is a *union* policy: files are never deleted from destination.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Set

from .duplicates import _hash_file
from .journal import Journal
from .metadata import MediaMetadata, extract_metadata
from .organizer import _safe_move
from .parallel import parallel_map
from .templates import render_filename, render_template

logger = logging.getLogger(__name__)

_DEFAULT_ALGORITHM = "blake2b"
_DEFAULT_CHUNK = 1 << 20  # 1 MiB


# ---------------------------------------------------------------------------
# Plan dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SyncAddition:
    """A source file that will be added to the destination."""

    metadata: MediaMetadata
    destination: Path
    renamed: bool = False  # True if the name was suffixed with hash


@dataclass
class SyncSkip:
    """A source file skipped because identical content exists in destination."""

    metadata: MediaMetadata
    matching_destination: Path


@dataclass
class SyncConflict:
    """A source file whose name collides with a destination file of different content.

    Resolved automatically by renaming with a ``_<hash8>`` suffix; stored here
    for reporting purposes.
    """

    metadata: MediaMetadata
    original_name: str
    resolved_destination: Path
    src_hash: str
    dst_hash: str


@dataclass
class SyncPlan:
    """Result of :func:`plan_sync`."""

    additions: List[SyncAddition] = field(default_factory=list)
    skipped_identical: List[SyncSkip] = field(default_factory=list)
    conflicts: List[SyncConflict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def total_source(self) -> int:
        return len(self.additions) + len(self.skipped_identical) + len(self.conflicts)

    def to_dict(self) -> dict:
        return {
            "additions": len(self.additions),
            "skipped_identical": len(self.skipped_identical),
            "conflicts": len(self.conflicts),
            "errors": len(self.errors),
            "details": {
                "additions": [
                    {"src": str(a.metadata.source_path), "dst": str(a.destination), "renamed": a.renamed}
                    for a in self.additions
                ],
                "skipped_identical": [
                    {"src": str(s.metadata.source_path), "dst": str(s.matching_destination)}
                    for s in self.skipped_identical
                ],
                "conflicts": [
                    {
                        "src": str(c.metadata.source_path),
                        "original_name": c.original_name,
                        "resolved_destination": str(c.resolved_destination),
                    }
                    for c in self.conflicts
                ],
            },
        }


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _build_destination_hash_set(
    dst_files: Sequence[Path],
    algorithm: str,
    chunk_size: int,
    workers: int,
    show_progress: bool,
) -> Dict[str, Path]:
    """Return a mapping digest → first matching path for all files in dst."""

    def _hash(p: Path):
        digest, _ = _hash_file(p, algorithm, chunk_size)
        return p, digest

    results = parallel_map(
        _hash,
        dst_files,
        workers=workers,
        show_progress=show_progress,
        description="Indexando destino...",
    )

    index: Dict[str, Path] = {}
    for r in results:
        if isinstance(r, BaseException):
            continue
        path, digest = r
        if digest is not None:
            index.setdefault(digest, path)
    return index


def plan_sync(
    source_files: Sequence[MediaMetadata],
    destination: Path,
    *,
    destination_existing_files: Sequence[Path],
    algorithm: str = _DEFAULT_ALGORITHM,
    chunk_size: int = _DEFAULT_CHUNK,
    workers: int = 4,
    show_progress: bool = True,
    folder_template: str = "{year}/{month:02d}",
    filename_template: Optional[str] = None,
    extra: Optional[dict] = None,
    dry_run: bool = True,
) -> SyncPlan:
    """Build a sync plan without modifying any files."""
    plan = SyncPlan()
    extra = extra or {}

    # Index destination by hash
    dst_hash_index = _build_destination_hash_set(
        destination_existing_files, algorithm, chunk_size, workers, show_progress
    )
    dst_names_in_plan: Set[Path] = set(destination_existing_files)

    # Hash source files
    def _hash_src(item: MediaMetadata):
        digest, _ = _hash_file(item.source_path, algorithm, chunk_size)
        return item, digest

    src_results = parallel_map(
        _hash_src,
        list(source_files),
        workers=workers,
        show_progress=show_progress,
        description="Hasheando origen...",
    )

    for r in src_results:
        if isinstance(r, BaseException):
            plan.errors.append(str(r))
            continue

        item, src_digest = r
        if src_digest is None:
            plan.errors.append(f"No se pudo hashear {item.source_path}")
            continue

        # Identical content already exists in destination
        if src_digest in dst_hash_index:
            plan.skipped_identical.append(
                SyncSkip(metadata=item, matching_destination=dst_hash_index[src_digest])
            )
            continue

        # Resolve destination path using template
        try:
            if item.has_reliable_timestamp:
                rel = render_template(item, folder_template, extra)
                dest_dir = (destination / rel).resolve()
            else:
                dest_dir = (destination / "unknown_date").resolve()

            if filename_template:
                fname = render_filename(item, filename_template, extra)
            else:
                fname = item.source_path.name

            dest_candidate = dest_dir / fname
        except Exception as exc:
            plan.errors.append(f"Error resolviendo destino para {item.source_path}: {exc}")
            continue

        renamed = False
        conflict: Optional[SyncConflict] = None

        if dest_candidate in dst_names_in_plan or dest_candidate.exists():
            # Name collision — compute hash of the colliding destination file
            if dest_candidate.exists():
                dst_digest, _ = _hash_file(dest_candidate, algorithm, chunk_size)
            else:
                dst_digest = None

            if dst_digest and dst_digest == src_digest:
                # Rare: race between index build and now; treat as identical
                plan.skipped_identical.append(
                    SyncSkip(metadata=item, matching_destination=dest_candidate)
                )
                continue

            # True conflict: same name, different content → rename with hash suffix
            stem = dest_candidate.stem
            suffix = dest_candidate.suffix
            dest_candidate = dest_dir / f"{stem}_{src_digest[:8]}{suffix}"
            renamed = True
            conflict = SyncConflict(
                metadata=item,
                original_name=dest_candidate.name,
                resolved_destination=dest_candidate,
                src_hash=src_digest,
                dst_hash=dst_digest or "",
            )

        dst_names_in_plan.add(dest_candidate)
        dst_hash_index[src_digest] = dest_candidate

        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)

        addition = SyncAddition(metadata=item, destination=dest_candidate, renamed=renamed)
        plan.additions.append(addition)

        if conflict is not None:
            plan.conflicts.append(conflict)

    return plan


# ---------------------------------------------------------------------------
# Applying the plan
# ---------------------------------------------------------------------------


def apply_sync(
    plan: SyncPlan,
    *,
    action: str = "copy",
    dry_run: bool = True,
    show_progress: bool = True,
    journal: Optional[Journal] = None,
    run_id: Optional[str] = None,
) -> int:
    """Apply additions from *plan*.  Returns number of successfully applied actions."""
    if not plan.additions:
        return 0

    success = 0
    seq = 0

    def _apply(addition: SyncAddition):
        nonlocal seq
        src = addition.metadata.source_path
        dst = addition.destination

        if dry_run:
            return True, None

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if action == "move":
                _safe_move(src, dst)
            else:
                shutil.copy2(str(src), str(dst))
            return True, None
        except OSError as exc:
            return False, str(exc)

    results = parallel_map(
        _apply,
        plan.additions,
        show_progress=show_progress,
        description=f"Aplicando sync ({action})...",
    )

    for addition, result in zip(plan.additions, results):
        if isinstance(result, BaseException):
            logger.error("Error al aplicar sync para %s: %s", addition.metadata.source_path, result)
            continue

        ok, err = result
        if ok:
            success += 1
            if journal and run_id and not dry_run:
                try:
                    journal.record(
                        run_id,
                        seq=seq,
                        action=action,
                        src=addition.metadata.source_path,
                        dst=addition.destination,
                    )
                except Exception as jexc:
                    logger.warning("Error escribiendo en journal: %s", jexc)
        else:
            logger.error("Falló sync %s → %s: %s", addition.metadata.source_path, addition.destination, err)
        seq += 1

    return success
