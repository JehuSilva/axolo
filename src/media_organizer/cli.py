"""Command line interface for the media organizer."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import typer
from rich.console import Console
from rich.table import Table

from .clustering import ClusterParameters, ClusterSummary, PhotoClusterer
from .config import BUILTIN_PROFILES, OrganizerConfig, load_run_config
from .media_scanner import ScanOptions, iter_media_files
from .metadata import MediaMetadata, extract_metadata
from .organizer import MediaOrganizer, OrganizeSummary
from .similarity import SimilarityAnalyzer, SimilarityReport
from .templates import DEFAULT_TEMPLATES
from .timeline import TimelineAnalyzer, TimelineReport

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


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )


def _collect_metadata(paths: Iterable[Path]) -> Tuple[List[MediaMetadata], List[str]]:
    metadata_items: list[MediaMetadata] = []
    errors: list[str] = []
    for path in paths:
        try:
            metadata_items.append(extract_metadata(path))
        except Exception as exc:  # pragma: no cover - errores inesperados de extract_metadata
            logger.warning("Error al extraer metadatos de %s: %s", path, exc)
            errors.append(f"{path}: {exc}")
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
    dry_run: Optional[bool] = typer.Option(None, "--dry-run/--no-dry-run", help="Muestra los cambios sin mover archivos."),
    recursive: Optional[bool] = typer.Option(None, "--recursive/--no-recursive", help="Buscar archivos de forma recursiva."),
    follow_symlinks: Optional[bool] = typer.Option(None, "--follow-symlinks/--no-follow-symlinks", help="Seguir enlaces simbólicos."),
    include_ext: Optional[List[str]] = typer.Option(None, "--include-ext", help="Extensiones permitidas (puede repetirse)."),
    exclude_ext: Optional[List[str]] = typer.Option(None, "--exclude-ext", help="Extensiones a excluir (puede repetirse)."),
    extra: Optional[List[str]] = typer.Option(None, "--extra", help="Pares clave=valor para usar en el template."),
    log_level: str = typer.Option("INFO", "--log-level", help="Nivel de logging (DEBUG, INFO, WARNING, ERROR)."),
) -> None:
    """Organiza archivos multimedia según el template configurado."""
    _setup_logging(log_level)

    # Load base config from YAML file, then override with any CLI flags provided.
    file_cfg = load_run_config(config_path) if config_path else {}

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

    organizer = MediaOrganizer(config=config)
    files = list(iter_media_files(config.source, scan_options))

    if not files:
        console.print("[yellow]No se encontraron archivos para procesar.[/yellow]")
        raise typer.Exit(code=0)

    console.print(f"Procesando {len(files)} archivos desde {config.source} hacia {config.destination}...")
    summary = organizer.organize(files)
    _render_summary(summary)


@app.command()
def cluster(
    source: Path = typer.Option(..., "--source", "-s", help="Directorio de origen a analizar."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="Buscar archivos de forma recursiva."),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks", help="Seguir enlaces simbólicos."),
    include_ext: Optional[List[str]] = typer.Option(
        None,
        "--include-ext",
        help="Extensiones permitidas (puede repetirse).",
    ),
    exclude_ext: Optional[List[str]] = typer.Option(
        None,
        "--exclude-ext",
        help="Extensiones a excluir (puede repetirse).",
    ),
    time_window: float = typer.Option(
        90.0,
        "--time-window",
        help="Ventana temporal en minutos para considerar elementos del mismo evento.",
    ),
    min_samples: int = typer.Option(
        3,
        "--min-samples",
        help="Número mínimo de elementos para formar un clúster (DBSCAN).",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Archivo JSON donde guardar los clústeres detectados.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="No escribe archivos; sólo muestra vista previa en consola.",
    ),
    show_noise: bool = typer.Option(
        False,
        "--show-noise",
        help="Muestra los elementos que no pertenecen a ningún clúster.",
    ),
    log_level: str = typer.Option("INFO", "--log-level", help="Nivel de logging (DEBUG, INFO, WARNING, ERROR)."),
) -> None:
    """Agrupa fotografías y videos sin moverlos para sugerir álbumes."""
    _setup_logging(log_level)

    if time_window <= 0:
        raise typer.BadParameter("La ventana temporal debe ser mayor a 0.", param_name="time-window")
    if min_samples < 1:
        raise typer.BadParameter("min-samples debe ser al menos 1.", param_name="min-samples")

    scan_options = ScanOptions(
        recursive=recursive,
        follow_symlinks=follow_symlinks,
        include_extensions={ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in (include_ext or [])},
        exclude_extensions={ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in (exclude_ext or [])},
    )

    files = list(iter_media_files(source, scan_options))

    if not files:
        console.print("[yellow]No se encontraron archivos para analizar.[/yellow]")
        raise typer.Exit(code=0)

    console.print(f"Analizando {len(files)} archivos desde {source}...")

    metadata_items, errors = _collect_metadata(files)

    if errors:
        console.print(f"[yellow]Se omitieron {len(errors)} archivos por errores de metadata.[/yellow]")

    try:
        params = ClusterParameters(time_window_minutes=time_window, min_samples=min_samples)
        clusterer = PhotoClusterer(params=params)
        summary = clusterer.cluster(metadata_items)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    _render_cluster_preview(summary, show_noise=show_noise)

    if output and not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            json.dump(summary.to_dict(), handle, ensure_ascii=False, indent=2)
        console.print(f"[green]Resultados guardados en {output}[/green]")
    elif output and dry_run:
        console.print("[yellow]Modo dry-run: se omitió la escritura del archivo de salida.[/yellow]")


@app.command()
def similars(
    source: Path = typer.Option(..., "--source", "-s", help="Directorio con fotografías a analizar."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="Buscar archivos de forma recursiva."),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks", help="Seguir enlaces simbólicos."),
    include_ext: Optional[List[str]] = typer.Option(None, "--include-ext", help="Extensiones permitidas."),
    exclude_ext: Optional[List[str]] = typer.Option(None, "--exclude-ext", help="Extensiones a excluir."),
    threshold: int = typer.Option(8, "--threshold", "-t", help="Máxima distancia Hamming para considerar similitud."),
    hash_size: int = typer.Option(16, "--hash-size", help="Tamaño del hash perceptual."),
    method: str = typer.Option("phash", "--method", help="Método de hash a usar (phash|ahash|dhash|whash)."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Archivo JSON con el reporte."),
    max_pairs: Optional[int] = typer.Option(None, "--max-pairs", help="Limita la cantidad de pares mostrados en consola."),
    log_level: str = typer.Option("INFO", "--log-level", help="Nivel de logging (DEBUG, INFO, WARNING, ERROR)."),
) -> None:
    """Detecta fotografías similares mediante hashing perceptual."""
    _setup_logging(log_level)

    if threshold < 0:
        raise typer.BadParameter("El umbral debe ser mayor o igual a 0.", param_name="threshold")
    if hash_size <= 0:
        raise typer.BadParameter("hash-size debe ser mayor a 0.", param_name="hash-size")
    if max_pairs is not None and max_pairs <= 0:
        raise typer.BadParameter("max-pairs debe ser mayor que 0.", param_name="max-pairs")

    method = method.lower()
    if method not in {"phash", "ahash", "dhash", "whash"}:
        raise typer.BadParameter("Método no soportado. Usa phash, ahash, dhash o whash.", param_name="method")

    scan_options = ScanOptions(
        recursive=recursive,
        follow_symlinks=follow_symlinks,
        include_extensions={ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in (include_ext or [])},
        exclude_extensions={ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in (exclude_ext or [])},
    )

    files = list(iter_media_files(source, scan_options))
    if not files:
        console.print("[yellow]No se encontraron archivos para analizar.[/yellow]")
        raise typer.Exit(code=0)

    console.print(f"Analizando {len(files)} archivos en busca de similitudes...")
    metadata_items, errors = _collect_metadata(files)
    if errors:
        console.print(f"[yellow]Se omitieron {len(errors)} archivos por errores de metadata.[/yellow]")

    analyzer = SimilarityAnalyzer(threshold=threshold, hash_size=hash_size, method=method)
    report = analyzer.analyze(metadata_items)
    _render_similarity_report(report, max_pairs=max_pairs)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, ensure_ascii=False, indent=2)
        console.print(f"[green]Reporte guardado en {output}[/green]")


@app.command()
def timeline(
    source: Path = typer.Option(..., "--source", "-s", help="Directorio con fotografías a analizar."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="Buscar archivos de forma recursiva."),
    follow_symlinks: bool = typer.Option(False, "--follow-symlinks", help="Seguir enlaces simbólicos."),
    include_ext: Optional[List[str]] = typer.Option(None, "--include-ext", help="Extensiones permitidas."),
    exclude_ext: Optional[List[str]] = typer.Option(None, "--exclude-ext", help="Extensiones a excluir."),
    granularity: str = typer.Option("month", "--granularity", "-g", help="hour|day|week|month|year"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Ruta para exportar datos (.json, .csv o .tsv)."),
    chart: Optional[Path] = typer.Option(None, "--chart", help="Archivo HTML para visualizar la distribución."),
    limit: int = typer.Option(50, "--limit", help="Cantidad máxima de filas a mostrar en consola."),
    log_level: str = typer.Option("INFO", "--log-level", help="Nivel de logging (DEBUG, INFO, WARNING, ERROR)."),
) -> None:
    """Resume cuántas fotos se tomaron en el tiempo."""
    _setup_logging(log_level)

    if limit <= 0:
        raise typer.BadParameter("limit debe ser mayor a 0.", param_name="limit")

    granularity = granularity.lower()
    try:
        analyzer = TimelineAnalyzer(granularity=granularity)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_name="granularity") from exc

    scan_options = ScanOptions(
        recursive=recursive,
        follow_symlinks=follow_symlinks,
        include_extensions={ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in (include_ext or [])},
        exclude_extensions={ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in (exclude_ext or [])},
    )

    files = list(iter_media_files(source, scan_options))
    if not files:
        console.print("[yellow]No se encontraron archivos para analizar.[/yellow]")
        raise typer.Exit(code=0)

    console.print(f"Generando resumen temporal para {len(files)} archivos...")
    metadata_items, errors = _collect_metadata(files)
    if errors:
        console.print(f"[yellow]Se omitieron {len(errors)} archivos por errores de metadata.[/yellow]")

    report = analyzer.summarize(metadata_items)
    _render_timeline_report(report, limit=limit)

    if output:
        if output.suffix.lower() not in {".json", ".csv", ".tsv"}:
            raise typer.BadParameter("El archivo de salida debe terminar en .json, .csv o .tsv.", param_name="output")
        _export_timeline_report(report, output)
        console.print(f"[green]Datos exportados a {output}[/green]")

    if chart:
        if not report.points:
            console.print("[yellow]No se genero grafico porque no hay datos disponibles.[/yellow]")
        else:
            _export_timeline_chart(report, chart)
            console.print(f"[green]Grafico generado en {chart}[/green]")

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


def _render_cluster_preview(summary: ClusterSummary, show_noise: bool = False, sample_size: int = 4) -> None:
    if summary.considered_items == 0:
        console.print("[yellow]No se encontraron fotos ni videos para agrupar con los filtros proporcionados.[/yellow]")
        return

    if summary.clusters:
        table = Table(title="Clusters detectados")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Rango temporal", style="green")
        table.add_column("Elementos", style="magenta", justify="right")
        table.add_column("Etiquetas sugeridas", style="yellow")
        table.add_column("Ejemplos", style="white")

        for cluster in summary.clusters:
            start = cluster.start.strftime("%Y-%m-%d %H:%M")
            end = cluster.end.strftime("%Y-%m-%d %H:%M")
            range_label = start if start == end else f"{start} → {end}"
            tags = ", ".join(cluster.suggested_tags) if cluster.suggested_tags else "-"
            examples = ", ".join(item.source_path.name for item in cluster.members[:sample_size])
            table.add_row(cluster.label, range_label, str(cluster.size), tags, examples)

        console.print(table)
    else:
        console.print("[yellow]No se detectaron clústeres con los parámetros especificados.[/yellow]")

    console.print(
        f"[blue]Total analizados:[/blue] {summary.total_items}  "
        f"[blue]Fotos/Videos considerados:[/blue] {summary.considered_items}  "
        f"[blue]Clústeres:[/blue] {len(summary.clusters)}  "
        f"[blue]Elementos sin clúster:[/blue] {len(summary.noise)}"
    )

    if show_noise and summary.noise:
        noise_table = Table(title="Elementos sin clúster")
        noise_table.add_column("Archivo", style="cyan")
        noise_table.add_column("Capturado", style="green")
        for item in summary.noise:
            noise_table.add_row(str(item.source_path), item.captured_at.isoformat())
        console.print(noise_table)


def _render_similarity_report(report: SimilarityReport, max_pairs: Optional[int] = None) -> None:
    if report.processed == 0:
        console.print("[yellow]No se procesaron imágenes para comparar.[/yellow]")
        return

    total_pairs = len(report.pairs)
    display_pairs = report.pairs
    truncated = False
    if max_pairs is not None and total_pairs > max_pairs:
        display_pairs = report.pairs[:max_pairs]
        truncated = True

    if display_pairs:
        table = Table(title="Fotografías similares detectadas")
        table.add_column("Dist.", style="magenta", justify="right")
        table.add_column("Primera foto", style="cyan")
        table.add_column("Capturada", style="green")
        table.add_column("Segunda foto", style="cyan")
        table.add_column("Capturada", style="green")
        for pair in display_pairs:
            table.add_row(
                str(pair.distance),
                str(pair.first.source_path),
                pair.first.captured_at.isoformat(),
                str(pair.second.source_path),
                pair.second.captured_at.isoformat(),
            )
        console.print(table)
    else:
        console.print("[green]No se encontraron fotografías similares con el umbral especificado.[/green]")

    console.print(
        f"[blue]Elementos escaneados:[/blue] {report.scanned}  "
        f"[blue]Procesados:[/blue] {report.processed}  "
        f"[blue]Pares detectados:[/blue] {total_pairs}  "
        f"[blue]Umbral:[/blue] {report.threshold}"
    )

    if truncated:
        console.print(
            f"[yellow]Se muestran únicamente los primeros {max_pairs} pares. Usa --max-pairs para ajustar el límite.[/yellow]"
        )


def _render_timeline_report(report: TimelineReport, *, limit: int) -> None:
    if not report.points:
        console.print("[yellow]No hay datos para generar el resumen temporal.[/yellow]")
        return

    total_points = len(report.points)
    if total_points > limit:
        display_points = report.points[-limit:]
        truncated = True
    else:
        display_points = report.points
        truncated = False

    max_count = max(point.count for point in display_points) or 1
    table = Table(title=f"Capturas por periodo ({report.granularity})")
    table.add_column("Periodo", style="cyan")
    table.add_column("Cantidad", style="magenta", justify="right")
    table.add_column("Grafica", style="green")

    for point in display_points:
        bar_units = max(1, int((point.count / max_count) * 30)) if point.count else 0
        bar = "#" * bar_units
        table.add_row(point.label, str(point.count), bar)

    console.print(table)
    console.print(
        f"[blue]Total de elementos:[/blue] {report.total_items}  "
        f"[blue]Periodos generados:[/blue] {total_points}"
    )
    if truncated:
        console.print(
            f"[yellow]Mostrando los últimos {limit} periodos. Ajusta --limit para ver más o menos filas.[/yellow]"
        )


def _export_timeline_report(report: TimelineReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    suffix = output.suffix.lower()
    if suffix == ".json":
        with output.open("w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, ensure_ascii=False, indent=2)
        return
    delimiter = "," if suffix == ".csv" else "\t"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(["label", "start", "end", "count"])
        for point in report.points:
            writer.writerow([point.label, point.start.isoformat(), point.end.isoformat(), point.count])


def _export_timeline_chart(report: TimelineReport, chart_path: Path) -> None:
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    labels = [point.label for point in report.points]
    counts = [point.count for point in report.points]
    payload = {
        "labels": labels,
        "counts": counts,
    }
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <title>Media Organizer - Timeline</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body {{ font-family: sans-serif; margin: 2rem; }}
    canvas {{ max-width: 960px; }}
  </style>
</head>
<body>
  <h1>Capturas por periodo ({report.granularity})</h1>
  <canvas id="timelineChart" width="960" height="480"></canvas>
  <script>
    const payload = {json.dumps(payload, ensure_ascii=False)};
    const ctx = document.getElementById('timelineChart').getContext('2d');
    new Chart(ctx, {{
      type: 'line',
      data: {{
        labels: payload.labels,
        datasets: [{{
          label: 'Número de capturas',
          data: payload.counts,
          borderColor: '#2563eb',
          backgroundColor: 'rgba(37, 99, 235, 0.2)',
          tension: 0.25,
          fill: true,
        }}],
      }},
      options: {{
        responsive: true,
        plugins: {{
          legend: {{ display: true }},
        }},
        scales: {{
          y: {{
            beginAtZero: true,
            ticks: {{ precision: 0 }},
            title: {{ display: true, text: 'Número de capturas' }},
          }},
          x: {{
            title: {{ display: true, text: 'Periodo' }},
          }},
        }},
      }},
    }});
  </script>
</body>
</html>
"""
    with chart_path.open("w", encoding="utf-8") as handle:
        handle.write(html)
