"""Tests for Phase 2: parallel_map, Journal, and undo command."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from media_organizer.cli import app
from media_organizer.journal import Journal
from media_organizer.parallel import parallel_map


# ---------------------------------------------------------------------------
# parallel_map
# ---------------------------------------------------------------------------


def test_parallel_map_preserves_order():
    items = list(range(20))
    results = parallel_map(lambda x: x * 2, items, workers=4, show_progress=False)
    assert results == [x * 2 for x in items]


def test_parallel_map_serial_matches_parallel():
    items = list(range(10))
    serial = parallel_map(lambda x: x + 1, items, workers=1, show_progress=False)
    threaded = parallel_map(lambda x: x + 1, items, workers=4, show_progress=False)
    assert serial == threaded


def test_parallel_map_captures_exceptions_without_aborting():
    def flaky(x):
        if x == 3:
            raise ValueError("boom")
        return x

    results = parallel_map(flaky, list(range(6)), workers=2, show_progress=False)
    assert results[0] == 0
    assert results[2] == 2
    assert isinstance(results[3], ValueError)
    assert results[5] == 5


def test_parallel_map_empty_input():
    assert parallel_map(lambda x: x, [], workers=4, show_progress=False) == []


def test_parallel_map_single_item():
    results = parallel_map(lambda x: x * 10, [7], workers=4, show_progress=False)
    assert results == [70]


def test_parallel_map_workers_capped_to_items():
    """workers > len(items) should not raise."""
    items = [1, 2]
    results = parallel_map(lambda x: x, items, workers=100, show_progress=False)
    assert results == items


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


@pytest.fixture()
def journal(tmp_path: Path) -> Journal:
    db = tmp_path / "journal.db"
    j = Journal(path=db)
    yield j
    j.close()


def test_journal_start_and_finish_run(journal: Journal, tmp_path: Path):
    run_id = journal.start_run(
        "run",
        source=tmp_path / "src",
        destination=tmp_path / "dst",
        dry_run=False,
    )
    assert run_id
    run = journal.run_by_id(run_id)
    assert run["status"] == "running"

    journal.finish_run(run_id, "completed")
    run = journal.run_by_id(run_id)
    assert run["status"] == "completed"
    assert run["finished_at"] is not None


def test_journal_record_and_operations_for(journal: Journal, tmp_path: Path):
    run_id = journal.start_run("run")
    src = tmp_path / "a.jpg"
    dst = tmp_path / "b.jpg"
    op_id = journal.record(run_id, seq=0, action="move", src=src, dst=dst, size=1234)
    assert isinstance(op_id, int)

    ops = journal.operations_for(run_id)
    assert len(ops) == 1
    assert ops[0]["action"] == "move"
    assert ops[0]["src"] == str(src)
    assert ops[0]["dst"] == str(dst)
    assert ops[0]["size"] == 1234


def test_journal_mark_reverted(journal: Journal, tmp_path: Path):
    run_id = journal.start_run("run")
    op_id = journal.record(run_id, seq=0, action="move", src=tmp_path / "a", dst=tmp_path / "b")
    journal.mark_reverted(op_id)

    ops = journal.operations_for(run_id)
    assert ops[0]["reverted_at"] is not None


def test_journal_list_runs(journal: Journal):
    for i in range(5):
        run_id = journal.start_run("run")
        journal.finish_run(run_id, "completed")
    runs = journal.list_runs(limit=3)
    assert len(runs) == 3


def test_journal_last_revertible_run_id(journal: Journal, tmp_path: Path):
    # dry-run should not appear
    dry_id = journal.start_run("run", dry_run=True)
    journal.finish_run(dry_id, "completed")

    real_id = journal.start_run("run", dry_run=False)
    journal.finish_run(real_id, "completed")

    assert journal.last_revertible_run_id() == real_id


def test_journal_env_var_override(tmp_path: Path, monkeypatch):
    db = tmp_path / "custom.db"
    monkeypatch.setenv("MEDIA_ORGANIZER_JOURNAL", str(db))
    j = Journal()
    try:
        assert j._path == db
        assert db.exists()
    finally:
        j.close()


def test_journal_context_manager(tmp_path: Path):
    db = tmp_path / "ctx.db"
    with Journal(path=db) as j:
        run_id = j.start_run("run")
        j.finish_run(run_id, "completed")
    assert db.exists()


# ---------------------------------------------------------------------------
# undo command
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def journal_env(tmp_path: Path, monkeypatch):
    """Point MEDIA_ORGANIZER_JOURNAL to a tmp path for CLI tests."""
    db = tmp_path / "journal.db"
    monkeypatch.setenv("MEDIA_ORGANIZER_JOURNAL", str(db))
    return tmp_path


def _make_jpg(path: Path) -> Path:
    """Create a minimal JPEG-like file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    return path


