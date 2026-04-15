from datetime import datetime, timedelta, timezone

from typer.testing import CliRunner

from media_organizer.cli import app
from media_organizer.metadata import MediaCategory, MediaMetadata, MediaType, TimestampSource


def test_cluster_command_creates_preview(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()

    file_a = source / "a.jpg"
    file_b = source / "b.jpg"
    file_c = source / "c.jpg"

    for file in (file_a, file_b, file_c):
        file.write_bytes(b"\x00")

    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

    metadata_map = {
        file_a: MediaMetadata(
            source_path=file_a,
            media_type=MediaType.IMAGE,
            category=MediaCategory.PHOTOS_VIDEOS,
            captured_at=base,
            timestamp_source=TimestampSource.METADATA,
        ),
        file_b: MediaMetadata(
            source_path=file_b,
            media_type=MediaType.IMAGE,
            category=MediaCategory.PHOTOS_VIDEOS,
            captured_at=base + timedelta(minutes=30),
            timestamp_source=TimestampSource.METADATA,
        ),
        file_c: MediaMetadata(
            source_path=file_c,
            media_type=MediaType.IMAGE,
            category=MediaCategory.PHOTOS_VIDEOS,
            captured_at=base + timedelta(hours=5),
            timestamp_source=TimestampSource.METADATA,
        ),
    }

    def fake_extract(path):
        return metadata_map[path]

    monkeypatch.setattr("media_organizer.cli.extract_metadata", fake_extract)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "cluster",
            "--source",
            str(source),
            "--time-window",
            "120",
            "--min-samples",
            "2",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "Clusters detectados" in result.stdout
    assert "C01" in result.stdout
