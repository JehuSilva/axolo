"""Core organizer logic."""

from __future__ import annotations

from collections import Counter
import errno
import logging
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from .config import ROUTING_SUBFOLDERS, OrganizerConfig
from .journal import Journal
from .metadata import MediaCategory, MediaMetadata, MediaType, extract_metadata
from .parallel import parallel_map
from .templates import render_filename, render_template

logger = logging.getLogger(__name__)

_STATUS_TO_ACTION = {"moved": "move", "copied": "copy", "linked": "link"}


@dataclass
class FileResult:
    source: Path
    destination: Path
    status: str
    message: Optional[str] = None
    category: Optional[MediaCategory] = None


@dataclass
class OrganizeSummary:
    results: List[FileResult] = field(default_factory=list)

    @property
    def moved(self) -> int:
        return sum(1 for item in self.results if item.status == "moved")

    @property
    def copied(self) -> int:
        return sum(1 for item in self.results if item.status == "copied")

    @property
    def linked(self) -> int:
        return sum(1 for item in self.results if item.status == "linked")

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.results if item.status == "skipped")

    @property
    def failed(self) -> int:
        return sum(1 for item in self.results if item.status == "failed")

    @property
    def dry_run(self) -> int:
        return sum(1 for item in self.results if item.status == "dry-run")

    @property
    def total(self) -> int:
        return len(self.results)

    def status_counts(self) -> Counter[str]:
        return Counter(item.status for item in self.results)

    def category_counts(self) -> Counter[str]:
        labels: list[str] = []
        for item in self.results:
            if isinstance(item.category, MediaCategory):
                labels.append(item.category.label())
            elif isinstance(item.category, str):
                labels.append(item.category)
        return Counter(labels)

    def add(self, result: FileResult) -> None:
        self.results.append(result)


