"""Utilities for detecting and grouping Insta360 dual-lens paired files.

Insta360 cameras (e.g. X3) record two files per capture — one per lens:
  VID_20260415_182446_00_001.insv  (lens 00)
  VID_20260415_182446_10_001.insv  (lens 10)

These represent a single logical asset. This module provides helpers to
detect pairs and collapse them to a single representative for the
duplicates command, while the organizer still moves both files physically.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Sequence

from .metadata import MediaMetadata

# Matches: IMG_YYYYMMDD_HHMMSS_<lens>_<seq> or VID_YYYYMMDD_HHMMSS_<lens>_<seq>
# Lens codes used by Insta360: '00' (primary/front) and '10' (secondary/back).
_PAIR_RE = re.compile(
    r"^(?P<prefix>(?:IMG|VID)_\d{8}_\d{6})_(?P<lens>00|10)_(?P<seq>\d+)$"
)


def pair_key(metadata: MediaMetadata) -> str | None:
    """Return a stable key shared by both lens files of the same capture.

    Returns None for files that do not match the Insta360 dual-lens pattern.
    """
    stem = metadata.source_path.stem
    match = _PAIR_RE.match(stem)
    if match is None:
        return None
    return f"{match.group('prefix')}_{match.group('seq')}"


def _lens_code(metadata: MediaMetadata) -> str:
    """Return the lens code ('00', '10') or empty string if not a paired file."""
    stem = metadata.source_path.stem
    match = _PAIR_RE.match(stem)
    return match.group("lens") if match else ""


def group_by_asset(items: Sequence[MediaMetadata]) -> list[list[MediaMetadata]]:
    """Group items into asset-level lists.

    Paired lens files are placed in the same sub-list. Files without a
    pair pattern each form a single-element list. Input order is preserved
    for the first occurrence of each group.
    """
    paired: dict[str, list[MediaMetadata]] = defaultdict(list)
    singles: list[MediaMetadata] = []

    for item in items:
        key = pair_key(item)
        if key is None:
            singles.append(item)
        else:
            paired[key].append(item)

    result: list[list[MediaMetadata]] = []
    singles_idx = 0
    added_pairs: set[str] = set()

    for item in items:
        key = pair_key(item)
        if key is None:
            result.append([singles[singles_idx]])
            singles_idx += 1
        elif key not in added_pairs:
            result.append(list(paired[key]))
            added_pairs.add(key)

    return result


def deduplicate_assets(items: Sequence[MediaMetadata]) -> list[MediaMetadata]:
    """Return one representative per asset group (prefer lens '00').

    Non-paired files pass through unchanged. Paired groups are collapsed to
    the lens-'00' file, or the first available file if '00' is absent.
    """
    representatives: list[MediaMetadata] = []
    seen_keys: set[str] = set()

    for item in items:
        key = pair_key(item)
        if key is None:
            representatives.append(item)
            continue
        if key in seen_keys:
            continue
        # Find the best representative: prefer lens '00'.
        candidates = [i for i in items if pair_key(i) == key]
        primary = next(
            (c for c in candidates if _lens_code(c) == "00"),
            candidates[0],
        )
        representatives.append(primary)
        seen_keys.add(key)

    return representatives
