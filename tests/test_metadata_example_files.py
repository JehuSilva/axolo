from datetime import datetime, timezone
from pathlib import Path

import pytest

from axolo.metadata import TimestampSource, extract_metadata


@pytest.mark.parametrize(
    ("filename", "expected_utc", "expected_source"),
    [
        (
            "AirBrush_20210823224920_1.jpg",
            datetime(2021, 8, 24, 4, 49, 20, tzinfo=timezone.utc),
            TimestampSource.FILENAME,
        ),
        (
            "CA17-20240109125401a_1.jpg",
            datetime(2024, 1, 9, 18, 54, 1, tzinfo=timezone.utc),
            TimestampSource.FILENAME,
        ),
        (
            "20221024-202545-730_1.mp4",
            datetime(2022, 10, 25, 1, 25, 45, tzinfo=timezone.utc),
            TimestampSource.CONTAINER_METADATA,
        ),
        (
            "IMG_3397_1.mov",
            datetime(2023, 9, 10, 16, 15, 34, tzinfo=timezone.utc),
            TimestampSource.CONTAINER_METADATA,
        ),
    ],
)
def test_example_files_have_reliable_metadata(filename, expected_utc, expected_source):
    path = Path("example_files") / filename
    metadata = extract_metadata(path)

    assert metadata.timestamp_source == expected_source
    assert metadata.has_reliable_timestamp is True
    assert metadata.captured_at.astimezone(timezone.utc) == expected_utc


def test_extract_metadata_heic_sample():
    path = Path(__file__).resolve().parents[1] / "data" / "examples" / "IMG_1504_1.heic"
    if not path.exists():
        pytest.skip("sample HEIC not present")

    metadata = extract_metadata(path)

    assert metadata.captured_at is not None
    assert metadata.timestamp_source == TimestampSource.METADATA
    assert metadata.camera_make
    assert metadata.camera_model
