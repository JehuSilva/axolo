"""Interactive terminal wizard using questionary + Rich.

Entry point: ``axolo tui`` (no arguments).

Main menu offers:
  1. Organizar     — guides the user through the ``run`` command
  2. Duplicados    — guides through the ``duplicates`` command
  3. Sincronizar   — guides through the ``sync`` command
  4. Historial     — shows journal runs; optionally triggers undo
  5. Salir

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
            "[red]El TUI requiere 'questionary'. "
            "Instálalo con: pip install questionary[/red]"
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
            console.print(f"[red]La ruta no existe: {path}[/red]")
            continue
        return path


def _ask_action(choices=("move", "copy", "link"), default="move") -> str:
    result = questionary.select(
        "Acción a aplicar:",
        choices=list(choices),
        default=default,
    ).ask()
    if result is None:
        raise KeyboardInterrupt
    return result


def _ask_workers() -> int:
    default = str(min(os.cpu_count() or 4, 8))
    raw = questionary.text(f"Número de hilos (workers) [{default}]:").ask()
    if raw is None:
        raise KeyboardInterrupt
    raw = raw.strip() or default
    try:
        w = int(raw)
        return max(1, min(32, w))
    except ValueError:
        return int(default)


_TEMPLATE_DESCRIPTIONS: dict[str, str] = {
    "default":                    "Año / Mes en español  →  2024/Abril/foto.jpg",
    "year_month":                 "Numérico  →  2024/04",
    "year_month_cap":             "Mes capitalizado  →  2024/Abril",
    "year_month_day":             "Con día  →  2024/04/15",
    "year_month_name":            "Mes minúsculas  →  2024/abril",
    "year_month_name_short":      "Mes abreviado  →  2024/abr",
    "year_month_name_day":        "Con día en español  →  2024/Abril/Abril 15",
    "camera":                     "Por cámara  →  canon/eos-r5/2024/04",
    "music_genre_artist":         "Género y artista  →  rock/the-beatles",
    "music_genre":                "Solo género  →  rock",
    "documents_year_month":       "Documentos numérico  →  2024/04",
    "documents_year_month_cap":   "Documentos capitalizado  →  2024/Abril",
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

    console.rule("[bold cyan]Organizar archivos multimedia")

    source = _ask_path("Directorio de origen:")
    destination = _ask_path("Directorio de destino:", must_exist=False)
    action = _ask_action(choices=("move", "copy", "link"))
    workers = _ask_workers()
    dry_run = questionary.confirm("¿Modo dry-run (sin mover archivos reales)?", default=True).ask()
    if dry_run is None:
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
                console.print("[dim]Configuración cargada de config.yaml[/dim]")
            if _file_cfg.get("routing_filename_templates"):
                routing_filename_templates.update(_file_cfg["routing_filename_templates"])
        except Exception:
            pass

    _TYPE_LABELS = {
        "fotos": "Fotos",
        "videos": "Videos",
        "360-fotos": "Fotos 360°",
        "360-videos": "Videos 360°",
        "musica": "Música",
        "documentos": "Documentos",
        "otros": "Otros",
    }

    t = Table(title="Configuración por tipo de archivo")
    t.add_column("Tipo", style="cyan")
    t.add_column("Carpeta destino", style="green")
    t.add_column("Perfil de organización", style="magenta")
    for key, label in _TYPE_LABELS.items():
        parts = ROUTING_SUBFOLDERS.get(key, (key,))
        subfolder = "/".join(parts) if parts else "(raíz categoría)"
        profile_name = routing.get(key, "default")
        t.add_row(label, subfolder, profile_name)
    console.print(t)

    customize = questionary.confirm(
        "¿Quieres personalizar el perfil de algún tipo de archivo?", default=False
    ).ask()
    if customize is None:
        raise KeyboardInterrupt

    if customize:
        profile_choices = _build_profile_choices()
        for key, label in _TYPE_LABELS.items():
            change = questionary.confirm(
                f"  ¿Cambiar perfil para {label}? (actual: {routing.get(key, 'default')})",
                default=False,
            ).ask()
            if change:
                new_profile = questionary.select(
                    f"  Perfil para {label}:",
                    choices=profile_choices,
                    default=routing.get(key, "default"),
                ).ask()
                if new_profile is None:
                    raise KeyboardInterrupt
                routing[key] = new_profile

    setup_logging("INFO")
    files = list(iter_media_files(source, ScanOptions()))

    if not files:
        console.print("[yellow]No se encontraron archivos en el origen.[/yellow]")
        return

    console.print(f"[green]Encontrados {len(files)} archivos.[/green]")

    preview_table = Table(title="Vista previa (primeros 10 archivos)")
    preview_table.add_column("Archivo", style="cyan")
    for f in files[:10]:
        preview_table.add_row(str(f))
    if len(files) > 10:
        preview_table.add_row(f"… y {len(files) - 10} más")
    console.print(preview_table)

    confirmed = questionary.confirm("¿Continuar?", default=dry_run).ask()
    if not confirmed:
        console.print("[yellow]Cancelado.[/yellow]")
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
    t = Table(title="Resultado")
    t.add_column("Estado", style="magenta")
    t.add_column("Cantidad", style="cyan", justify="right")
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

    console.rule("[bold cyan]Buscar archivos duplicados")

    source = _ask_path("Directorio a analizar:")
    algorithm = questionary.select(
        "Algoritmo de hash:",
        choices=["blake2b", "sha256", "md5"],
        default="blake2b",
    ).ask()
    if algorithm is None:
        raise KeyboardInterrupt

    workers = _ask_workers()
    setup_logging("INFO")

    files = list(iter_media_files(source, ScanOptions()))
    if not files:
        console.print("[yellow]No se encontraron archivos.[/yellow]")
        return

    meta_results = parallel_map(extract_metadata, files, workers=workers, show_progress=True,
                                description="Extrayendo metadatos...")
    metadata_items = [r for r in meta_results if not isinstance(r, BaseException)]

    analyzer = DuplicateAnalyzer(algorithm=algorithm, workers=workers)
    report = analyzer.analyze(metadata_items, show_progress=True)

    if not report.groups:
        console.print("[green]No se encontraron duplicados.[/green]")
        return

    console.print(f"[yellow]Se encontraron {len(report.groups)} grupos de duplicados.[/yellow]")

    t = Table(title="Grupos de duplicados")
    t.add_column("#", style="magenta", justify="right")
    t.add_column("Canónico", style="cyan")
    t.add_column("Duplicado", style="yellow")
    for i, group in enumerate(report.groups[:20], 1):
        for j, dup in enumerate(group.duplicates):
            t.add_row(
                str(i) if j == 0 else "",
                str(group.canonical.metadata.source_path) if j == 0 else "",
                str(dup.metadata.source_path),
            )
    console.print(t)

    apply_action = questionary.confirm("¿Aplicar una acción sobre los duplicados?", default=False).ask()
    if not apply_action:
        return

    action = questionary.select("Acción:", choices=["move", "link", "delete"]).ask()
    if action is None:
        raise KeyboardInterrupt

    quarantine = None
    if action == "move":
        quarantine = _ask_path("Directorio de cuarentena:", must_exist=False)

    dry_run = questionary.confirm("¿Dry-run?", default=True).ask()
    if dry_run is None:
        raise KeyboardInterrupt

    outcomes = apply_duplicate_actions(
        report, action,  # type: ignore[arg-type]
        quarantine=quarantine,
        dry_run=dry_run,
        show_progress=True,
    )
    errors = sum(1 for o in outcomes if o.error)
    console.print(f"[green]{len(outcomes)} acciones aplicadas. {errors} error(es).[/green]")


def _wizard_sync() -> None:
    """Wizard for the ``sync`` command."""
    from .logging_setup import setup_logging
    from .media_scanner import ScanOptions, iter_media_files
    from .metadata import extract_metadata
    from .parallel import parallel_map
    from .sync import apply_sync, plan_sync

    console.rule("[bold cyan]Sincronizar directorios")

    source = _ask_path("Directorio de origen:")
    destination = _ask_path("Directorio de destino:", must_exist=False)
    action = _ask_action(choices=("copy", "move"), default="copy")
    workers = _ask_workers()
    dry_run = questionary.confirm("¿Dry-run?", default=True).ask()
    if dry_run is None:
        raise KeyboardInterrupt

    setup_logging("INFO")

    src_files = list(iter_media_files(source, ScanOptions()))
    dst_files = list(iter_media_files(destination, ScanOptions())) if destination.exists() else []

    if not src_files:
        console.print("[yellow]No se encontraron archivos en el origen.[/yellow]")
        return

    meta_results = parallel_map(extract_metadata, src_files, workers=workers, show_progress=True,
                                description="Extrayendo metadatos...")
    metadata_items = [r for r in meta_results if not isinstance(r, BaseException)]

    sync_plan = plan_sync(
        metadata_items, destination,
        destination_existing_files=dst_files,
        workers=workers, show_progress=True, dry_run=dry_run,
    )

    console.print(
        f"[green]Nuevos:[/green] {len(sync_plan.additions)}  "
        f"[yellow]Idénticos (omitidos):[/yellow] {len(sync_plan.skipped_identical)}  "
        f"[red]Conflictos:[/red] {len(sync_plan.conflicts)}"
    )

    if not sync_plan.additions:
        console.print("[green]Nada que sincronizar.[/green]")
        return

    confirmed = questionary.confirm("¿Continuar?", default=dry_run).ask()
    if not confirmed:
        console.print("[yellow]Cancelado.[/yellow]")
        return

    applied = apply_sync(sync_plan, action=action, dry_run=dry_run, show_progress=True)
    if dry_run:
        console.print(f"[yellow]Dry-run: {applied} archivo(s) serían sincronizados.[/yellow]")
    else:
        console.print(f"[green]{applied} archivo(s) sincronizados.[/green]")


def _wizard_history() -> None:
    """Show journal history and optionally trigger undo."""
    from .journal import Journal

    console.rule("[bold cyan]Historial de operaciones")

    try:
        journal = Journal()
    except Exception as exc:
        console.print(f"[red]No se pudo abrir el journal: {exc}[/red]")
        return

    with journal:
        runs = journal.list_runs(limit=20)
        if not runs:
            console.print("[yellow]No hay runs registrados.[/yellow]")
            return

        t = Table(title="Runs recientes")
        t.add_column("Run ID", style="cyan")
        t.add_column("Comando", style="magenta")
        t.add_column("Inicio", style="white")
        t.add_column("Estado", style="green")
        t.add_column("Dry-run", style="yellow")
        for r in runs:
            t.add_row(
                r["run_id"][:8] + "…",
                r["command"],
                r["started_at"][:19],
                r["status"] or "—",
                "sí" if r["dry_run"] else "no",
            )
        console.print(t)

        run_choices = [r["run_id"][:8] + "… " + r["started_at"][:19] for r in runs]
        run_choices.append("← Volver")
        choice = questionary.select("Selecciona un run para ver detalles / deshacer:", choices=run_choices).ask()
        if choice is None or choice == "← Volver":
            return

        idx = run_choices.index(choice)
        selected = runs[idx]
        run_id = selected["run_id"]

        ops = journal.operations_for(run_id)
        ops_table = Table(title=f"Operaciones del run {run_id[:8]}…")
        ops_table.add_column("Seq", style="magenta", justify="right")
        ops_table.add_column("Acción", style="cyan")
        ops_table.add_column("Origen", style="yellow")
        ops_table.add_column("Destino", style="green")
        for op in ops[:50]:
            ops_table.add_row(str(op["seq"]), op["action"], op["src"], op["dst"] or "—")
        console.print(ops_table)

        if selected["dry_run"] or selected["status"] in ("reverted",):
            console.print("[dim]Este run no se puede deshacer (dry-run o ya revertido).[/dim]")
            return

        do_undo = questionary.confirm("¿Deshacer este run?", default=False).ask()
        if not do_undo:
            return

        dry = questionary.confirm("¿Dry-run para el undo?", default=True).ask()
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
    console.print("[dim]>>> \\[Axolo Data]: Organizing the chaos. <<<[/dim]\n")

    MENU_CHOICES = [
        "Organizar archivos",
        "Buscar duplicados",
        "Sincronizar carpetas",
        "Ver historial y deshacer",
        "Salir",
    ]

    while True:
        choice = questionary.select(
            "¿Qué deseas hacer?",
            choices=MENU_CHOICES,
        ).ask()

        if choice is None or choice == "Salir":
            console.print("[bold green]¡Hasta luego![/bold green]")
            break

        try:
            if choice == "Organizar archivos":
                _wizard_run()
            elif choice == "Buscar duplicados":
                _wizard_duplicates()
            elif choice == "Sincronizar carpetas":
                _wizard_sync()
            elif choice == "Ver historial y deshacer":
                _wizard_history()
        except KeyboardInterrupt:
            console.print("\n[yellow]Operación cancelada.[/yellow]")
        except Exception as exc:
            console.print(f"[red]Error inesperado: {exc}[/red]")

        console.print()
