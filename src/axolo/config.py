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
    "fotos",       # images in Fotos_y_Videos (non-panoramic)
    "videos",      # videos in Fotos_y_Videos (non-panoramic)
    "360-fotos",   # panoramic images
    "360-videos",  # panoramic videos
    "musica",      # audio files
    "documentos",  # documents
    "otros",       # everything else
})

# Accepted aliases → canonical routing key (for YAML authoring convenience)
_ROUTING_KEY_ALIASES: Dict[str, str] = {
    "music":  "musica",
    "docs":   "documentos",
    "other":  "otros",
    "fotos":  "fotos",
    "videos": "videos",
}

# ── Subfolder structure per routing key ───────────────────────────────────────
# Defines the path segments appended to the category root before the template.
# E.g., "fotos" → category_root / "Fotos" / <template_result>

ROUTING_SUBFOLDERS: Dict[str, tuple[str, ...]] = {
    "fotos":      ("Fotos",),
    "videos":     ("Videos",),
    "360-fotos":  ("360", "Fotos"),
    "360-videos": ("360", "Videos"),
    "musica":     (),
    "documentos": (),
    "otros":      (),
}

# ── Default folder templates per routing key ──────────────────────────────────

DEFAULT_ROUTING: Dict[str, str] = {
    "fotos":      "default",           # {year}/{month_name_cap}
    "videos":     "default",           # {year}/{month_name_cap}
    "360-fotos":  "default",           # {year}/{month_name_cap}
    "360-videos": "default",           # {year}/{month_name_cap}
    "musica":     "music_genre_artist", # {music_genre}/{music_artist}
    "documentos": "year_month_cap",    # {year}/{month_name_cap}
    "otros":      "year_month_cap",   # {year}/{month_name}
}

# ── Default filename-renaming templates per routing key ───────────────────────
# When present, the file is renamed using this template. None = keep original.

DEFAULT_ROUTING_FILENAME_TEMPLATES: Dict[str, str] = {
    "musica": "{music_artist} - {music_title}",
}


# ── Built-in named profiles (selectable with --profile) ───────────────────────

class TemplateProfile(BaseModel):
    name: str
    template: str
    filename_template: Optional[str] = None
    description: Optional[str] = None


BUILTIN_PROFILES: Dict[str, TemplateProfile] = {
    "fotos-cronologico": TemplateProfile(
        name="fotos-cronologico",
        template="{year}/{month_name_cap}/{month_name_cap} {day}",
        description="Fotos y videos por año, mes y día. Ej: 2026/Abril/Abril 15/foto.jpg",
    ),
    "fotos-compacto": TemplateProfile(
        name="fotos-compacto",
        template="{year}/{month:02d}/{day:02d}",
        description="Carpetas numéricas. Ej: 2026/04/15/foto.jpg",
    ),
    "fotos-por-camara": TemplateProfile(
        name="fotos-por-camara",
        template="{camera_make}/{camera_model}/{year}/{month:02d}",
        description="Agrupado por cámara. Ej: canon/eos-r5/2026/04/foto.jpg",
    ),
    "musica": TemplateProfile(
        name="musica",
        template="{music_genre}/{music_artist}",
        filename_template="{music_artist} - {music_title}",
        description="Música por género y artista; renombra a Artista_Titulo.ext.",
    ),
    "musica-con-album": TemplateProfile(
        name="musica-con-album",
        template="{music_genre}/{music_artist}/{music_album}",
        filename_template="{music_artist} - {music_title}",
        description="Música con álbum incluido. Ej: Musica/rock/beatles/let-it-be/beatles_let-it-be.mp3",
    ),
    "musica-por-artista": TemplateProfile(
        name="musica-por-artista",
        template="{music_artist}/{music_album}",
        filename_template="{music_artist} - {music_title}",
        description="Sin género; agrupa por artista y álbum.",
    ),
    "documentos": TemplateProfile(
        name="documentos",
        template="{year}/{month:02d}",
        description="Documentos por año y mes. Ej: Documentos/2026/04/contrato.pdf",
    ),
    "documentos-por-mes": TemplateProfile(
        name="documentos-por-mes",
        template="{year}/{month_name_cap}",
        description="Documentos con mes en español. Ej: Documentos/2026/Abril/contrato.pdf",
    ),
    "year-month": TemplateProfile(
        name="year-month",
        template="{year}/{month:02d}",
        description="Año y mes numérico.",
    ),
    "year-month-name": TemplateProfile(
        name="year-month-name",
        template="{year}/{month_name}",
        description="Año y nombre de mes (minúsculas).",
    ),
    "eventos": TemplateProfile(
        name="eventos",
        template="{year}/{month:02d}/{evento}",
        description="Requiere --extra evento=NombreEvento.",
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
                "recursive", "follow_symlinks", "extra",
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
