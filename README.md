```
 █████╗ ██╗  ██╗ ██████╗ ██╗      ██████╗
██╔══██╗╚██╗██╔╝██╔═══██╗██║     ██╔═══██╗
███████║ ╚███╔╝ ██║   ██║██║     ██║   ██║
██╔══██║ ██╔██╗ ██║   ██║██║     ██║   ██║
██║  ██║██╔╝ ██╗╚██████╔╝███████╗╚██████╔╝
╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝ ╚═════╝
```

**Stop organizing media by hand. Axolo does it right, fast, and safely.**

Axolo is a command-line tool that transforms a chaotic media directory into a clean, structured library. It reads real metadata — EXIF, ID3, QuickTime atoms, PDF info — and places every file exactly where it belongs. Preview every change before it happens, undo anything that went wrong, and never touch a duplicate again.

---

## Why Axolo

- **Metadata-first** — uses EXIF, ID3, QuickTime, ffprobe, and pypdf. Filename patterns and `mtime` are fallbacks, not defaults.
- **Safe by design** — `--dry-run` is the default for destructive commands. Every move, copy, or link is logged to an undo journal.
- **Fast** — parallel workers for metadata extraction, hashing, and file I/O. Configurable via `--workers N`.
- **Flexible** — built-in profiles for common use cases; fully customizable templates with `{placeholder}` strings.
- **360° ready** — native Insta360 X3 support (`.insp` / `.insv`); dual-lens pairs grouped as one asset.

---

## Installation

