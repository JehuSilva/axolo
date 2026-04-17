"""Tests for Phase 5: shared helpers, conftest fixtures, CLI integration."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from axolo.cli import app
from axolo.commands._shared import (
    collect_metadata,
    humanize_bytes,
    parse_extra,
    render_runs_table,
    render_summary,
    validate_workers,
)
from axolo.organizer import OrganizeSummary, FileResult
from axolo.metadata import MediaCategory


# ---------------------------------------------------------------------------
# Shared helper: humanize_bytes
# ---------------------------------------------------------------------------


def test_humanize_bytes_bytes():
    assert humanize_bytes(500) == "500.0 B"


def test_humanize_bytes_kb():
    assert humanize_bytes(2048) == "2.0 KB"


def test_humanize_bytes_mb():
    assert humanize_bytes(1024 * 1024 * 3) == "3.0 MB"


def test_humanize_bytes_gb():
    assert humanize_bytes(1024 ** 3 * 5) == "5.0 GB"


# ---------------------------------------------------------------------------
# Shared helper: parse_extra
# ---------------------------------------------------------------------------


def test_parse_extra_valid():
    result = parse_extra(["evento=CumpleaƱos", "year=2025"])
    assert result == {"evento": "CumpleaƱos", "year": "2025"}


def test_parse_extra_none():
    assert parse_extra(None) == {}


def test_parse_extra_empty_list():
    assert parse_extra([]) == {}


def test_parse_extra_invalid_raises(capsys):
    import typer
    with pytest.raises(typer.BadParameter):
        parse_extra(["noequalssign"])


# ---------------------------------------------------------------------------
# Shared helper: validate_workers
# ---------------------------------------------------------------------------


def test_validate_workers_valid():
    assert validate_workers(4) == 4
    assert validate_workers(1) == 1
    assert validate_workers(32) == 32


def test_validate_workers_too_low_raises():
    import typer
    with pytest.raises(typer.BadParameter):
        validate_workers(0)


def test_validate_workers_too_high_raises():
    import typer
    with pytest.raises(typer.BadParameter):
        validate_workers(33)


# ---------------------------------------------------------------------------
# Shared helper: render_summary
# ---------------------------------------------------------------------------


def _make_summary(status: str = "moved") -> OrganizeSummary:
    s = OrganizeSummary()
    s.add(FileResult(
        source=Path("/src/photo.jpg"),
        destination=Path("/dst/photo.jpg"),
        status=status,
        category=MediaCategory.PHOTOS_VIDEOS,
    ))
    return s


def test_render_summary_does_not_raise(capsys):
    render_summary(_make_summary("moved"))


def test_render_summary_dry_run(capsys):
    render_summary(_make_summary("dry-run"))


def test_render_summary_failed(capsys):
    render_summary(_make_summary("failed"))


# ---------------------------------------------------------------------------
# Shared helper: render_runs_table
# ---------------------------------------------------------------------------


def test_render_runs_table_empty(capsys):
    render_runs_table([])


def test_render_runs_table_with_runs(capsys):
    runs = [{
        "run_id": "abc123def456",
        "command": "run",
        "started_at": "2026-04-16T10:00:00",
        "finished_at": "2026-04-16T10:01:00",
        "status": "completed",
        "dry_run": 0,
        "source": "/media",
    }]
    render_runs_table(runs)


# ---------------------------------------------------------------------------
# conftest fixtures smoke tests
# ---------------------------------------------------------------------------


def test_media_tree_fixture(media_tree):
    assert (media_tree / "photos" / "photo1.jpg").exists()
    assert (media_tree / "videos" / "clip1.mp4").exists()
    assert (media_tree / "audio" / "track1.mp3").exists()
    assert (media_tree / "docs" / "document.pdf").exists()


def test_journal_db_fixture(journal_db):
    run_id = journal_db.start_run("run")
    journal_db.finish_run(run_id, "completed")
    assert journal_db.run_by_id(run_id)["status"] == "completed"


def test_monkeypatch_home_fixture(monkeypatch_home):
    import os
    assert os.environ["HOME"] == str(monkeypatch_home)
    from axolo.journal import Journal
    j = Journal()
    try:
        assert "fake_home" in str(j._path)
    finally:
        j.close()


# ---------------------------------------------------------------------------
# CLI integration: run command with real .jpg file
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner():
    return CliRunner()


def _make_jpg(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    return path


def test_run_dry_run_produces_table(runner, tmp_path, monkeypatch_home):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_jpg(src / "photo.jpg")

    result = runner.invoke(app, [
        "run",
        "--source", str(src),
        "--destination", str(dst),
        "--action", "copy",
        "--dry-run",
        "--template", "default",
        "--workers", "1",
        "--no-journal",
    ])
    assert result.exit_code == 0
    assert "dry-run" in result.output or "Resumen" in result.output


def test_run_actual_copy(runner, tmp_path, monkeypatch_home):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _make_jpg(src / "photo.jpg")

    result = runner.invoke(app, [
        "run",
        "--source", str(src),
        "--destination", str(dst),
        "--action", "copy",
        "--no-dry-run",
        "--template", "default",
        "--workers", "1",
        "--no-journal",
    ])
    assert result.exit_code == 0
    all_jpgs = list(dst.rglob("*.jpg"))
    assert len(all_jpgs) >= 1


def test_duplicates_cli_no_action(runner, tmp_path, monkeypatch_home):
    src = tmp_path / "src"
    content = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    _make_jpg(src / "photo1.jpg")
    (src / "photo2.jpg").write_bytes(content)  # identical content

    result = runner.invoke(app, [
        "duplicates",
        "--source", str(src),
        "--algorithm", "blake2b",
        "--workers", "1",
        "--no-journal",
    ])
    assert result.exit_code == 0


def test_duplicates_cli_json_output(runner, tmp_path, monkeypatch_home):
    src = tmp_path / "src"
    content = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    _make_jpg(src / "photo1.jpg")
    (src / "photo2.jpg").write_bytes(content)
    report = tmp_path / "report.json"

    result = runner.invoke(app, [
        "duplicates",
        "--source", str(src),
        "--algorithm", "blake2b",
        "--output", str(report),
        "--workers", "1",
        "--no-journal",
    ])
    assert result.exit_code == 0
    assert report.exists()
    import json
    data = json.loads(report.read_text())
    assert "groups" in data


def test_undo_list_via_cli(runner, monkeypatch_home):
    result = runner.invoke(app, ["undo", "--list"])
    assert result.exit_code == 0


def test_help_shows_all_commands(runner):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("run", "duplicates", "sync", "undo", "tui"):
        assert cmd in result.output


# ---------------------------------------------------------------------------
# commands/ package imports
# ---------------------------------------------------------------------------


def test_commands_package_imports():
    from axolo.commands import run, duplicates, undo, sync_cmd, tui_cmd
    assert callable(run.command)
    assert callable(duplicates.command)
    assert callable(undo.command)
    assert callable(sync_cmd.command)
    assert callable(tui_cmd.command)


# ---------------------------------------------------------------------------
# i18n module
# ---------------------------------------------------------------------------


def test_i18n_month_names():
    from axolo.i18n import MONTH_NAMES_ES, MONTH_NAMES_ES_SHORT, MONTH_NAMES_ES_CAP
    assert MONTH_NAMES_ES[1] == "enero"
    assert MONTH_NAMES_ES[12] == "diciembre"
    assert MONTH_NAMES_ES_SHORT[4] == "abr"
    assert MONTH_NAMES_ES_CAP[4] == "Abril"


def test_templates_uses_i18n():
    from axolo.templates import MONTH_NAMES_ES
    assert MONTH_NAMES_ES[1] == "enero"
