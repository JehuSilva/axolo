from datetime import datetime, timezone

from typer.testing import CliRunner

from media_organizer.cli import app
from media_organizer.metadata import MediaCategory, MediaMetadata, MediaType, TimestampSource
from media_organizer.similarity import SimilarityReport, SimilarPair
from media_organizer.timeline import TimelineReport, TimelinePoint


def _make_metadata(path, captured_at):
    return MediaMetadata(
        source_path=path,
        media_type=MediaType.IMAGE,
        category=MediaCategory.PHOTOS_VIDEOS,
        captured_at=captured_at,
        timestamp_source=TimestampSource.METADATA,
    )


def test_similars_command_outputs_pairs(tmp_path, monkeypatch):
    source = tmp_path / "photos"
    source.mkdir()
    file_a = source / "a.jpg"
    file_b = source / "b.jpg"
    file_a.write_bytes(b"00")
    file_b.write_bytes(b"00")

    captured = datetime(2024, 1, 1, tzinfo=timezone.utc)
    metadata_map = {
        file_a: _make_metadata(file_a, captured),
        file_b: _make_metadata(file_b, captured),
    }

    def fake_extract(path):
        return metadata_map[path]

    class DummyAnalyzer:
        def __init__(self, **kwargs):
            pass

        def analyze(self, items):
            pair = SimilarPair(first=metadata_map[file_a], second=metadata_map[file_b], distance=3)
            return SimilarityReport(pairs=[pair], processed=2, scanned=2, skipped=0, threshold=3)

    monkeypatch.setattr("media_organizer.cli.extract_metadata", fake_extract)
    monkeypatch.setattr("media_organizer.cli.SimilarityAnalyzer", DummyAnalyzer)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "similars",
            "--source",
            str(source),
            "--threshold",
            "3",
        ],
    )

    assert result.exit_code == 0
    assert "Pares detectados: 1" in result.stdout
    assert str(file_a) in result.stdout


def test_timeline_command_shows_table(tmp_path, monkeypatch):
    source = tmp_path / "photos"
    source.mkdir()
    file_a = source / "a.jpg"
    file_b = source / "b.jpg"
    file_a.write_bytes(b"00")
    file_b.write_bytes(b"00")

    metadata = [
        _make_metadata(file_a, datetime(2023, 5, 1, tzinfo=timezone.utc)),
        _make_metadata(file_b, datetime(2023, 6, 1, tzinfo=timezone.utc)),
    ]

    def fake_extract(path):
        for item in metadata:
            if item.source_path == path:
                return item
        raise KeyError(path)

    class DummyTimelineAnalyzer:
        def __init__(self, granularity):
            self.granularity = granularity

        def summarize(self, _items):
            points = [
                TimelinePoint(label="2023-05", start=metadata[0].captured_at, end=metadata[0].captured_at, count=1),
                TimelinePoint(label="2023-06", start=metadata[1].captured_at, end=metadata[1].captured_at, count=1),
            ]
            return TimelineReport(points=points, granularity=self.granularity, total_items=2)

    monkeypatch.setattr("media_organizer.cli.extract_metadata", fake_extract)
    monkeypatch.setattr("media_organizer.cli.TimelineAnalyzer", DummyTimelineAnalyzer)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "timeline",
            "--source",
            str(source),
            "--granularity",
            "month",
        ],
    )

    assert result.exit_code == 0
    assert "Capturas por periodo" in result.stdout
    assert "2023-05" in result.stdout
