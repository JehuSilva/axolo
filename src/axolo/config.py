"""Configuration models and helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from .templates import DEFAULT_TEMPLATES

logger = logging.getLogger(__name__)


# ── Routing keys ──────────────────────────────────────────────────────────────
# A routing key uniquely identifies a file's destination category+subcategory.
# These are the valid names for entries in the config.yaml  `profiles:` list.

ROUTING_KEYS = frozenset({
    "photos",      # images → destination/Photos/
    "videos",      # videos → destination/Videos/
    "360-photos",  # panoramic images
    "360-videos",  # panoramic videos
    "music",       # audio files
    "documents",   # documents
    "hidden",      # hidden files (name starts with '.')
    "others",      # everything else
})

# Accepted aliases → canonical routing key (for YAML authoring convenience)
_ROUTING_KEY_ALIASES: Dict[str, str] = {
    "music":     "music",
    "docs":      "documents",
    "other":     "others",
    "photos":    "photos",
    "videos":    "videos",
    "fotos":     "photos",
    "musica":    "music",
    "documentos": "documents",
    "ocultos":   "hidden",
    "otros":     "others",
    "360-fotos": "360-photos",
}

# ── Subfolder structure per routing key ───────────────────────────────────────
# Defines the path segments appended to the category root before the template.
# E.g., "photos" → category_root / "Photos" / <template_result>

ROUTING_SUBFOLDERS: Dict[str, tuple[str, ...]] = {
    "photos":     ("Photos",),
    "videos":     ("Videos",),
    "360-photos": ("360", "Photos"),
    "360-videos": ("360", "Videos"),
    "music":      (),
    "documents":  (),
    "hidden":     (),
    "others":     (),
}

# ── Default folder templates per routing key ──────────────────────────────────

DEFAULT_ROUTING: Dict[str, str] = {
    "photos":     "default",            # {year}/{month_name_cap}
    "videos":     "default",            # {year}/{month_name_cap}
    "360-photos": "default",            # {year}/{month_name_cap}
    "360-videos": "default",            # {year}/{month_name_cap}
    "music":      "music_genre_artist", # {music_genre}/{music_artist}
    "documents":  "year_month_cap",     # {year}/{month_name_cap}
    "hidden":     "year_month_cap",     # {year}/{month_name_cap}
    "others":     "year_month_cap",     # {year}/{month_name}
}

# ── Default filename-renaming templates per routing key ───────────────────────
# When present, the file is renamed using this template. None = keep original.

DEFAULT_ROUTING_FILENAME_TEMPLATES: Dict[str, str] = {
    "music": "{music_artist} - {music_title}",
}


# ── Built-in named profiles (selectable with --profile) ───────────────────────

class TemplateProfile(BaseModel):
    name: str
    template: str
    filename_template: Optional[str] = None
    description: Optional[str] = None


BUILTIN_PROFILES: Dict[str, TemplateProfile] = {
    "photos-chronological": TemplateProfile(
        name="photos-chronological",
        template="{year}/{month_name_cap}/{month_name_cap} {day}",
        description="Photos and videos by year, month, and day. E.g.: 2026/April/April 15/photo.jpg",
    ),
    "photos-compact": TemplateProfile(
        name="photos-compact",
        template="{year}/{month:02d}/{day:02d}",
        description="Numeric folders. E.g.: 2026/04/15/photo.jpg",
    ),
    "photos-by-camera": TemplateProfile(
        name="photos-by-camera",
        template="{camera_make}/{camera_model}/{year}/{month:02d}",
        description="Grouped by camera. E.g.: canon/eos-r5/2026/04/photo.jpg",
    ),
    "music": TemplateProfile(
        name="music",
        template="{music_genre}/{music_artist}",
        filename_template="{music_artist} - {music_title}",
        description="Music by genre and artist; renames to Artist_Title.ext.",
    ),
    "music-with-album": TemplateProfile(
        name="music-with-album",
        template="{music_genre}/{music_artist}/{music_album}",
        filename_template="{music_artist} - {music_title}",
        description="Music with album included. E.g.: Music/rock/beatles/let-it-be/beatles_let-it-be.mp3",
    ),
    "music-by-artist": TemplateProfile(
        name="music-by-artist",
        template="{music_artist}/{music_album}",
        filename_template="{music_artist} - {music_title}",
        description="No genre; groups by artist and album.",
    ),
    "documents": TemplateProfile(
        name="documents",
        template="{year}/{month:02d}",
        description="Documents by year and month. E.g.: Documents/2026/04/contract.pdf",
    ),
    "documents-by-month": TemplateProfile(
        name="documents-by-month",
        template="{year}/{month_name_cap}",
        description="Documents with month name. E.g.: Documents/2026/Abril/contract.pdf",
    ),
    "year-month": TemplateProfile(
        name="year-month",
        template="{year}/{month:02d}",
        description="Numeric year and month.",
    ),
    "year-month-name": TemplateProfile(
        name="year-month-name",
        template="{year}/{month_name}",
        description="Year and month name (lowercase).",
    ),
    "events": TemplateProfile(
        name="events",
        template="{year}/{month:02d}/{evento}",
        description="Requires --extra evento=EventName.",
    ),
}


# ── OrganizerConfig ───────────────────────────────────────────────────────────

class OrganizerConfig(BaseModel):
    source: Path
    destination: Path
    action: Literal["move", "copy", "link"] = "move"
    link_kind: Literal["hard", "symbolic"] = "symbolic"
    template: str = "default"
    dry_run: bool = False
    recursive: bool = True
    follow_symlinks: bool = False
    include_extensions: list[str] = Field(default_factory=list)
    exclude_extensions: list[str] = Field(default_factory=list)
    extra: dict[str, str] = Field(default_factory=dict)
    # Per-routing-key folder template overrides (from config.yaml profiles: list)
    routing: dict[str, str] = Field(default_factory=dict)
    # Per-routing-key filename renaming overrides
    routing_filename_templates: dict[str, str] = Field(default_factory=dict)

    @field_validator("source", "destination", mode="before")
    @classmethod
    def _expand_path(cls, value: object) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str):
            return Path(value).expanduser()
        raise TypeError("Paths must be strings or Path instances.")

    @field_validator("template")
    @classmethod
    def _validate_template(cls, value: str) -> str:
        if not value:
            raise ValueError("Template cannot be empty.")
        return value

    def resolve_template(self, profiles=None) -> str:
        """Returns the resolved global template string."""
        return self._resolve_raw(self.template)

    def resolve_template_for_routing_key(self, key: str) -> str:
        """Returns the folder template for a routing key.

        Resolution order:
          1. Per-key override from config YAML (routing dict)
          2. Built-in default for this key (DEFAULT_ROUTING)
          3. Global template fallback
        """
        if key in self.routing:
            return self._resolve_raw(self.routing[key])
        if key in DEFAULT_ROUTING:
            return self._resolve_raw(DEFAULT_ROUTING[key])
        return self._resolve_raw(self.template)

    def resolve_filename_template_for_routing_key(self, key: str) -> Optional[str]:
        """Returns the filename renaming template for a routing key, or None."""
        if key in self.routing_filename_templates:
            return self.routing_filename_templates[key]
        if key in DEFAULT_ROUTING_FILENAME_TEMPLATES:
            return DEFAULT_ROUTING_FILENAME_TEMPLATES[key]
        return None

    def _resolve_raw(self, raw: str) -> str:
        if raw in DEFAULT_TEMPLATES:
            return DEFAULT_TEMPLATES[raw]
        if raw in BUILTIN_PROFILES:
            return BUILTIN_PROFILES[raw].template
        return raw

    def normalized_include_extensions(self) -> set[str]:
        return {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in self.include_extensions}

    def normalized_exclude_extensions(self) -> set[str]:
        return {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in self.exclude_extensions}


# ── YAML loader ───────────────────────────────────────────────────────────────

def load_run_config(path: Path) -> dict:
    """Load execution configuration from a YAML file.

    Reads execution settings (source, destination, action, dry_run, etc.) and
    the per-category ``profiles:`` list, which maps routing keys to templates.
    Profile *definitions* live in code (BUILTIN_PROFILES / DEFAULT_ROUTING).
    """
    if not path.exists():
        logger.debug("Configuration file %s does not exist.", path)
        return {}

    with path.open("r", encoding="utf-8") as handle:
        try:
            raw = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            raise ValueError(
                f"Failed to read configuration file '{path}': {exc}"
            ) from exc

    config: dict = {}

    # Scalar execution settings
    for key in ("source", "destination", "action", "template", "dry_run",
                "recursive", "follow_symlinks", "include_hidden", "extra",
                "include_extensions", "exclude_extensions"):
        if key in raw:
            config[key] = raw[key]

    # Legacy single-profile key
    if "profile" in raw and "template" not in config:
        config["template"] = raw["profile"]

    # Per-category routing from profiles: list
    profiles_list = raw.get("profiles", [])
    if isinstance(profiles_list, list) and profiles_list:
        routing: dict[str, str] = {}
        routing_filename: dict[str, str] = {}
        for item in profiles_list:
            if not isinstance(item, dict):
                continue
            raw_name = str(item.get("name", "")).strip()
            tmpl = str(item.get("template", "")).strip()
            fname_tmpl = str(item.get("filename_template", "")).strip()

            # Normalize alias → canonical key
            canonical = _ROUTING_KEY_ALIASES.get(raw_name, raw_name)
            if canonical not in ROUTING_KEYS:
                logger.warning("Unknown routing key in config: '%s' (skipped).", raw_name)
                continue
            if tmpl:
                routing[canonical] = tmpl
            if fname_tmpl:
                routing_filename[canonical] = fname_tmpl

        if routing:
            config["routing"] = routing
        if routing_filename:
            config["routing_filename_templates"] = routing_filename

    return config
