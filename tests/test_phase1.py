"""Phase 1 regression tests: bug fixes, logging, and progress bars."""

from __future__ import annotations

import errno
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from media_organizer.config import OrganizerConfig
from media_organizer.duplicates import DuplicateAnalyzer, _pick_canonical
from media_organizer.logging_setup import setup_logging
from media_organizer.media_scanner import ScanOptions, iter_media_files
from media_organizer.metadata import (
    MediaCategory,
    MediaMetadata,
    MediaType,
    TimestampSource,
    detect_media_type,
)
from media_organizer.organizer import MediaOrganizer, _safe_move


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_metadata(path: Path) -> MediaMetadata:
    return MediaMetadata(
        source_path=path,
        media_type=MediaType.IMAGE,
        category=MediaCategory.PHOTOS_VIDEOS,
        captured_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        timestamp_source=TimestampSource.METADATA,
    )


def _fake_extract(path: Path) -> MediaMetadata:
    return _make_metadata(path)


# ---------------------------------------------------------------------------
# Bug fix: dry-run must not create directories
# ---------------------------------------------------------------------------

def test_dry_run_does_not_create_directories(tmp_path, monkeypatch):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()

    (source / "photo.jpg").write_bytes(b"x")

    monkeypatch.setattr("media_organizer.organizer.extract_metadata", _fake_extract)

    config = OrganizerConfig(
        source=source,
        destination=destination,
        action="move",
        template="default",
        dry_run=True,
    )
    organizer = MediaOrganizer(config=config, show_progress=False)
    files = list(iter_media_files(source, ScanOptions()))
    organizer.organize(files)

    # Destination root should not exist after a dry run
    assert not destination.exists(), "dry-run must not create any directories"


# ---------------------------------------------------------------------------
# Bug fix: cross-device move (EXDEV) is handled safely
# ---------------------------------------------------------------------------

def test_safe_move_same_device(tmp_path):
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_bytes(b"hello")
    _safe_move(src, dst)
    assert dst.read_bytes() == b"hello"
    assert not src.exists()


def test_safe_move_cross_device(tmp_path):
    """Simulate EXDEV by making rename raise OSError(EXDEV)."""
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_bytes(b"cross-device content")

    original_rename = Path.rename

    def fake_rename(self: Path, target: Path) -> Path:
        raise OSError(errno.EXDEV, "Cross-device link")

    with patch.object(Path, "rename", fake_rename):
        _safe_move(src, dst)

    assert dst.read_bytes() == b"cross-device content"
    assert not src.exists()


def test_safe_move_cross_device_cleans_up_on_error(tmp_path):
    """Temp file is removed if the copy fails midway."""
    src = tmp_path / "a.txt"
    src.write_bytes(b"data")

    original_rename = Path.rename

    def fake_rename(self: Path, target: Path) -> Path:
        raise OSError(errno.EXDEV, "Cross-device link")

    import shutil as _shutil

    def bad_copyfileobj(fsrc, fdst, length=None):
        raise IOError("disk full simulation")

    with patch.object(Path, "rename", fake_rename), \
         patch("media_organizer.organizer.shutil.copyfileobj", bad_copyfileobj):
        with pytest.raises(IOError, match="disk full"):
            _safe_move(src, tmp_path / "dst.txt")

    # No orphaned .mo_tmp files
    leftover = list(tmp_path.glob("*.mo_tmp"))
    assert leftover == [], f"Orphaned temp files: {leftover}"


# ---------------------------------------------------------------------------
# Bug fix: .dng must not be marked panoramic
# ---------------------------------------------------------------------------

def test_dng_is_not_panoramic(tmp_path):
    dng = tmp_path / "photo.dng"
    dng.write_bytes(b"")
    from media_organizer.metadata import PANORAMIC_360_EXTENSIONS
    assert ".dng" not in PANORAMIC_360_EXTENSIONS


# ---------------------------------------------------------------------------
# Bug fix: year validation in filename timestamp parsing
# ---------------------------------------------------------------------------

def test_filename_year_out_of_range_returns_no_match():
    from media_organizer.metadata import _parse_timestamp_from_filename

    # Future year far beyond current+1 should be rejected
    result = _parse_timestamp_from_filename("photo_29991231_120000.jpg")
    assert result is None

    # Pre-epoch year
    result = _parse_timestamp_from_filename("photo_19690101.jpg")
    assert result is None


def test_filename_year_in_valid_range():
    from media_organizer.metadata import _parse_timestamp_from_filename

    result = _parse_timestamp_from_filename("IMG_20230615_123000")
    assert result is not None
    assert result.year == 2023


