```
 █████╗ ██╗  ██╗ ██████╗ ██╗      ██████╗     ██████╗  █████╗ ████████╗ █████╗
██╔══██╗╚██╗██╔╝██╔═══██╗██║     ██╔═══██╗    ██╔══██╗██╔══██╗╚══██╔══╝██╔══██╗
███████║ ╚███╔╝ ██║   ██║██║     ██║   ██║    ██║  ██║███████║   ██║   ███████║
██╔══██║ ██╔██╗ ██║   ██║██║     ██║   ██║    ██║  ██║██╔══██║   ██║   ██╔══██║
██║  ██║██╔╝ ██╗╚██████╔╝███████╗╚██████╔╝    ██████╔╝██║  ██║   ██║   ██║  ██║
╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝ ╚═════╝     ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝

>>> [Axolo Data]: Organizing the chaos. <<<
```

Automatically organizes your media files — photos, videos, audio and documents — into configurable folder templates.

## Features

- Extracts metadata (EXIF, ID3, PDF, Office, QuickTime) to sort media files and documents.
- Automatically classifies files into categories (`Photos_Videos`, `Music`, `Documents`, `Other`).
- Within each category organizes by subfolder (Photos/, Videos/, 360/…) then by year/month (customizable via templates).
- **Parallelism**: metadata extraction, hashing and file moves run in concurrent threads (`--workers N`).
- **`dry-run` mode** to preview results without moving files (enabled by default in `duplicates`, `sync` and `undo`).
- **Operations journal** (SQLite) to undo any `run`, `duplicates` or `sync` execution.
- **Dedup-aware sync**: `sync` only adds new content to the destination — never deletes.
- **Interactive wizard** (`tui`): guided menu for all commands without memorizing flags.
- HEIC support via `pillow-heif` and extended video compatibility (ffprobe and DJI tags).
- Native support for 360° cameras (Insta360 X3): `.insp`, `.insv` formats; 360 files go to `Photos_Videos/360/`; lens pairs (`_00_`/`_10_`) are grouped as a single asset in reports.
- Files without a reliable date are automatically placed in `unknown_date/` within their category.

## Requirements

