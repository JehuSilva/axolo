"""Tests for Insta360 X3 file format support (.insp, .insv, .dng)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from media_organizer.metadata import (
    MediaType,
    TimestampSource,
    detect_media_type,
    extract_metadata,
)

FILES_EXAMPLES = Path(__file__).parent.parent / "files_examples"


def _skip_if_missing(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"Archivo de ejemplo no encontrado: {path}")


# ---------------------------------------------------------------------------
# detect_media_type
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename, expected_type",
    [
        ("IMG_20260415_182550_00_003.insp", MediaType.IMAGE),
        ("IMG_20260415_182550_00_003.dng",  MediaType.IMAGE),
        ("VID_20260415_182446_00_001.insv",  MediaType.VIDEO),
        ("VID_20260415_182446_10_001.insv",  MediaType.VIDEO),
    ],
)
def test_detect_media_type_360_formats(filename: str, expected_type: MediaType) -> None:
    assert detect_media_type(Path(filename)) == expected_type


# ---------------------------------------------------------------------------
# extract_metadata — using real example files
# ---------------------------------------------------------------------------

def test_insp_metadata() -> None:
    path = FILES_EXAMPLES / "IMG_20260415_182550_00_003.insp"
    _skip_if_missing(path)

    meta = extract_metadata(path)

    assert meta.media_type == MediaType.IMAGE
    assert meta.is_panoramic is True
    assert meta.timestamp_source == TimestampSource.METADATA
    assert meta.captured_at.year == 2026
    assert meta.captured_at.month == 4
    assert meta.captured_at.day == 15
    assert meta.camera_make is not None and meta.camera_make != ""
    assert meta.camera_model is not None and meta.camera_model != ""


def test_dng_metadata() -> None:
    path = FILES_EXAMPLES / "IMG_20260415_182550_00_003.dng"
    _skip_if_missing(path)

    meta = extract_metadata(path)

    assert meta.media_type == MediaType.IMAGE
    assert meta.is_panoramic is True
    # Pillow cannot open DNG natively — timestamp comes from filename pattern
    assert meta.captured_at.year == 2026
    assert meta.captured_at.month == 4
    assert meta.captured_at.day == 15
    assert meta.captured_at.hour == 18
    assert meta.captured_at.minute == 25


@pytest.mark.parametrize(
    "filename, expected_hour, expected_minute",
    [
        ("VID_20260415_182446_00_001.insv", 18, 24),
        ("VID_20260415_182446_10_001.insv", 18, 24),
        ("VID_20260415_182523_00_002.insv", 18, 25),
        ("VID_20260415_182523_10_002.insv", 18, 25),
    ],
)
def test_insv_metadata(filename: str, expected_hour: int, expected_minute: int) -> None:
    path = FILES_EXAMPLES / filename
    _skip_if_missing(path)

    meta = extract_metadata(path)

    assert meta.media_type == MediaType.VIDEO
    assert meta.is_panoramic is True
    # Normalize to UTC for comparison — the QuickTime atom stores UTC and
    # the local machine timezone may differ from the camera's recording timezone.
    utc_dt = meta.captured_at.astimezone(timezone.utc)
    assert utc_dt.year == 2026
    assert utc_dt.month == 4
    assert utc_dt.day == 15
    assert utc_dt.hour == expected_hour
    assert utc_dt.minute == expected_minute
    # camera info: either from container metadata or fallback
    assert meta.camera_make is not None and meta.camera_make != ""
    assert meta.camera_model is not None and meta.camera_model != ""


def test_insv_360_camera_fallback() -> None:
    """When container metadata has no camera info, fallback values are populated."""
    path = FILES_EXAMPLES / "VID_20260415_182446_00_001.insv"
    _skip_if_missing(path)

    meta = extract_metadata(path)

    # Regardless of whether ffprobe is available, make/model must be filled.
    assert meta.camera_make is not None
    assert meta.camera_model is not None
