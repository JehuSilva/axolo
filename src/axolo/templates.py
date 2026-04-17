"""Template helpers for organizing media into directories."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional

from .i18n import MONTH_NAMES_ES, MONTH_NAMES_ES_CAP, MONTH_NAMES_ES_SHORT
from .metadata import MediaMetadata

DEFAULT_TEMPLATES: Dict[str, str] = {
    "default": "{year}/{month_name_cap}",
    "year_month": "{year}/{month:02d}",
    "year_month_cap": "{year}/{month_name_cap}",
    "year_month_day": "{year}/{month:02d}/{day:02d}",
    "year_month_name": "{year}/{month_name}",
    "year_month_name_short": "{year}/{month_name_short}",
    "year_month_name_day": "{year}/{month_name_cap}/{month_name_cap} {day}",
    "camera": "{camera_make}/{camera_model}/{year}/{month:02d}",
    "music_genre_artist": "{music_genre}/{music_artist}",
    "music_genre": "{music_genre}",
    "documents_year_month": "{year}/{month:02d}",
    "documents_year_month_cap": "{year}/{month_name_cap}",
}

VALID_PLACEHOLDER_RE = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)(:[^}]*)?}")


def available_placeholders() -> set[str]:
    return {
        "year",
        "month",
        "day",
        "hour",
        "minute",
        "second",
        "stem",
        "ext",
        "camera_make",
        "camera_model",
        "month_name",
        "month_name_cap",
        "month_name_short",
        "category",
        "category_label",
        "category_slug",
        "music_artist",
        "music_title",
        "music_genre",
        "music_album",
    }


def build_context(metadata: MediaMetadata, extra: Optional[dict[str, str]] = None) -> dict[str, object]:
    dt = metadata.captured_at
    context: dict[str, object] = {
        "year": dt.year,
        "month": dt.month,
        "day": dt.day,
        "hour": dt.hour,
        "minute": dt.minute,
        "second": dt.second,
        "stem": metadata.stem,
        "ext": metadata.suffix.lstrip("."),
        "camera_make": _slug(metadata.camera_make) if metadata.camera_make else "unknown",
        "camera_model": _slug(metadata.camera_model) if metadata.camera_model else "unknown",
        "month_name": MONTH_NAMES_ES[dt.month],
        "month_name_cap": MONTH_NAMES_ES_CAP[dt.month],
        "month_name_short": MONTH_NAMES_ES_SHORT[dt.month],
        "category": metadata.category.folder_name(),
        "category_label": metadata.category.label(),
        "category_slug": _slug(metadata.category.label()),
        "music_artist": _camel(metadata.music_artist) if metadata.music_artist else "unknown",
        "music_title": _camel(metadata.music_title) if metadata.music_title else "unknown",
        "music_genre": _camel(metadata.music_genre) if metadata.music_genre else "unknown",
        "music_album": _camel(metadata.music_album) if metadata.music_album else "unknown",
    }
    if extra:
        context.update(extra)
    return context


def render_template(
    metadata: MediaMetadata,
    template: str,
    extra: Optional[dict[str, str]] = None,
) -> Path:
    _validate_template(template, extra or {})
    context = build_context(metadata, extra)
    relative = template.format(**context)
    return Path(relative)


def render_filename(
    metadata: MediaMetadata,
    filename_template: str,
    extra: Optional[dict[str, str]] = None,
) -> str:
    """Renders a filename from a template and appends the original extension."""
    _validate_template(filename_template, extra or {})
    context = build_context(metadata, extra)
    stem = filename_template.format(**context)
    return stem + metadata.suffix


def _validate_template(template: str, extra: dict[str, str]) -> None:
    allowed = available_placeholders() | set(extra.keys())
    unmatched = [
        match.group(1)
        for match in VALID_PLACEHOLDER_RE.finditer(template)
        if match.group(1) not in allowed
    ]
    if unmatched:
        raise ValueError(
            f"El template contiene placeholders desconocidos: {', '.join(sorted(set(unmatched)))}"
        )


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-") or "unknown"


def _camel(value: str) -> str:
    """Sanitizes a string for use in file paths, preserving original casing."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value.strip())
    value = re.sub(r"\s+", " ", value)
    return value.strip() or "Unknown"