- Python 3.10 or higher.
- [FFmpeg](https://ffmpeg.org/) installed and available in `PATH` for video/audio metadata extraction.
- Dependencies are installed with `pip install -e .` and include `mutagen` (audio), `pypdf` (PDF), `questionary` (TUI) and `rich` (console).

## Installation

```bash
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -e ".[dev]"     # includes dev and test dependencies
```

## Available commands

| Command | Description |
|---------|-------------|
| `run` | Organizes files into the configured folder structure |
| `duplicates` | Detects and manages exact duplicates (byte-for-byte) |
| `sync` | Syncs two folders without duplicating content |
| `undo` | Reverts operations from a previous run |
| `tui` | Interactive wizard guiding through all commands |

---

## `run` — organize files

```bash
# With a configuration file
axolo run --config config.yaml --dry-run

# Without a config file (CLI prompts for missing fields)
axolo run --source ~/Media --destination /mnt/organized --dry-run

# Move real files with 8 workers
axolo run --source ~/Media --destination /mnt/organized --action move --workers 8
```

### Main flags

| Flag | Description | Default |
|------|-------------|---------|
| `--source` / `-s` | Source directory | — |
| `--destination` / `-d` | Destination directory | — |
| `--action` | `move` \| `copy` \| `link` | Prompted |
| `--link-kind` | `hard` \| `symbolic` (for `--action link`) | `symbolic` |
| `--template` | Folder template or profile name | Prompted |
| `--profile` / `-p` | Alias for `--template` | — |
| `--config` / `-c` | Path to YAML configuration file | — |
| `--dry-run` / `--no-dry-run` | Preview without modifying files | Prompted |
| `--workers N` | Parallel workers (1–32) | `min(cpu_count, 8)` |
| `--no-journal` | Disable the operations log | Journal enabled |
| `--quiet` | Suppress console output | off |
| `--verbose` | Enable DEBUG logging | off |
| `--json-logs` | Emit logs as JSON Lines to stdout | off |
| `--extra key=value` | Extra variables for the template | — |

The `--dry-run` flag never modifies files. Output shows a table with source, computed destination, category and status.

---

## `duplicates` — detect and manage duplicates

```bash
# Detection only (no modifications)
axolo duplicates --source ~/Media

# Save JSON report
axolo duplicates --source ~/Media --output duplicates.json

# Move duplicates to quarantine (dry-run enabled by default)
axolo duplicates --source ~/Media --action move --quarantine ~/Media/_dup --dry-run

# Run real move
axolo duplicates --source ~/Media --action move --quarantine ~/Media/_dup --no-dry-run

# Replace duplicates with hard links
axolo duplicates --source ~/Media --action link --no-dry-run

# Prioritize files in a specific directory as "canonical"
axolo duplicates --source ~/Media --prefer-under ~/Media/Archive
```

### Main flags

| Flag | Description | Default |
|------|-------------|---------|
| `--source` / `-s` | Directory to analyze | Required |
| `--algorithm` | `blake2b` \| `sha256` \| `md5` | `blake2b` |
| `--min-size` | Minimum file size in bytes to compare | `1` |
| `--prefer-under PATH` | Directory whose content is treated as canonical | — |
| `--action` | `move` \| `link` \| `delete` | — (report only) |
| `--quarantine PATH` | Destination for `--action move` | — |
| `--link-kind` | `hard` \| `symbolic` (for `--action link`) | `hard` |
| `--dry-run` / `--no-dry-run` | Simulate actions without executing | `--dry-run` |
| `--output` / `-o` | Path to save the JSON report | — |
| `--workers N` | Parallel workers for hashing | `min(cpu_count, 8)` |

### Canonical file selection

The canonical file in each group is chosen with this priority:
1. Files under the path specified with `--prefer-under` (avoids deleting the original when a copy has a shorter path).
2. File with the oldest `mtime` (most likely the original).
3. Lexicographic path order (deterministic tiebreak).

---

## `sync` — sync folders

Copies (or moves) to the destination only content that does not already exist there, identified by hash. Never deletes files from the destination.

```bash
# Preview what would be added without touching anything
axolo sync --source ~/NewFiles --destination ~/Archive --dry-run

# Real sync
axolo sync --source ~/NewFiles --destination ~/Archive --action copy --no-dry-run

# Save the plan as JSON
axolo sync --source ~/A --destination ~/B --output plan.json
```

### Conflict resolution policy

| Situation | Result |
|-----------|--------|
| Identical hash at destination | File skipped (content already exists) |
| Free name, new hash | File added normally |
| Name taken, different content | File renamed with `_<hash8>` suffix |

### Main flags

| Flag | Description | Default |
|------|-------------|---------|
| `--source` / `-s` | Source directory | Required |
| `--destination` / `-d` | Destination directory | Required |
| `--action` | `copy` \| `move` | `copy` |
| `--algorithm` | `blake2b` \| `sha256` \| `md5` | `blake2b` |
| `--template` | Destination folder template | `default` |
| `--dry-run` / `--no-dry-run` | Preview without modifying files | `--dry-run` |
| `--output` / `-o` | Path to save the JSON plan | — |
| `--workers N` | Parallel workers | `min(cpu_count, 8)` |

---

## `undo` — revert operations

Reverts in reverse order all operations from a previous `run`, `duplicates` or `sync`.

```bash
# List runs recorded in the journal
axolo undo --list

# Preview what the last run would undo
axolo undo --dry-run

# Actually undo a specific run
axolo undo --run-id <uuid> --no-dry-run
```

### What can and cannot be undone

| Original action | Undo result |
|-----------------|-------------|
| `move` A → B | Moves B back to A |
| `copy` A → B | Deletes B (the copy) |
| `link` (hard/sym) | Deletes the created link |
| `delete` | Not reversible; error is reported |

### Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--run-id ID` | Run to revert | Last non-reverted run |
| `--list` | List recent runs and exit | — |
| `--dry-run` / `--no-dry-run` | Simulate without modifying | `--dry-run` |
| `--limit N` | Number of runs to show with `--list` | `10` |

### Journal

Operations are automatically saved to `~/.axolo/journal.db` (SQLite). Override the path with the `AXOLO_JOURNAL` environment variable.

---

## `tui` — interactive wizard

Guided menu to run any command without memorizing flags.

```bash
axolo tui
```

The wizard offers:

1. **Organize files** — wizard for the `run` command.
2. **Find duplicates** — wizard for `duplicates` with preview.
3. **Sync folders** — wizard for `sync`.
4. **View history & undo** — lists journal runs, lets you select one and execute `undo`.
5. **Exit**.

---

## Configuration (`config.yaml`)

The recommended approach is to define execution settings in a YAML file:

```bash
cp profiles.sample.yaml config.yaml
```

Minimal structure:

```yaml
source: ~/Media
destination: /mnt/organized
action: copy          # move | copy | link
dry_run: false
recursive: true
follow_symlinks: false
```

### Per-category profiles (`profiles:`)

| Key | Description | Subfolder within category |
|-----|-------------|--------------------------|
| `fotos` | Non-panoramic photos | `Photos_Videos/Photos/` |
| `videos` | Non-panoramic videos | `Photos_Videos/Videos/` |
| `360-fotos` | Panoramic photos (.insp) | `Photos_Videos/360/Photos/` |
| `360-videos` | Panoramic videos (.insv) | `Photos_Videos/360/Videos/` |
| `musica` | Audio (alias: `music`) | `Music/` |
| `documentos` | Documents (alias: `docs`) | `Documents/` |
| `otros` | Everything else (alias: `other`) | `Other/` |

Full example:

```yaml
source: ~/Media
destination: /mnt/organized
action: copy

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

dry_run: false
recursive: true
```

### Category defaults

| Category | Default template | Example |
|----------|-----------------|---------|
| Photos | `{year}/{month_name_cap}` | `Photos_Videos/Photos/2026/April/photo.jpg` |
| Videos | `{year}/{month_name_cap}` | `Photos_Videos/Videos/2026/April/video.mp4` |
| 360 Photos | `{year}/{month_name_cap}` | `Photos_Videos/360/Photos/2026/April/img.insp` |
| 360 Videos | `{year}/{month_name_cap}` | `Photos_Videos/360/Videos/2026/April/vid.insv` |
| Music | `{music_genre}/{music_artist}` + rename `Artist - Title` | `Music/rock/the-beatles/` |
| Documents | `{year}/{month_name_cap}` | `Documents/2026/April/contract.pdf` |
| Other | `{year}/{month_name}` | `Other/2026/april/file.zip` |

---

## Available templates

### Named templates

| Name | Pattern | Example |
|------|---------|---------|
| `default` | `{year}/{month_name_cap}` | `2026/April` |
| `year_month_cap` | `{year}/{month_name_cap}` | `2026/April` |
| `year_month` | `{year}/{month:02d}` | `2026/04` |
| `year_month_name` | `{year}/{month_name}` | `2026/april` |
| `year_month_name_day` | `{year}/{month_name_cap}/{month_name_cap} {day}` | `2026/April/April 15` |
| `year_month_day` | `{year}/{month:02d}/{day:02d}` | `2026/04/15` |
| `music_genre` | `{music_genre}` | `rock` |
| `music_genre_artist` | `{music_genre}/{music_artist}` | `rock/the-beatles` |
| `camera` | `{camera_make}/{camera_model}/{year}/{month:02d}` | `canon/eos-r5/2026/04` |

### Built-in profiles (with `--profile`)

| Name | Description |
|------|-------------|
| `fotos-cronologico` | Year / month / day |
| `fotos-compacto` | Numeric folders `YYYY/MM/DD` |
| `fotos-por-camara` | Grouped by camera make and model |
| `musica` | Genre and artist; renames to `Artist - Title` |
| `musica-con-album` | Genre / artist / album |
| `musica-por-artista` | Artist / album (no genre) |
| `documentos` | Numeric year and month |
| `documentos-por-mes` | Year and month name |
| `eventos` | Requires `--extra evento=EventName` |
| `year-month` | `YYYY/MM` |
| `year-month-name` | `YYYY/month-name` |

### Available placeholders

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `{year}` | Capture year | `2026` |
| `{month}` / `{month:02d}` | Numeric month | `4` / `04` |
| `{day}` / `{day:02d}` | Numeric day | `5` / `05` |
| `{hour}`, `{minute}`, `{second}` | Capture time | `18`, `24`, `46` |
| `{month_name}` | Month name (lowercase) | `april` |
| `{month_name_short}` | Abbreviated month | `apr` |
| `{month_name_cap}` | Capitalized month | `April` |
| `{stem}` | Filename without extension | `IMG_20260415` |
| `{ext}` | Extension without dot | `jpg` |
| `{camera_make}` | Camera make (slug) | `canon` |
| `{camera_model}` | Camera model (slug) | `eos-r5` |
| `{music_artist}` | Artist (from ID3/Vorbis/MP4) | `the-beatles` |
| `{music_title}` | Song title | `let-it-be` |
| `{music_genre}` | Music genre | `rock` |
| `{music_album}` | Album | `abbey-road` |
| `{category}` | Category folder | `Photos_Videos` |
| `{category_label}` | Human-readable category label | `Photos and Videos` |
| `{category_slug}` | Category slug | `photos-videos` |

Extra variables can be injected with `--extra key=value`.

---

## 360° camera compatibility (Insta360 X3)

| Extension | Type | Metadata |
|-----------|------|----------|
| `.insp` | 360 photo (JPEG with 360 data appended) | EXIF via Pillow |
| `.insv` | 360 video (MP4 container) | QuickTime atoms + ffprobe |

`.insp` and `.insv` files are organized inside `Photos_Videos/360/`. Lens pairs (`_00_`/`_10_`) are counted as a single asset in `duplicates` and `sync`.

---

## FAQ

### Why are my photos going to `unknown_date/`?

The organizer could not find a reliable date (no EXIF, no filename with a date pattern). You can rename files with a date (`YYYYMMDD_*.jpg`) or add EXIF metadata so they are classified correctly.

### Do EXIF timestamps include a timezone?

No. The EXIF standard stores local time without a timezone. The organizer treats it as the system's local time. A `--assume-tz` flag is pending full implementation if you need to override this.

### Where is the journal stored?

At `~/.axolo/journal.db`. Override the path with the `AXOLO_JOURNAL` environment variable.

### How do I disable the journal?

Use `--no-journal` on any command.

### Can `sync` delete files from the destination?

No. `sync` is an append-only operation (union policy). It never deletes or overwrites files at the destination; name conflicts are resolved by renaming the incoming file with a `_<hash8>` suffix.

---

## Tests

```bash
# Run all tests
pytest

# Single file
pytest tests/test_organizer.py -v

# Specific test
pytest tests/test_organizer.py::test_axolo_resolves_collisions

# With coverage
pytest --cov=axolo

# Skip tests that require real media files or ffprobe
pytest --ignore=tests/test_metadata_example_files.py --ignore=tests/test_metadata_insta360.py
```

Test suite coverage is ≥ 80% excluding tests that require real media files.
