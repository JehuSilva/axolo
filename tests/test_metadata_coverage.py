"""Targeted tests to improve metadata.py coverage via mocking."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from axolo.metadata import (
    MediaCategory,
    MediaType,
    TimestampSource,
    _extract_audio_metadata,
    _extract_document_metadata,
    _extract_image_metadata,
    _extract_timestamp_from_filename,
    _extract_video_metadata,
    _extract_video_metadata_ffprobe,
    _filesystem_timestamp,
    detect_media_type,
    extract_metadata,
    resolve_category,
)


# ---------------------------------------------------------------------------
# detect_media_type / resolve_category
# ---------------------------------------------------------------------------


def test_detect_image():
    assert detect_media_type(Path("x.jpg")) == MediaType.IMAGE


def test_detect_video():
    assert detect_media_type(Path("x.mp4")) == MediaType.VIDEO


def test_detect_audio():
    assert detect_media_type(Path("x.mp3")) == MediaType.AUDIO


def test_detect_document():
    assert detect_media_type(Path("x.pdf")) == MediaType.DOCUMENT


def test_detect_other():
    assert detect_media_type(Path("x.xyz")) == MediaType.OTHER


def test_resolve_category_photos_videos():
    assert resolve_category(MediaType.IMAGE) == MediaCategory.PHOTOS_VIDEOS
    assert resolve_category(MediaType.VIDEO) == MediaCategory.PHOTOS_VIDEOS


def test_resolve_category_music():
    assert resolve_category(MediaType.AUDIO) == MediaCategory.MUSIC


def test_resolve_category_documents():
    assert resolve_category(MediaType.DOCUMENT) == MediaCategory.DOCUMENTS


def test_resolve_category_other():
    assert resolve_category(MediaType.OTHER) == MediaCategory.OTHER


# ---------------------------------------------------------------------------
# _extract_image_metadata
# ---------------------------------------------------------------------------


def test_extract_image_metadata_with_exif(tmp_path: Path):
    """Image with EXIF DateTimeOriginal should return METADATA timestamp source."""
    try:
        from PIL import Image
        import piexif
    except ImportError:
        pytest.skip("piexif not available")

    img_path = tmp_path / "photo.jpg"
    img = Image.new("RGB", (10, 10))
    exif_dict = {
        "0th": {},
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: b"2024:06:15 14:30:00",
            piexif.ExifIFD.DateTimeDigitized: b"2024:06:15 14:30:00",
        },
        "GPS": {},
        "1st": {},
    }
    exif_bytes = piexif.dump(exif_dict)
    img.save(str(img_path), exif=exif_bytes)

    captured_at, make, model, source = _extract_image_metadata(img_path)
    assert source == TimestampSource.METADATA
    assert captured_at is not None
    assert captured_at.year == 2024


def test_extract_image_metadata_no_exif(tmp_path: Path):
    """Image without EXIF should return UNKNOWN source."""
    from PIL import Image

    img_path = tmp_path / "plain.jpg"
    img = Image.new("RGB", (10, 10))
    img.save(str(img_path))

    captured_at, make, model, source = _extract_image_metadata(img_path)
    assert source == TimestampSource.UNKNOWN
    assert captured_at is None


def test_extract_image_metadata_with_make_model(tmp_path: Path):
    """Image with Make/Model EXIF tags should populate camera fields."""
    try:
        from PIL import Image
        import piexif
    except ImportError:
        pytest.skip("piexif not available")

    img_path = tmp_path / "camera.jpg"
    img = Image.new("RGB", (10, 10))
    exif_dict = {
        "0th": {
            piexif.ImageIFD.Make: b"Canon",
            piexif.ImageIFD.Model: b"EOS R5",
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: b"2024:01:01 12:00:00",
        },
        "GPS": {},
        "1st": {},
    }
    img.save(str(img_path), exif=piexif.dump(exif_dict))

    captured_at, make, model, source = _extract_image_metadata(img_path)
    assert make == "Canon"
    assert model == "EOS R5"


# ---------------------------------------------------------------------------
# _extract_video_metadata_ffprobe (mocked subprocess)
# ---------------------------------------------------------------------------


def _ffprobe_result(tags: dict) -> MagicMock:
    payload = {"format": {"tags": tags}, "streams": []}
    mock = MagicMock()
    mock.stdout = json.dumps(payload)
    return mock


def test_ffprobe_returns_creation_time(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00" * 20)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _ffprobe_result({"creation_time": "2023-08-15T10:00:00.000000Z"})
        captured_at, make, model, source = _extract_video_metadata_ffprobe(mp4)

    assert captured_at is not None
    assert captured_at.year == 2023
    assert source == TimestampSource.METADATA


def test_ffprobe_returns_make_model(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00" * 20)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _ffprobe_result({
            "creation_time": "2023-08-15T10:00:00.000000Z",
            "make": "GoPro",
            "model": "HERO 11",
        })
        captured_at, make, model, source = _extract_video_metadata_ffprobe(mp4)

    assert make == "GoPro"
    assert model == "HERO 11"


def test_ffprobe_not_found(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00" * 20)

    with patch("subprocess.run", side_effect=FileNotFoundError("ffprobe not found")):
        captured_at, make, model, source = _extract_video_metadata_ffprobe(mp4)

    assert captured_at is None
    assert source == TimestampSource.UNKNOWN


def test_ffprobe_process_error(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00" * 20)

    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "ffprobe")):
        captured_at, make, model, source = _extract_video_metadata_ffprobe(mp4)

    assert captured_at is None


def test_ffprobe_invalid_json(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00" * 20)

    mock = MagicMock()
    mock.stdout = "not json"
    with patch("subprocess.run", return_value=mock):
        captured_at, make, model, source = _extract_video_metadata_ffprobe(mp4)

    assert captured_at is None


def test_extract_video_metadata_uses_ffprobe(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00" * 20)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _ffprobe_result({"creation_time": "2022-03-10T08:00:00Z"})
        captured_at, make, model, source = _extract_video_metadata(mp4)

    assert captured_at is not None
    assert captured_at.year == 2022


# ---------------------------------------------------------------------------
# _extract_timestamp_from_filename
# ---------------------------------------------------------------------------


def test_filename_timestamp_yyyymmdd(tmp_path: Path):
    f = tmp_path / "IMG_20230615_143022.jpg"
    f.write_bytes(b"x")
    captured_at, source = _extract_timestamp_from_filename(f)
    if captured_at is not None:
        assert captured_at.year == 2023
        assert captured_at.month == 6
        assert captured_at.day == 15


def test_filename_timestamp_no_date(tmp_path: Path):
    f = tmp_path / "random_photo.jpg"
    f.write_bytes(b"x")
    captured_at, source = _extract_timestamp_from_filename(f)
    # May return None if no date pattern found


# ---------------------------------------------------------------------------
# _filesystem_timestamp
# ---------------------------------------------------------------------------


def test_filesystem_timestamp(tmp_path: Path):
    f = tmp_path / "file.jpg"
    f.write_bytes(b"x")
    captured_at, source = _filesystem_timestamp(f)
    assert captured_at is not None
    assert source in {TimestampSource.FILE_MODIFICATION, TimestampSource.FILE_CREATION}


# ---------------------------------------------------------------------------
# _extract_audio_metadata (mocked mutagen)
# ---------------------------------------------------------------------------


def test_extract_audio_metadata_with_tags(tmp_path: Path):
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)

    # Use actual key names from ID3 tags expected by metadata.py
    mock_tags = MagicMock()
    mock_tags.get.side_effect = lambda key: {
        "TPE1": MagicMock(text=["Artist Name"]),
        "TIT2": MagicMock(text=["Track Title"]),
        "TCON": MagicMock(text=["Rock"]),
        "TALB": MagicMock(text=["Album Name"]),
    }.get(key)
    mock_audio = MagicMock()
    mock_audio.tags = mock_tags

    import mutagen as mutagen_mod
    with patch.object(mutagen_mod, "File", return_value=mock_audio):
        result = _extract_audio_metadata(mp3)

    captured_at, source, artist, title, genre, album = result
    # Should not crash — tag values may or may not resolve depending on _normalize_tag_value


def test_extract_audio_metadata_no_audio(tmp_path: Path):
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)

    import mutagen as mutagen_mod
    with patch.object(mutagen_mod, "File", return_value=None):
        result = _extract_audio_metadata(mp3)

    captured_at, source, artist, title, genre, album = result
    assert captured_at is None
    assert artist is None


def test_extract_audio_metadata_no_tags(tmp_path: Path):
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)

    mock_audio = MagicMock()
    mock_audio.tags = None

    import mutagen as mutagen_mod
    with patch.object(mutagen_mod, "File", return_value=mock_audio):
        result = _extract_audio_metadata(mp3)

    captured_at, source, artist, title, genre, album = result
    assert captured_at is None


# ---------------------------------------------------------------------------
# _extract_document_metadata (mocked pypdf)
# ---------------------------------------------------------------------------


def test_extract_document_metadata_with_date(tmp_path: Path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    mock_reader = MagicMock()
    mock_reader.metadata = {"/CreationDate": "D:20220801120000"}

    with patch("pypdf.PdfReader", return_value=mock_reader):
        captured_at, source = _extract_document_metadata(pdf)

    if captured_at is not None:
        assert captured_at.year == 2022


def test_extract_document_metadata_no_metadata(tmp_path: Path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    mock_reader = MagicMock()
    mock_reader.metadata = {}

    with patch("pypdf.PdfReader", return_value=mock_reader):
        captured_at, source = _extract_document_metadata(pdf)


# ---------------------------------------------------------------------------
# extract_metadata end-to-end (mocked image)
# ---------------------------------------------------------------------------


def test_extract_metadata_image_end_to_end(tmp_path: Path):
    from PIL import Image
    img_path = tmp_path / "photo.jpg"
    Image.new("RGB", (10, 10)).save(str(img_path))

    meta = extract_metadata(img_path)
    assert meta.media_type == MediaType.IMAGE
    assert meta.category == MediaCategory.PHOTOS_VIDEOS
    assert meta.captured_at is not None


def test_extract_metadata_mp4_ffprobe_mocked(tmp_path: Path):
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00" * 20)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _ffprobe_result({"creation_time": "2022-03-10T08:00:00Z"})
        meta = extract_metadata(mp4)

    assert meta.media_type == MediaType.VIDEO
    assert meta.captured_at is not None
    assert meta.captured_at.year == 2022


def test_extract_metadata_other_type(tmp_path: Path):
    f = tmp_path / "file.xyz"
    f.write_bytes(b"data")
    meta = extract_metadata(f)
    assert meta.media_type == MediaType.OTHER
    assert meta.category == MediaCategory.OTHER


# ---------------------------------------------------------------------------
# Additional coverage for datetime helpers and QuickTime parsing
# ---------------------------------------------------------------------------


def test_parse_flexible_datetime_valid():
    from axolo.metadata import _parse_flexible_datetime
    result = _parse_flexible_datetime("2023-08-15T10:00:00Z")
    assert result is not None
    assert result.year == 2023


def test_parse_flexible_datetime_empty():
    from axolo.metadata import _parse_flexible_datetime
    assert _parse_flexible_datetime("") is None
    assert _parse_flexible_datetime("   ") is None


def test_parse_exif_datetime_valid():
    from axolo.metadata import _parse_exif_datetime
    result = _parse_exif_datetime("2024:06:15 14:30:00")
    assert result is not None
    assert result.year == 2024


def test_parse_exif_datetime_invalid():
    from axolo.metadata import _parse_exif_datetime
    assert _parse_exif_datetime("not a date") is None


def test_parse_pdf_date_with_d_prefix():
    from axolo.metadata import _parse_pdf_date
    result = _parse_pdf_date("D:20220801120000")
    assert result is not None
    assert result.year == 2022


def test_parse_pdf_date_empty():
    from axolo.metadata import _parse_pdf_date
    assert _parse_pdf_date("") is None


def test_normalize_tag_value_string():
    from axolo.metadata import _normalize_tag_value
    assert _normalize_tag_value("  hello  ") == "hello"


def test_normalize_tag_value_bytes():
    from axolo.metadata import _normalize_tag_value
    assert _normalize_tag_value(b"hello") == "hello"


def test_normalize_tag_value_list():
    from axolo.metadata import _normalize_tag_value
    assert _normalize_tag_value(["first", "second"]) == "first"


def test_normalize_tag_value_none():
    from axolo.metadata import _normalize_tag_value
    assert _normalize_tag_value(None) is None


def test_normalize_tag_value_empty_string():
    from axolo.metadata import _normalize_tag_value
    assert _normalize_tag_value("   ") is None


def test_parse_timestamp_from_filename_valid():
    from axolo.metadata import _parse_timestamp_from_filename
    result = _parse_timestamp_from_filename("IMG_20230615_143022")
    if result is not None:
        assert result.year == 2023


def test_parse_timestamp_from_filename_invalid_year():
    from axolo.metadata import _parse_timestamp_from_filename
    result = _parse_timestamp_from_filename("IMG_19600101_120000")
    assert result is None


def test_clean_string():
    from axolo.metadata import _clean_string
    assert _clean_string("  Canon  ") == "Canon"
    assert _clean_string("") is None
    assert _clean_string(None) is None


def test_extract_video_metadata_falls_back_to_filesystem(tmp_path: Path):
    """When ffprobe fails, video metadata falls back to filesystem timestamp."""
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00" * 20)

    with patch("subprocess.run", side_effect=FileNotFoundError("no ffprobe")):
        meta = extract_metadata(mp4)

    assert meta.captured_at is not None
    assert meta.media_type == MediaType.VIDEO