**Requirements:** Python 3.10+ and [FFmpeg](https://ffmpeg.org/) in your `PATH`.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Verify:

```bash
axolo --help
```

---

## Quick Start

```bash
# Preview what would happen — no files are touched
axolo run --source ~/Downloads/Photos --destination ~/Library/Organized --dry-run

# Organize for real with 8 parallel workers
axolo run --source ~/Downloads/Photos --destination ~/Library/Organized --action move --workers 8

# Not sure about the flags? Use the interactive wizard
axolo tui
```

---

## Commands

| Command | What it does |
|---------|--------------|
| `run` | Organize files into a structured folder tree |
| `duplicates` | Find and act on byte-identical files |
| `sync` | Copy only new content to a destination — never overwrites |
| `undo` | Reverse any previous run from the journal |
| `tui` | Interactive step-by-step wizard for all commands |

---

## `run` — Organize Your Library

```bash
# Dry run (safe preview)
axolo run --source ~/Media --destination /mnt/organized --dry-run

# Move files using a built-in profile
axolo run --source ~/Media --destination /mnt/organized --action move --profile fotos-cronologico

# Use a config file
axolo run --config config.yaml
```

### Key flags

| Flag | Description | Default |
|------|-------------|---------|
| `--source` / `-s` | Source directory | required |
| `--destination` / `-d` | Destination directory | required |
| `--action` | `move` \| `copy` \| `link` | prompted |
| `--profile` / `-p` | Built-in profile name | — |
| `--template` | Custom `{placeholder}` template | prompted |
| `--config` / `-c` | Path to YAML config file | — |
| `--dry-run` | Preview without touching files | prompted |
| `--workers N` | Parallel workers (1–32) | `min(cpu_count, 8)` |
| `--no-journal` | Skip the undo log | journal on |
| `--extra key=value` | Inject extra template variables | — |

### Output structure (defaults)

```
Organized/
├── Photos_Videos/
│   ├── Photos/2026/April/IMG_4521.jpg
│   ├── Videos/2026/April/clip.mp4
│   └── 360/Photos/2026/April/scene.insp
├── Music/
│   └── rock/the-beatles/abbey-road.mp3
├── Documents/
│   └── 2026/April/contract.pdf
└── Other/
    └── 2026/april/archive.zip
```

Files with no detectable date land in `unknown_date/` inside their category.

---

## `duplicates` — Reclaim Space

Finds byte-identical files using fast content hashing. Dry-run is the default — you see the report before anything changes.

```bash
# Report only
axolo duplicates --source ~/Media

# Save report to JSON
axolo duplicates --source ~/Media --output duplicates.json

# Move duplicates to quarantine (preview first)
axolo duplicates --source ~/Media --action move --quarantine ~/Media/_dup --dry-run

# Replace duplicates with hard links (saves disk space)
axolo duplicates --source ~/Media --action link --no-dry-run

# Keep files in ~/Archive as canonical
axolo duplicates --source ~/Media --prefer-under ~/Media/Archive --action move --quarantine ~/Media/_dup --no-dry-run
```

### Canonical file selection

When multiple identical files exist, the one to keep is chosen by:
1. Presence under `--prefer-under` path (explicit preference)
2. Oldest modification time (most likely the original)
3. Lexicographic path (deterministic tiebreak)

### Key flags

| Flag | Description | Default |
|------|-------------|---------|
| `--source` / `-s` | Directory to scan | required |
| `--algorithm` | `blake2b` \| `sha256` \| `md5` | `blake2b` |
| `--prefer-under PATH` | Treat this directory's files as canonical | — |
| `--action` | `move` \| `link` \| `delete` | report only |
| `--quarantine PATH` | Destination for moved duplicates | — |
| `--dry-run` / `--no-dry-run` | Simulate without changes | `--dry-run` |
| `--output` / `-o` | Save JSON report | — |
| `--workers N` | Parallel hashing workers | `min(cpu_count, 8)` |

---

## `sync` — Add Without Overwriting

Copies (or moves) only content that doesn't already exist at the destination, identified by hash — not filename. It never deletes or overwrites destination files.

```bash
# Preview additions
axolo sync --source ~/NewFiles --destination ~/Archive --dry-run

# Apply
axolo sync --source ~/NewFiles --destination ~/Archive --action copy --no-dry-run

# Save the plan to JSON
axolo sync --source ~/A --destination ~/B --output plan.json
```

### Conflict policy

| Scenario | Result |
|----------|--------|
| Same hash already at destination | Skipped — content exists |
| New hash, name available | Copied normally |
| Name taken, different content | Renamed with `_<hash8>` suffix |

---

## `undo` — Roll Back Any Run

Every non-dry-run operation is recorded in `~/.axolo/journal.db`. Reverse any of them precisely.

```bash
# List recorded runs
axolo undo --list

# Preview what undo would do
axolo undo --dry-run

# Undo a specific run
axolo undo --run-id <uuid> --no-dry-run
```

### Reversibility

| Original action | Undo result |
|-----------------|-------------|
| `move` A → B | Moves B back to A |
| `copy` A → B | Deletes B |
| `link` (hard/sym) | Removes the link |
| `delete` | Not reversible — reported as error |

Override the journal path with `AXOLO_JOURNAL=/path/to/journal.db`.

---

## `tui` — Interactive Wizard

No flags to memorize. The wizard guides you through every command with prompts and previews.

```bash
axolo tui
```

Covers: organize, find duplicates, sync folders, view history, and undo.

---

## Configuration File

For repeatable workflows, define everything in a YAML file:

```yaml
source: ~/Media
destination: /mnt/organized
action: copy          # move | copy | link
dry_run: false
recursive: true
follow_symlinks: false

profiles:
  - name: fotos
    template: year_month_cap
    # → Photos_Videos/Photos/2026/April/photo.jpg

  - name: videos
    template: year_month_cap
    # → Photos_Videos/Videos/2026/April/video.mp4

  - name: musica
    template: music_genre
    filename_template: "{music_artist} - {music_title}"
    # → Music/rock/the-beatles - let-it-be.mp3

  - name: documentos
    template: year_month_cap
    # → Documents/2026/April/contract.pdf
```

```bash
axolo run --config config.yaml --dry-run
```

---

## Templates & Placeholders

### Built-in named templates

| Name | Pattern | Example |
|------|---------|---------|
| `default` | `{year}/{month_name_cap}` | `2026/April` |
| `year_month` | `{year}/{month:02d}` | `2026/04` |
| `year_month_day` | `{year}/{month:02d}/{day:02d}` | `2026/04/15` |
| `year_month_name_day` | `{year}/{month_name_cap}/{month_name_cap} {day}` | `2026/April/April 15` |
| `music_genre` | `{music_genre}` | `rock` |
| `music_genre_artist` | `{music_genre}/{music_artist}` | `rock/the-beatles` |
| `camera` | `{camera_make}/{camera_model}/{year}/{month:02d}` | `canon/eos-r5/2026/04` |

### Built-in profiles (`--profile`)

| Profile | Description |
|---------|-------------|
| `fotos-cronologico` | Year / month / day |
| `fotos-compacto` | Compact numeric `YYYY/MM/DD` |
| `fotos-por-camara` | Grouped by camera make and model |
| `musica` | Genre and artist; renames to `Artist - Title` |
| `musica-con-album` | Genre / artist / album |
| `musica-por-artista` | Artist / album (no genre) |
| `documentos` | Numeric year and month |
| `documentos-por-mes` | Year and month name |
| `eventos` | Requires `--extra evento=EventName` |

### All available placeholders

| Placeholder | Example |
|-------------|---------|
| `{year}` | `2026` |
| `{month}` / `{month:02d}` | `4` / `04` |
| `{day}` / `{day:02d}` | `5` / `05` |
| `{hour}`, `{minute}`, `{second}` | `18`, `24`, `46` |
| `{month_name}` | `april` |
| `{month_name_short}` | `apr` |
| `{month_name_cap}` | `April` |
| `{stem}` | `IMG_20260415` |
| `{ext}` | `jpg` |
| `{camera_make}` | `canon` |
| `{camera_model}` | `eos-r5` |
| `{music_artist}` | `the-beatles` |
| `{music_title}` | `let-it-be` |
| `{music_genre}` | `rock` |
| `{music_album}` | `abbey-road` |
| `{category}` | `Photos_Videos` |
| `{category_label}` | `Photos and Videos` |
| `{category_slug}` | `photos-videos` |

Inject arbitrary variables with `--extra key=value`.

---

## 360° Camera Support (Insta360 X3)

| Format | Type | Metadata source |
|--------|------|-----------------|
| `.insp` | 360° photo (JPEG container) | EXIF via Pillow |
| `.insv` | 360° video (MP4 container) | QuickTime atoms + ffprobe |

These files are routed to `Photos_Videos/360/`. Dual-lens pairs (`_00_` / `_10_`) are treated as a single asset in `duplicates` and `sync` reports.

---

## FAQ

**Why are some files going to `unknown_date/`?**
No reliable date was found — no EXIF, no recognizable filename pattern. Rename files with a date prefix (`YYYYMMDD_*.jpg`) or embed EXIF data to fix this.

**Do EXIF timestamps include a timezone?**
No. EXIF stores local time without timezone information. Axolo treats it as the system's local time.

**Where is the journal?**
`~/.axolo/journal.db`. Override with `AXOLO_JOURNAL=/custom/path.db`. Disable per-run with `--no-journal`.

**Can `sync` delete files from the destination?**
Never. `sync` is strictly append-only. Name conflicts are resolved by renaming the incoming file with a `_<hash8>` suffix — the destination is never modified.

---

## Running Tests

```bash
# Standard test suite
pytest --ignore=tests/test_metadata_example_files.py --ignore=tests/test_metadata_insta360.py

# Single module
pytest tests/test_organizer.py -v

# Specific test
pytest tests/test_organizer.py::test_axolo_resolves_collisions

# With coverage
pytest --cov=axolo --ignore=tests/test_metadata_example_files.py --ignore=tests/test_metadata_insta360.py
```

Tests that require real media files or `ffprobe` (`test_metadata_example_files.py`, `test_metadata_insta360.py`) are excluded from the standard run.