class AxoloOrganizer:
    def __init__(
        self,
        config: OrganizerConfig,
        *,
        show_progress: bool = True,
        workers: int = 4,
        journal: Optional[Journal] = None,
    ) -> None:
        self.config = config
        self.show_progress = show_progress
        self.workers = workers
        self.journal = journal
        # Protects collision resolution so parallel action-apply can't race
        self._dest_lock = threading.Lock()

    def organize(self, files: Sequence[Path]) -> OrganizeSummary:
        summary = OrganizeSummary()

        run_id: Optional[str] = None
        if self.journal and not self.config.dry_run:
            run_id = self.journal.start_run(
                "run",
                source=self.config.source,
                destination=self.config.destination,
                dry_run=self.config.dry_run,
            )

        try:
            self._organize_impl(files, summary, run_id)
        except Exception:
            if self.journal and run_id:
                self.journal.finish_run(run_id, "aborted")
            raise

        if self.journal and run_id:
            self.journal.finish_run(run_id, "completed")

        return summary

    def _organize_impl(
        self, files: Sequence[Path], summary: OrganizeSummary, run_id: Optional[str]
    ) -> None:
        files_list = list(files)

        # ── Phase 1: extract metadata in parallel ──────────────────────
        meta_results = parallel_map(
            extract_metadata,
            files_list,
            workers=self.workers,
            show_progress=self.show_progress,
            description="Extracting metadata...",
        )

        # ── Phase 2: resolve destinations (serial, collision-safe) ──────
        plan: list[tuple[MediaMetadata, Path]] = []
        planned: set[Path] = set()

        for file_path, result in zip(files_list, meta_results):
            if isinstance(result, BaseException):
                logger.error("Failed to extract metadata from %s: %s", file_path, result)
                summary.add(FileResult(
                    source=file_path,
                    destination=file_path,
                    status="failed",
                    message=str(result),
                ))
                continue
            metadata: MediaMetadata = result
            try:
                destination = self._resolve_destination(metadata, planned)
            except Exception as exc:
                logger.error("Failed to resolve destination for %s: %s", file_path, exc)
                summary.add(FileResult(
                    source=file_path,
                    destination=file_path,
                    status="failed",
                    message=str(exc),
                    category=metadata.category,
                ))
                continue
            plan.append((metadata, destination))

        # ── Phase 3: apply actions in parallel ──────────────────────────
        def _apply(pair: tuple[MediaMetadata, Path]) -> FileResult:
            return self._apply_action(pair[0], pair[1])

        action_results = parallel_map(
            _apply,
            plan,
            workers=self.workers,
            show_progress=self.show_progress,
            description="Applying actions...",
        )

        for seq, ((metadata, destination), action_result) in enumerate(
            zip(plan, action_results)
        ):
            if isinstance(action_result, BaseException):
                result = FileResult(
                    source=metadata.source_path,
                    destination=destination,
                    status="failed",
                    message=str(action_result),
                    category=metadata.category,
                )
            else:
                result = action_result

            if (
                self.journal
                and run_id
                and result.status in _STATUS_TO_ACTION
            ):
                try:
                    size = destination.stat().st_size if destination.exists() else None
                except OSError:
                    size = None
                self.journal.record(
                    run_id,
                    seq=seq,
                    action=_STATUS_TO_ACTION[result.status],
                    src=metadata.source_path,
                    dst=destination,
                    size=size,
                )

            summary.add(result)

    def _get_routing_key(self, metadata: MediaMetadata) -> str:
        if metadata.category == MediaCategory.HIDDEN:
            return "hidden"
        if metadata.is_panoramic:
            return "360-videos" if metadata.media_type == MediaType.VIDEO else "360-photos"
        if metadata.category == MediaCategory.PHOTOS_VIDEOS:
            return "videos" if metadata.media_type == MediaType.VIDEO else "photos"
        if metadata.category == MediaCategory.MUSIC:
            return "music"
        if metadata.category == MediaCategory.DOCUMENTS:
            return "documents"
        return "others"

    def _resolve_destination(
        self, metadata: MediaMetadata, planned: Optional[set[Path]] = None
    ) -> Path:
        routing_key = self._get_routing_key(metadata)

        if metadata.category == MediaCategory.PHOTOS_VIDEOS:
            category_root = self.config.destination.resolve()
        else:
            category_root = (self.config.destination / metadata.category.folder_name()).resolve()
        base_dir = category_root
        for subfolder in ROUTING_SUBFOLDERS.get(routing_key, ()):
            base_dir = base_dir / subfolder

        folder_template = self.config.resolve_template_for_routing_key(routing_key)

        if metadata.has_reliable_timestamp:
            relative = render_template(metadata, folder_template, self.config.extra)
            destination_dir = (base_dir / relative).resolve()
        else:
            destination_dir = (base_dir / "unknown_date").resolve()
            logger.warning(
                "No reliable capture date found for %s; will be placed in %s",
                metadata.source_path,
                destination_dir,
            )

        filename_tmpl = self.config.resolve_filename_template_for_routing_key(routing_key)
        if filename_tmpl:
            filename = render_filename(metadata, filename_tmpl, self.config.extra)
        else:
            filename = metadata.source_path.name

        stem = Path(filename).stem
        suffix = Path(filename).suffix or metadata.suffix

        if not self.config.dry_run:
            destination_dir.mkdir(parents=True, exist_ok=True)

        # Collision resolution — also checks in-flight reservations so
        # parallel batches don't pick the same destination.
        with self._dest_lock:
            candidate = destination_dir / filename
            counter = 1
            while candidate.exists() or (planned is not None and candidate in planned):
                candidate = destination_dir / f"{stem}_{counter}{suffix}"
                counter += 1
            if planned is not None:
                planned.add(candidate)

        return candidate

    def _apply_action(self, metadata: MediaMetadata, destination: Path) -> FileResult:
        source = metadata.source_path
        status = "skipped"
        message: Optional[str] = None

        if self.config.dry_run:
            status = "dry-run"
            message = "Skipped — dry-run mode active."
            logger.info("[dry-run] %s -> %s", source, destination)
            return FileResult(
                source=source,
                destination=destination,
                status=status,
                message=message,
                category=metadata.category,
            )

        action = self.config.action
        try:
            if action == "move":
                _safe_move(source, destination)
                status = "moved"
            elif action == "copy":
                shutil.copy2(str(source), str(destination))
                status = "copied"
            elif action == "link":
                self._create_link(source, destination, self.config.link_kind)
                status = "linked"
            else:
                raise ValueError(f"Unknown action: {action}")
            if status in {"moved", "copied"} and metadata.has_reliable_timestamp:
                _apply_captured_at_mtime(destination, metadata.captured_at)
            logger.info("%s -> %s (%s)", source, destination, status)
        except Exception as exc:
            status = "failed"
            message = str(exc)
            logger.error("Failed to apply action on %s: %s", source, exc)

        return FileResult(
            source=source,
            destination=destination,
            status=status,
            message=message,
            category=metadata.category,
        )

    @staticmethod
    def _create_link(source: Path, destination: Path, link_kind: str = "symbolic") -> None:
        if link_kind == "hard":
            os.link(source, destination)
        else:
            try:
                os.symlink(source, destination)
            except (NotImplementedError, OSError) as exc:
                logger.warning(
                    "symlink not supported (%s), falling back to hardlink for %s", exc, source
                )
                os.link(source, destination)


def _apply_captured_at_mtime(path: Path, captured_at: "datetime") -> None:
    """Set atime/mtime of *path* to `captured_at` so the filesystem reflects the
    real capture date (Finder, ls -l, etc.)."""
    try:
        ts = captured_at.timestamp()
        os.utime(path, (ts, ts))
    except (OSError, OverflowError, ValueError) as exc:
        logger.debug("Could not set mtime of %s to %s: %s", path, captured_at, exc)


def _safe_move(source: Path, destination: Path) -> None:
    """Move *source* to *destination*, handling cross-device (EXDEV) moves safely."""
    try:
        source.rename(destination)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    tmp: Optional[Path] = None
    try:
        tmp_fd, tmp_str = tempfile.mkstemp(dir=destination.parent, suffix=".mo_tmp")
        os.close(tmp_fd)
        tmp = Path(tmp_str)
        with source.open("rb") as src_fh, tmp.open("wb") as dst_fh:
            shutil.copyfileobj(src_fh, dst_fh)
            dst_fh.flush()
            os.fsync(dst_fh.fileno())
        shutil.copystat(str(source), str(tmp))
        tmp.replace(destination)
        source.unlink()
    except Exception:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        raise
