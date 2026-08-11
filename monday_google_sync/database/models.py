"""Data access objects: settings, synced item state, run history, error log."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from database.database import connection, execute, query, transaction
from utils.config import (S_AUTO_WRITE, S_LAST_CHECKED, S_LAST_SYNC, S_MAPPINGS,
                          S_MONDAY_BOARD_ID, S_MONDAY_BOARD_NAME, S_MONDAY_COLUMNS,
                          S_MONDAY_WORKSPACE_ID, S_MONDAY_WORKSPACE_NAME, S_SHEET_ID,
                          S_SHEET_NAME, S_WORKSHEET)
from utils.logger import get_logger

log = get_logger("models")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def to_local_display(value: str | None) -> str:
    """'August 11, 2026 - 3:08 PM' in the machine's local time zone."""
    dt = parse_iso(value)
    if dt is None:
        return "Never"
    local = dt.astimezone()
    hour = local.strftime("%I").lstrip("0") or "12"
    return f"{local.strftime('%B')} {local.day}, {local.year} - {hour}:{local.strftime('%M %p')}"


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

class SettingsStore:
    """Key/value application settings. No secrets live here."""

    def get(self, key: str, default: str | None = None) -> str | None:
        row = connection().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row and row["value"] is not None else default

    def set(self, key: str, value: str | None) -> None:
        execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')",
            (key, value))

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get(key)
        return default if raw is None else raw == "1"

    def set_bool(self, key: str, value: bool) -> None:
        self.set(key, "1" if value else "0")

    def get_json(self, key: str, default: Any = None) -> Any:
        raw = self.get(key)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except ValueError:
            log.warning("Setting '%s' is not valid JSON; ignoring it", key)
            return default

    def set_json(self, key: str, value: Any) -> None:
        self.set(key, json.dumps(value, separators=(",", ":")))

    def delete(self, key: str) -> None:
        execute("DELETE FROM settings WHERE key = ?", (key,))


# --------------------------------------------------------------------------- #
# Typed configuration view over the settings store
# --------------------------------------------------------------------------- #

@dataclass
class ColumnMapping:
    """One Monday column mapped onto one worksheet header."""
    monday_id: str
    monday_title: str
    sheet_header: str

    def as_dict(self) -> dict[str, str]:
        return {"monday_id": self.monday_id, "monday_title": self.monday_title,
                "sheet_header": self.sheet_header}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ColumnMapping":
        return ColumnMapping(str(d.get("monday_id", "")), str(d.get("monday_title", "")),
                             str(d.get("sheet_header", "")))


