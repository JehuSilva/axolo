"""Shared pytest fixtures for the axolo test suite."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from axolo import parallel as _parallel_module
from axolo import organizer as _organizer_module
from axolo import duplicates as _duplicates_module
from axolo import sync as _sync_module
from axolo.commands import _shared as _shared_module
from axolo.journal import Journal


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "parallel: test exercises real thread pools; opts out of serial-workers fixture",
    )


@pytest.fixture(autouse=True)
def _serialize_workers(request, monkeypatch):
    # Four-worker defaults in parallel_map / AxoloOrganizer / DuplicateAnalyzer /
    # plan_sync stacked ffprobe subprocesses and 1 MiB hash reads across the suite
    # until memory was exhausted. Patch parallel_map to force workers=1 everywhere
    # it's imported; tests that exercise parallelism opt out with @pytest.mark.parallel.
    if "parallel" in request.keywords:
        return

    original = _parallel_module.parallel_map

    def serial_parallel_map(fn, items, **kwargs):
        kwargs["workers"] = 1
        return original(fn, items, **kwargs)

    monkeypatch.setattr(_parallel_module, "parallel_map", serial_parallel_map)
    for mod in (_organizer_module, _duplicates_module, _sync_module, _shared_module):
        monkeypatch.setattr(mod, "parallel_map", serial_parallel_map)


@pytest.fixture()
def media_tree(tmp_path: Path) -> Path:
    """Create a small synthetic media tree under tmp_path/media.

    Layout:
        media/
          photos/
            photo1.jpg   — minimal JPEG header
            photo2.jpg
          videos/
            clip1.mp4    — zero bytes (only name matters for scanner)
          audio/
            track1.mp3
          docs/
            document.pdf

    Returns the ``media/`` root path.
    """
    root = tmp_path / "media"

    _jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    (root / "photos").mkdir(parents=True)
    (root / "photos" / "photo1.jpg").write_bytes(_jpeg_bytes)
    (root / "photos" / "photo2.jpg").write_bytes(_jpeg_bytes + b"\x01" * 50)

    (root / "videos").mkdir()
    (root / "videos" / "clip1.mp4").write_bytes(b"\x00" * 20)

    (root / "audio").mkdir()
    (root / "audio" / "track1.mp3").write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)

    (root / "docs").mkdir()
    (root / "docs" / "document.pdf").write_bytes(b"%PDF-1.4\n" + b"\x00" * 20)

    return root


@pytest.fixture()
def journal_db(tmp_path: Path) -> Journal:
    """Return an isolated Journal backed by a temp SQLite database."""
    db = tmp_path / "journal.db"
    j = Journal(path=db)
    yield j
    j.close()


@pytest.fixture()
def monkeypatch_home(tmp_path: Path, monkeypatch) -> Path:
    """Redirect ~/.axolo to a temp directory.

    Returns the temp home substitute so tests can inspect files written there.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Also redirect the journal env var so Journal() uses the temp path
    journal_dir = fake_home / ".axolo"
    journal_dir.mkdir(parents=True)
    monkeypatch.setenv("AXOLO_JOURNAL", str(journal_dir / "journal.db"))
    return fake_home
