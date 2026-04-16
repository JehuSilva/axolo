"""Command line interface for the media organizer."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TimeElapsedColumn
from rich.table import Table

from .config import BUILTIN_PROFILES, OrganizerConfig, load_run_config
from .duplicates import (
    ActionOutcome,
    DuplicateActionError,
    DuplicateAnalyzer,
    DuplicatesReport,
    apply_duplicate_actions,
)
from .logging_setup import setup_logging
from .media_scanner import ScanOptions, iter_media_files
from .metadata import MediaMetadata, extract_metadata
from .organizer import MediaOrganizer, OrganizeSummary
from .templates import DEFAULT_TEMPLATES

logger = logging.getLogger(__name__)
console = Console()
app = typer.Typer(add_completion=False, help="Organiza fotos y videos en carpetas.")


def _parse_extra(extra: Optional[List[str]]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not extra:
        return result
    for item in extra:
        if "=" not in item:
            raise typer.BadParameter(f"El argumento extra '{item}' debe tener el formato clave=valor")
        key, value = item.split("=", 1)
        result[key] = value
    return result


def _collect_metadata(
    paths: Iterable[Path], *, show_progress: bool = True
) -> Tuple[List[MediaMetadata], List[str]]:
    paths_list = list(paths)
    metadata_items: list[MediaMetadata] = []
    errors: list[str] = []
    with Progress(
        SpinnerColumn(),
        "[progress.description]{task.description}",
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        disable=not show_progress,
    ) as prog:
        task = prog.add_task("Extrayendo metadatos...", total=len(paths_list))
        for path in paths_list:
            try:
                metadata_items.append(extract_metadata(path))
            except Exception as exc:
                logger.warning("Error al extraer metadatos de %s: %s", path, exc)
                errors.append(f"{path}: {exc}")
            prog.advance(task)
    return metadata_items, errors


@app.command()
def run(
    source: Optional[Path] = typer.Option(None, "--source", "-s", help="Directorio de origen a analizar."),
    destination: Optional[Path] = typer.Option(None, "--destination", "-d", help="Directorio de destino."),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Archivo YAML de configuración de ejecución.",
    ),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Nombre del perfil a usar."),
    template: Optional[str] = typer.Option(None, "--template", help="Template personalizado (ignora --profile)."),
    action: Optional[str] = typer.Option(None, "--action", "-a", help="Acción sobre los archivos (move|copy|link)."),
    link_kind: str = typer.Option("symbolic", "--link-kind", help="Tipo de enlace para --action link: hard|symbolic."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run/--no-dry-run", help="Muestra los cambios sin mover archivos."),
    recursive: Optional[bool] = typer.Option(None, "--recursive/--no-recursive", help="Buscar archivos de forma recursiva."),
    follow_symlinks: Optional[bool] = typer.Option(None, "--follow-symlinks/--no-follow-symlinks", help="Seguir enlaces simbólicos."),
    include_ext: Optional[List[str]] = typer.Option(None, "--include-ext", help="Extensiones permitidas (puede repetirse)."),
    exclude_ext: Optional[List[str]] = typer.Option(None, "--exclude-ext", help="Extensiones a excluir (puede repetirse)."),
    extra: Optional[List[str]] = typer.Option(None, "--extra", help="Pares clave=valor para usar en el template."),
    log_level: str = typer.Option("INFO", "--log-level", help="Nivel de logging (DEBUG, INFO, WARNING, ERROR)."),
    quiet: bool = typer.Option(False, "--quiet/--no-quiet", help="Suprime mensajes de consola; solo errores críticos."),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose", help="Activa logging DEBUG."),
    json_logs: bool = typer.Option(False, "--json-logs/--no-json-logs", help="Emite logs como JSON Lines (desactiva Rich)."),
) -> None:
    """Organiza archivos multimedia según el template configurado."""
    setup_logging(log_level, quiet=quiet, verbose=verbose, json_logs=json_logs)
    show_progress = not (quiet or json_logs)

    # Load base config from YAML file, then override with any CLI flags provided.
    file_cfg: dict = {}
    if config_path:
        try:
            file_cfg = load_run_config(config_path)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_name="config") from exc

    effective_source = source or (Path(file_cfg["source"]).expanduser() if "source" in file_cfg else None)
    effective_dest = destination or (Path(file_cfg["destination"]).expanduser() if "destination" in file_cfg else None)

    if effective_source is None:
        effective_source = Path(
            typer.prompt("Directorio de origen (source)")
        ).expanduser()
    if effective_dest is None:
        effective_dest = Path(
            typer.prompt("Directorio de destino (destination)")
        ).expanduser()

    raw_action = action or file_cfg.get("action") or typer.prompt(
        "Acción a aplicar (move / copy / link)",
        default="move",
    )
    effective_action = raw_action.lower()
    if effective_action not in {"move", "copy", "link"}:
        raise typer.BadParameter("La acción debe ser move, copy o link.", param_name="action")

    link_kind = link_kind.lower()
    if link_kind not in {"hard", "symbolic"}:
        raise typer.BadParameter("link-kind debe ser 'hard' o 'symbolic'.", param_name="link-kind")

    # Resolve template: CLI --template > CLI --profile > config file > prompt
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

    effective_include = include_ext or file_cfg.get("include_extensions", [])
    effective_exclude = exclude_ext or file_cfg.get("exclude_extensions", [])

    config = OrganizerConfig(
        source=effective_source,
        destination=effective_dest,
        action=effective_action,
        link_kind=link_kind,
        template=effective_template,
        dry_run=effective_dry_run,
        recursive=effective_recursive,
        follow_symlinks=effective_symlinks,
        include_extensions=effective_include,
        exclude_extensions=effective_exclude,
        extra=extra_values,
    )

    scan_options = ScanOptions(
        recursive=config.recursive,
        follow_symlinks=config.follow_symlinks,
        include_extensions=config.normalized_include_extensions(),
        exclude_extensions=config.normalized_exclude_extensions(),
    )

    organizer = MediaOrganizer(config=config, show_progress=show_progress)
    files = list(iter_media_files(config.source, scan_options))

    if not files:
        console.print("[yellow]No se encontraron archivos para procesar.[/yellow]")
        raise typer.Exit(code=0)

    console.print(f"Procesando {len(files)} archivos desde {config.source} hacia {config.destination}...")
    summary = organizer.organize(files)
    _render_summary(summary)


def _render_summary(summary: OrganizeSummary) -> None:
    table = Table(title="Resumen de organización")
    table.add_column("Archivo origen", style="cyan", no_wrap=True)
    table.add_column("Destino", style="green")
    table.add_column("Estado", style="magenta")
    table.add_column("Categoría", style="yellow")
    table.add_column("Mensaje", style="white")

    for result in summary.results:
        category_label = "-"
        if result.category is not None:
            try:
                category_label = result.category.label()
            except AttributeError:
                category_label = str(result.category)
        table.add_row(
            str(result.source),
            str(result.destination),
            result.status,
            category_label,
            result.message or "",
        )

    console.print(table)
    summary_table = Table(title="Resumen por estado")
    summary_table.add_column("Estado", style="magenta")
    summary_table.add_column("Cantidad", style="cyan", justify="right")
    summary_table.add_column("Porcentaje", style="white", justify="right")

    counts = summary.status_counts()
    ordered_statuses = ["moved", "copied", "linked", "dry-run", "skipped", "failed"]
    total = summary.total

    for status in ordered_statuses:
        value = counts.get(status, 0)
        percentage = f"{(value / total * 100):.1f}%" if total else "0.0%"
        summary_table.add_row(status, str(value), percentage)

    remaining_statuses = sorted(set(counts.keys()) - set(ordered_statuses))
    for status in remaining_statuses:
        value = counts[status]
        percentage = f"{(value / total * 100):.1f}%" if total else "0.0%"
        summary_table.add_row(status, str(value), percentage)

    summary_table.add_row("total", str(total), "100.0%" if total else "0.0%")
    console.print(summary_table)

    category_counts = summary.category_counts()
    if category_counts:
        category_table = Table(title="Resumen por categoría")
        category_table.add_column("Categoría", style="yellow")
        category_table.add_column("Cantidad", style="cyan", justify="right")
        category_table.add_column("Porcentaje", style="white", justify="right")

        for label, value in category_counts.items():
            percentage = f"{(value / total * 100):.1f}%" if total else "0.0%"
            category_table.add_row(label, str(value), percentage)
        category_table.add_row("total", str(total), "100.0%" if total else "0.0%")
        console.print(category_table)


# ---------------------------------------------------------------------------
# duplicates command
# ---------------------------------------------------------------------------


def _humanize_bytes(n: int) -> str:
    """Return a human-readable representation of *n* bytes."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} PB"


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
        action_label = outcome.action.upper()
        dest_str = str(outcome.destination) if outcome.destination else "—"
        if outcome.error:
            status = f"[red]ERROR: {outcome.error}[/red]"
        elif outcome.dry_run:
            status = "[yellow]dry-run[/yellow]"
        else:
            status = "[green]OK[/green]"
        table.add_row(action_label, str(outcome.source), dest_str, status)

    console.print(table)

    errors = [o for o in outcomes if o.error]
    if errors:
        console.print(f"[red]{len(errors)} error(es) durante la ejecución de acciones.[/red]")


