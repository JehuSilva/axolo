"""Command line interface for the media organizer."""

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
from .organizer import MediaOrganizer, _safe_move
from .sync import apply_sync, plan_sync
from .templates import DEFAULT_TEMPLATES

logger = logging.getLogger(__name__)
app = typer.Typer(add_completion=False, help="Organiza fotos y videos en carpetas.")


# ---------------------------------------------------------------------------
# run command
# ---------------------------------------------------------------------------

@app.command()
def run(
    source: Optional[Path] = typer.Option(None, "--source", "-s", help="Directorio de origen a analizar."),
    destination: Optional[Path] = typer.Option(None, "--destination", "-d", help="Directorio de destino."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Archivo YAML de configuración."),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Nombre del perfil a usar."),
    template: Optional[str] = typer.Option(None, "--template", help="Template personalizado (ignora --profile)."),
    action: Optional[str] = typer.Option(None, "--action", "-a", help="Acción: move|copy|link."),
    link_kind: str = typer.Option("symbolic", "--link-kind", help="Tipo de enlace: hard|symbolic."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run/--no-dry-run", help="Muestra cambios sin mover archivos."),
    recursive: Optional[bool] = typer.Option(None, "--recursive/--no-recursive", help="Buscar de forma recursiva."),
    follow_symlinks: Optional[bool] = typer.Option(None, "--follow-symlinks/--no-follow-symlinks"),
    include_ext: Optional[List[str]] = typer.Option(None, "--include-ext"),
    exclude_ext: Optional[List[str]] = typer.Option(None, "--exclude-ext"),
    extra: Optional[List[str]] = typer.Option(None, "--extra", help="Pares clave=valor para el template."),
    workers: int = typer.Option(_DEFAULT_WORKERS, "--workers", help="Número de hilos paralelos (1-32)."),
    no_journal: bool = typer.Option(False, "--no-journal/--journal", help="Desactiva el journal de operaciones."),
    log_level: str = typer.Option("INFO", "--log-level", help="Nivel de logging."),
    quiet: bool = typer.Option(False, "--quiet/--no-quiet", help="Suprime mensajes de consola."),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose", help="Activa logging DEBUG."),
    json_logs: bool = typer.Option(False, "--json-logs/--no-json-logs", help="Emite logs como JSON Lines."),
) -> None:
    """Organiza archivos multimedia según el template configurado."""
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
        effective_source = Path(typer.prompt("Directorio de origen (source)")).expanduser()
    if effective_dest is None:
        effective_dest = Path(typer.prompt("Directorio de destino (destination)")).expanduser()

    raw_action = action or file_cfg.get("action") or typer.prompt(
        "Acción a aplicar (move / copy / link)", default="move"
    )
    effective_action = raw_action.lower()
    if effective_action not in {"move", "copy", "link"}:
        raise typer.BadParameter("La acción debe ser move, copy o link.")

    link_kind = link_kind.lower()
    if link_kind not in {"hard", "symbolic"}:
        raise typer.BadParameter("link-kind debe ser 'hard' o 'symbolic'.")

    if template:
        effective_template = template
    elif profile:
        effective_template = profile
    elif "template" in file_cfg:
        effective_template = file_cfg["template"]
    else:
        available = sorted(set(DEFAULT_TEMPLATES) | set(BUILTIN_PROFILES))
        console.print(f"[cyan]Perfiles disponibles:[/cyan] {', '.join(available)}")
        effective_template = typer.prompt("Perfil o template a usar", default="default")

    if effective_template not in DEFAULT_TEMPLATES and effective_template not in BUILTIN_PROFILES:
        raise typer.BadParameter(
            f"El perfil '{effective_template}' no está definido. "
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
    )

    scan_options = ScanOptions(
        recursive=config.recursive,
        follow_symlinks=config.follow_symlinks,
        include_extensions=config.normalized_include_extensions(),
        exclude_extensions=config.normalized_exclude_extensions(),
    )

    files = list(iter_media_files(config.source, scan_options))
    if not files:
        console.print("[yellow]No se encontraron archivos para procesar.[/yellow]")
        raise typer.Exit(code=0)

    journal = Journal() if not no_journal else None
    organizer = MediaOrganizer(
        config=config,
        show_progress=show_progress,
        workers=workers,
        journal=journal,
    )

    console.print(
        f"Procesando [bold]{len(files)}[/bold] archivos "
        f"desde [cyan]{config.source}[/cyan] hacia [green]{config.destination}[/green]..."
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
        console.print("[yellow]No se procesaron archivos para comparar.[/yellow]")
        return

    total_groups = len(report.groups)
    display_groups = report.groups
    truncated = False
    if max_groups is not None and total_groups > max_groups:
        display_groups = report.groups[:max_groups]
        truncated = True

    if display_groups:
        table = Table(title="Archivos duplicados detectados")
        table.add_column("#", style="magenta", justify="right")
        table.add_column("Canónico", style="cyan")
        table.add_column("Duplicado", style="yellow")
        table.add_column("Tamaño", style="green", justify="right")
        table.add_column("Hash", style="white")
        table.add_column("Recuperable", style="red", justify="right")

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
        console.print("[green]No se encontraron archivos duplicados.[/green]")

    console.print(
        f"[blue]Escaneados:[/blue] {report.scanned}  "
        f"[blue]Hasheados:[/blue] {report.processed}  "
        f"[blue]Grupos:[/blue] {total_groups}  "
        f"[blue]Recuperable:[/blue] {_humanize_bytes(report.reclaimable_bytes)}  "
        f"[blue]Algoritmo:[/blue] {report.algorithm}"
    )
    if truncated:
        console.print(
            f"[yellow]Se muestran solo los primeros {max_groups} grupos. "
            f"Usa --max-groups para ajustar el límite.[/yellow]"
        )


def _render_action_outcomes(outcomes: List[ActionOutcome], dry_run: bool) -> None:
    if not outcomes:
        return
    if dry_run:
        console.print("[yellow]Dry run — ningún archivo ha sido modificado.[/yellow]")

    table = Table(title="Acciones sobre duplicados")
    table.add_column("Acción", style="magenta")
    table.add_column("Origen", style="cyan")
    table.add_column("Destino / Canónico", style="green")
    table.add_column("Estado", style="white")

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
        console.print(f"[red]{len(errors)} error(es) durante la ejecución de acciones.[/red]")


@app.command()
def duplicates(
    source: Path = typer.Option(..., "--source", "-s", help="Directorio a analizar."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks"),
    include_ext: Optional[List[str]] = typer.Option(None, "--include-ext"),
    exclude_ext: Optional[List[str]] = typer.Option(None, "--exclude-ext"),
    algorithm: str = typer.Option("blake2b", "--algorithm", help="blake2b|sha256|md5."),
    min_size: int = typer.Option(1, "--min-size", help="Tamaño mínimo en bytes."),
    prefer_under: Optional[Path] = typer.Option(None, "--prefer-under", help="Directorio preferido al elegir canónico."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Archivo JSON con el reporte."),
    action: Optional[str] = typer.Option(None, "--action", help="move|link|delete."),
    quarantine: Optional[Path] = typer.Option(None, "--quarantine", help="Destino para --action move."),
    link_kind: str = typer.Option("hard", "--link-kind", help="hard|symbolic."),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    max_groups: Optional[int] = typer.Option(None, "--max-groups"),
    workers: int = typer.Option(_DEFAULT_WORKERS, "--workers", help="Número de hilos paralelos (1-32)."),
    no_journal: bool = typer.Option(False, "--no-journal/--journal"),
    log_level: str = typer.Option("INFO", "--log-level"),
    quiet: bool = typer.Option(False, "--quiet/--no-quiet"),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose"),
    json_logs: bool = typer.Option(False, "--json-logs/--no-json-logs"),
) -> None:
    """Detecta archivos duplicados exactos (byte-a-byte) en la fuente indicada."""
    setup_logging(log_level, quiet=quiet, verbose=verbose, json_logs=json_logs)
    show_progress = not (quiet or json_logs)
    workers = _validate_workers(workers)

    algorithm = algorithm.lower()
    if algorithm not in {"blake2b", "sha256", "md5"}:
        raise typer.BadParameter("Algoritmo no soportado. Usa blake2b, sha256 o md5.")
    if min_size < 0:
        raise typer.BadParameter("min-size no puede ser negativo.")
    if max_groups is not None and max_groups <= 0:
        raise typer.BadParameter("max-groups debe ser mayor que 0.")

    if action is not None:
        action = action.lower()
        if action not in {"move", "link", "delete"}:
            raise typer.BadParameter("Acción no soportada. Usa move, link o delete.")
        if action == "move" and quarantine is None:
            raise typer.BadParameter("--quarantine es obligatorio con --action move.")

    link_kind = link_kind.lower()
    if link_kind not in {"hard", "symbolic"}:
        raise typer.BadParameter("link-kind no válido. Usa 'hard' o 'symbolic'.")

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
        console.print("[yellow]No se encontraron archivos para analizar.[/yellow]")
        raise typer.Exit(code=0)

    console.print(f"Analizando [bold]{len(files)}[/bold] archivos en busca de duplicados...")
    metadata_items, errors = _collect_metadata(files, workers=workers, show_progress=show_progress)
    if errors:
        console.print(f"[yellow]Se omitieron {len(errors)} archivos por errores de metadatos.[/yellow]")

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
        console.print(f"[green]Reporte guardado en {output}[/green]")

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
            console.print(f"[red]Error de configuración: {exc}[/red]")
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
    run_id: Optional[str] = typer.Option(None, "--run-id", help="ID del run a revertir (default: último run)."),
    list_runs: bool = typer.Option(False, "--list", help="Listar runs recientes y salir."),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Simula sin modificar archivos."),
    limit: int = typer.Option(10, "--limit", help="Número de runs a mostrar con --list."),
    log_level: str = typer.Option("INFO", "--log-level"),
    quiet: bool = typer.Option(False, "--quiet/--no-quiet"),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose"),
) -> None:
    """Revierte las operaciones de un run anterior registrado en el journal."""
    setup_logging(log_level, quiet=quiet, verbose=verbose)

    try:
        journal = Journal()
    except Exception as exc:
        console.print(f"[red]No se pudo abrir el journal: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    with journal:
        if list_runs:
            _render_runs_table(journal.list_runs(limit=limit))
            return

        target_run_id = run_id or journal.last_revertible_run_id()
        if target_run_id is None:
            console.print("[yellow]No hay runs registrados para revertir.[/yellow]")
            raise typer.Exit(code=0)

        run_meta = journal.run_by_id(target_run_id)
        if run_meta is None:
            console.print(f"[red]Run no encontrado: {target_run_id}[/red]")
            raise typer.Exit(code=1)

        ops = journal.operations_for(target_run_id)
        if not ops:
            console.print(f"[yellow]No se encontraron operaciones para el run {target_run_id}[/yellow]")
            raise typer.Exit(code=0)

        console.print(
            f"Revirtiendo [bold]{len(ops)}[/bold] operación(es) "
            f"del run [cyan]{target_run_id[:8]}…[/cyan] "
            f"([italic]{run_meta['command']} · {run_meta['started_at'][:19]}[/italic])"
        )
        if dry_run:
            console.print("[yellow]Modo dry-run: no se modificará ningún archivo.[/yellow]")

        table = Table(title="Resultados de undo")
        table.add_column("Acción orig.", style="magenta")
        table.add_column("Origen original", style="cyan")
        table.add_column("Destino original", style="yellow")
        table.add_column("Estado", style="white")

        reverted = 0
        errors = 0
        for op in reversed(ops):
            if op["reverted_at"]:
                table.add_row(op["action"], op["src"], op["dst"] or "—", "[dim]ya revertido[/dim]")
                continue

            action = op["action"]
            src = Path(op["src"])
            dst = Path(op["dst"]) if op["dst"] else None
            status_text = ""

            try:
                if action == "move":
                    # move A→B was recorded as src=A, dst=B → undo moves B back to A
                    if dst is None:
                        raise ValueError("dst es None para una operación move")
                    if not dry_run:
                        src.parent.mkdir(parents=True, exist_ok=True)
                        _safe_move(dst, src)
                    status_text = "[yellow]dry-run[/yellow]" if dry_run else "[green]revertido[/green]"

                elif action == "copy":
                    # copy A→B → undo deletes B
                    if dst is None:
                        raise ValueError("dst es None para una operación copy")
                    if not dry_run and dst.exists():
                        dst.unlink()
                    status_text = "[yellow]dry-run[/yellow]" if dry_run else "[green]revertido[/green]"

                elif action in {"link", "link-hard", "link-symbolic"}:
                    # link at src replaced by link pointing to dst → undo unlinks src
                    if not dry_run and src.exists():
                        src.unlink()
                    status_text = "[yellow]dry-run[/yellow]" if dry_run else "[green]revertido[/green]"

                elif action == "delete":
                    status_text = "[red]no reversible (delete permanente)[/red]"
                    errors += 1
                    table.add_row(action, str(src), str(dst) if dst else "—", status_text)
                    continue

                else:
                    status_text = f"[dim]acción desconocida: {action}[/dim]"

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
                f"[green]{reverted} operación(es) revertidas.[/green]"
                + (f"  [red]{errors} error(es).[/red]" if errors else "")
            )
        elif dry_run:
            console.print(f"[yellow]Dry-run: {len(ops)} operación(es) serían revertidas.[/yellow]")


# ---------------------------------------------------------------------------
# sync command
# ---------------------------------------------------------------------------


@app.command()
def sync(
    source: Path = typer.Option(..., "--source", "-s", help="Directorio de origen."),
    destination: Path = typer.Option(..., "--destination", "-d", help="Directorio de destino."),
    action: str = typer.Option("copy", "--action", help="copy|move."),
    template: str = typer.Option("default", "--template", help="Template de carpeta destino."),
    algorithm: str = typer.Option("blake2b", "--algorithm", help="blake2b|sha256|md5."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks"),
    include_ext: Optional[List[str]] = typer.Option(None, "--include-ext"),
    exclude_ext: Optional[List[str]] = typer.Option(None, "--exclude-ext"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Ruta para guardar el plan JSON."),
    workers: int = typer.Option(_DEFAULT_WORKERS, "--workers"),
    no_journal: bool = typer.Option(False, "--no-journal/--journal"),
    log_level: str = typer.Option("INFO", "--log-level"),
    quiet: bool = typer.Option(False, "--quiet/--no-quiet"),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose"),
    json_logs: bool = typer.Option(False, "--json-logs/--no-json-logs"),
    extra: Optional[List[str]] = typer.Option(None, "--extra"),
) -> None:
    """Sincroniza origen → destino de forma union dedup-aware (nunca borra)."""
    setup_logging(log_level, quiet=quiet, verbose=verbose, json_logs=json_logs)
    show_progress = not (quiet or json_logs)
    workers = _validate_workers(workers)

    action = action.lower()
    if action not in {"copy", "move"}:
        raise typer.BadParameter("La acción debe ser copy o move.")
    algorithm = algorithm.lower()
    if algorithm not in {"blake2b", "sha256", "md5"}:
        raise typer.BadParameter("Algoritmo no soportado.")

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
        console.print("[yellow]No se encontraron archivos en el origen.[/yellow]")
        raise typer.Exit(code=0)

    dst_files = list(iter_media_files(destination, scan_opts)) if destination.exists() else []

    console.print(
        f"Sincronizando [bold]{len(src_files)}[/bold] archivos desde [cyan]{source}[/cyan] "
        f"hacia [green]{destination}[/green] "
        f"([bold]{len(dst_files)}[/bold] archivos existentes en destino)..."
    )

    src_metadata, src_errors = _collect_metadata(src_files, workers=workers, show_progress=show_progress)
    if src_errors:
        console.print(f"[yellow]Se omitieron {len(src_errors)} archivos del origen por errores de metadatos.[/yellow]")

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
        console.print(f"[green]Plan guardado en {output}[/green]")

    if not sync_plan.additions:
        console.print("[green]Nada que sincronizar.[/green]")
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
        console.print(f"[yellow]Dry-run: {len(sync_plan.additions)} archivo(s) serían copiados/movidos.[/yellow]")
    else:
        console.print(f"[green]{applied} archivo(s) sincronizados correctamente.[/green]")


def _render_sync_plan(plan: SyncPlan, *, dry_run: bool = True) -> None:
    if plan.skipped_identical:
        console.print(f"[green]Idénticos (omitidos):[/green] {len(plan.skipped_identical)}")

    if plan.conflicts:
        console.print(f"[yellow]Conflictos renombrados:[/yellow] {len(plan.conflicts)}")
        t = Table(title="Conflictos de nombre resueltos")
        t.add_column("Origen", style="cyan")
        t.add_column("Nombre original", style="yellow")
        t.add_column("Renombrado a", style="green")
        for c in plan.conflicts:
            t.add_row(str(c.metadata.source_path), c.original_name, str(c.resolved_destination))
        console.print(t)

    if plan.additions:
        label = "A copiar/mover" if dry_run else "Añadidos"
        t = Table(title=f"{label} ({len(plan.additions)})")
        t.add_column("Origen", style="cyan")
        t.add_column("Destino", style="green")
        t.add_column("Renombrado", style="yellow")
        for a in plan.additions[:50]:
            t.add_row(str(a.metadata.source_path), str(a.destination), "sí" if a.renamed else "no")
        if len(plan.additions) > 50:
            console.print(f"[dim]…y {len(plan.additions) - 50} más[/dim]")
        console.print(t)

    if plan.errors:
        console.print(f"[red]Errores durante el análisis:[/red] {len(plan.errors)}")


# ---------------------------------------------------------------------------
# tui command
# ---------------------------------------------------------------------------


@app.command()
def tui() -> None:
    """Lanza el asistente interactivo (TUI) para guiar por los comandos disponibles."""
    run_tui()


