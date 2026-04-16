"""SQLite-backed operation journal for reversible media operations.

Every file action (move / copy / link) performed by the organizer is
appended here so that ``media-organizer undo`` can replay it in reverse.

Default location: ``~/.media-organizer/journal.db``
Override via env var: ``MEDIA_ORGANIZER_JOURNAL=/path/to/journal.db``
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path.home() / ".media-organizer" / "journal.db"
_ENV_VAR = "MEDIA_ORGANIZER_JOURNAL"

_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    command     TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    source      TEXT,
    destination TEXT,
    dry_run     INTEGER NOT NULL DEFAULT 0,
    status      TEXT,
    args_json   TEXT
);

CREATE TABLE IF NOT EXISTS operations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    seq         INTEGER NOT NULL,
    action      TEXT NOT NULL,
    src         TEXT NOT NULL,
    dst         TEXT,
    src_hash    TEXT,
    size        INTEGER,
    reverted_at TEXT,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_ops_run ON operations(run_id);
"""


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class Journal:
    """Append-only SQLite operation log.

    Usage::

        with Journal() as j:
            run_id = j.start_run("run", source=src, destination=dst)
            j.record(run_id, seq=0, action="move", src=old, dst=new)
            j.finish_run(run_id)
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        env_path = os.environ.get(_ENV_VAR)
        if path is None and env_path:
            path = Path(env_path)
        self._path = path or _DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.executescript(_DDL)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(
        self,
        command: str,
        *,
        source: Optional[Path] = None,
        destination: Optional[Path] = None,
        dry_run: bool = False,
        args: Optional[dict] = None,
    ) -> str:
        """Insert a new run record and return its ``run_id``."""
        run_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO runs
                (run_id, command, started_at, source, destination, dry_run, status, args_json)
            VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                run_id,
                command,
                _now_iso(),
                str(source) if source else None,
                str(destination) if destination else None,
                int(dry_run),
                json.dumps(args or {}),
            ),
        )
        self._conn.commit()
        return run_id

    def finish_run(self, run_id: str, status: str = "completed") -> None:
        """Update the run status and record the finish timestamp."""
        self._conn.execute(
            "UPDATE runs SET finished_at = ?, status = ? WHERE run_id = ?",
            (_now_iso(), status, run_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def record(
        self,
        run_id: str,
        *,
        seq: int,
        action: str,
        src: Path,
        dst: Optional[Path] = None,
        src_hash: Optional[str] = None,
        size: Optional[int] = None,
        error: Optional[str] = None,
    ) -> int:
        """Append one operation.  Returns the auto-increment ``id``."""
        cur = self._conn.execute(
            """
            INSERT INTO operations
                (run_id, seq, action, src, dst, src_hash, size, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                seq,
                action,
                str(src),
                str(dst) if dst else None,
                src_hash,
                size,
                error,
            ),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def mark_reverted(self, op_id: int) -> None:
        """Mark a single operation as reverted."""
        self._conn.execute(
            "UPDATE operations SET reverted_at = ? WHERE id = ?",
            (_now_iso(), op_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_runs(self, limit: int = 20) -> list[dict]:
        """Return the most recent *limit* runs, newest first."""
        cur = self._conn.execute(
            """
            SELECT run_id, command, started_at, finished_at,
                   source, destination, dry_run, status, args_json
            FROM runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def operations_for(self, run_id: str) -> list[dict]:
        """Return all operations for *run_id*, ordered by ``seq``."""
        cur = self._conn.execute(
            """
            SELECT id, run_id, seq, action, src, dst, src_hash, size, reverted_at, error
            FROM operations
            WHERE run_id = ?
            ORDER BY seq ASC
            """,
            (run_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def last_revertible_run_id(self) -> Optional[str]:
        """Return the most recent non-dry-run, non-reverted run_id."""
        cur = self._conn.execute(
            """
            SELECT run_id FROM runs
            WHERE dry_run = 0 AND status NOT IN ('reverted', 'running')
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return row[0] if row else None

    def run_by_id(self, run_id: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT run_id, command, started_at, finished_at, source, destination, dry_run, status FROM runs WHERE run_id = ?",
            (run_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
