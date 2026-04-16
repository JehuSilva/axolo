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
# Run all tests (excluding tests that require real media files / ffprobe)
pytest --ignore=tests/test_metadata_example_files.py --ignore=tests/test_metadata_insta360.py

# Run a single test file
pytest tests/test_organizer.py

# Run a specific test
pytest tests/test_organizer.py::test_media_organizer_resolves_collisions

# Run with coverage
pytest --cov=media_organizer --ignore=tests/test_metadata_example_files.py --ignore=tests/test_metadata_insta360.py

# Run the CLI
media-organizer run --source ~/Media --destination /mnt/organized --dry-run
media-organizer duplicates --source ~/Media --algorithm blake2b --output duplicates.json
media-organizer duplicates --source ~/Media --action move --quarantine ~/Media/_duplicados --dry-run
media-organizer sync --source ~/NuevosArchivos --destination ~/Archivo --dry-run
media-organizer undo --list
media-organizer undo --run-id <uuid> --no-dry-run
media-organizer tui
```

## Architecture

The package lives under `src/media_organizer/` and is installed as the `media-organizer` CLI entry point (`media_organizer.cli:app`).

### Module map

| Module | Role |
|--------|------|
| `cli.py` | Typer app — thin dispatcher; registers all commands; imports shared helpers from `commands/_shared.py` |
| `commands/_shared.py` | Shared CLI helpers: `collect_metadata`, `validate_workers`, `parse_extra`, `humanize_bytes`, `render_summary`, `render_runs_table`, `console` |
| `commands/{run,duplicates,undo,sync_cmd,tui_cmd}.py` | Re-export stubs for external import of individual commands |
| `media_scanner.py` | `iter_media_files()` — walks source directory, yields `Path` objects filtered by `ScanOptions` |
| `metadata.py` | `extract_metadata()` — detects media type, extracts timestamps via EXIF/ffprobe/QuickTime/mutagen/pypdf; falls back to filename pattern then filesystem mtime |
| `templates.py` | `render_template()` / `render_filename()` — formats paths from `MediaMetadata` using `{placeholder}` strings |
| `i18n.py` | Spanish month name lists (`MONTH_NAMES_ES`, `MONTH_NAMES_ES_SHORT`, `MONTH_NAMES_ES_CAP`); imported by `templates.py` |
| `organizer.py` | `MediaOrganizer.organize()` — 3-phase pipeline: parallel metadata extraction → serial destination resolution (collision-safe) → parallel file actions (move/copy/link) |
| `config.py` | `OrganizerConfig` (Pydantic v2), `BUILTIN_PROFILES`, routing constants (`ROUTING_KEYS`, `ROUTING_SUBFOLDERS`, `DEFAULT_ROUTING`) |
| `duplicates.py` | `DuplicateAnalyzer` — size-group then parallel hash to find byte-identical files; `apply_duplicate_actions` — move/link/delete non-canonical copies |
| `sync.py` | `plan_sync()` / `apply_sync()` — union dedup-aware sync: hashes destination, copies only new content, renames name-collisions with `_<hash8>` suffix |
| `journal.py` | SQLite-backed operation log at `~/.media-organizer/journal.db`; records every move/copy/link for `undo` |
| `parallel.py` | `parallel_map()` — `ThreadPoolExecutor` wrapper; returns results in input order; captures per-item exceptions without aborting |
| `logging_setup.py` | `setup_logging()` — `RichHandler` for console + rotating JSON Lines file handler at `~/.media-organizer/logs/`; injects `correlation_id` per run |
| `lens_pairing.py` | Detects Insta360 dual-lens pairs (`_00_`/`_10_` pattern); `deduplicate_assets()` collapses them to one asset |
| `tui.py` | Interactive wizard (`questionary` + Rich): menus for run/duplicates/sync/history-undo |

### Data flow: `run` command

1. `media_scanner.py` — `iter_media_files()` yields `Path` objects.
2. `parallel_map(extract_metadata, files)` — parallel I/O phase.
3. `_resolve_destination(metadata, planned)` — serial, collision-safe; respects `dry_run` (no `mkdir` in dry mode); tracks in-flight destinations in `planned: set[Path]`.
4. `parallel_map(_apply_action, plan)` — parallel move/copy/link via `_safe_move` (handles cross-device EXDEV).
5. `journal.record(...)` — each successful action is logged for `undo`.

### Data flow: `duplicates` command

1. `_collect_metadata` — parallel metadata extraction.
2. `DuplicateAnalyzer.analyze()`:
   - Phase 1: `_group_by_size()` — free, one `stat()` per file.
   - Phase 2: `parallel_map(_hash_candidate, candidates)` — only hashes files sharing a size.
3. `apply_duplicate_actions()` — move/link/delete non-canonical files (dry-run default).

### Data flow: `sync` command

1. Scan source and destination with `iter_media_files`.
2. `_build_destination_hash_set()` — parallel hash of all destination files.
3. `plan_sync()` — for each source file: skip (identical hash), add (new hash), or rename (name collision, different content).
4. `apply_sync()` — copy or move additions; records in journal.

### Key design decisions

- **Parallelism**: all I/O-bound phases use `ThreadPoolExecutor` via `parallel_map`. Pass `--workers 1` to force serial execution (useful in tests with non-thread-safe mocks).
- **Collision resolution**: destination path resolution is serial and uses `_dest_lock` + `planned: set[Path]` so parallel workers never pick the same destination.
- **Cross-device move**: `_safe_move` tries `os.rename` first; on `EXDEV` falls back to `copy2+fsync+os.replace+unlink` with temp-file cleanup on failure.
- **Canonical selection**: `_pick_canonical` uses `(prefer_under, oldest_mtime, lexicographic_path)` — not path length, which could delete the original when a copy has a shorter path.
- **dry_run flag**: `_resolve_destination` skips `mkdir` when `dry_run=True` so no directories are created during preview.
- **Journal**: append-only SQLite at `~/.media-organizer/journal.db` (override with `MEDIA_ORGANIZER_JOURNAL`). Only non-dry-run, successful actions are recorded.
- **Timestamps**: EXIF timestamps are naive local time (no timezone). Year validation rejects values outside `[1970, current_year+1]`.
- **360 camera**: `.insp`/`.insv` set `is_panoramic=True`; organizer routes them to `360/Fotos` or `360/Videos`. `.dng` is NOT treated as 360 (common misclassification fixed).
- **Month names**: defined in `i18n.py`, imported by `templates.py`. Locale is Spanish; structure is ready for future locales.
- **HEIC**: `pillow-heif` is optional; handled with try/except import.

### Testing

Test files follow the naming pattern `tests/test_<module>.py`. Shared fixtures live in `tests/conftest.py`:

- `media_tree(tmp_path)` — synthetic media file tree (jpg, mp4, mp3, pdf).
- `journal_db(tmp_path)` — isolated Journal backed by a temp SQLite file.
- `monkeypatch_home(tmp_path)` — redirects `~/.media-organizer` and `MEDIA_ORGANIZER_JOURNAL` to a temp path.

Tests that need real media files or `ffprobe` live in `test_metadata_example_files.py` and `test_metadata_insta360.py` — these are ignored in the standard CI run.

**Template placeholders** available in `--template` / `--profile` strings:
`{year}`, `{month}`, `{day}`, `{hour}`, `{minute}`, `{second}`, `{stem}`, `{ext}`, `{camera_make}`, `{camera_model}`, `{month_name}`, `{month_name_short}`, `{month_name_cap}`, `{category}`, `{category_label}`, `{category_slug}`, `{music_artist}`, `{music_title}`, `{music_genre}`, `{music_album}`. Extra variables can be injected via `--extra key=value`.
