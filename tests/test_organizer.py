import os
from datetime import datetime, timezone
from pathlib import Path

from axolo.config import OrganizerConfig
from axolo.media_scanner import ScanOptions, iter_media_files
from axolo.organizer import AxoloOrganizer
from axolo.metadata import MediaCategory, MediaMetadata, MediaType, TimestampSource


def _set_file_timestamp(path: Path, dt: datetime) -> None:
    timestamp = dt.replace(tzinfo=timezone.utc).timestamp()
    os.utime(path, (timestamp, timestamp))


def test_media_organizer_resolves_collisions(tmp_path, monkeypatch):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()

    file_path = source / "photo.jpg"
    file_path.write_bytes(b"test")
    _set_file_timestamp(file_path, datetime(2023, 1, 2, 12, 0))

    # default template is {year}/{month_name_cap} → 2023/Enero
    preexisting_dir = destination / "Photos" / "2023" / "Enero"
    preexisting_dir.mkdir(parents=True)
    (preexisting_dir / "photo.jpg").write_bytes(b"existing")

    def fake_extract(path: Path) -> MediaMetadata:
        return MediaMetadata(
            source_path=path,
            media_type=MediaType.IMAGE,
            category=MediaCategory.PHOTOS_VIDEOS,
            captured_at=datetime(2023, 1, 2, 12, 0, tzinfo=timezone.utc),
            original_name=path.name,
            timestamp_source=TimestampSource.METADATA,
        )

    monkeypatch.setattr("axolo.organizer.extract_metadata", fake_extract)

    config = OrganizerConfig(
        source=source,
        destination=destination,
        action="copy",
        template="default",
        dry_run=False,
    )

    organizer = AxoloOrganizer(config=config)
    files = list(iter_media_files(source, ScanOptions(recursive=True)))
    summary = organizer.organize(files)

    assert summary.copied == 1
    new_file = destination / "Photos" / "2023" / "Enero" / "photo_1.jpg"
    assert new_file.exists()
    assert summary.failed == 0
    assert summary.status_counts()["copied"] == 1
    assert summary.results[0].category == MediaCategory.PHOTOS_VIDEOS


def test_media_organizer_routes_panoramic_video_to_360_videos(tmp_path, monkeypatch):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()

    file_path = source / "VID_20260415_182446_00_001.insv"
    file_path.write_bytes(b"fake insv data")

    def fake_extract(path: Path) -> MediaMetadata:
        return MediaMetadata(
            source_path=path,
            media_type=MediaType.VIDEO,
            category=MediaCategory.PHOTOS_VIDEOS,
            captured_at=datetime(2026, 4, 15, 18, 24, 46, tzinfo=timezone.utc),
            original_name=path.name,
            timestamp_source=TimestampSource.FILENAME,
            is_panoramic=True,
        )

    monkeypatch.setattr("axolo.organizer.extract_metadata", fake_extract)

    config = OrganizerConfig(
        source=source,
        destination=destination,
        action="copy",
        template="default",
        dry_run=True,
    )

    organizer = AxoloOrganizer(config=config)
    files = list(iter_media_files(source, ScanOptions(recursive=True)))
    summary = organizer.organize(files)

    # default template is {year}/{month_name_cap} → 2026/Abril
    expected = destination / "360" / "Videos" / "2026" / "Abril" / file_path.name
    assert summary.results[0].destination == expected


def test_media_organizer_routes_panoramic_photo_to_360_photos(tmp_path, monkeypatch):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()

    file_path = source / "IMG_20260415_182550_00_003.insp"
    file_path.write_bytes(b"fake insp data")

    def fake_extract(path: Path) -> MediaMetadata:
        return MediaMetadata(
            source_path=path,
            media_type=MediaType.IMAGE,
            category=MediaCategory.PHOTOS_VIDEOS,
            captured_at=datetime(2026, 4, 15, 18, 25, 50, tzinfo=timezone.utc),
            original_name=path.name,
            timestamp_source=TimestampSource.METADATA,
            is_panoramic=True,
        )

    monkeypatch.setattr("axolo.organizer.extract_metadata", fake_extract)

    config = OrganizerConfig(
        source=source,
        destination=destination,
        action="copy",
        template="default",
        dry_run=True,
    )

    organizer = AxoloOrganizer(config=config)
    files = list(iter_media_files(source, ScanOptions(recursive=True)))
    summary = organizer.organize(files)

    # default template is {year}/{month_name_cap} → 2026/Abril
    expected = destination / "360" / "Photos" / "2026" / "Abril" / file_path.name
    assert summary.results[0].destination == expected


