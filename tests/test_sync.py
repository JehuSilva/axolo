"""Tests for Phase 3: sync command (union dedup-aware)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from axolo.cli import app
from axolo.journal import Journal
from axolo.metadata import extract_metadata
from axolo.sync import SyncPlan, apply_sync, plan_sync


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file(path: Path, content: bytes = b"\xff\xd8\xff" + b"\x00" * 100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _metadata(path: Path):
    return extract_metadata(path)


# ---------------------------------------------------------------------------
# plan_sync
# ---------------------------------------------------------------------------


def test_plan_sync_identical_file_skipped(tmp_path: Path):
    content = b"\xff\xd8\xff" + b"\x00" * 200
    src_file = _make_file(tmp_path / "src" / "photo.jpg", content)
    dst_file = _make_file(tmp_path / "dst" / "photo.jpg", content)

    src_meta = [_metadata(src_file)]
    dst_files = [dst_file]

    plan = plan_sync(
        src_meta,
        tmp_path / "dst",
        destination_existing_files=dst_files,
        workers=1,
        show_progress=False,
        dry_run=True,
    )

    assert len(plan.skipped_identical) == 1
    assert len(plan.additions) == 0


def test_plan_sync_new_file_added(tmp_path: Path):
    src_file = _make_file(tmp_path / "src" / "photo.jpg", b"\xff\xd8\xff" + b"\x00" * 100)

    src_meta = [_metadata(src_file)]

    plan = plan_sync(
        src_meta,
        tmp_path / "dst",
        destination_existing_files=[],
        workers=1,
        show_progress=False,
        dry_run=True,
    )

    assert len(plan.additions) == 1
    assert len(plan.skipped_identical) == 0


def test_plan_sync_name_collision_different_content(tmp_path: Path):
    src_content = b"\xff\xd8\xff" + b"\x00" * 100
    dst_content = b"\xff\xd8\xff" + b"\x01" * 100
    src_file = _make_file(tmp_path / "src" / "photo.jpg", src_content)
    # With folder_template="" and a reliable FILE_CREATION timestamp the file
    # resolves directly to dst/photo.jpg (no date sub-folder).
    dst_file = _make_file(tmp_path / "dst" / "photo.jpg", dst_content)

    src_meta = [_metadata(src_file)]

    plan = plan_sync(
        src_meta,
        tmp_path / "dst",
        destination_existing_files=[dst_file],
        workers=1,
        show_progress=False,
        folder_template="",
        dry_run=True,
    )

    assert len(plan.additions) == 1
    addition = plan.additions[0]
    # The file should have been renamed with a hash suffix
    assert addition.renamed is True
    assert addition.destination != dst_file
    # Original name conflict should be recorded
    assert len(plan.conflicts) == 1


def test_plan_sync_dry_run_does_not_create_dirs(tmp_path: Path):
    src_file = _make_file(tmp_path / "src" / "photo.jpg")
    dst_root = tmp_path / "dst"

    plan = plan_sync(
        [_metadata(src_file)],
        dst_root,
        destination_existing_files=[],
        workers=1,
        show_progress=False,
        dry_run=True,
    )

    assert len(plan.additions) == 1
    # dry_run=True → no directories created
    assert not dst_root.exists() or not any(dst_root.rglob("*"))


def test_plan_sync_empty_source(tmp_path: Path):
    plan = plan_sync(
        [],
        tmp_path / "dst",
        destination_existing_files=[],
        workers=1,
        show_progress=False,
    )
    assert plan.additions == []
    assert plan.skipped_identical == []


def test_plan_sync_multiple_files_mixed(tmp_path: Path):
    """3 files: 1 identical, 1 new, 1 name-conflict different content."""
    identical_content = b"\xff\xd8\xff" + b"\x00" * 50
    new_content = b"\xff\xd8\xff" + b"\x01" * 50
    conflict_src = b"\xff\xd8\xff" + b"\x02" * 50
    conflict_dst = b"\xff\xd8\xff" + b"\x03" * 50

    _make_file(tmp_path / "src" / "a.jpg", identical_content)
    _make_file(tmp_path / "src" / "b.jpg", new_content)
    _make_file(tmp_path / "src" / "c.jpg", conflict_src)
    # With folder_template="" and FILE_CREATION timestamps (reliable), files
    # resolve directly to dst/<name> — no date or unknown_date sub-folder.
    dst_a = _make_file(tmp_path / "dst" / "a.jpg", identical_content)
    dst_c = _make_file(tmp_path / "dst" / "c.jpg", conflict_dst)

    src_meta = [_metadata(tmp_path / "src" / n) for n in ("a.jpg", "b.jpg", "c.jpg")]

    plan = plan_sync(
        src_meta,
        tmp_path / "dst",
        destination_existing_files=[dst_a, dst_c],
        workers=1,
        show_progress=False,
        folder_template="",
        dry_run=True,
    )

    assert len(plan.skipped_identical) == 1
    assert len(plan.additions) == 2  # b.jpg + renamed c.jpg
    assert len(plan.conflicts) == 1


# ---------------------------------------------------------------------------
# apply_sync
# ---------------------------------------------------------------------------


def test_apply_sync_copy(tmp_path: Path):
    src_file = _make_file(tmp_path / "src" / "photo.jpg")
    dst_path = tmp_path / "dst" / "photo.jpg"

    src_meta = [_metadata(src_file)]
    plan = plan_sync(
        src_meta,
        tmp_path / "dst",
        destination_existing_files=[],
        workers=1,
        show_progress=False,
        folder_template="",
        dry_run=False,
    )

    applied = apply_sync(plan, action="copy", dry_run=False, show_progress=False)

    assert applied == 1
    assert plan.additions[0].destination.exists()
    assert src_file.exists()  # copy keeps source


def test_apply_sync_move(tmp_path: Path):
    src_file = _make_file(tmp_path / "src" / "photo.jpg")

    src_meta = [_metadata(src_file)]
    plan = plan_sync(
        src_meta,
        tmp_path / "dst",
        destination_existing_files=[],
        workers=1,
        show_progress=False,
        folder_template="",
        dry_run=False,
    )

    applied = apply_sync(plan, action="move", dry_run=False, show_progress=False)

    assert applied == 1
    assert plan.additions[0].destination.exists()
    assert not src_file.exists()  # move removes source


def test_apply_sync_dry_run_no_changes(tmp_path: Path):
    src_file = _make_file(tmp_path / "src" / "photo.jpg")
    dst_root = tmp_path / "dst"

    src_meta = [_metadata(src_file)]
    plan = plan_sync(
        src_meta,
        dst_root,
        destination_existing_files=[],
        workers=1,
        show_progress=False,
        folder_template="",
        dry_run=True,
    )

    applied = apply_sync(plan, action="copy", dry_run=True, show_progress=False)

    assert applied == 1  # counted as "would apply"
    assert not plan.additions[0].destination.exists()


def test_apply_sync_records_journal(tmp_path: Path):
    src_file = _make_file(tmp_path / "src" / "photo.jpg")
    db = tmp_path / "journal.db"
    journal = Journal(path=db)
    run_id = journal.start_run("sync")

    src_meta = [_metadata(src_file)]
    plan = plan_sync(
        src_meta,
        tmp_path / "dst",
        destination_existing_files=[],
        workers=1,
        show_progress=False,
        folder_template="",
        dry_run=False,
    )
    apply_sync(plan, action="copy", dry_run=False, show_progress=False, journal=journal, run_id=run_id)
    journal.finish_run(run_id, "completed")

    ops = journal.operations_for(run_id)
    journal.close()
    assert len(ops) == 1
    assert ops[0]["action"] == "copy"


# ---------------------------------------------------------------------------
# sync CLI command
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def journal_env(tmp_path: Path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("AXOLO_JOURNAL", str(db))
    return tmp_path


def test_sync_cli_dry_run(runner, journal_env, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_file(src / "photo.jpg")

    result = runner.invoke(app, [
        "sync",
        "--source", str(src),
        "--destination", str(dst),
        "--action", "copy",
        "--dry-run",
        "--workers", "1",
        "--no-journal",
    ])
    assert result.exit_code == 0
    assert not dst.exists() or not any(dst.rglob("*.jpg"))


def test_sync_cli_actual_copy(runner, journal_env, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_file(src / "photo.jpg")

    result = runner.invoke(app, [
        "sync",
        "--source", str(src),
        "--destination", str(dst),
        "--action", "copy",
        "--no-dry-run",
        "--workers", "1",
        "--no-journal",
        "--template", "{year}/{month:02d}",
    ])
    assert result.exit_code == 0
    # At least one jpg should exist somewhere under dst
    all_jpgs = list(dst.rglob("*.jpg"))
    assert len(all_jpgs) >= 1


def test_sync_cli_no_source_files(runner, tmp_path):
    src = tmp_path / "empty_src"
    src.mkdir()
    dst = tmp_path / "dst"

    result = runner.invoke(app, [
        "sync",
        "--source", str(src),
        "--destination", str(dst),
        "--no-journal",
    ])
    assert result.exit_code == 0
    assert "No files found" in result.output


def test_sync_cli_report_json(runner, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_file(src / "photo.jpg")
    report_path = tmp_path / "report.json"

    result = runner.invoke(app, [
        "sync",
        "--source", str(src),
        "--destination", str(dst),
        "--dry-run",
        "--output", str(report_path),
        "--workers", "1",
        "--no-journal",
    ])

    assert result.exit_code == 0
    assert report_path.exists()
    import json
    data = json.loads(report_path.read_text())
    assert "additions" in data
    assert "skipped_identical" in data
