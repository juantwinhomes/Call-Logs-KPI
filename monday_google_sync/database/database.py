"""SQLite connection handling and schema migrations.

One file, one schema version table, forward-only migrations. The connection is
per-thread because the sync worker runs off the GUI thread and sqlite3 objects
are not safe to share across threads.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from utils.config import db_path
from utils.logger import get_logger

log = get_logger("database")

_local = threading.local()
_init_lock = threading.Lock()
_initialised = False

SCHEMA_VERSION = 1

_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, (
        """
        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT,
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        # One row per Monday item the app has successfully written to the sheet.
        # content_hash is the hash of the mapped, monitored values, so an item
        # whose updated_at moved but whose monitored fields did not is a skip.
        """
        CREATE TABLE IF NOT EXISTS synced_items (
            board_id            TEXT NOT NULL,
            item_id             TEXT NOT NULL,
            item_name           TEXT,
            monday_updated_at   TEXT,
            content_hash        TEXT,
            sheet_row           INTEGER,
            last_synced_at      TEXT,
            sync_status         TEXT,
            PRIMARY KEY (board_id, item_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_synced_items_board ON synced_items(board_id)",
        """
        CREATE TABLE IF NOT EXISTS sync_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            outcome         TEXT NOT NULL DEFAULT 'running',
            new_items       INTEGER NOT NULL DEFAULT 0,
            updated_items   INTEGER NOT NULL DEFAULT 0,
            rows_added      INTEGER NOT NULL DEFAULT 0,
            rows_updated    INTEGER NOT NULL DEFAULT 0,
            skipped         INTEGER NOT NULL DEFAULT 0,
            duplicates      INTEGER NOT NULL DEFAULT 0,
            errors          INTEGER NOT NULL DEFAULT 0,
            message         TEXT,
            dry_run         INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_sync_runs_started ON sync_runs(started_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS error_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            run_id      INTEGER,
            scope       TEXT,
            code        TEXT,
            message     TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_error_log_time ON error_log(occurred_at DESC)",
    )),
)


def _connect() -> sqlite3.Connection:
    path: Path = db_path()
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def connection() -> sqlite3.Connection:
    """Thread-local connection, initialising the schema on first use."""
    ensure_schema()
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


def close_thread_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        finally:
            _local.conn = None


def ensure_schema() -> None:
    global _initialised
    if _initialised:
        return
    with _init_lock:
        if _initialised:
            return
        conn = _connect()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            current = int(row["version"]) if row else 0
            if not row:
                conn.execute("INSERT INTO schema_version (version) VALUES (0)")
            for version, statements in _MIGRATIONS:
                if version <= current:
                    continue
                log.info("Applying database migration %d", version)
                conn.execute("BEGIN")
                try:
                    for sql in statements:
                        conn.execute(sql)
                    conn.execute("UPDATE schema_version SET version = ?", (version,))
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            _initialised = True
            log.info("Database ready at %s (schema v%d)", db_path().name, SCHEMA_VERSION)
        finally:
            conn.close()


def transaction():
    """Context manager giving an explicit transaction on the thread connection."""
    return _Transaction(connection())


class _Transaction:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self.conn.execute("BEGIN")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False


def query(sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
    return list(connection().execute(sql, tuple(params)).fetchall())


def execute(sql: str, params: Iterable = ()) -> sqlite3.Cursor:
    return connection().execute(sql, tuple(params))