def test_undo_list_no_runs(runner, journal_env):
    result = runner.invoke(app, ["undo", "--list"])
    assert result.exit_code == 0
    assert "No hay" in result.output or "runs" in result.output.lower() or result.output.strip()


def test_undo_no_run_id_without_runs(runner, journal_env):
    result = runner.invoke(app, ["undo"])
    assert result.exit_code != 0 or "No hay" in result.output or "No se encontró" in result.output


def test_undo_dry_run_move(runner, journal_env, tmp_path):
    """Undo dry-run on a move operation should report without modifying files."""
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    # Manually insert a completed move operation into the journal
    db = journal_env / "journal.db"
    j = Journal(path=db)
    run_id = j.start_run("run", dry_run=False)
    src_file = src_dir / "photo.jpg"
    dst_file = dst_dir / "photo.jpg"
    _make_jpg(src_file)
    shutil.copy2(str(src_file), str(dst_file))
    src_file.unlink()
    j.record(run_id, seq=0, action="move", src=src_file, dst=dst_file, size=103)
    j.finish_run(run_id, "completed")
    j.close()

    result = runner.invoke(app, ["undo", "--run-id", run_id, "--dry-run"])
    assert result.exit_code == 0
    # Dry-run: dst should NOT have been moved back
    assert dst_file.exists()


def test_undo_actual_move(runner, journal_env, tmp_path):
    """Undo a move operation should move the file back to its source."""
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()

    src_file = src_dir / "photo.jpg"
    dst_file = dst_dir / "photo.jpg"
    _make_jpg(dst_file)  # simulate file already moved to dst

    db = journal_env / "journal.db"
    j = Journal(path=db)
    run_id = j.start_run("run", dry_run=False)
    j.record(run_id, seq=0, action="move", src=src_file, dst=dst_file, size=103)
    j.finish_run(run_id, "completed")
    j.close()

    result = runner.invoke(app, ["undo", "--run-id", run_id, "--no-dry-run"])
    assert result.exit_code == 0
    assert src_file.exists()
    assert not dst_file.exists()


def test_undo_actual_copy(runner, journal_env, tmp_path):
    """Undo a copy operation should remove the destination file."""
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    dst_file = dst_dir / "photo.jpg"
    src_file = tmp_path / "original.jpg"
    _make_jpg(src_file)
    _make_jpg(dst_file)

    db = journal_env / "journal.db"
    j = Journal(path=db)
    run_id = j.start_run("run", dry_run=False)
    j.record(run_id, seq=0, action="copy", src=src_file, dst=dst_file, size=103)
    j.finish_run(run_id, "completed")
    j.close()

    result = runner.invoke(app, ["undo", "--run-id", run_id, "--no-dry-run"])
    assert result.exit_code == 0
    assert not dst_file.exists()
    assert src_file.exists()


def test_undo_delete_reports_error(runner, journal_env, tmp_path):
    """Undo a delete operation is not supported and should report error."""
    db = journal_env / "journal.db"
    j = Journal(path=db)
    run_id = j.start_run("run", dry_run=False)
    j.record(run_id, seq=0, action="delete", src=tmp_path / "gone.jpg", dst=None)
    j.finish_run(run_id, "completed")
    j.close()

    result = runner.invoke(app, ["undo", "--run-id", run_id, "--no-dry-run"])
    # Should not crash; should report inability to undo delete
    assert result.exit_code == 0
    assert "delete" in result.output.lower() or "no se puede" in result.output.lower()


def test_undo_marks_run_as_reverted(runner, journal_env, tmp_path):
    """After a successful undo, the run should be marked 'reverted'."""
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    dst_file = dst_dir / "photo.jpg"
    _make_jpg(dst_file)

    db = journal_env / "journal.db"
    j = Journal(path=db)
    run_id = j.start_run("run", dry_run=False)
    j.record(run_id, seq=0, action="move", src=tmp_path / "photo.jpg", dst=dst_file, size=103)
    j.finish_run(run_id, "completed")
    j.close()

    runner.invoke(app, ["undo", "--run-id", run_id, "--no-dry-run"])

    j2 = Journal(path=db)
    run = j2.run_by_id(run_id)
    j2.close()
    assert run["status"] == "reverted"
