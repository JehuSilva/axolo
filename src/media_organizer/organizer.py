"""Core organizer logic."""

from __future__ import annotations

from collections import Counter
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from .config import ROUTING_SUBFOLDERS, OrganizerConfig
from .metadata import MediaCategory, MediaMetadata, MediaType, extract_metadata
from .templates import render_filename, render_template

logger = logging.getLogger(__name__)


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


class MediaOrganizer:
    def __init__(self, config: OrganizerConfig) -> None:
        self.config = config

    def organize(self, files: Iterable[Path]) -> OrganizeSummary:
        summary = OrganizeSummary()
        for file_path in files:
            metadata: Optional[MediaMetadata] = None
            try:
                metadata = extract_metadata(file_path)
                destination = self._resolve_destination(metadata)
                result = self._apply_action(metadata, destination)
            except Exception as exc:  # pragma: no cover - errores inesperados
                logger.exception("Error al procesar %s", file_path)
                result = FileResult(
                    source=file_path,
                    destination=file_path,
                    status="failed",
                    message=str(exc),
                    category=metadata.category if metadata else None,
                )
            summary.add(result)
        return summary

    def _get_routing_key(self, metadata: MediaMetadata) -> str:
        """Maps a file's metadata to its routing key."""
        if metadata.is_panoramic:
            return "360-videos" if metadata.media_type == MediaType.VIDEO else "360-fotos"
        if metadata.category == MediaCategory.PHOTOS_VIDEOS:
            return "videos" if metadata.media_type == MediaType.VIDEO else "fotos"
        if metadata.category == MediaCategory.MUSIC:
            return "musica"
        if metadata.category == MediaCategory.DOCUMENTS:
            return "documentos"
        return "otros"

    def _resolve_destination(self, metadata: MediaMetadata) -> Path:
        routing_key = self._get_routing_key(metadata)

        # Build base directory: category root + routing subfolders
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
                "No se encontró fecha de captura confiable para %s; se moverá a %s",
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
        destination_dir.mkdir(parents=True, exist_ok=True)

        candidate = destination_dir / filename
        counter = 1
        while candidate.exists():
            candidate = destination_dir / f"{stem}_{counter}{suffix}"
            counter += 1
        return candidate

    def _apply_action(self, metadata: MediaMetadata, destination: Path) -> FileResult:
        source = metadata.source_path
        status = "skipped"
        message: Optional[str] = None

        if self.config.dry_run:
            status = "dry-run"
            message = "Se omitió el movimiento por estar en modo dry-run."
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
                shutil.move(str(source), str(destination))
                status = "moved"
            elif action == "copy":
                shutil.copy2(str(source), str(destination))
                status = "copied"
            elif action == "link":
                self._create_link(source, destination)
                status = "linked"
            else:
                raise ValueError(f"Acción desconocida: {action}")
            logger.info("%s -> %s (%s)", source, destination, status)
        except Exception as exc:
            status = "failed"
            message = str(exc)
            logger.error("Error al aplicar la acción sobre %s: %s", source, exc)

        return FileResult(
            source=source,
            destination=destination,
            status=status,
            message=message,
            category=metadata.category,
        )

    @staticmethod
    def _create_link(source: Path, destination: Path) -> None:
        try:
            os.symlink(source, destination)
        except (NotImplementedError, OSError):
            # Cuando el sistema no permite symlinks, se intenta con hardlink
            os.link(source, destination)
