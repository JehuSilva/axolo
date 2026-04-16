# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup and Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

FFmpeg must be installed and available in `PATH` for video metadata extraction.

## Common Commands

```bash
# Run all tests
pytest

# Run a single test file
pytest tests/test_organizer.py

# Run a specific test
pytest tests/test_organizer.py::test_media_organizer_resolves_collisions

# Run with coverage
pytest --cov=media_organizer

# Run the CLI
media-organizer run --source ~/Media --destination /mnt/organized --dry-run
media-organizer duplicates --source ~/Media --algorithm blake2b --output duplicates.json
media-organizer duplicates --source ~/Media --action move --quarantine ~/Media/_duplicados --dry-run
```

## Architecture

The package lives under `src/media_organizer/` and is installed as the `media-organizer` CLI entry point (`media_organizer.cli:app`).

**Data flow for `run` command:**
1. `media_scanner.py` — `iter_media_files()` walks the source directory and yields `Path` objects, filtered by `ScanOptions` (extensions, recursion, symlinks).
2. `metadata.py` — `extract_metadata()` detects media type and extracts timestamps via: EXIF (images via Pillow), ffprobe (videos), QuickTime atom parsing (MOV fallback), mutagen (audio), pypdf / zipfile XML (documents). Falls back to filename patterns, then filesystem mtime. Returns `MediaMetadata`.
3. `templates.py` — `render_template()` formats a path string using `MediaMetadata` fields as context. Built-in templates: `default`, `year_month_day`, `year_month_name`, `year_month_name_short`, `camera`.
4. `organizer.py` — `MediaOrganizer.organize()` drives the loop: resolves destination path under `<dest>/<CategoryFolder>/<template>/`, handles filename collisions, then applies move/copy/link. Files with unreliable timestamps go to `unknown_date/`.
5. `cli.py` — Typer app. Each command collects inputs, calls the appropriate module, and renders Rich tables as output.

**360 camera support** (Insta 360 X3): `.insp`/`.insv` added to IMAGE/VIDEO extension sets; `PANORAMIC_360_EXTENSIONS = {".insp", ".insv"}` drives the `is_panoramic` flag. When `is_panoramic=True`, `organizer.py:_resolve_destination` prepends `360/` inside the category folder. `lens_pairing.py` detects dual-lens pairs (`_00_`/`_10_` suffix pattern) and `deduplicate_assets()` collapses them to one representative per capture in duplicate reports.

**Specialized analysis commands** (read-only, never move files):
- `duplicates.py` — `DuplicateAnalyzer` groups files by size then hashes (`blake2b` by default) to detect byte-identical copies across all media types. Supports optional actions (`move`/`link`/`delete`) via `apply_duplicate_actions`; `--dry-run` is on by default. Reports `reclaimable_bytes` per group.

**Configuration:**
- `config.py` — `OrganizerConfig` (Pydantic v2) holds runtime config. `TemplateProfile` allows named templates loaded from a YAML file (`--profiles-path`). Custom profiles follow the schema in `profiles.sample.yaml`.

**Key design decisions:**
- `MediaCategory` (PHOTOS_VIDEOS, MUSIC, DOCUMENTS, OTHER) maps from `MediaType` and determines the top-level destination folder name.
- `TimestampSource` enum tracks provenance (METADATA > FILE_CREATION > FILENAME > FILE_MODIFICATION). `has_reliable_timestamp` excludes UNKNOWN and FILE_MODIFICATION; files failing this check go to `unknown_date/`.
- Month names are hardcoded in Spanish (`MONTH_NAMES_ES` / `MONTH_NAMES_ES_SHORT`) in `templates.py`.
- HEIC support requires `pillow-heif`; it is optional at runtime and handled with a try/except import.

**Template placeholders** available in `--template` / `--profile` strings:
`{year}`, `{month}`, `{day}`, `{hour}`, `{minute}`, `{second}`, `{stem}`, `{ext}`, `{camera_make}`, `{camera_model}`, `{month_name}`, `{month_name_short}`, `{category}`, `{category_label}`, `{category_slug}`. Extra variables can be injected via `--extra key=value`.
