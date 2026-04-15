from datetime import datetime, timedelta, timezone
from pathlib import Path

from media_organizer.metadata import MediaCategory, MediaMetadata, MediaType, TimestampSource
from media_organizer.timeline import TimelineAnalyzer


def _metadata(path: Path, captured_at: datetime) -> MediaMetadata:
    return MediaMetadata(
        source_path=path,
        media_type=MediaType.IMAGE,
        category=MediaCategory.PHOTOS_VIDEOS,
        captured_at=captured_at,
        timestamp_source=TimestampSource.METADATA,
    )


def test_timeline_analyzer_groups_by_day(tmp_path):
    base = datetime(2023, 7, 1, 10, 0, tzinfo=timezone.utc)
    items = [
        _metadata(tmp_path / "a.jpg", base),
        _metadata(tmp_path / "b.jpg", base + timedelta(hours=2)),
        _metadata(tmp_path / "c.jpg", base + timedelta(days=1)),
    ]

    analyzer = TimelineAnalyzer(granularity="day")
    report = analyzer.summarize(items)

    assert len(report.points) == 2
    assert report.points[0].label == "2023-07-01"
    assert report.points[0].count == 2
    assert report.points[1].label == "2023-07-02"
    assert report.total_items == 3


def test_timeline_analyzer_orders_months(tmp_path):
    base = datetime(2022, 11, 15, 12, 0, tzinfo=timezone.utc)
    items = [
        _metadata(tmp_path / "a.jpg", base),
        _metadata(tmp_path / "b.jpg", base + timedelta(days=40)),
    ]
    analyzer = TimelineAnalyzer(granularity="month")
    report = analyzer.summarize(items)

    assert [point.label for point in report.points] == ["2022-11", "2022-12"]
    assert report.points[0].count == 1
    assert report.points[1].count == 1