@app.command()
def duplicates(
    source: Path = typer.Option(..., "--source", "-s", help="Directorio con archivos multimedia a analizar."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="Buscar archivos de forma recursiva."),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks", help="Seguir enlaces simbólicos."),
    include_ext: Optional[List[str]] = typer.Option(None, "--include-ext", help="Extensiones permitidas."),
    exclude_ext: Optional[List[str]] = typer.Option(None, "--exclude-ext", help="Extensiones a excluir."),
    algorithm: str = typer.Option("blake2b", "--algorithm", help="Algoritmo de hash: blake2b|sha256|md5."),
    min_size: int = typer.Option(1, "--min-size", help="Ignora archivos más pequeños que este valor (bytes)."),
    prefer_under: Optional[Path] = typer.Option(None, "--prefer-under", help="Directorio preferido al elegir el canónico."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Archivo JSON con el reporte de duplicados."),
    action: Optional[str] = typer.Option(None, "--action", help="Acción sobre duplicados: move|link|delete."),
    quarantine: Optional[Path] = typer.Option(None, "--quarantine", help="Directorio destino para --action move."),
    link_kind: str = typer.Option("hard", "--link-kind", help="Tipo de enlace para --action link: hard|symbolic."),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Simula acciones sin modificar archivos."),
    max_groups: Optional[int] = typer.Option(None, "--max-groups", help="Limita la cantidad de grupos mostrados en consola."),
    log_level: str = typer.Option("INFO", "--log-level", help="Nivel de logging (DEBUG, INFO, WARNING, ERROR)."),
    quiet: bool = typer.Option(False, "--quiet/--no-quiet", help="Suprime mensajes de consola; solo errores críticos."),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose", help="Activa logging DEBUG."),
    json_logs: bool = typer.Option(False, "--json-logs/--no-json-logs", help="Emite logs como JSON Lines (desactiva Rich)."),
) -> None:
    """Detecta archivos duplicados exactos (byte-a-byte) en la fuente indicada."""
    setup_logging(log_level, quiet=quiet, verbose=verbose, json_logs=json_logs)
    show_progress = not (quiet or json_logs)

    # Validate options early.
    algorithm = algorithm.lower()
    if algorithm not in {"blake2b", "sha256", "md5"}:
        raise typer.BadParameter(
            "Algoritmo no soportado. Usa blake2b, sha256 o md5.", param_name="algorithm"
        )
    if min_size < 0:
        raise typer.BadParameter("min-size no puede ser negativo.", param_name="min-size")
    if max_groups is not None and max_groups <= 0:
        raise typer.BadParameter("max-groups debe ser mayor que 0.", param_name="max-groups")

    valid_actions = {"move", "link", "delete"}
    if action is not None:
        action = action.lower()
        if action not in valid_actions:
            raise typer.BadParameter(
                "Acción no soportada. Usa move, link o delete.", param_name="action"
            )
        if action == "move" and quarantine is None:
            raise typer.BadParameter(
                "--quarantine es obligatorio cuando se usa --action move.", param_name="quarantine"
            )

    link_kind = link_kind.lower()
    if link_kind not in {"hard", "symbolic"}:
        raise typer.BadParameter(
            "link-kind no válido. Usa 'hard' o 'symbolic'.", param_name="link-kind"
        )

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

    console.print(f"Analizando {len(files)} archivos en busca de duplicados...")
    metadata_items, errors = _collect_metadata(files, show_progress=show_progress)
    if errors:
        console.print(
            f"[yellow]Se omitieron {len(errors)} archivos por errores de metadatos.[/yellow]"
        )

    try:
        analyzer = DuplicateAnalyzer(
            algorithm=algorithm,
            min_size=min_size,
            prefer_under=prefer_under,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_name="algorithm") from exc

    report = analyzer.analyze(metadata_items, show_progress=show_progress)
    _render_duplicates_report(report, max_groups=max_groups)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, ensure_ascii=False, indent=2)
        console.print(f"[green]Reporte guardado en {output}[/green]")

    if action:
        try:
            outcomes = apply_duplicate_actions(
                report,
                action,  # type: ignore[arg-type]
                quarantine=quarantine,
                relative_to=source,
                link_kind=link_kind,  # type: ignore[arg-type]
                dry_run=dry_run,
                show_progress=show_progress,
            )
        except DuplicateActionError as exc:
            console.print(f"[red]Error de configuración: {exc}[/red]")
            raise typer.Exit(code=1) from exc

        _render_action_outcomes(outcomes, dry_run=dry_run)
