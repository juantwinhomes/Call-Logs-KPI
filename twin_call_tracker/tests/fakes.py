"""In-memory stand-ins for Monday.com and Google Sheets.

They implement the same surface the sync engine uses, so the engine under test is
the real one — only the network is replaced.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from services.google_sheets_service import SheetTable, _norm
from services.monday_service import MondayBoard, MondayColumn, MondayItem


class FakeMonday:
    """Holds items in memory and honours the `since` filter the same way."""

    def __init__(self, board_id: str = "100", board_name: str = "Test Board") -> None:
        self.board_id = board_id
        self.board_name = board_name
        self.items_by_id: dict[str, MondayItem] = {}
        self.calls: list[str] = []
        self.fail_with: Exception | None = None

    # -- test helpers ------------------------------------------------------- #
    def add(self, item_id: str, name: str, updated_at: str, **values: str) -> MondayItem:
        item = MondayItem(id=item_id, name=name, board_id=self.board_id,
                          updated_at=updated_at, values=dict(values))
        self.items_by_id[item_id] = item
        return item

    def touch(self, item_id: str, updated_at: str, **values: str) -> MondayItem:
        item = self.items_by_id[item_id]
        item.updated_at = updated_at
        item.values.update(values)
        return item

    # -- the interface the engine uses -------------------------------------- #
    def board(self, board_id: str) -> MondayBoard:
        self.calls.append(f"board({board_id})")
        if self.fail_with:
            raise self.fail_with
        return MondayBoard(id=str(board_id), name=self.board_name, item_count=len(self.items_by_id))

    def columns(self, board_id: str) -> list[MondayColumn]:
        return [MondayColumn("__name__", "Item Name", "name"),
                MondayColumn("status", "Status", "status"),
                MondayColumn("owner", "Owner", "person"),
                MondayColumn("due", "Date", "date")]

    def items(self, board_id: str, *, since: datetime | None = None,
              column_ids: Iterable[str] | None = None,
              progress: Callable[[int], None] | None = None) -> tuple[list[MondayItem], int]:
        self.calls.append(f"items(since={since.isoformat() if since else None})")
        if self.fail_with:
            raise self.fail_with
        out: list[MondayItem] = []
        for item in self.items_by_id.values():
            if since is None:
                out.append(item)
                continue
            dt = item.updated_dt
            if dt is None or dt > since:
                out.append(item)
        if progress:
            progress(len(self.items_by_id))
        return out, len(self.items_by_id)

    def whoami(self) -> str:
        return "Test User (Test Account)"


class FakeSheets:
    """A worksheet as a list of lists, plus a record of every write."""

    def __init__(self, headers: list[str] | None = None,
                 rows: list[list[str]] | None = None) -> None:
        self.headers: list[str] = list(headers or [])
        self.rows: list[list[str]] = [list(r) for r in (rows or [])]
        self.appended: list[list[str]] = []
        self.updated: list[tuple[int, list[str]]] = []
        self.header_writes: list[list[str]] = []
        self.reads = 0
        self.fail_on_append: Exception | None = None
        self.fail_on_update: Exception | None = None
        self.header_row_index = 1

    # -- the interface the engine uses -------------------------------------- #
    def read_table(self, spreadsheet_id: str, worksheet_title: str) -> SheetTable:
        self.reads += 1
        width = len(self.headers)
        rows = [list(r) + [""] * max(0, width - len(r)) for r in self.rows]
        return SheetTable(headers=list(self.headers), rows=rows,
                          header_row_index=self.header_row_index)

    def ensure_headers(self, spreadsheet_id: str, worksheet_title: str, table: SheetTable,
                       required: Iterable[str]) -> SheetTable:
        missing = [h for h in required if h and table.header_index(h) is None]
        if missing:
            self.header_writes.append(list(missing))
            self.headers.extend(missing)
            table.headers = list(self.headers)
            width = len(table.headers)
            table.rows = [r + [""] * (width - len(r)) if len(r) < width else r
                          for r in table.rows]
            self.rows = [r + [""] * (width - len(r)) if len(r) < width else r
                         for r in self.rows]
        return table

    def append_rows(self, spreadsheet_id: str, worksheet_title: str,
                    rows: list[list[str]]) -> int:
        if self.fail_on_append:
            raise self.fail_on_append
        first = self.header_row_index + len(self.rows) + 1
        for row in rows:
            self.appended.append(list(row))
            self.rows.append(list(row))
        return first

    def update_rows(self, spreadsheet_id: str, worksheet_title: str,
                    updates: list[tuple[int, list[str]]]) -> int:
        if self.fail_on_update:
            raise self.fail_on_update
        for row_number, values in updates:
            self.updated.append((row_number, list(values)))
            idx = row_number - (self.header_row_index + 1)
            while len(self.rows) <= idx:
                self.rows.append([""] * len(self.headers))
            self.rows[idx] = list(values)
        return len(updates)

    def spreadsheet_title(self, spreadsheet_id: str) -> str:
        return "Test Spreadsheet"

    # -- assertions helpers ------------------------------------------------- #
    def cell(self, row_number: int, header: str) -> str:
        idx = next((i for i, h in enumerate(self.headers) if _norm(h) == _norm(header)), None)
        if idx is None:
            return ""
        row = self.rows[row_number - (self.header_row_index + 1)]
        return row[idx] if idx < len(row) else ""

    def column(self, header: str) -> list[str]:
        idx = next((i for i, h in enumerate(self.headers) if _norm(h) == _norm(header)), None)
        if idx is None:
            return []
        return [(r[idx] if idx < len(r) else "") for r in self.rows]


class FakeConnStatus:
    def __init__(self, ok: bool = True, state: str = "Connected") -> None:
        from services.auth_service import ConnState
        self.state = state if not ok else ConnState.CONNECTED
        self.account = "test@example.com"
        self.detail = ""

    @property
    def ok(self) -> bool:
        from services.auth_service import ConnState
        return self.state == ConnState.CONNECTED


class FakeAuth:
    """Minimal AuthService replacement: both services report Connected."""

    class _Side:
        def __init__(self, ok: bool = True, state: str | None = None) -> None:
            from services.auth_service import ConnState
            self._status = FakeConnStatus(ok, state or ConnState.CONNECTED)

        def check(self) -> Any:
            return self._status

        def access_token(self) -> str:
            return "test-token"

    def __init__(self, monday_ok: bool = True, google_ok: bool = True,
                 monday_state: str | None = None, google_state: str | None = None) -> None:
        self.monday = self._Side(monday_ok, monday_state)
        self.google = self._Side(google_ok, google_state)

    def statuses(self):
        return self.monday.check(), self.google.check()


def iso(y: int, mo: int, d: int, h: int = 12, mi: int = 0) -> str:
    """A fixed timestamp, for data loaded before the first run."""
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def later(minutes: int = 1) -> str:
    """A timestamp after 'now'.

    The engine compares an item's updated_at against a checkpoint taken from the
    real clock, so an edit made *after* a run has to be stamped relative to now
    rather than to a hardcoded date.
    """
    from datetime import timedelta
    dt = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=minutes)
    return dt.isoformat().replace("+00:00", "Z")
