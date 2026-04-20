"""Interactive terminal wizard using questionary + Rich.

Entry point: ``axolo tui`` (no arguments).

Main menu offers:
  1. Organize     — guides the user through the ``run`` command
  2. Duplicates   — guides through the ``duplicates`` command
  3. Sync         — guides through the ``sync`` command
  4. History      — shows journal runs; optionally triggers undo
  5. Exit

The wizard uses questionary prompts for path / option selection, then calls
the Python API directly (no subprocess).  Rich is used for preview tables
and progress bars (same as the non-interactive commands).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

console = Console()

_AVAILABLE = True
try:
    import questionary
except ImportError:  # pragma: no cover
    _AVAILABLE = False


def _require_questionary() -> None:
    if not _AVAILABLE:
        console.print(
            "[red]The TUI requires 'questionary'. "
            "Install it with: pip install questionary[/red]"
        )
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Individual wizards
# ---------------------------------------------------------------------------


def _ask_path(message: str, must_exist: bool = True) -> Path:
    """Ask for a filesystem path with basic validation."""
    while True:
        raw = questionary.path(message).ask()
        if raw is None:
            raise KeyboardInterrupt
        path = Path(raw).expanduser()
        if must_exist and not path.exists():
            console.print(f"[red]Path does not exist: {path}[/red]")
            continue
        return path


def _ask_action(choices=("move", "copy", "link"), default="move") -> str:
    result = questionary.select(
        "Action to apply:",
        choices=list(choices),
        default=default,
    ).ask()
    if result is None:
        raise KeyboardInterrupt
    return result


def _ask_workers() -> int:
    default = str(min(os.cpu_count() or 4, 8))
    raw = questionary.text(f"Number of parallel workers [{default}]:").ask()
    if raw is None:
        raise KeyboardInterrupt
    raw = raw.strip() or default
    try:
        w = int(raw)
        return max(1, min(32, w))
    except ValueError:
        return int(default)


_TEMPLATE_DESCRIPTIONS: dict[str, str] = {
    "default":                    "Year / Month  →  2024/April/photo.jpg",
    "year_month":                 "Numeric  →  2024/04",
    "year_month_cap":             "Capitalized month  →  2024/April",
    "year_month_day":             "With day  →  2024/04/15",
    "year_month_name":            "Lowercase month  →  2024/april",
    "year_month_name_short":      "Abbreviated month  →  2024/apr",
    "year_month_name_day":        "With day name  →  2024/April/April 15",
    "camera":                     "By camera  →  canon/eos-r5/2024/04",
    "music_genre_artist":         "Genre and artist  →  rock/the-beatles",
    "music_genre":                "Genre only  →  rock",
    "documents_year_month":       "Documents numeric  →  2024/04",
    "documents_year_month_cap":   "Documents capitalized  →  2024/April",
}


def _build_profile_choices() -> list:
    """Return questionary.Choice list covering DEFAULT_TEMPLATES + BUILTIN_PROFILES."""
    from .config import BUILTIN_PROFILES
    from .templates import DEFAULT_TEMPLATES

    seen: set[str] = set()
    choices = []
    for name in DEFAULT_TEMPLATES:
        desc = _TEMPLATE_DESCRIPTIONS.get(name, name)
        choices.append(questionary.Choice(title=f"{name} — {desc}", value=name))
        seen.add(name)
    for name, prof in BUILTIN_PROFILES.items():
        if name not in seen:
            choices.append(questionary.Choice(title=f"{name} — {prof.description}", value=name))
    return choices


def _wizard_run() -> None:
    """Wizard for the ``run`` (organize) command."""
    from .config import DEFAULT_ROUTING, OrganizerConfig, ROUTING_SUBFOLDERS, load_run_config
    from .logging_setup import setup_logging
    from .media_scanner import ScanOptions, iter_media_files
    from .organizer import AxoloOrganizer

    console.rule("[bold cyan]Organize media files")

    source = _ask_path("Source directory:")
    destination = _ask_path("Destination directory:", must_exist=False)
    action = _ask_action(choices=("move", "copy", "link"))
    workers = _ask_workers()
    dry_run = questionary.confirm("Dry-run mode (no real file moves)?", default=True).ask()
    if dry_run is None:
        raise KeyboardInterrupt
    include_hidden = questionary.confirm(
        "Include hidden files (names starting with '.')? They will be routed to Hidden/.",
        default=False,
    ).ask()
    if include_hidden is None:
        raise KeyboardInterrupt

    # Load routing defaults: auto-detect ./config.yaml, fall back to DEFAULT_ROUTING
    routing: dict = dict(DEFAULT_ROUTING)
    routing_filename_templates: dict = {}
    _auto_cfg = Path("config.yaml")
    if _auto_cfg.exists():
        try:
            _file_cfg = load_run_config(_auto_cfg)
            if _file_cfg.get("routing"):
                routing.update(_file_cfg["routing"])
                console.print("[dim]Configuration loaded from config.yaml[/dim]")
            if _file_cfg.get("routing_filename_templates"):
                routing_filename_templates.update(_file_cfg["routing_filename_templates"])
        except Exception:
            pass

    _TYPE_LABELS = {
        "photos": "Photos",
        "videos": "Videos",
        "360-photos": "360° Photos",
        "360-videos": "360° Videos",
        "music": "Music",
        "documents": "Documents",
        "hidden": "Hidden files",
        "others": "Other",
    }

    t = Table(title="Configuration by file type")
    t.add_column("Type", style="cyan")
    t.add_column("Destination folder", style="green")
    t.add_column("Organization profile", style="magenta")
    for key, label in _TYPE_LABELS.items():
        parts = ROUTING_SUBFOLDERS.get(key, (key,))
        subfolder = "/".join(parts) if parts else "(category root)"
        profile_name = routing.get(key, "default")
        t.add_row(label, subfolder, profile_name)
    console.print(t)

    customize = questionary.confirm(
        "Customize the profile for any file type?", default=False
    ).ask()
    if customize is None:
        raise KeyboardInterrupt

    if customize:
        profile_choices = _build_profile_choices()
        for key, label in _TYPE_LABELS.items():
            change = questionary.confirm(
                f"  Change profile for {label}? (current: {routing.get(key, 'default')})",
                default=False,
            ).ask()
            if change:
                new_profile = questionary.select(
                    f"  Profile for {label}:",
                    choices=profile_choices,
                    default=routing.get(key, "default"),
                ).ask()
                if new_profile is None:
                    raise KeyboardInterrupt
                routing[key] = new_profile

    setup_logging("INFO")
    files = list(iter_media_files(source, ScanOptions(include_hidden=include_hidden)))

    if not files:
        console.print("[yellow]No files found in source.[/yellow]")
        return

    console.print(f"[green]Found {len(files)} files.[/green]")

    preview_table = Table(title="Preview (first 10 files)")
    preview_table.add_column("File", style="cyan")
    for f in files[:10]:
        preview_table.add_row(str(f))
    if len(files) > 10:
        preview_table.add_row(f"… and {len(files) - 10} more")
    console.print(preview_table)

    confirmed = questionary.confirm("Continue?", default=dry_run).ask()
    if not confirmed:
        console.print("[yellow]Cancelled.[/yellow]")
        return

    config = OrganizerConfig(
        source=source,
        destination=destination,
        action=action,
        template="default",
        dry_run=dry_run,
        routing=routing,
        routing_filename_templates=routing_filename_templates,
    )
    organizer = AxoloOrganizer(config=config, workers=workers)
    summary = organizer.organize(files)

    counts = summary.status_counts()
    t = Table(title="Result")
    t.add_column("Status", style="magenta")
    t.add_column("Count", style="cyan", justify="right")
    for status, count in sorted(counts.items()):
        t.add_row(status, str(count))
    console.print(t)


def _wizard_duplicates() -> None:
    """Wizard for the ``duplicates`` command."""
    from .duplicates import DuplicateAnalyzer, apply_duplicate_actions
    from .logging_setup import setup_logging
    from .media_scanner import ScanOptions, iter_media_files
    from .metadata import extract_metadata
    from .parallel import parallel_map

    console.rule("[bold cyan]Find duplicate files")

    source = _ask_path("Directory to analyze:")
    algorithm = questionary.select(
        "Hash algorithm:",
        choices=["blake2b", "sha256", "md5"],
        default="blake2b",
    ).ask()
    if algorithm is None:
        raise KeyboardInterrupt

    workers = _ask_workers()
    setup_logging("INFO")

    files = list(iter_media_files(source, ScanOptions()))
    if not files:
        console.print("[yellow]No files found.[/yellow]")
        return

    meta_results = parallel_map(extract_metadata, files, workers=workers, show_progress=True,
                                description="Extracting metadata...")
    metadata_items = [r for r in meta_results if not isinstance(r, BaseException)]

    analyzer = DuplicateAnalyzer(algorithm=algorithm, workers=workers)
    report = analyzer.analyze(metadata_items, show_progress=True)

    if not report.groups:
        console.print("[green]No duplicates found.[/green]")
        return

    console.print(f"[yellow]Found {len(report.groups)} duplicate groups.[/yellow]")

    t = Table(title="Duplicate groups")
    t.add_column("#", style="magenta", justify="right")
    t.add_column("Canonical", style="cyan")
    t.add_column("Duplicate", style="yellow")
    for i, group in enumerate(report.groups[:20], 1):
        for j, dup in enumerate(group.duplicates):
            t.add_row(
                str(i) if j == 0 else "",
                str(group.canonical.metadata.source_path) if j == 0 else "",
                str(dup.metadata.source_path),
            )
    console.print(t)

    apply_action = questionary.confirm("Apply an action on duplicates?", default=False).ask()
    if not apply_action:
        return

    action = questionary.select("Action:", choices=["move", "link", "delete"]).ask()
    if action is None:
        raise KeyboardInterrupt

    quarantine = None
    if action == "move":
        quarantine = _ask_path("Quarantine directory:", must_exist=False)

    dry_run = questionary.confirm("Dry-run?", default=True).ask()
    if dry_run is None:
        raise KeyboardInterrupt

    outcomes = apply_duplicate_actions(
        report, action,  # type: ignore[arg-type]
        quarantine=quarantine,
        relative_to=source,
        dry_run=dry_run,
        show_progress=True,
    )
    errors = sum(1 for o in outcomes if o.error)
    console.print(f"[green]{len(outcomes)} actions applied. {errors} error(s).[/green]")


def _wizard_sync() -> None:
    """Wizard for the ``sync`` command."""
    from .logging_setup import setup_logging
    from .media_scanner import ScanOptions, iter_media_files
    from .metadata import extract_metadata
    from .parallel import parallel_map
    from .sync import apply_sync, plan_sync

    console.rule("[bold cyan]Sync directories")

    source = _ask_path("Source directory:")
    destination = _ask_path("Destination directory:", must_exist=False)
    action = _ask_action(choices=("copy", "move"), default="copy")
    workers = _ask_workers()
    dry_run = questionary.confirm("Dry-run?", default=True).ask()
    if dry_run is None:
        raise KeyboardInterrupt

    setup_logging("INFO")

    src_files = list(iter_media_files(source, ScanOptions()))
    dst_files = list(iter_media_files(destination, ScanOptions())) if destination.exists() else []

    if not src_files:
        console.print("[yellow]No files found in source.[/yellow]")
        return

    meta_results = parallel_map(extract_metadata, src_files, workers=workers, show_progress=True,
                                description="Extracting metadata...")
    metadata_items = [r for r in meta_results if not isinstance(r, BaseException)]

    sync_plan = plan_sync(
        metadata_items, destination,
        destination_existing_files=dst_files,
        workers=workers, show_progress=True, dry_run=dry_run,
    )

    console.print(
        f"[green]New:[/green] {len(sync_plan.additions)}  "
        f"[yellow]Identical (skipped):[/yellow] {len(sync_plan.skipped_identical)}  "
        f"[red]Conflicts:[/red] {len(sync_plan.conflicts)}"
    )

    if not sync_plan.additions:
        console.print("[green]Nothing to sync.[/green]")
        return

    confirmed = questionary.confirm("Continue?", default=dry_run).ask()
    if not confirmed:
        console.print("[yellow]Cancelled.[/yellow]")
        return

    applied = apply_sync(sync_plan, action=action, dry_run=dry_run, show_progress=True)
    if dry_run:
        console.print(f"[yellow]Dry-run: {applied} file(s) would be synced.[/yellow]")
    else:
        console.print(f"[green]{applied} file(s) synced.[/green]")


def _wizard_history() -> None:
    """Show journal history and optionally trigger undo."""
    from .journal import Journal

    console.rule("[bold cyan]Operation history")

    try:
        journal = Journal()
    except Exception as exc:
        console.print(f"[red]Could not open the journal: {exc}[/red]")
        return

    with journal:
        runs = journal.list_runs(limit=20)
        if not runs:
            console.print("[yellow]No runs recorded.[/yellow]")
            return

        t = Table(title="Recent runs")
        t.add_column("Run ID", style="cyan")
        t.add_column("Command", style="magenta")
        t.add_column("Started", style="white")
        t.add_column("Status", style="green")
        t.add_column("Dry-run", style="yellow")
        for r in runs:
            t.add_row(
                r["run_id"][:8] + "…",
                r["command"],
                r["started_at"][:19],
                r["status"] or "—",
                "yes" if r["dry_run"] else "no",
            )
        console.print(t)

        run_choices = [r["run_id"][:8] + "… " + r["started_at"][:19] for r in runs]
        run_choices.append("← Back")
        choice = questionary.select("Select a run to view details / undo:", choices=run_choices).ask()
        if choice is None or choice == "← Back":
            return

        idx = run_choices.index(choice)
        selected = runs[idx]
        run_id = selected["run_id"]

        ops = journal.operations_for(run_id)
        ops_table = Table(title=f"Operations for run {run_id[:8]}…")
        ops_table.add_column("Seq", style="magenta", justify="right")
        ops_table.add_column("Action", style="cyan")
        ops_table.add_column("Source", style="yellow")
        ops_table.add_column("Destination", style="green")
        for op in ops[:50]:
            ops_table.add_row(str(op["seq"]), op["action"], op["src"], op["dst"] or "—")
        console.print(ops_table)

        if selected["dry_run"] or selected["status"] in ("reverted",):
            console.print("[dim]This run cannot be undone (dry-run or already reverted).[/dim]")
            return

        do_undo = questionary.confirm("Undo this run?", default=False).ask()
        if not do_undo:
            return

        dry = questionary.confirm("Dry-run for the undo?", default=True).ask()
        if dry is None:
            dry = True

        # Delegate to CLI undo logic via typer runner
        from typer.testing import CliRunner
        from .cli import app as cli_app

        runner = CliRunner()
        dry_flag = "--dry-run" if dry else "--no-dry-run"
        result = runner.invoke(cli_app, ["undo", "--run-id", run_id, dry_flag])
        console.print(result.output)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_tui() -> None:
    """Launch the interactive TUI wizard."""
    _require_questionary()

    console.print("[bold green]"
        " █████╗ ██╗  ██╗ ██████╗ ██╗      ██████╗     ██████╗  █████╗ ████████╗ █████╗ \n"
        "██╔══██╗╚██╗██╔╝██╔═══██╗██║     ██╔═══██╗    ██╔══██╗██╔══██╗╚══██╔══╝██╔══██╗\n"
        "███████║ ╚███╔╝ ██║   ██║██║     ██║   ██║    ██║  ██║███████║   ██║   ███████║\n"
        "██╔══██║ ██╔██╗ ██║   ██║██║     ██║   ██║    ██║  ██║██╔══██║   ██║   ██╔══██║\n"
        "██║  ██║██╔╝ ██╗╚██████╔╝███████╗╚██████╔╝    ██████╔╝██║  ██║   ██║   ██║  ██║\n"
        "╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝ ╚═════╝     ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝\n"
        "[/bold green]"
    )
    console.print("[dim]>>> [Axolo Data]: Organizing the chaos. <<<[/dim]\n")

    MENU_CHOICES = [
        "Organize files",
        "Find duplicates",
        "Sync folders",
        "View history & undo",
        "Exit",
    ]

    while True:
        choice = questionary.select(
            "What would you like to do?",
            choices=MENU_CHOICES,
        ).ask()

        if choice is None or choice == "Exit":
            console.print("[bold green]Goodbye![/bold green]")
            break

        try:
            if choice == "Organize files":
                _wizard_run()
            elif choice == "Find duplicates":
                _wizard_duplicates()
            elif choice == "Sync folders":
                _wizard_sync()
            elif choice == "View history & undo":
                _wizard_history()
        except KeyboardInterrupt:
            console.print("\n[yellow]Operation cancelled.[/yellow]")
        except Exception as exc:
            console.print(f"[red]Unexpected error: {exc}[/red]")

        console.print()
