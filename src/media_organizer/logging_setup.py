"""Logging configuration for media-organizer.

Sets up:
- Console: RichHandler with coloured output (disabled in --quiet / --json-logs).
- File:    RotatingFileHandler writing JSON Lines to ~/.media-organizer/logs/.
- Each run gets a uuid4 correlation_id injected into every log record.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import uuid
from pathlib import Path
from typing import Optional

from rich.logging import RichHandler

_LOG_DIR = Path.home() / ".media-organizer" / "logs"
_LOG_FILE = "media-organizer.log"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 5

# Whether to render Rich progress bars.  Set by setup_logging().
show_progress: bool = True
_correlation_id: str = ""


class _CorrelationFilter(logging.Filter):
    """Inject correlation_id into every log record."""

    def __init__(self, correlation_id: str) -> None:
        super().__init__()
        self.correlation_id = correlation_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = self.correlation_id  # type: ignore[attr-defined]
        return True


class _JsonFormatter(logging.Formatter):
    """Format log records as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


def setup_logging(
    level: str = "INFO",
    *,
    quiet: bool = False,
    verbose: bool = False,
    json_logs: bool = False,
    log_dir: Optional[Path] = None,
) -> str:
    """Configure root logger and return the correlation_id for this run.

    Args:
        level: Log level string for the console handler ("INFO", "DEBUG", …).
        quiet: Raise console threshold to WARNING and disable progress bars.
        verbose: Set console threshold to DEBUG (overrides *level*).
        json_logs: Disable the console handler entirely; write JSONL to file only.
            Also disables progress bars.
        log_dir: Override the log file directory.

    Returns:
        A uuid4 string used as correlation_id in every log record.
    """
    global _correlation_id, show_progress

    correlation_id = str(uuid.uuid4())
    _correlation_id = correlation_id
    show_progress = not (quiet or json_logs)

    if verbose:
        console_level = logging.DEBUG
    elif quiet:
        console_level = logging.WARNING
    else:
        console_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    corr_filter = _CorrelationFilter(correlation_id)

    if not json_logs:
        console_handler = RichHandler(
            level=console_level,
            show_path=False,
            rich_tracebacks=True,
            markup=True,
        )
        console_handler.addFilter(corr_filter)
        root.addHandler(console_handler)

    log_directory = log_dir or _LOG_DIR
    try:
        log_directory.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_directory / _LOG_FILE,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(_JsonFormatter())
        file_handler.addFilter(corr_filter)
        root.addHandler(file_handler)
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "No se pudo crear el directorio de logs %s: %s", log_directory, exc
        )

    return correlation_id


def get_correlation_id() -> str:
    """Return the correlation_id set by the most recent setup_logging() call."""
    return _correlation_id
