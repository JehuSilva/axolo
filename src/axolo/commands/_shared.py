"""Shared helpers used by multiple CLI command modules."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import typer
from rich.console import Console
from rich.table import Table

from ..metadata import MediaMetadata, extract_metadata
from ..organizer import OrganizeSummary
from ..parallel import parallel_map

logger = logging.getLogger(__name__)
console = Console()

DEFAULT_WORKERS: int = min(os.cpu_count() or 4, 8)


def parse_extra(extra: Optional[List[str]]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not extra:
        return result
    for item in extra:
        if "=" not in item:
            raise typer.BadParameter(
                f"El argumento extra '{item}' debe tener el formato clave=valor"
            )
        key, value = item.split("=", 1)
        result[key] = value
    return result


def validate_workers(workers: int) -> int:
    if workers < 1:
        raise typer.BadParameter("--workers debe ser al menos 1.")
    if workers > 32:
        raise typer.BadParameter("--workers no puede superar 32.")
    return workers


def collect_metadata(
    paths: Iterable[Path], *, workers: int = 1, show_progress: bool = True
) -> Tuple[List[MediaMetadata], List[str]]:
    paths_list = list(paths)
    results = parallel_map(
        extract_metadata,
        paths_list,
        workers=workers,
        show_progress=show_progress,
        description="Extrayendo metadatos...",
    )
    metadata_items: list[MediaMetadata] = []
    errors: list[str] = []
    for path, result in zip(paths_list, results):
        if isinstance(result, BaseException):
            logger.warning("Error al extraer metadatos de %s: %s", path, result)
            errors.append(f"{path}: {result}")
        else:
            metadata_items.append(result)
    return metadata_items, errors


def humanize_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n:.1f} PB"


def render_summary(summary: OrganizeSummary) -> None:
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
            str(result.source), str(result.destination),
            result.status, category_label, result.message or "",
        )
    console.print(table)

    summary_table = Table(title="Resumen por estado")
    summary_table.add_column("Estado", style="magenta")
    summary_table.add_column("Cantidad", style="cyan", justify="right")
    summary_table.add_column("Porcentaje", style="white", justify="right")

    counts = summary.status_counts()
    ordered = ["moved", "copied", "linked", "dry-run", "skipped", "failed"]
    total = summary.total
    for status in ordered:
        value = counts.get(status, 0)
        pct = f"{(value / total * 100):.1f}%" if total else "0.0%"
        summary_table.add_row(status, str(value), pct)
    for status in sorted(set(counts.keys()) - set(ordered)):
        value = counts[status]
        pct = f"{(value / total * 100):.1f}%" if total else "0.0%"
        summary_table.add_row(status, str(value), pct)
    summary_table.add_row("total", str(total), "100.0%" if total else "0.0%")
    console.print(summary_table)

    category_counts = summary.category_counts()
    if category_counts:
        cat_table = Table(title="Resumen por categoría")
        cat_table.add_column("Categoría", style="yellow")
        cat_table.add_column("Cantidad", style="cyan", justify="right")
        cat_table.add_column("Porcentaje", style="white", justify="right")
        for label, value in category_counts.items():
            pct = f"{(value / total * 100):.1f}%" if total else "0.0%"
            cat_table.add_row(label, str(value), pct)
        cat_table.add_row("total", str(total), "100.0%" if total else "0.0%")
        console.print(cat_table)


def render_runs_table(runs: list[dict]) -> None:
    if not runs:
        console.print("[yellow]No hay runs registrados en el journal.[/yellow]")
        return
    table = Table(title="Runs registrados en el journal")
    table.add_column("Run ID", style="cyan")
    table.add_column("Comando", style="magenta")
    table.add_column("Inicio", style="white")
    table.add_column("Estado", style="green")
    table.add_column("Dry-run", style="yellow")
    table.add_column("Fuente", style="blue")
    for r in runs:
        table.add_row(
            r["run_id"][:8] + "…",
            r["command"],
            r["started_at"][:19],
            r["status"] or "—",
            "sí" if r["dry_run"] else "no",
            r["source"] or "—",
        )
    console.print(table)