@dataclass
class SyncConfig:
    workspace_id: str = ""
    workspace_name: str = ""
    board_id: str = ""
    board_name: str = ""
    monitored_columns: list[str] = field(default_factory=list)
    spreadsheet_id: str = ""
    spreadsheet_name: str = ""
    worksheet: str = ""
    mappings: list[ColumnMapping] = field(default_factory=list)
    auto_write: bool = False

    # The item-name pseudo column: Monday exposes the title outside column_values.
    NAME_COLUMN_ID = "__name__"

    @property
    def is_complete(self) -> bool:
        return bool(self.board_id and self.spreadsheet_id and self.worksheet and self.mappings)

    def missing_reasons(self) -> list[str]:
        out = []
        if not self.board_id:
            out.append("no Monday.com board is chosen")
        if not self.spreadsheet_id:
            out.append("no Google spreadsheet is chosen")
        if not self.worksheet:
            out.append("no worksheet tab is chosen")
        if not self.mappings:
            out.append("no column mappings are defined")
        else:
            from utils.config import TRACK_ITEM_ID
            headers = {m.sheet_header.strip().lower() for m in self.mappings}
            if TRACK_ITEM_ID.lower() not in headers:
                out.append(f'no column is mapped to "{TRACK_ITEM_ID}"')
        return out

    @classmethod
    def load(cls, s: SettingsStore) -> "SyncConfig":
        raw_maps = s.get_json(S_MAPPINGS, []) or []
        return cls(
            workspace_id=s.get(S_MONDAY_WORKSPACE_ID, "") or "",
            workspace_name=s.get(S_MONDAY_WORKSPACE_NAME, "") or "",
            board_id=s.get(S_MONDAY_BOARD_ID, "") or "",
            board_name=s.get(S_MONDAY_BOARD_NAME, "") or "",
            monitored_columns=list(s.get_json(S_MONDAY_COLUMNS, []) or []),
            spreadsheet_id=s.get(S_SHEET_ID, "") or "",
            spreadsheet_name=s.get(S_SHEET_NAME, "") or "",
            worksheet=s.get(S_WORKSHEET, "") or "",
            mappings=[ColumnMapping.from_dict(m) for m in raw_maps if isinstance(m, dict)],
            auto_write=s.get_bool(S_AUTO_WRITE, False),
        )

    def save(self, s: SettingsStore) -> None:
        s.set(S_MONDAY_WORKSPACE_ID, self.workspace_id)
        s.set(S_MONDAY_WORKSPACE_NAME, self.workspace_name)
        s.set(S_MONDAY_BOARD_ID, self.board_id)
        s.set(S_MONDAY_BOARD_NAME, self.board_name)
        s.set_json(S_MONDAY_COLUMNS, self.monitored_columns)
        s.set(S_SHEET_ID, self.spreadsheet_id)
        s.set(S_SHEET_NAME, self.spreadsheet_name)
        s.set(S_WORKSHEET, self.worksheet)
        s.set_json(S_MAPPINGS, [m.as_dict() for m in self.mappings])
        s.set_bool(S_AUTO_WRITE, self.auto_write)
        log.info("Configuration saved: board=%s sheet=%s tab=%s mappings=%d auto_write=%s",
                 self.board_id or "-", self.spreadsheet_id or "-", self.worksheet or "-",
                 len(self.mappings), self.auto_write)


# --------------------------------------------------------------------------- #
# Checkpoint
# --------------------------------------------------------------------------- #

class Checkpoint:
    """The last *successful* synchronisation point (requirement 20)."""

    def __init__(self, settings: SettingsStore) -> None:
        self._s = settings

    def get(self) -> str | None:
        return self._s.get(S_LAST_SYNC)

    def commit(self, value: str) -> None:
        self._s.set(S_LAST_SYNC, value)
        log.info("Sync checkpoint committed: %s", value)

    def reset(self) -> None:
        self._s.delete(S_LAST_SYNC)
        log.info("Sync checkpoint cleared - the next refresh will re-examine every item")

    def last_checked(self) -> str | None:
        return self._s.get(S_LAST_CHECKED)

    def touch_last_checked(self, value: str | None = None) -> None:
        self._s.set(S_LAST_CHECKED, value or utc_now_iso())


# --------------------------------------------------------------------------- #
# Synced item state
# --------------------------------------------------------------------------- #

@dataclass
class SyncedItem:
    board_id: str
    item_id: str
    item_name: str = ""
    monday_updated_at: str | None = None
    content_hash: str | None = None
    sheet_row: int | None = None
    last_synced_at: str | None = None
    sync_status: str | None = None


class SyncedItemRepo:
    def all_for_board(self, board_id: str) -> dict[str, SyncedItem]:
        rows = query("SELECT * FROM synced_items WHERE board_id = ?", (str(board_id),))
        return {r["item_id"]: SyncedItem(
            board_id=r["board_id"], item_id=r["item_id"], item_name=r["item_name"] or "",
            monday_updated_at=r["monday_updated_at"], content_hash=r["content_hash"],
            sheet_row=r["sheet_row"], last_synced_at=r["last_synced_at"],
            sync_status=r["sync_status"]) for r in rows}

    def upsert(self, item: SyncedItem) -> None:
        execute(
            """
            INSERT INTO synced_items (board_id, item_id, item_name, monday_updated_at,
                                      content_hash, sheet_row, last_synced_at, sync_status)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(board_id, item_id) DO UPDATE SET
                item_name         = excluded.item_name,
                monday_updated_at = excluded.monday_updated_at,
                content_hash      = excluded.content_hash,
                sheet_row         = excluded.sheet_row,
                last_synced_at    = excluded.last_synced_at,
                sync_status       = excluded.sync_status
            """,
            (str(item.board_id), str(item.item_id), item.item_name, item.monday_updated_at,
             item.content_hash, item.sheet_row, item.last_synced_at, item.sync_status))

    def upsert_many(self, items: Iterable[SyncedItem]) -> None:
        with transaction():
            for it in items:
                self.upsert(it)

    def count(self, board_id: str | None = None) -> int:
        if board_id:
            row = connection().execute(
                "SELECT COUNT(*) c FROM synced_items WHERE board_id = ?", (str(board_id),)).fetchone()
        else:
            row = connection().execute("SELECT COUNT(*) c FROM synced_items").fetchone()
        return int(row["c"]) if row else 0

    def clear_board(self, board_id: str) -> None:
        execute("DELETE FROM synced_items WHERE board_id = ?", (str(board_id),))


