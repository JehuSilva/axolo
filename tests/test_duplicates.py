"""Tests for the exact-duplicate detection module."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from axolo.duplicates import (
    ActionOutcome,
    DuplicateActionError,
    DuplicateAnalyzer,
    apply_duplicate_actions,
)
from axolo.metadata import MediaCategory, MediaMetadata, MediaType, TimestampSource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_DT = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


def _metadata(path: Path, captured_at: datetime = _BASE_DT) -> MediaMetadata:
    return MediaMetadata(
        source_path=path,
        media_type=MediaType.IMAGE,
        category=MediaCategory.PHOTOS_VIDEOS,
        captured_at=captured_at,
        timestamp_source=TimestampSource.METADATA,
    )


# ---------------------------------------------------------------------------
# DuplicateAnalyzer — core detection
# ---------------------------------------------------------------------------


def test_detect_exact_duplicates(tmp_path):
    """Three files with identical content form one group; one unique stays out."""
    content = b"hello duplicate world"
    unique_content = b"i am unique"

    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    c = tmp_path / "c.jpg"
    d = tmp_path / "d.jpg"

    a.write_bytes(content)
    b.write_bytes(content)
    c.write_bytes(content)
    d.write_bytes(unique_content)

    items = [_metadata(a), _metadata(b), _metadata(c), _metadata(d)]
    report = DuplicateAnalyzer().analyze(items)

    assert len(report.groups) == 1
    group = report.groups[0]
    # canonical + 2 duplicates = 3 total members
    assert len(group.duplicates) == 2
    assert group.reclaimable_bytes == len(content) * 2
    assert report.reclaimable_bytes == len(content) * 2
    assert report.processed == 3  # only the 3 same-size files get hashed
    assert report.scanned == 4


def test_no_duplicates_when_all_unique(tmp_path):
    """No groups when every file has different content."""
    for i in range(3):
        (tmp_path / f"file_{i}.jpg").write_bytes(f"unique content {i}".encode())

    items = [_metadata(tmp_path / f"file_{i}.jpg") for i in range(3)]
    report = DuplicateAnalyzer().analyze(items)

    assert len(report.groups) == 0
    assert report.reclaimable_bytes == 0


def test_size_prefilter_skips_hashing_for_unique_sizes(tmp_path):
    """Files with distinct sizes must not be hashed at all."""
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbbb")  # different size

    items = [_metadata(a), _metadata(b)]

    hash_call_count = []

    original_hash_file = __import__(
        "axolo.duplicates", fromlist=["_hash_file"]
    )._hash_file

    def counting_hash(path, algorithm, chunk_size):
        hash_call_count.append(path)
        return original_hash_file(path, algorithm, chunk_size)

    with patch("axolo.duplicates._hash_file", side_effect=counting_hash):
        report = DuplicateAnalyzer().analyze(items)

    assert len(hash_call_count) == 0, "No files should be hashed when no sizes match"
    assert len(report.groups) == 0


def test_canonical_selection_prefers_lexicographic_path(tmp_path):
    """Without prefer_under, the lexicographically first path is chosen as canonical."""
    content = b"same content"
    alpha = tmp_path / "a_photo.jpg"
    beta = tmp_path / "z_copy.jpg"

    alpha.write_bytes(content)
    beta.write_bytes(content)

    items = [_metadata(beta), _metadata(alpha)]  # beta comes first in the list
    report = DuplicateAnalyzer().analyze(items)

    assert len(report.groups) == 1
    # 'a_photo.jpg' < 'z_copy.jpg' lexicographically → alpha is canonical
    assert report.groups[0].canonical.metadata.source_path == alpha


def test_canonical_selection_prefer_under(tmp_path):
    """Files under --prefer-under are chosen as canonical even when copied elsewhere."""
    content = b"same content"
    primary = tmp_path / "primary" / "photo.jpg"
    backup = tmp_path / "backup" / "photo.jpg"

    primary.parent.mkdir()
    backup.parent.mkdir()
    primary.write_bytes(content)
    backup.write_bytes(content)

    analyzer = DuplicateAnalyzer(prefer_under=tmp_path / "primary")
    items = [_metadata(backup), _metadata(primary)]
    report = analyzer.analyze(items)

    assert len(report.groups) == 1
    assert report.groups[0].canonical.metadata.source_path == primary


def test_insta360_lens_pair_not_reported_as_duplicate(tmp_path):
    """Insta360 dual-lens files (00/10) with identical bytes must not appear as dupes."""
    content = b"360 video content"

    lens_00 = tmp_path / "VID_20240601_120000_00_001.insv"
    lens_10 = tmp_path / "VID_20240601_120000_10_001.insv"

    lens_00.write_bytes(content)
    lens_10.write_bytes(content)

    items = [
        MediaMetadata(
            source_path=lens_00,
            media_type=MediaType.VIDEO,
            category=MediaCategory.PHOTOS_VIDEOS,
            captured_at=_BASE_DT,
            timestamp_source=TimestampSource.METADATA,
            is_panoramic=True,
        ),
        MediaMetadata(
            source_path=lens_10,
            media_type=MediaType.VIDEO,
            category=MediaCategory.PHOTOS_VIDEOS,
            captured_at=_BASE_DT,
            timestamp_source=TimestampSource.METADATA,
            is_panoramic=True,
        ),
    ]

    report = DuplicateAnalyzer().analyze(items)

    assert len(report.groups) == 0, "Insta360 lens pairs must not be reported as duplicates"
    assert report.skipped >= 1  # the collapsed lens-10 file counts as skipped


def test_min_size_filters_small_files(tmp_path):
    """Files smaller than min_size are excluded from duplicate detection."""
    tiny = b"x"
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(tiny)
    b.write_bytes(tiny)

    items = [_metadata(a), _metadata(b)]
    # min_size=2 means 1-byte files are skipped
    report = DuplicateAnalyzer(min_size=2).analyze(items)

    assert len(report.groups) == 0
    assert report.skipped >= 2


def test_invalid_algorithm_raises_value_error():
    with pytest.raises(ValueError, match="Unsupported hash algorithm"):
        DuplicateAnalyzer(algorithm="shaXXX")


def test_sha256_algorithm(tmp_path):
    """Analyzer works correctly with sha256 algorithm."""
    content = b"sha256 test"
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_bytes(content)
    b.write_bytes(content)

    items = [_metadata(a), _metadata(b)]
    report = DuplicateAnalyzer(algorithm="sha256").analyze(items)

    assert len(report.groups) == 1
    assert report.algorithm == "sha256"


# ---------------------------------------------------------------------------
# apply_duplicate_actions
# ---------------------------------------------------------------------------


def _make_dup_report(tmp_path: Path):
    """Create a minimal DuplicatesReport with one group of two files.

    Returns (report, canonical_path, dup_path) where canonical/dup reflect
    what the analyzer actually chose — i.e. the shorter path is canonical.
    """
    content = b"duplicate content for action tests"
    path_a = tmp_path / "a.jpg"
    path_b = tmp_path / "b_copy.jpg"
    path_a.write_bytes(content)
    path_b.write_bytes(content)

    items = [_metadata(path_a), _metadata(path_b)]
    report = DuplicateAnalyzer().analyze(items)
    assert len(report.groups) == 1, "Setup: expected one duplicate group"
    canonical_path = report.groups[0].canonical.metadata.source_path
    dup_path = report.groups[0].duplicates[0].metadata.source_path
    return report, canonical_path, dup_path


def test_dry_run_does_not_modify_disk(tmp_path):
    """dry_run=True must not move, link, or delete any files."""
    report, canonical, dup = _make_dup_report(tmp_path)
    quarantine = tmp_path / "quarantine"

    outcomes = apply_duplicate_actions(report, "move", quarantine=quarantine, dry_run=True)

    assert dup.exists(), "Duplicate must still exist in dry-run mode"
    assert not quarantine.exists(), "Quarantine directory must not be created in dry-run"
    assert len(outcomes) == 1
    assert outcomes[0].dry_run is True
    assert outcomes[0].error is None


def test_action_move_relocates_duplicate(tmp_path):
    """move action moves the duplicate to the quarantine directory."""
    report, canonical, dup = _make_dup_report(tmp_path)
    quarantine = tmp_path / "quarantine"

    outcomes = apply_duplicate_actions(
        report, "move", quarantine=quarantine, relative_to=tmp_path, dry_run=False
    )

    assert not dup.exists(), "Duplicate must have been moved"
    assert canonical.exists(), "Canonical must remain untouched"
    expected_dest = quarantine / dup.name
    assert expected_dest.exists(), "Duplicate must exist in quarantine"
    assert len(outcomes) == 1
    assert outcomes[0].action == "move"
    assert outcomes[0].error is None


def test_action_delete_removes_duplicate(tmp_path):
    """delete action removes the duplicate file."""
    report, canonical, dup = _make_dup_report(tmp_path)

    outcomes = apply_duplicate_actions(report, "delete", dry_run=False)

    assert not dup.exists(), "Duplicate must have been deleted"
    assert canonical.exists(), "Canonical must remain untouched"
    assert len(outcomes) == 1
    assert outcomes[0].action == "delete"
    assert outcomes[0].error is None


def test_action_link_hard_replaces_duplicate(tmp_path):
    """link action with link_kind='hard' replaces the duplicate with a hard link."""
    report, canonical, dup = _make_dup_report(tmp_path)

    outcomes = apply_duplicate_actions(
        report, "link", link_kind="hard", dry_run=False
    )

    assert dup.exists(), "Linked file must still be accessible"
    assert canonical.exists(), "Canonical must remain untouched"
    # Both paths should point to the same inode (hard link).
    assert dup.stat().st_ino == canonical.stat().st_ino, "Hard link must share inode"
    assert len(outcomes) == 1
    assert outcomes[0].action == "link"
    assert outcomes[0].error is None


def test_move_requires_quarantine():
    """apply_duplicate_actions raises DuplicateActionError when move lacks quarantine."""
    from axolo.duplicates import DuplicatesReport

    empty_report = DuplicatesReport(
        groups=[], processed=0, scanned=0, skipped=0, hashed_bytes=0, algorithm="blake2b"
    )
    with pytest.raises(DuplicateActionError, match="quarantine"):
        apply_duplicate_actions(empty_report, "move", quarantine=None, dry_run=True)
