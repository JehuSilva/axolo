"""Detect exact byte-level duplicate files using cryptographic hashing.

Two-phase strategy:
1. Group files by size in bytes (free, one stat() per file).
2. Hash only files that share a size with at least one other file.

This avoids reading large files when no size-match exists, making the
scan very fast even on libraries with many unique large videos.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from .lens_pairing import deduplicate_assets
from .metadata import MediaMetadata

logger = logging.getLogger(__name__)

_VALID_ALGORITHMS = frozenset({"blake2b", "sha256", "md5"})


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def _hash_file(path: Path, algorithm: str, chunk_size: int) -> Tuple[Optional[str], int]:
    """Hash *path* reading in *chunk_size*-byte chunks.

    Returns ``(hex_digest, bytes_read)``.  On ``OSError`` returns
    ``(None, 0)`` and logs a warning so the caller can count the file as
    skipped without crashing the whole scan.
    """
    try:
        h = hashlib.new(algorithm)
        bytes_read = 0
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
                bytes_read += len(chunk)
        return h.hexdigest(), bytes_read
    except OSError as exc:
        logger.warning("No se pudo hashear %s: %s", path, exc)
        return None, 0


def _group_by_size(items: Sequence[MediaMetadata]) -> Dict[int, List[MediaMetadata]]:
    """Group *items* by file size in bytes.

    Files whose ``stat()`` fails are silently dropped (the caller tracks
    them as skipped).
    """
    groups: Dict[int, List[MediaMetadata]] = {}
    for item in items:
        try:
            size = os.stat(item.source_path).st_size
        except OSError as exc:
            logger.warning("No se pudo leer tamaño de %s: %s", item.source_path, exc)
            continue
        groups.setdefault(size, []).append(item)
    return groups


def _pick_canonical(files: List[MediaMetadata]) -> MediaMetadata:
    """Choose the canonical representative from a duplicate group.

    Selection policy (applied in order):
    1. Shortest string representation of ``source_path`` — heuristic for
       "original lives in the shallowest folder".
    2. Earliest ``captured_at`` timestamp — oldest capture date wins.
    3. Lexicographic order of ``source_path`` — deterministic tiebreaker.
    """
    return min(
        files,
        key=lambda m: (len(str(m.source_path)), m.captured_at, str(m.source_path)),
    )


# ---------------------------------------------------------------------------
# Report dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DuplicateFile:
    """A single file within a duplicate group."""

    metadata: MediaMetadata
    size: int

    def to_dict(self) -> dict:
        return {
            "path": str(self.metadata.source_path),
            "size": self.size,
            "captured_at": self.metadata.captured_at.isoformat(),
            "media_type": self.metadata.media_type.value,
        }


@dataclass
class DuplicateGroup:
    """A set of byte-identical files sharing the same cryptographic hash."""

    digest: str
    size: int
    canonical: DuplicateFile
    duplicates: List[DuplicateFile]  # excludes the canonical file

    @property
    def reclaimable_bytes(self) -> int:
        """Bytes that could be freed by removing all non-canonical files."""
        return self.size * len(self.duplicates)

    def to_dict(self) -> dict:
        return {
            "digest": self.digest,
            "size": self.size,
            "reclaimable_bytes": self.reclaimable_bytes,
            "canonical": self.canonical.to_dict(),
            "duplicates": [d.to_dict() for d in self.duplicates],
        }


@dataclass
class DuplicatesReport:
    """Summary of a duplicate-detection scan."""

    groups: List[DuplicateGroup]
    processed: int     # files successfully hashed
    scanned: int       # total files received (before Insta360 dedup)
    skipped: int       # lens pairs + stat/hash errors + below min_size
    hashed_bytes: int  # total bytes read during hashing
    algorithm: str

    @property
    def reclaimable_bytes(self) -> int:
        return sum(g.reclaimable_bytes for g in self.groups)

    def to_dict(self) -> dict:
        return {
            "algorithm": self.algorithm,
            "scanned": self.scanned,
            "processed": self.processed,
            "skipped": self.skipped,
            "hashed_bytes": self.hashed_bytes,
            "reclaimable_bytes": self.reclaimable_bytes,
            "groups": [g.to_dict() for g in self.groups],
        }


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class DuplicateAnalyzer:
    """Detect exact byte-level duplicates using a two-phase size→hash strategy."""

    def __init__(
        self,
        algorithm: str = "blake2b",
        chunk_size: int = 1 << 20,  # 1 MiB
        min_size: int = 1,
    ) -> None:
        if algorithm not in _VALID_ALGORITHMS:
            raise ValueError(
                f"Algoritmo de hash no soportado: '{algorithm}'. "
                f"Usa uno de: {', '.join(sorted(_VALID_ALGORITHMS))}"
            )
        if chunk_size <= 0:
            raise ValueError("chunk_size debe ser mayor que 0.")
        if min_size < 0:
            raise ValueError("min_size no puede ser negativo.")
        self.algorithm = algorithm
        self.chunk_size = chunk_size
        self.min_size = min_size

    def analyze(self, items: Sequence[MediaMetadata]) -> DuplicatesReport:
        """Scan *items* and return a report with groups of byte-identical files."""
        scanned = len(items)

        # Collapse Insta360 dual-lens pairs so they are never reported as
        # duplicates — each physical capture is a single logical asset.
        deduped = deduplicate_assets(items)
        skipped = scanned - len(deduped)

        # Phase 1: group by file size.
        size_groups = _group_by_size(deduped)
        stat_ok_count = sum(len(v) for v in size_groups.values())
        skipped += len(deduped) - stat_ok_count  # files whose stat() failed

        # Collect candidates that share a size with ≥1 other file.
        candidates: List[Tuple[MediaMetadata, int]] = []
        for size, group in size_groups.items():
            if size < self.min_size:
                skipped += len(group)
                continue
            if len(group) < 2:
                continue  # singleton — cannot be a duplicate
            for item in group:
                candidates.append((item, size))

        logger.debug(
            "Fase 1 completada: %d candidatos a hashear de %d archivos escaneados.",
            len(candidates),
            scanned,
        )

        # Phase 2: hash each candidate and group by (size, digest).
        hash_groups: Dict[Tuple[int, str], List[MediaMetadata]] = {}
        processed = 0
        hashed_bytes = 0

        for item, size in candidates:
            digest, nbytes = _hash_file(item.source_path, self.algorithm, self.chunk_size)
            if digest is None:
                skipped += 1
                continue
            processed += 1
            hashed_bytes += nbytes
            hash_groups.setdefault((size, digest), []).append(item)

        logger.debug(
            "Fase 2 completada: %d archivos hasheados, %d bytes leídos.",
            processed,
            hashed_bytes,
        )

        # Build duplicate groups — only entries with ≥2 members.
        # Sort by descending size so the largest files appear first in the report.
        groups: List[DuplicateGroup] = []
        for (size, digest), members in sorted(
            hash_groups.items(), key=lambda kv: (-kv[0][0], kv[0][1])
        ):
            if len(members) < 2:
                continue
            canonical_meta = _pick_canonical(members)
            canonical = DuplicateFile(metadata=canonical_meta, size=size)
            dups = [
                DuplicateFile(metadata=m, size=size)
                for m in members
                if m is not canonical_meta
            ]
            groups.append(
                DuplicateGroup(digest=digest, size=size, canonical=canonical, duplicates=dups)
            )

        logger.info(
            "Detección de duplicados: %d grupos, %d bytes recuperables.",
            len(groups),
            sum(g.reclaimable_bytes for g in groups),
        )

        return DuplicatesReport(
            groups=groups,
            processed=processed,
            scanned=scanned,
            skipped=skipped,
            hashed_bytes=hashed_bytes,
            algorithm=self.algorithm,
        )


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


class DuplicateActionError(Exception):
    """Raised for unrecoverable action configuration errors."""


@dataclass
class ActionOutcome:
    """Result of applying an action to a single duplicate file."""

    source: Path
    destination: Optional[Path]  # None for delete
    action: str                  # "move" | "link" | "delete"
    dry_run: bool
    error: Optional[str] = None


def apply_duplicate_actions(
    report: DuplicatesReport,
    action: Literal["move", "link", "delete"],
    *,
    quarantine: Optional[Path] = None,
    relative_to: Optional[Path] = None,
    link_kind: Literal["hard", "symbolic"] = "hard",
    dry_run: bool = True,
) -> List[ActionOutcome]:
    """Apply *action* to all non-canonical files in *report*.

    Args:
        report: Result from :meth:`DuplicateAnalyzer.analyze`.
        action: What to do with non-canonical duplicates:
            ``"move"`` — move to *quarantine* (requires *quarantine*);
            ``"link"`` — replace with a hard/symlink pointing to canonical;
            ``"delete"`` — remove the file permanently.
        quarantine: Required target directory for ``"move"``.
        relative_to: Source root used to compute relative paths under
            *quarantine*.  If omitted the file's parent directory is used.
        link_kind: ``"hard"`` (default) or ``"symbolic"``.  Only used with
            ``action="link"``.
        dry_run: When ``True`` (default) no files are modified; outcomes
            still describe what *would* happen.

    Returns:
        List of :class:`ActionOutcome` for every non-canonical duplicate.

    Raises:
        :class:`DuplicateActionError`: for invalid configuration (e.g.
            ``action="move"`` without *quarantine*).
    """
    if action == "move" and quarantine is None:
        raise DuplicateActionError(
            "Se requiere --quarantine cuando se usa --action move."
        )
    if link_kind not in {"hard", "symbolic"}:
        raise DuplicateActionError(
            f"link_kind no válido: '{link_kind}'. Usa 'hard' o 'symbolic'."
        )

    outcomes: List[ActionOutcome] = []

    for group in report.groups:
        canonical_path = group.canonical.metadata.source_path

        for dup_file in group.duplicates:
            src = dup_file.metadata.source_path

            if action == "move":
                assert quarantine is not None  # guarded above
                base = relative_to or src.parent
                try:
                    rel = src.relative_to(base)
                except ValueError:
                    rel = Path(src.name)
                dest = quarantine / rel

                if not dry_run:
                    try:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src), str(dest))
                    except OSError as exc:
                        outcomes.append(
                            ActionOutcome(
                                source=src, destination=dest,
                                action="move", dry_run=False, error=str(exc),
                            )
                        )
                        continue
                outcomes.append(
                    ActionOutcome(source=src, destination=dest, action="move", dry_run=dry_run)
                )

            elif action == "link":
                dest = canonical_path
                if not dry_run:
                    # Atomic replacement: create new link at a temp path, then
                    # rename it over src.  This way src is never left deleted
                    # if the link creation fails.
                    tmp = src.with_name(src.name + ".dedup_tmp")
                    try:
                        if link_kind == "hard":
                            tmp.hardlink_to(canonical_path)
                        else:
                            tmp.symlink_to(canonical_path)
                        tmp.replace(src)
                    except OSError as exc:
                        tmp.unlink(missing_ok=True)
                        outcomes.append(
                            ActionOutcome(
                                source=src, destination=dest,
                                action="link", dry_run=False, error=str(exc),
                            )
                        )
                        continue
                outcomes.append(
                    ActionOutcome(source=src, destination=dest, action="link", dry_run=dry_run)
                )

            elif action == "delete":
                if not dry_run:
                    try:
                        src.unlink()
                    except OSError as exc:
                        outcomes.append(
                            ActionOutcome(
                                source=src, destination=None,
                                action="delete", dry_run=False, error=str(exc),
                            )
                        )
                        continue
                outcomes.append(
                    ActionOutcome(source=src, destination=None, action="delete", dry_run=dry_run)
                )

    return outcomes
