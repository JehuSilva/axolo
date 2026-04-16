"""Shared pytest fixtures for the media-organizer test suite."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from media_organizer.journal import Journal


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
    """Redirect ~/.media-organizer to a temp directory.

    Returns the temp home substitute so tests can inspect files written there.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Also redirect the journal env var so Journal() uses the temp path
    journal_dir = fake_home / ".media-organizer"
    journal_dir.mkdir(parents=True)
    monkeypatch.setenv("MEDIA_ORGANIZER_JOURNAL", str(journal_dir / "journal.db"))
    return fake_home
