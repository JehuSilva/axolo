from datetime import datetime, timedelta, timezone
from pathlib import Path

from media_organizer.clustering import ClusterParameters, PhotoClusterer
from media_organizer.metadata import MediaCategory, MediaMetadata, MediaType, TimestampSource


def _make_metadata(path: Path, captured_at: datetime, camera_model: str = "Cam A") -> MediaMetadata:
    return MediaMetadata(
        source_path=path,
        media_type=MediaType.IMAGE,
        category=MediaCategory.PHOTOS_VIDEOS,
        captured_at=captured_at,
        camera_model=camera_model,
        timestamp_source=TimestampSource.METADATA,
    )


def test_photo_clusterer_groups_by_time(tmp_path):
    base = datetime(2023, 5, 1, 10, 0, tzinfo=timezone.utc)
    files = [
        tmp_path / "photo_1.jpg",
        tmp_path / "photo_2.jpg",
        tmp_path / "photo_3.jpg",
        tmp_path / "photo_4.jpg",
        tmp_path / "photo_5.jpg",
        tmp_path / "photo_6.jpg",
        tmp_path / "lonely.jpg",
    ]
    for file in files:
        file.write_bytes(b"\x00")

    items = [
        _make_metadata(files[0], base),
        _make_metadata(files[1], base + timedelta(minutes=15)),
        _make_metadata(files[2], base + timedelta(minutes=30)),
        _make_metadata(files[3], base + timedelta(hours=5)),
        _make_metadata(files[4], base + timedelta(hours=5, minutes=20)),
        _make_metadata(files[5], base + timedelta(hours=5, minutes=45)),
        _make_metadata(files[6], base + timedelta(hours=15)),
    ]

    params = ClusterParameters(time_window_minutes=60, min_samples=2)
    clusterer = PhotoClusterer(params=params)
    summary = clusterer.cluster(items)

    assert summary.considered_items == 7
    assert len(summary.clusters) == 2
    assert summary.clusters[0].label == "C01"
    assert summary.clusters[0].size == 3
    assert summary.clusters[1].size == 3
    assert len(summary.noise) == 1
    assert summary.noise[0].source_path.name == "lonely.jpg"


def test_cluster_summary_serialization(tmp_path):
    base = datetime(2023, 5, 1, 10, 0, tzinfo=timezone.utc)
    file_path = tmp_path / "photo.jpg"
    file_path.write_bytes(b"\x00")
    item = _make_metadata(file_path, base, camera_model="Cam Z")

    params = ClusterParameters(time_window_minutes=120, min_samples=1)
    clusterer = PhotoClusterer(params=params)
    summary = clusterer.cluster([item])

    assert len(summary.clusters) == 1
    result = summary.to_dict()
    assert result["total_items"] == 1
    assert result["considered_items"] == 1
    assert len(result["clusters"]) == 1
    serialized_cluster = result["clusters"][0]
    assert serialized_cluster["cluster_id"] == "C01"
    assert serialized_cluster["size"] == 1
    assert serialized_cluster["members"][0]["path"].endswith("photo.jpg")
