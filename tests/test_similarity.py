from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from media_organizer.metadata import MediaCategory, MediaMetadata, MediaType, TimestampSource
from media_organizer.similarity import SimilarityAnalyzer


def _make_image(path: Path, color: tuple[int, int, int]) -> None:
    image = Image.new("RGB", (32, 32), color)
    image.save(path, format="JPEG")


def _metadata(path: Path, captured_at: datetime) -> MediaMetadata:
    return MediaMetadata(
        source_path=path,
        media_type=MediaType.IMAGE,
        category=MediaCategory.PHOTOS_VIDEOS,
        captured_at=captured_at,
        timestamp_source=TimestampSource.METADATA,
    )


def test_similarity_analyzer_detects_similar_images(tmp_path):
    base = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    img_a = tmp_path / "a.jpg"
    img_b = tmp_path / "b.jpg"
    img_c = tmp_path / "c.jpg"

    _make_image(img_a, (200, 10, 10))
    _make_image(img_b, (205, 15, 15))  # similar tone
    _make_image(img_c, (10, 200, 10))  # different tone

    items = [
        _metadata(img_a, base),
        _metadata(img_b, base + timedelta(seconds=5)),
        _metadata(img_c, base + timedelta(minutes=2)),
    ]

    analyzer = SimilarityAnalyzer(threshold=4, hash_size=8)
    report = analyzer.analyze(items)

    assert report.processed == 3
    assert len(report.pairs) == 1
    pair = report.pairs[0]
    assert Path(pair.first.source_path) == img_a
    assert Path(pair.second.source_path) == img_b


def test_similarity_analyzer_skips_non_images(tmp_path):
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    img_a = tmp_path / "a.jpg"
    _make_image(img_a, (10, 10, 10))

    media_items = [
        MediaMetadata(
            source_path=img_a,
            media_type=MediaType.IMAGE,
            category=MediaCategory.PHOTOS_VIDEOS,
            captured_at=base,
            timestamp_source=TimestampSource.METADATA,
        ),
        MediaMetadata(
            source_path=tmp_path / "video.mp4",
            media_type=MediaType.VIDEO,
            category=MediaCategory.PHOTOS_VIDEOS,
            captured_at=base,
            timestamp_source=TimestampSource.METADATA,
        ),
    ]

    analyzer = SimilarityAnalyzer(threshold=3, hash_size=8)
    report = analyzer.analyze(media_items)

    assert report.processed == 1
    assert report.scanned == 2
    assert report.skipped >= 1
    assert report.pairs == []