def test_media_organizer_sets_destination_mtime_to_captured_at(tmp_path, monkeypatch):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()

    file_path = source / "photo.jpg"
    file_path.write_bytes(b"test")
    _set_file_timestamp(file_path, datetime(2000, 1, 1, 0, 0))

    captured = datetime(2024, 11, 1, 11, 9, 22, tzinfo=timezone.utc)

    def fake_extract(path: Path) -> MediaMetadata:
        return MediaMetadata(
            source_path=path,
            media_type=MediaType.IMAGE,
            category=MediaCategory.PHOTOS_VIDEOS,
            captured_at=captured,
            original_name=path.name,
            timestamp_source=TimestampSource.METADATA,
        )

    monkeypatch.setattr("axolo.organizer.extract_metadata", fake_extract)

    config = OrganizerConfig(
        source=source,
        destination=destination,
        action="copy",
        template="default",
        dry_run=False,
    )

    organizer = AxoloOrganizer(config=config)
    files = list(iter_media_files(source, ScanOptions(recursive=True)))
    summary = organizer.organize(files)

    assert summary.copied == 1
    dest = summary.results[0].destination
    assert dest.exists()
    assert dest.stat().st_mtime == captured.timestamp()


def test_media_organizer_sends_unreliable_files_to_unknown(tmp_path, monkeypatch):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()

    file_path = source / "photo.jpg"
    file_path.write_bytes(b"test")

    def fake_extract(path: Path) -> MediaMetadata:
        return MediaMetadata(
            source_path=path,
            media_type=MediaType.IMAGE,
            category=MediaCategory.PHOTOS_VIDEOS,
            captured_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            original_name=path.name,
            timestamp_source=TimestampSource.FILE_MODIFICATION,
        )

    monkeypatch.setattr("axolo.organizer.extract_metadata", fake_extract)

    config = OrganizerConfig(
        source=source,
        destination=destination,
        action="copy",
        template="default",
        dry_run=True,
    )

    organizer = AxoloOrganizer(config=config)
    files = list(iter_media_files(source, ScanOptions(recursive=True)))
    summary = organizer.organize(files)

    assert summary.dry_run == 1
    expected = destination / "Photos" / "unknown_date" / "photo.jpg"
    assert summary.results[0].destination == expected
    assert summary.status_counts()["dry-run"] == 1
    assert summary.results[0].category == MediaCategory.PHOTOS_VIDEOS


def test_hidden_file_is_detected_in_metadata(tmp_path):
    """extract_metadata assigns MediaCategory.HIDDEN to files whose name starts with '.'."""
    from axolo.metadata import extract_metadata, MediaCategory

    hidden = tmp_path / ".DS_Store"
    hidden.write_bytes(b"bplist00")

    meta = extract_metadata(hidden)
    assert meta.category == MediaCategory.HIDDEN


def test_media_organizer_routes_hidden_file_to_hidden(tmp_path, monkeypatch):
    """Hidden files are routed to Hidden/ when include_hidden is enabled."""
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()

    file_path = source / ".DS_Store"
    file_path.write_bytes(b"bplist00")

    def fake_extract(path: Path) -> MediaMetadata:
        return MediaMetadata(
            source_path=path,
            media_type=MediaType.OTHER,
            category=MediaCategory.HIDDEN,
            captured_at=datetime(2024, 3, 10, 9, 0, tzinfo=timezone.utc),
            original_name=path.name,
            timestamp_source=TimestampSource.METADATA,
        )

    monkeypatch.setattr("axolo.organizer.extract_metadata", fake_extract)

    config = OrganizerConfig(
        source=source,
        destination=destination,
        action="copy",
        template="default",
        dry_run=True,
    )

    organizer = AxoloOrganizer(config=config)
    from axolo.media_scanner import ScanOptions, iter_media_files
    files = list(iter_media_files(source, ScanOptions(include_hidden=True)))
    summary = organizer.organize(files)

    assert summary.dry_run == 1
    # Hidden files go to destination/Hidden/{year}/{month_name_cap}/
    expected = destination / "Hidden" / "2024" / "Marzo" / ".DS_Store"
    assert summary.results[0].destination == expected
    assert summary.results[0].category == MediaCategory.HIDDEN
