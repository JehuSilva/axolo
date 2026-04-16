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
media-organizer cluster --source ~/Media --time-window 120 --min-samples 3 --dry-run
media-organizer similars --source ~/Media --threshold 5 --output similitudes.json
media-organizer timeline --source ~/Media --granularity month
```

## Architecture

The package lives under `src/media_organizer/` and is installed as the `media-organizer` CLI entry point (`media_organizer.cli:app`).

**Data flow for `run` command:**
1. `media_scanner.py` — `iter_media_files()` walks the source directory and yields `Path` objects, filtered by `ScanOptions` (extensions, recursion, symlinks).
2. `metadata.py` — `extract_metadata()` detects media type and extracts timestamps via: EXIF (images via Pillow), ffprobe (videos), QuickTime atom parsing (MOV fallback), mutagen (audio), pypdf / zipfile XML (documents). Falls back to filename patterns, then filesystem mtime. Returns `MediaMetadata`.
3. `templates.py` — `render_template()` formats a path string using `MediaMetadata` fields as context. Built-in templates: `default`, `year_month_day`, `year_month_name`, `year_month_name_short`, `camera`.
4. `organizer.py` — `MediaOrganizer.organize()` drives the loop: resolves destination path under `<dest>/<CategoryFolder>/<template>/`, handles filename collisions, then applies move/copy/link. Files with unreliable timestamps go to `unknown_date/`.
5. `cli.py` — Typer app. Each command collects inputs, calls the appropriate module, and renders Rich tables as output.

**Specialized analysis commands** (read-only, never move files):
- `clustering.py` — `PhotoClusterer` uses DBSCAN (scikit-learn) on timestamps converted to minutes to suggest album groups. `ClusterParameters` controls `time_window_minutes` (eps) and `min_samples`.
- `similarity.py` — `SimilarityAnalyzer` computes perceptual hashes (ImageHash library) for images and finds pairs within a Hamming distance threshold.
- `timeline.py` — `TimelineAnalyzer` buckets `MediaMetadata` timestamps by hour/day/week/month/year for a capture frequency report. Can export CSV/JSON/TSV and generate a Chart.js HTML chart.

**Configuration:**
- `config.py` — `OrganizerConfig` (Pydantic v2) holds runtime config. `TemplateProfile` allows named templates loaded from a YAML file (`--profiles-path`). Custom profiles follow the schema in `profiles.sample.yaml`.

**Key design decisions:**
- `MediaCategory` (PHOTOS_VIDEOS, MUSIC, DOCUMENTS, OTHER) maps from `MediaType` and determines the top-level destination folder name.
- `TimestampSource` enum tracks provenance (METADATA > FILE_CREATION > FILENAME > FILE_MODIFICATION). `has_reliable_timestamp` excludes UNKNOWN and FILE_MODIFICATION; files failing this check go to `unknown_date/`.
- Month names are hardcoded in Spanish (`MONTH_NAMES_ES` / `MONTH_NAMES_ES_SHORT`) in `templates.py`.
- HEIC support requires `pillow-heif`; clustering requires `numpy` + `scikit-learn`; both are optional at runtime and handled with try/except imports.

**Template placeholders** available in `--template` / `--profile` strings:
`{year}`, `{month}`, `{day}`, `{hour}`, `{minute}`, `{second}`, `{stem}`, `{ext}`, `{camera_make}`, `{camera_model}`, `{month_name}`, `{month_name_short}`, `{category}`, `{category_label}`, `{category_slug}`. Extra variables can be injected via `--extra key=value`.