# --------------------------------------------------------------------------- #
# Run history and errors
# --------------------------------------------------------------------------- #

@dataclass
class RunTotals:
    new_items: int = 0
    updated_items: int = 0
    rows_added: int = 0
    rows_updated: int = 0
    skipped: int = 0
    duplicates: int = 0
    errors: int = 0

    def as_tuple(self) -> tuple[int, ...]:
        return (self.new_items, self.updated_items, self.rows_added, self.rows_updated,
                self.skipped, self.duplicates, self.errors)

    @property
    def changes(self) -> int:
        return self.rows_added + self.rows_updated


@dataclass
class SyncRun:
    id: int
    started_at: str
    finished_at: str | None
    outcome: str
    totals: RunTotals
    message: str | None
    dry_run: bool


class SyncRunRepo:
    def start(self, dry_run: bool = False) -> int:
        cur = execute("INSERT INTO sync_runs (started_at, outcome, dry_run) VALUES (?,?,?)",
                      (utc_now_iso(), "running", 1 if dry_run else 0))
        return int(cur.lastrowid)

    def finish(self, run_id: int, outcome: str, totals: RunTotals, message: str | None) -> None:
        execute(
            """
            UPDATE sync_runs SET finished_at = ?, outcome = ?, new_items = ?, updated_items = ?,
                   rows_added = ?, rows_updated = ?, skipped = ?, duplicates = ?, errors = ?,
                   message = ? WHERE id = ?
            """,
            (utc_now_iso(), outcome, *totals.as_tuple(), message, run_id))

    def recent(self, limit: int = 50) -> list[SyncRun]:
        rows = query("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,))
        return [SyncRun(
            id=r["id"], started_at=r["started_at"], finished_at=r["finished_at"],
            outcome=r["outcome"],
            totals=RunTotals(r["new_items"], r["updated_items"], r["rows_added"],
                             r["rows_updated"], r["skipped"], r["duplicates"], r["errors"]),
            message=r["message"], dry_run=bool(r["dry_run"])) for r in rows]

    def last_successful(self) -> SyncRun | None:
        runs = [r for r in self.recent(50) if r.outcome == "success"]
        return runs[0] if runs else None

    def mark_stale_running(self) -> int:
        """A run left as 'running' means the app died mid-sync. Label it honestly."""
        cur = execute(
            "UPDATE sync_runs SET outcome = 'interrupted', finished_at = ?, "
            "message = COALESCE(message, 'The application closed before this run finished.') "
            "WHERE outcome = 'running'", (utc_now_iso(),))
        return cur.rowcount or 0


class ErrorRepo:
    def add(self, scope: str, code: str, message: str, run_id: int | None = None) -> None:
        execute("INSERT INTO error_log (occurred_at, run_id, scope, code, message) VALUES (?,?,?,?,?)",
                (utc_now_iso(), run_id, scope, code, message))

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(r) for r in query(
            "SELECT * FROM error_log ORDER BY id DESC LIMIT ?", (limit,))]

    def count(self) -> int:
        row = connection().execute("SELECT COUNT(*) c FROM error_log").fetchone()
        return int(row["c"]) if row else 0
