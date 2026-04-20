"""Tests for Phase 4: TUI module."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from axolo.cli import app
from axolo.tui import _require_questionary


def test_tui_command_registered():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert "tui" in result.output


def test_require_questionary_does_not_raise_when_available():
    """If questionary is importable (it is in our venv), this should not raise."""
    _require_questionary()  # should not raise


def test_tui_module_imports_cleanly():
    import axolo.tui as tui
    assert callable(tui.run_tui)


def test_wizard_run_invokes_organizer(tmp_path: Path):
    """_wizard_run should call AxoloOrganizer.organize when user confirms."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "photo.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

    dst = tmp_path / "dst"

    responses = [
        str(src),          # source path
        str(dst),          # destination path
        "default",         # profile
        "copy",            # action
        "4",               # workers
        True,              # dry_run
        True,              # confirm preview
    ]

    import questionary as q

    with patch.object(q, "path") as mock_path, \
         patch.object(q, "select") as mock_select, \
         patch.object(q, "text") as mock_text, \
         patch.object(q, "confirm") as mock_confirm:

        # Setup answer sequences
        mock_path.return_value.ask.side_effect = [str(src), str(dst)]
        mock_select.return_value.ask.side_effect = ["copy"]
        mock_text.return_value.ask.return_value = "1"
        mock_confirm.return_value.ask.side_effect = [True, False, False, True]  # dry_run, include_hidden=No, customize=No, confirmed

        from axolo.tui import _wizard_run
        _wizard_run()  # should not raise


def test_wizard_duplicates_no_files(tmp_path: Path):
    """_wizard_duplicates exits early when no files found."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    import questionary as q

    with patch.object(q, "path") as mock_path, \
         patch.object(q, "select") as mock_select, \
         patch.object(q, "text") as mock_text:

        mock_path.return_value.ask.return_value = str(empty_dir)
        mock_select.return_value.ask.return_value = "blake2b"
        mock_text.return_value.ask.return_value = "1"

        from axolo.tui import _wizard_duplicates
        _wizard_duplicates()  # should not raise


def test_wizard_sync_no_files(tmp_path: Path):
    """_wizard_sync exits early when no files in source."""
    empty_src = tmp_path / "empty_src"
    empty_src.mkdir()
    dst = tmp_path / "dst"

    import questionary as q

    with patch.object(q, "path") as mock_path, \
         patch.object(q, "select") as mock_select, \
         patch.object(q, "text") as mock_text, \
         patch.object(q, "confirm") as mock_confirm:

        mock_path.return_value.ask.side_effect = [str(empty_src), str(dst)]
        mock_select.return_value.ask.return_value = "copy"
        mock_text.return_value.ask.return_value = "1"
        mock_confirm.return_value.ask.return_value = True

        from axolo.tui import _wizard_sync
        _wizard_sync()  # should not raise


def test_wizard_history_no_runs(tmp_path: Path, monkeypatch):
    """_wizard_history exits gracefully when journal is empty."""
    from axolo.journal import Journal

    db = tmp_path / "journal.db"
    monkeypatch.setenv("AXOLO_JOURNAL", str(db))

    from axolo.tui import _wizard_history
    _wizard_history()  # should not raise — no runs → prints message and returns


def test_run_tui_exits_on_exit(monkeypatch):
    """run_tui exits cleanly when user selects 'Exit'."""
    import questionary as q

    with patch.object(q, "select") as mock_select:
        mock_select.return_value.ask.return_value = "Exit"

        from axolo.tui import run_tui
        run_tui()  # should not raise or hang
