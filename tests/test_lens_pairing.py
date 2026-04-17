"""Tests for Insta360 dual-lens pairing utilities."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from axolo.lens_pairing import deduplicate_assets, group_by_asset, pair_key
from axolo.metadata import MediaCategory, MediaMetadata, MediaType, TimestampSource


def _make_meta(filename: str, media_type: MediaType = MediaType.VIDEO) -> MediaMetadata:
    return MediaMetadata(
        source_path=Path(filename),
        media_type=media_type,
        category=MediaCategory.PHOTOS_VIDEOS,
        captured_at=datetime(2026, 4, 15, 18, 24, 46, tzinfo=timezone.utc),
        timestamp_source=TimestampSource.FILENAME,
        is_panoramic=True,
    )


# ---------------------------------------------------------------------------
# pair_key
# ---------------------------------------------------------------------------

def test_pair_key_lens_00() -> None:
    meta = _make_meta("VID_20260415_182446_00_001.insv")
    assert pair_key(meta) == "VID_20260415_182446_001"


def test_pair_key_lens_10() -> None:
    meta = _make_meta("VID_20260415_182446_10_001.insv")
    assert pair_key(meta) == "VID_20260415_182446_001"


def test_pair_key_same_for_both_lenses() -> None:
    lens_00 = _make_meta("VID_20260415_182446_00_001.insv")
    lens_10 = _make_meta("VID_20260415_182446_10_001.insv")
    assert pair_key(lens_00) == pair_key(lens_10)


def test_pair_key_different_sequence() -> None:
    vid1 = _make_meta("VID_20260415_182446_00_001.insv")
    vid2 = _make_meta("VID_20260415_182523_00_002.insv")
    assert pair_key(vid1) != pair_key(vid2)


def test_pair_key_non_360_filename() -> None:
    meta = _make_meta("vacation_photo.jpg", media_type=MediaType.IMAGE)
    assert pair_key(meta) is None


def test_pair_key_img_prefix() -> None:
    meta = _make_meta("IMG_20260415_182550_00_003.insp")
    assert pair_key(meta) == "IMG_20260415_182550_003"


# ---------------------------------------------------------------------------
# deduplicate_assets
# ---------------------------------------------------------------------------

def test_deduplicate_removes_secondary_lens() -> None:
    lens_00 = _make_meta("VID_20260415_182446_00_001.insv")
    lens_10 = _make_meta("VID_20260415_182446_10_001.insv")

    result = deduplicate_assets([lens_00, lens_10])

    assert len(result) == 1
    assert result[0] is lens_00


def test_deduplicate_prefers_lens_00_regardless_of_order() -> None:
    lens_10 = _make_meta("VID_20260415_182446_10_001.insv")
    lens_00 = _make_meta("VID_20260415_182446_00_001.insv")

    result = deduplicate_assets([lens_10, lens_00])

    assert len(result) == 1
    assert result[0] is lens_00


def test_deduplicate_multiple_pairs() -> None:
    a_00 = _make_meta("VID_20260415_182446_00_001.insv")
    a_10 = _make_meta("VID_20260415_182446_10_001.insv")
    b_00 = _make_meta("VID_20260415_182523_00_002.insv")
    b_10 = _make_meta("VID_20260415_182523_10_002.insv")

    result = deduplicate_assets([a_00, a_10, b_00, b_10])

    assert len(result) == 2
    assert result[0] is a_00
    assert result[1] is b_00


def test_deduplicate_non_paired_files_pass_through() -> None:
    normal_jpg = _make_meta("photo.jpg", MediaType.IMAGE)
    normal_mp4 = _make_meta("video.mp4", MediaType.VIDEO)

    result = deduplicate_assets([normal_jpg, normal_mp4])

    assert len(result) == 2
    assert result[0] is normal_jpg
    assert result[1] is normal_mp4


def test_deduplicate_mixed_paired_and_normal() -> None:
    normal = _make_meta("holiday.jpg", MediaType.IMAGE)
    lens_00 = _make_meta("VID_20260415_182446_00_001.insv")
    lens_10 = _make_meta("VID_20260415_182446_10_001.insv")

    result = deduplicate_assets([normal, lens_00, lens_10])

    assert len(result) == 2


# ---------------------------------------------------------------------------
# group_by_asset
# ---------------------------------------------------------------------------

def test_group_by_asset_pairs_both_lenses() -> None:
    lens_00 = _make_meta("VID_20260415_182446_00_001.insv")
    lens_10 = _make_meta("VID_20260415_182446_10_001.insv")

    groups = group_by_asset([lens_00, lens_10])

    assert len(groups) == 1
    group = groups[0]
    assert len(group) == 2
    assert lens_00 in group
    assert lens_10 in group


def test_group_by_asset_single_files_each_in_own_group() -> None:
    a = _make_meta("photo_a.jpg", MediaType.IMAGE)
    b = _make_meta("photo_b.jpg", MediaType.IMAGE)

    groups = group_by_asset([a, b])

    assert len(groups) == 2
    assert [a] in groups
    assert [b] in groups
