"""Axolo Data — command-line interface."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

import typer
from rich.table import Table

from .commands._shared import (
    DEFAULT_WORKERS as _DEFAULT_WORKERS,
    collect_metadata as _collect_metadata,
    console,
    humanize_bytes as _humanize_bytes,
    parse_extra as _parse_extra,
    render_runs_table as _render_runs_table,
    render_summary as _render_summary,
    validate_workers as _validate_workers,
)
from .tui import run_tui
from .config import BUILTIN_PROFILES, OrganizerConfig, load_run_config
from .duplicates import (
    ActionOutcome,
    DuplicateActionError,
    DuplicateAnalyzer,
    DuplicatesReport,
    apply_duplicate_actions,
)
from .journal import Journal
from .logging_setup import setup_logging
from .media_scanner import ScanOptions, iter_media_files
from .metadata import MediaMetadata, extract_metadata
from .organizer import AxoloOrganizer, _safe_move
from .sync import apply_sync, plan_sync
from .templates import DEFAULT_TEMPLATES

logger = logging.getLogger(__name__)
app = typer.Typer(add_completion=False, help="Axolo Data · Organizing the chaos.")


# ---------------------------------------------------------------------------
# run command
# ---------------------------------------------------------------------------

@app.command()
def run(
    source: Optional[Path] = typer.Option(None, "--source", "-s", help="Source directory to scan."),
    destination: Optional[Path] = typer.Option(None, "--destination", "-d", help="Destination directory."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="YAML configuration file."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Profile name to use."),
    template: Optional[str] = typer.Option(None, "--template", help="Custom template (overrides --profile)."),
    action: Optional[str] = typer.Option(None, "--action", "-a", help="Action: move|copy|link."),
    link_kind: str = typer.Option("symbolic", "--link-kind", help="Link type: hard|symbolic."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run/--no-dry-run", help="Preview changes without moving files."),
    recursive: Optional[bool] = typer.Option(None, "--recursive/--no-recursive", help="Scan directories recursively."),
    follow_symlinks: Optional[bool] = typer.Option(None, "--follow-symlinks/--no-follow-symlinks"),
    include_ext: Optional[List[str]] = typer.Option(None, "--include-ext"),
    exclude_ext: Optional[List[str]] = typer.Option(None, "--exclude-ext"),
    extra: Optional[List[str]] = typer.Option(None, "--extra", help="key=value pairs for the template."),
    workers: int = typer.Option(_DEFAULT_WORKERS, "--workers", help="Number of parallel workers (1-32)."),
    no_journal: bool = typer.Option(False, "--no-journal/--journal", help="Disable the operations journal."),
    log_level: str = typer.Option("INFO", "--log-level", help="Logging level."),
    quiet: bool = typer.Option(False, "--quiet/--no-quiet", help="Suppress console output."),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose", help="Enable DEBUG logging."),
    json_logs: bool = typer.Option(False, "--json-logs/--no-json-logs", help="Emit logs as JSON Lines."),
) -> None:
    """Organize media files according to the configured template."""
    setup_logging(log_level, quiet=quiet, verbose=verbose, json_logs=json_logs)
    show_progress = not (quiet or json_logs)
    workers = _validate_workers(workers)

    file_cfg: dict = {}
    if config_path:
        try:
            file_cfg = load_run_config(config_path)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    effective_source = source or (Path(file_cfg["source"]).expanduser() if "source" in file_cfg else None)
    effective_dest = destination or (Path(file_cfg["destination"]).expanduser() if "destination" in file_cfg else None)

    if effective_source is None:
        effective_source = Path(typer.prompt("Source directory")).expanduser()
    if effective_dest is None:
        effective_dest = Path(typer.prompt("Destination directory")).expanduser()

    raw_action = action or file_cfg.get("action") or typer.prompt(
        "Action to apply (move / copy / link)", default="move"
    )
    effective_action = raw_action.lower()
    if effective_action not in {"move", "copy", "link"}:
        raise typer.BadParameter("Action must be move, copy or link.")

    link_kind = link_kind.lower()
    if link_kind not in {"hard", "symbolic"}:
        raise typer.BadParameter("link-kind must be 'hard' or 'symbolic'.")

    if template:
        effective_template = template
    elif profile:
        effective_template = profile
    elif "template" in file_cfg:
        effective_template = file_cfg["template"]
    else:
        available = sorted(set(DEFAULT_TEMPLATES) | set(BUILTIN_PROFILES))
        console.print(f"[cyan]Perfiles disponibles:[/cyan] {', '.join(available)}")
        effective_template = typer.prompt("Profile or template to use", default="default")

    if effective_template not in DEFAULT_TEMPLATES and effective_template not in BUILTIN_PROFILES:
        raise typer.BadParameter(
            f"Profile '{effective_template}' is not defined. "
            f"Perfiles disponibles: {', '.join(sorted(BUILTIN_PROFILES))}",
        )

    effective_dry_run = dry_run if dry_run is not None else file_cfg.get("dry_run", False)
    effective_recursive = recursive if recursive is not None else file_cfg.get("recursive", True)
    effective_symlinks = follow_symlinks if follow_symlinks is not None else file_cfg.get("follow_symlinks", False)
    extra_values = _parse_extra(extra)
    if not extra_values and "extra" in file_cfg:
        extra_values = file_cfg["extra"]

    config = OrganizerConfig(
        source=effective_source,
        destination=effective_dest,
        action=effective_action,
        link_kind=link_kind,
        template=effective_template,
        dry_run=effective_dry_run,
        recursive=effective_recursive,
        follow_symlinks=effective_symlinks,
        include_extensions=include_ext or file_cfg.get("include_extensions", []),
        exclude_extensions=exclude_ext or file_cfg.get("exclude_extensions", []),
        extra=extra_values,
        routing=file_cfg.get("routing", {}),
        routing_filename_templates=file_cfg.get("routing_filename_templates", {}),
    )

    scan_options = ScanOptions(
        recursive=config.recursive,
        follow_symlinks=config.follow_symlinks,
        include_extensions=config.normalized_include_extensions(),
        exclude_extensions=config.normalized_exclude_extensions(),
    )

    files = list(iter_media_files(config.source, scan_options))
    if not files:
        console.print("[yellow]No files found to process.[/yellow]")
        raise typer.Exit(code=0)

    journal = Journal() if not no_journal else None
    organizer = AxoloOrganizer(
        config=config,
        show_progress=show_progress,
        workers=workers,
        journal=journal,
    )

    console.print(
        f"Processing [bold]{len(files)}[/bold] files "
        f"from [cyan]{config.source}[/cyan] to [green]{config.destination}[/green]..."
    )
    summary = organizer.organize(files)
    if journal:
        journal.close()

    _render_summary(summary)


# ---------------------------------------------------------------------------
# duplicates command
# ---------------------------------------------------------------------------


def _render_duplicates_report(report: DuplicatesReport, max_groups: Optional[int] = None) -> None:
    if report.processed == 0:
        console.print("[yellow]No files processed for comparison.[/yellow]")
        return

    total_groups = len(report.groups)
    display_groups = report.groups
    truncated = False
    if max_groups is not None and total_groups > max_groups:
        display_groups = report.groups[:max_groups]
        truncated = True

    if display_groups:
        table = Table(title="Duplicate files detected")
        table.add_column("#", style="magenta", justify="right")
        table.add_column("Canonical", style="cyan")
        table.add_column("Duplicate", style="yellow")
        table.add_column("Size", style="green", justify="right")
        table.add_column("Hash", style="white")
        table.add_column("Reclaimable", style="red", justify="right")

        for idx, group in enumerate(display_groups, start=1):
            canonical_str = str(group.canonical.metadata.source_path)
            size_str = _humanize_bytes(group.size)
            rec_str = _humanize_bytes(group.reclaimable_bytes)
            digest_short = group.digest[:12]
            for dup_idx, dup in enumerate(group.duplicates):
                table.add_row(
                    str(idx) if dup_idx == 0 else "",
                    canonical_str if dup_idx == 0 else "",
                    str(dup.metadata.source_path),
                    size_str if dup_idx == 0 else "",
                    digest_short if dup_idx == 0 else "",
                    rec_str if dup_idx == 0 else "",
                )
        console.print(table)
    else:
        console.print("[green]No duplicate files found.[/green]")

    console.print(
        f"[blue]Scanned:[/blue] {report.scanned}  "
        f"[blue]Hashed:[/blue] {report.processed}  "
        f"[blue]Groups:[/blue] {total_groups}  "
        f"[blue]Reclaimable:[/blue] {_humanize_bytes(report.reclaimable_bytes)}  "
        f"[blue]Algorithm:[/blue] {report.algorithm}"
    )
    if truncated:
        console.print(
            f"[yellow]Showing only the first {max_groups} groups. "
            f"Use --max-groups to adjust the limit.[/yellow]"
        )


def _render_action_outcomes(outcomes: List[ActionOutcome], dry_run: bool) -> None:
    if not outcomes:
        return
    if dry_run:
        console.print("[yellow]Dry run — no files have been modified.[/yellow]")

    table = Table(title="Actions on duplicates")
    table.add_column("Action", style="magenta")
    table.add_column("Source", style="cyan")
    table.add_column("Destination / Canonical", style="green")
    table.add_column("Status", style="white")

    for outcome in outcomes:
        dest_str = str(outcome.destination) if outcome.destination else "—"
        if outcome.error:
            status = f"[red]ERROR: {outcome.error}[/red]"
        elif outcome.dry_run:
            status = "[yellow]dry-run[/yellow]"
        else:
            status = "[green]OK[/green]"
        table.add_row(outcome.action.upper(), str(outcome.source), dest_str, status)

    console.print(table)
    errors = [o for o in outcomes if o.error]
    if errors:
        console.print(f"[red]{len(errors)} error(s) while executing actions.[/red]")


@app.command()
def duplicates(
    source: Path = typer.Option(..., "--source", "-s", help="Directory to analyze."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks"),
    include_ext: Optional[List[str]] = typer.Option(None, "--include-ext"),
    exclude_ext: Optional[List[str]] = typer.Option(None, "--exclude-ext"),
    algorithm: str = typer.Option("blake2b", "--algorithm", help="blake2b|sha256|md5."),
    min_size: int = typer.Option(1, "--min-size", help="Minimum file size in bytes."),
    prefer_under: Optional[Path] = typer.Option(None, "--prefer-under", help="Preferred directory when selecting canonical copy."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="JSON file for the report."),
    action: Optional[str] = typer.Option(None, "--action", help="move|link|delete."),
    quarantine: Optional[Path] = typer.Option(None, "--quarantine", help="Destination for --action move."),
    link_kind: str = typer.Option("hard", "--link-kind", help="hard|symbolic."),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    max_groups: Optional[int] = typer.Option(None, "--max-groups"),
    workers: int = typer.Option(_DEFAULT_WORKERS, "--workers", help="Number of parallel workers (1-32)."),
    no_journal: bool = typer.Option(False, "--no-journal/--journal"),
    log_level: str = typer.Option("INFO", "--log-level"),
    quiet: bool = typer.Option(False, "--quiet/--no-quiet"),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose"),
    json_logs: bool = typer.Option(False, "--json-logs/--no-json-logs"),
) -> None:
    """Detect exact byte-level duplicate files in the given source directory."""
    setup_logging(log_level, quiet=quiet, verbose=verbose, json_logs=json_logs)
    show_progress = not (quiet or json_logs)
    workers = _validate_workers(workers)

    algorithm = algorithm.lower()
    if algorithm not in {"blake2b", "sha256", "md5"}:
        raise typer.BadParameter("Unsupported algorithm. Use blake2b, sha256 or md5.")
    if min_size < 0:
        raise typer.BadParameter("min-size cannot be negative.")
    if max_groups is not None and max_groups <= 0:
        raise typer.BadParameter("max-groups must be greater than 0.")

    if action is not None:
        action = action.lower()
        if action not in {"move", "link", "delete"}:
            raise typer.BadParameter("Unsupported action. Use move, link or delete.")
        if action == "move" and quarantine is None:
            raise typer.BadParameter("--quarantine is required with --action move.")

    link_kind = link_kind.lower()
    if link_kind not in {"hard", "symbolic"}:
        raise typer.BadParameter("Invalid link-kind. Use 'hard' or 'symbolic'.")

    scan_options = ScanOptions(
        recursive=recursive,
        follow_symlinks=follow_symlinks,
        include_extensions={
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in (include_ext or [])
        },
        exclude_extensions={
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in (exclude_ext or [])
        },
    )

    files = list(iter_media_files(source, scan_options))
    if not files:
        console.print("[yellow]No files found to analyze.[/yellow]")
        raise typer.Exit(code=0)

    console.print(f"Analyzing [bold]{len(files)}[/bold] files for duplicates...")
    metadata_items, errors = _collect_metadata(files, workers=workers, show_progress=show_progress)
    if errors:
        console.print(f"[yellow]Skipped {len(errors)} files due to metadata errors.[/yellow]")

    try:
        analyzer = DuplicateAnalyzer(
            algorithm=algorithm, min_size=min_size,
            prefer_under=prefer_under, workers=workers,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    report = analyzer.analyze(metadata_items, show_progress=show_progress)
    _render_duplicates_report(report, max_groups=max_groups)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, ensure_ascii=False, indent=2)
        console.print(f"[green]Report saved to {output}[/green]")

    if action:
        journal: Optional[Journal] = None
        run_id: Optional[str] = None
        if not no_journal and not dry_run:
            journal = Journal()
            run_id = journal.start_run(
                "duplicates", source=source, dry_run=dry_run,
                args={"action": action, "algorithm": algorithm},
            )
        try:
            outcomes = apply_duplicate_actions(
                report,
                action,  # type: ignore[arg-type]
                quarantine=quarantine,
                relative_to=source,
                link_kind=link_kind,  # type: ignore[arg-type]
                dry_run=dry_run,
                show_progress=show_progress,
                journal=journal,
                run_id=run_id,
            )
        except DuplicateActionError as exc:
            console.print(f"[red]Configuration error: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        finally:
            if journal and run_id:
                journal.finish_run(run_id, "completed")
            if journal:
                journal.close()

        _render_action_outcomes(outcomes, dry_run=dry_run)


# ---------------------------------------------------------------------------
# undo command
# ---------------------------------------------------------------------------

@app.command()
def undo(
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Run ID to revert (default: last run)."),
    list_runs: bool = typer.Option(False, "--list", help="List recent runs and exit."),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Simulate without modifying files."),
    limit: int = typer.Option(10, "--limit", help="Number of runs to show with --list."),
    log_level: str = typer.Option("INFO", "--log-level"),
    quiet: bool = typer.Option(False, "--quiet/--no-quiet"),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose"),
) -> None:
    """Revert the operations of a previous run recorded in the journal."""
    setup_logging(log_level, quiet=quiet, verbose=verbose)

    try:
        journal = Journal()
    except Exception as exc:
        console.print(f"[red]Could not open the journal: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    with journal:
        if list_runs:
            _render_runs_table(journal.list_runs(limit=limit))
            return

        target_run_id = run_id or journal.last_revertible_run_id()
        if target_run_id is None:
            console.print("[yellow]No runs recorded to revert.[/yellow]")
            raise typer.Exit(code=0)

        run_meta = journal.run_by_id(target_run_id)
        if run_meta is None:
            console.print(f"[red]Run not found: {target_run_id}[/red]")
            raise typer.Exit(code=1)

        ops = journal.operations_for(target_run_id)
        if not ops:
            console.print(f"[yellow]No operations found for run {target_run_id}[/yellow]")
            raise typer.Exit(code=0)

        console.print(
            f"Reverting [bold]{len(ops)}[/bold] operation(s) "
            f"from run [cyan]{target_run_id[:8]}…[/cyan] "
            f"([italic]{run_meta['command']} · {run_meta['started_at'][:19]}[/italic])"
        )
        if dry_run:
            console.print("[yellow]Dry-run mode: no files will be modified.[/yellow]")

        table = Table(title="Undo results")
        table.add_column("Orig. action", style="magenta")
        table.add_column("Original source", style="cyan")
        table.add_column("Original destination", style="yellow")
        table.add_column("Status", style="white")

        reverted = 0
        errors = 0
        for op in reversed(ops):
            if op["reverted_at"]:
                table.add_row(op["action"], op["src"], op["dst"] or "—", "[dim]already reverted[/dim]")
                continue

            action = op["action"]
            src = Path(op["src"])
            dst = Path(op["dst"]) if op["dst"] else None
            status_text = ""

            try:
                if action == "move":
                    # move A→B was recorded as src=A, dst=B → undo moves B back to A
                    if dst is None:
                        raise ValueError("dst is None for a move operation")
                    if not dry_run:
                        src.parent.mkdir(parents=True, exist_ok=True)
                        _safe_move(dst, src)
                    status_text = "[yellow]dry-run[/yellow]" if dry_run else "[green]revertido[/green]"

                elif action == "copy":
                    # copy A→B → undo deletes B
                    if dst is None:
                        raise ValueError("dst is None for a copy operation")
                    if not dry_run and dst.exists():
                        dst.unlink()
                    status_text = "[yellow]dry-run[/yellow]" if dry_run else "[green]revertido[/green]"

                elif action in {"link", "link-hard", "link-symbolic"}:
                    # link at src replaced by link pointing to dst → undo unlinks src
                    if not dry_run and src.exists():
                        src.unlink()
                    status_text = "[yellow]dry-run[/yellow]" if dry_run else "[green]revertido[/green]"

                elif action == "delete":
                    status_text = "[red]not reversible (permanent delete)[/red]"
                    errors += 1
                    table.add_row(action, str(src), str(dst) if dst else "—", status_text)
                    continue

                else:
                    status_text = f"[dim]unknown action: {action}[/dim]"

                if not dry_run and status_text.startswith("[green]"):
                    journal.mark_reverted(op["id"])
                    reverted += 1

            except Exception as exc:
                status_text = f"[red]ERROR: {exc}[/red]"
                logger.error("Error revirtiendo op %d: %s", op["id"], exc)
                errors += 1

            table.add_row(action, str(src), str(dst) if dst else "—", status_text)

        console.print(table)

        if not dry_run and reverted > 0:
            journal.finish_run(target_run_id, "reverted")
            console.print(
                f"[green]{reverted} operation(s) reverted.[/green]"
                + (f"  [red]{errors} error(s).[/red]" if errors else "")
            )
        elif dry_run:
            console.print(f"[yellow]Dry-run: {len(ops)} operation(s) would be reverted.[/yellow]")


# ---------------------------------------------------------------------------
# sync command
# ---------------------------------------------------------------------------


@app.command()
def sync(
    source: Path = typer.Option(..., "--source", "-s", help="Source directory."),
    destination: Path = typer.Option(..., "--destination", "-d", help="Destination directory."),
    action: str = typer.Option("copy", "--action", help="copy|move."),
    template: str = typer.Option("default", "--template", help="Template de carpeta destino."),
    algorithm: str = typer.Option("blake2b", "--algorithm", help="blake2b|sha256|md5."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks"),
    include_ext: Optional[List[str]] = typer.Option(None, "--include-ext"),
    exclude_ext: Optional[List[str]] = typer.Option(None, "--exclude-ext"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Path to save the JSON plan."),
    workers: int = typer.Option(_DEFAULT_WORKERS, "--workers"),
    no_journal: bool = typer.Option(False, "--no-journal/--journal"),
    log_level: str = typer.Option("INFO", "--log-level"),
    quiet: bool = typer.Option(False, "--quiet/--no-quiet"),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose"),
    json_logs: bool = typer.Option(False, "--json-logs/--no-json-logs"),
    extra: Optional[List[str]] = typer.Option(None, "--extra"),
) -> None:
    """Sync source → destination in a union dedup-aware manner (never deletes)."""
    setup_logging(log_level, quiet=quiet, verbose=verbose, json_logs=json_logs)
    show_progress = not (quiet or json_logs)
    workers = _validate_workers(workers)

    action = action.lower()
    if action not in {"copy", "move"}:
        raise typer.BadParameter("Action must be copy or move.")
    algorithm = algorithm.lower()
    if algorithm not in {"blake2b", "sha256", "md5"}:
        raise typer.BadParameter("Unsupported algorithm.")

    extra_values = _parse_extra(extra)

    # Resolve folder template string
    if template in DEFAULT_TEMPLATES:
        folder_tmpl = DEFAULT_TEMPLATES[template]
    elif template in BUILTIN_PROFILES:
        folder_tmpl = BUILTIN_PROFILES[template].template
    else:
        folder_tmpl = template  # treat as literal template string

    scan_opts = ScanOptions(
        recursive=recursive,
        follow_symlinks=follow_symlinks,
        include_extensions={
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in (include_ext or [])
        },
        exclude_extensions={
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in (exclude_ext or [])
        },
    )

    src_files = list(iter_media_files(source, scan_opts))
    if not src_files:
        console.print("[yellow]No files found in source.[/yellow]")
        raise typer.Exit(code=0)

    dst_files = list(iter_media_files(destination, scan_opts)) if destination.exists() else []

    console.print(
        f"Syncing [bold]{len(src_files)}[/bold] files from [cyan]{source}[/cyan] "
        f"to [green]{destination}[/green] "
        f"([bold]{len(dst_files)}[/bold] existing files at destination)..."
    )

    src_metadata, src_errors = _collect_metadata(src_files, workers=workers, show_progress=show_progress)
    if src_errors:
        console.print(f"[yellow]Skipped {len(src_errors)} source files due to metadata errors.[/yellow]")

    sync_plan = plan_sync(
        src_metadata,
        destination,
        destination_existing_files=dst_files,
        algorithm=algorithm,
        workers=workers,
        show_progress=show_progress,
        folder_template=folder_tmpl,
        extra=extra_values,
        dry_run=dry_run,
    )

    _render_sync_plan(sync_plan, dry_run=dry_run)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as fh:
            json.dump(sync_plan.to_dict(), fh, ensure_ascii=False, indent=2)
        console.print(f"[green]Plan saved to {output}[/green]")

    if not sync_plan.additions:
        console.print("[green]Nothing to sync.[/green]")
        raise typer.Exit(code=0)

    journal: Optional[Journal] = None
    run_id_sync: Optional[str] = None
    if not no_journal and not dry_run:
        journal = Journal()
        run_id_sync = journal.start_run(
            "sync", source=source, destination=destination, dry_run=dry_run,
            args={"action": action, "algorithm": algorithm},
        )

    try:
        applied = apply_sync(
            sync_plan,
            action=action,
            dry_run=dry_run,
            show_progress=show_progress,
            journal=journal,
            run_id=run_id_sync,
        )
    finally:
        if journal and run_id_sync:
            journal.finish_run(run_id_sync, "completed")
        if journal:
            journal.close()

    if dry_run:
        console.print(f"[yellow]Dry-run: {len(sync_plan.additions)} file(s) would be copied/moved.[/yellow]")
    else:
        console.print(f"[green]{applied} file(s) synced successfully.[/green]")


def _render_sync_plan(plan: SyncPlan, *, dry_run: bool = True) -> None:
    if plan.skipped_identical:
        console.print(f"[green]Idénticos (omitidos):[/green] {len(plan.skipped_identical)}")

    if plan.conflicts:
        console.print(f"[yellow]Conflictos renombrados:[/yellow] {len(plan.conflicts)}")
        t = Table(title="Resolved name conflicts")
        t.add_column("Source", style="cyan")
        t.add_column("Original name", style="yellow")
        t.add_column("Renamed to", style="green")
        for c in plan.conflicts:
            t.add_row(str(c.metadata.source_path), c.original_name, str(c.resolved_destination))
        console.print(t)

    if plan.additions:
        label = "To copy/move" if dry_run else "Added"
        t = Table(title=f"{label} ({len(plan.additions)})")
        t.add_column("Source", style="cyan")
        t.add_column("Destination", style="green")
        t.add_column("Renamed", style="yellow")
        for a in plan.additions[:50]:
            t.add_row(str(a.metadata.source_path), str(a.destination), "yes" if a.renamed else "no")
        if len(plan.additions) > 50:
            console.print(f"[dim]…and {len(plan.additions) - 50} more[/dim]")
        console.print(t)

    if plan.errors:
        console.print(f"[red]Errors during analysis:[/red] {len(plan.errors)}")


# ---------------------------------------------------------------------------
# tui command
# ---------------------------------------------------------------------------


@app.command()
def tui() -> None:
    """Launch the interactive TUI wizard."""
    run_tui()