# ---------------------------------------------------------------------------
# Bug fix: cli.py has logger defined (NameError regression)
# ---------------------------------------------------------------------------

def test_cli_logger_defined():
    """cli.py must define module-level logger to avoid NameError on errors."""
    import media_organizer.cli as cli_module
    assert hasattr(cli_module, "logger")
    assert isinstance(cli_module.logger, logging.Logger)


# ---------------------------------------------------------------------------
# Bug fix: _pick_canonical prefers older mtime over shorter path
# ---------------------------------------------------------------------------

def test_pick_canonical_prefers_older_mtime(tmp_path):
    import time

    older = tmp_path / "original.jpg"
    newer = tmp_path / "copy" / "original.jpg"
    newer.parent.mkdir()

    older.write_bytes(b"x")
    time.sleep(0.02)
    newer.write_bytes(b"x")

    items = [_make_metadata(newer), _make_metadata(older)]
    canonical = _pick_canonical(items)
    assert canonical.source_path == older


def test_pick_canonical_prefer_under_wins_over_mtime(tmp_path):
    import time

    preferred_dir = tmp_path / "primary"
    preferred_dir.mkdir()
    other_dir = tmp_path / "backup"
    other_dir.mkdir()

    # Write the "primary" file AFTER the backup so it has a newer mtime
    backup = other_dir / "photo.jpg"
    backup.write_bytes(b"x")
    time.sleep(0.02)
    primary = preferred_dir / "photo.jpg"
    primary.write_bytes(b"x")

    items = [_make_metadata(backup), _make_metadata(primary)]
    canonical = _pick_canonical(items, prefer_under=preferred_dir)
    # Even though primary is newer, prefer_under makes it canonical
    assert canonical.source_path == primary


# ---------------------------------------------------------------------------
# Bug fix: link_kind is honoured explicitly
# ---------------------------------------------------------------------------

def test_create_link_hard(tmp_path):
    src = tmp_path / "original.jpg"
    dst = tmp_path / "hardlink.jpg"
    src.write_bytes(b"data")
    MediaOrganizer._create_link(src, dst, link_kind="hard")
    assert dst.stat().st_ino == src.stat().st_ino


def test_create_link_symbolic(tmp_path):
    src = tmp_path / "original.jpg"
    dst = tmp_path / "symlink.jpg"
    src.write_bytes(b"data")
    MediaOrganizer._create_link(src, dst, link_kind="symbolic")
    assert dst.is_symlink()


# ---------------------------------------------------------------------------
# Logging setup: RichHandler + RotatingFileHandler
# ---------------------------------------------------------------------------

def test_setup_logging_creates_log_file(tmp_path):
    log_dir = tmp_path / "logs"
    setup_logging("INFO", log_dir=log_dir)

    logger = logging.getLogger("test_phase1_logger")
    logger.info("test message")

    log_file = log_dir / "media-organizer.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "test message" in content


def test_setup_logging_returns_correlation_id(tmp_path):
    cid = setup_logging("INFO", log_dir=tmp_path / "logs")
    assert isinstance(cid, str)
    assert len(cid) == 36  # uuid4 format


def test_setup_logging_log_file_is_json(tmp_path):
    import json as _json

    log_dir = tmp_path / "logs"
    setup_logging("INFO", log_dir=log_dir)
    logging.getLogger("phase1_json_test").info("structured entry")

    log_file = log_dir / "media-organizer.log"
    line = log_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    obj = _json.loads(line)
    assert "ts" in obj
    assert "level" in obj
    assert obj["msg"] == "structured entry"


def test_setup_logging_correlation_id_in_log(tmp_path):
    import json as _json

    log_dir = tmp_path / "logs"
    cid = setup_logging("INFO", log_dir=log_dir)
    logging.getLogger("corr_test").info("with id")

    log_file = log_dir / "media-organizer.log"
    line = log_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    obj = _json.loads(line)
    assert obj["correlation_id"] == cid


# ---------------------------------------------------------------------------
# Pydantic v2: field_validator works without deprecation warnings
# ---------------------------------------------------------------------------

def test_organizer_config_accepts_link_kind():
    cfg = OrganizerConfig(
        source=Path("/tmp"),
        destination=Path("/tmp/dst"),
        action="link",
        link_kind="hard",
        template="default",
    )
    assert cfg.link_kind == "hard"


def test_organizer_config_default_link_kind():
    cfg = OrganizerConfig(
        source=Path("/tmp"),
        destination=Path("/tmp/dst"),
        template="default",
    )
    assert cfg.link_kind == "symbolic"
