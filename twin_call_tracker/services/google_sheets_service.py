"""Google Sheets client: read the worksheet, append rows, update rows in place.

Writes are batched. The header row is authoritative for column position, so a
user reordering columns in the sheet does not corrupt data — the app re-reads the
header on every sync and maps by name.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from services.auth_service import GoogleAuth
from services.errors import (AppError, AuthError, NotFoundError, PermissionError_, RateLimitError,
                             SheetsError, wrap_network)
from utils.config import SHEETS_MAX_RETRIES
from utils.logger import get_logger

log = get_logger("sheets")

_SHEET_URL_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9\-_]+)")
_ID_RE = re.compile(r"^[a-zA-Z0-9\-_]{20,}$")


def extract_spreadsheet_id(text: str) -> str:
    """Accept a full URL or a bare id, because users paste both."""
    text = (text or "").strip()
    if not text:
        return ""
    m = _SHEET_URL_RE.search(text)
    if m:
        return m.group(1)
    if _ID_RE.match(text):
        return text
    return ""


@dataclass
class Worksheet:
    title: str
    sheet_id: int
    rows: int = 0
    cols: int = 0


@dataclass
class SheetTable:
    """A worksheet read into memory: its header and its data rows."""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)   # data rows, header excluded
    header_row_index: int = 1                             # 1-based row of the header

    def header_index(self, name: str) -> int | None:
        """Case- and space-insensitive header lookup."""
        target = _norm(name)
        for i, h in enumerate(self.headers):
            if _norm(h) == target:
                return i
        return None

    def cell(self, row: list[str], name: str) -> str:
        idx = self.header_index(name)
        if idx is None or idx >= len(row):
            return ""
        return (row[idx] or "").strip()

    @property
    def first_data_row(self) -> int:
        return self.header_row_index + 1


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def a1_column(index_zero_based: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    n = index_zero_based + 1
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def quote_title(title: str) -> str:
    """A1 sheet names need quoting, and single quotes are doubled."""
    return "'" + (title or "").replace("'", "''") + "'"


class GoogleSheetsService:
    def __init__(self, auth: GoogleAuth) -> None:
        self.auth = auth
        self._svc: Any = None

    # ------------------------------------------------------------------ core #
    def _service(self) -> Any:
        if self._svc is None:
            from googleapiclient.discovery import build
            creds = self.auth.credentials()
            # cache_discovery=False: the on-disk discovery cache only warns and is
            # useless in a frozen app.
            # static_discovery=True: use the Sheets description bundled with the
            # library rather than fetching it. This is the library default today,
            # but stating it means the packaged build cannot start depending on a
            # network round trip if that default ever changes - and the build only
            # ships the one document, so a fetch would be the only way it worked.
            self._svc = build("sheets", "v4", credentials=creds, cache_discovery=False,
                              static_discovery=True)
        return self._svc

    def reset(self) -> None:
        self._svc = None

    def _call(self, request: Any, what: str) -> Any:
        """Execute a request with retries on the statuses that deserve them."""
        from googleapiclient.errors import HttpError

        last: Exception | None = None
        for attempt in range(SHEETS_MAX_RETRIES):
            try:
                return request.execute(num_retries=0)
            except HttpError as exc:
                status = int(getattr(exc.resp, "status", 0) or 0)
                last = exc
                if status in (429, 500, 502, 503, 504) and attempt < SHEETS_MAX_RETRIES - 1:
                    wait = min(30.0, 1.5 * (2 ** attempt))
                    log.warning("Google Sheets %s returned HTTP %d; retrying in %.1fs",
                                what, status, wait)
                    time.sleep(wait)
                    continue
                raise self._http_error(exc, status, what) from exc
            except AuthError:
                raise
            except Exception as exc:
                last = exc
                if _looks_transport(exc) and attempt < SHEETS_MAX_RETRIES - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise wrap_network(exc, "Google Sheets") from exc
        raise wrap_network(last or RuntimeError("unknown"), "Google Sheets")

    @staticmethod
    def _http_error(exc: Any, status: int, what: str) -> AppError:
        detail = f"{what}: HTTP {status}: {getattr(exc, 'reason', '')} {str(exc)[:300]}"
        if status == 401:
            return AuthError("Your Google authentication has expired. Please reconnect your account.",
                             detail, service="Google")
        if status == 403:
            low = str(exc).lower()
            if "quota" in low or "rate" in low:
                return RateLimitError(
                    "Google Sheets is limiting requests right now. Please wait a moment and try "
                    "again.", detail)
            if "api has not been used" in low or "disabled" in low:
                return PermissionError_(
                    "The Google Sheets API is not enabled for this project. Please ask IT to enable "
                    "it in the Google Cloud console.", detail)
            return PermissionError_(
                "This Google account does not have permission to edit that spreadsheet. Please "
                "share the sheet with the connected account, giving it edit access.", detail)
        if status == 404:
            return NotFoundError(
                "That Google spreadsheet could not be found. It may have been deleted or moved, or "
                "the spreadsheet ID in Settings may be wrong.", detail)
        if status == 400:
            return SheetsError(
                "Google Sheets rejected the request. The worksheet tab named in Settings may have "
                "been renamed or deleted.", detail)
        if status == 429:
            return RateLimitError("Google Sheets is limiting requests. Please try again shortly.",
                                  detail)
        if 500 <= status < 600:
            return SheetsError("Google Sheets is currently unavailable. Please try again shortly.",
                               detail)
        return SheetsError(f"Google Sheets returned an unexpected response (code {status}).", detail)

    # -------------------------------------------------------------- metadata #
    def spreadsheet_title(self, spreadsheet_id: str) -> str:
        meta = self._call(self._service().spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="properties.title"), "spreadsheet title")
        return ((meta.get("properties") or {}).get("title") or "").strip()

    def worksheets(self, spreadsheet_id: str) -> list[Worksheet]:
        meta = self._call(self._service().spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties(sheetId,title,gridProperties)"), "worksheet list")
        out: list[Worksheet] = []
        for sh in meta.get("sheets") or []:
            p = sh.get("properties") or {}
            grid = p.get("gridProperties") or {}
            out.append(Worksheet(title=p.get("title") or "", sheet_id=int(p.get("sheetId") or 0),
                                 rows=int(grid.get("rowCount") or 0),
                                 cols=int(grid.get("columnCount") or 0)))
        log.info("Spreadsheet %s has %d worksheets", spreadsheet_id[:8] + "...", len(out))
        return out

    def worksheet(self, spreadsheet_id: str, title: str) -> Worksheet:
        for ws in self.worksheets(spreadsheet_id):
            if _norm(ws.title) == _norm(title):
                return ws
        raise NotFoundError(
            f'The worksheet tab "{title}" no longer exists in that spreadsheet. Please choose the '
            "tab again in Settings.", f"worksheet '{title}' not found")

    # ------------------------------------------------------------------ read #
    def read_table(self, spreadsheet_id: str, worksheet_title: str) -> SheetTable:
        """Read the whole tab. Row 1 is the header unless it is blank, in which
        case the first non-empty row is used, so a title banner does not break it."""
        rng = f"{quote_title(worksheet_title)}"
        resp = self._call(self._service().spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=rng,
            majorDimension="ROWS", valueRenderOption="FORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING"), "read worksheet")
        values: list[list[str]] = [[str(c) if c is not None else "" for c in row]
                                   for row in (resp.get("values") or [])]
        if not values:
            return SheetTable(headers=[], rows=[], header_row_index=1)
        header_idx = 0
        for i, row in enumerate(values[:10]):
            if any((c or "").strip() for c in row):
                header_idx = i
                break
        headers = [(c or "").strip() for c in values[header_idx]]
        rows = values[header_idx + 1:]
        width = len(headers)
        rows = [(r + [""] * (width - len(r)))[:max(width, len(r))] for r in rows]
        table = SheetTable(headers=headers, rows=rows, header_row_index=header_idx + 1)
        log.info("Read worksheet '%s': %d columns, %d data rows",
                 worksheet_title, len(headers), len(rows))
        return table

    # ----------------------------------------------------------------- write #
    def ensure_headers(self, spreadsheet_id: str, worksheet_title: str, table: SheetTable,
                       required: Iterable[str]) -> SheetTable:
        """Append any missing header cells, including the tracking columns.

        Existing headers are never renamed or reordered — only new ones are added
        to the right, so a sheet people already use keeps its shape.
        """
        missing = [h for h in required if h and table.header_index(h) is None]
        if not missing:
            return table
        start = len(table.headers)
        new_headers = table.headers + missing
        rng = (f"{quote_title(worksheet_title)}!{a1_column(start)}{table.header_row_index}:"
               f"{a1_column(len(new_headers) - 1)}{table.header_row_index}")
        self._call(self._service().spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=rng, valueInputOption="RAW",
            body={"values": [missing]}), "add header columns")
        log.info("Added %d header column(s) to '%s': %s",
                 len(missing), worksheet_title, ", ".join(missing))
        table.headers = new_headers
        table.rows = [r + [""] * (len(new_headers) - len(r)) if len(r) < len(new_headers) else r
                      for r in table.rows]
        return table

    def append_rows(self, spreadsheet_id: str, worksheet_title: str,
                    rows: list[list[str]]) -> int:
        """Append data rows. Returns the 1-based row number of the first new row."""
        if not rows:
            return 0
        resp = self._call(self._service().spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{quote_title(worksheet_title)}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            includeValuesInResponse=False,
            body={"values": rows, "majorDimension": "ROWS"}), "append rows")
        updated = (resp.get("updates") or {}).get("updatedRange") or ""
        first = _first_row_of_range(updated)
        log.info("Appended %d row(s) to '%s' starting at row %s",
                 len(rows), worksheet_title, first or "?")
        return first

    def update_rows(self, spreadsheet_id: str, worksheet_title: str,
                    updates: list[tuple[int, list[str]]]) -> int:
        """Overwrite whole rows in place. updates = [(1-based row, values), ...]."""
        if not updates:
            return 0
        data = []
        for row_number, values in updates:
            last_col = a1_column(max(0, len(values) - 1))
            data.append({
                "range": f"{quote_title(worksheet_title)}!A{row_number}:{last_col}{row_number}",
                "majorDimension": "ROWS",
                "values": [values],
            })
        resp = self._call(self._service().spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data}), "update rows")
        count = int(resp.get("totalUpdatedRows") or 0)
        log.info("Updated %d row(s) in '%s'", count, worksheet_title)
        return count

    # ------------------------------------------------------------------ test #
    def test(self, spreadsheet_id: str, worksheet_title: str = "") -> str:
        """Prove the connection end to end. Returns a human sentence."""
        title = self.spreadsheet_title(spreadsheet_id)
        sheets = self.worksheets(spreadsheet_id)
        if worksheet_title:
            ws = self.worksheet(spreadsheet_id, worksheet_title)
            return f'Opened "{title}" and found the tab "{ws.title}".'
        names = ", ".join(s.title for s in sheets[:6])
        more = "" if len(sheets) <= 6 else f" and {len(sheets) - 6} more"
        return f'Opened "{title}" with {len(sheets)} tab(s): {names}{more}.'


def _first_row_of_range(a1: str) -> int:
    """'Sheet1'!A57:K59 -> 57"""
    if not a1:
        return 0
    tail = a1.split("!")[-1]
    m = re.search(r"([A-Z]+)(\d+)", tail)
    return int(m.group(2)) if m else 0


def _looks_transport(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return any(k in name for k in ("timeout", "connection", "ssl", "socket", "broken", "reset"))
