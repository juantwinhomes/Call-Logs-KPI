"""Monday.com GraphQL client.

Only reads. The app never writes to Monday, which keeps the required scope to
boards:read and means a bug here cannot damage the board.

On change detection: Monday's `items_page` can filter server-side on
__last_updated__, but that rule is applied per board view and silently ignores
some column configurations, which would make the app miss edits. This client
therefore pages the board and filters on each item's own `updated_at`, which is
authoritative. Boards of a few thousand items cost a handful of requests.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

import requests

from services.auth_service import MondayAuth
from services.errors import (AppError, AuthError, MondayError, NotFoundError, PermissionError_,
                             RateLimitError, http_to_error, wrap_network)
from utils.config import HTTP_TIMEOUT, MONDAY_API_URL, MONDAY_PAGE_SIZE
from utils.logger import get_logger

log = get_logger("monday")

API_VERSION = "2024-10"
NAME_COLUMN_ID = "__name__"
NAME_COLUMN_TITLE = "Item Name"
# The item's own id is not a board column, but it is the deduplication key the
# worksheet must carry, so it is exposed as a pseudo column too.
ITEM_ID_COLUMN_ID = "__item_id__"
ITEM_ID_COLUMN_TITLE = "Monday Item ID"


@dataclass
class MondayColumn:
    id: str
    title: str
    type: str = ""


@dataclass
class MondayBoard:
    id: str
    name: str
    workspace_id: str = ""
    workspace_name: str = ""
    item_count: int = 0


@dataclass
class MondayWorkspace:
    id: str
    name: str


@dataclass
class MondayItem:
    """A board item flattened to text, which is what a spreadsheet holds."""
    id: str
    name: str
    board_id: str
    updated_at: str = ""
    created_at: str = ""
    group: str = ""
    values: dict[str, str] = field(default_factory=dict)

    def value_for(self, column_id: str) -> str:
        if column_id == NAME_COLUMN_ID:
            return self.name
        if column_id == ITEM_ID_COLUMN_ID:
            return self.id
        if column_id == "__group__":
            return self.group
        if column_id == "__created_at__":
            return self.created_at
        if column_id == "__updated_at__":
            return self.updated_at
        return self.values.get(column_id, "")

    @property
    def updated_dt(self) -> datetime | None:
        if not self.updated_at:
            return None
        try:
            return datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))
        except ValueError:
            return None


class MondayService:
    def __init__(self, auth: MondayAuth) -> None:
        self.auth = auth
        self._session = requests.Session()

    # ------------------------------------------------------------------ core #
    def _post(self, query: str, variables: dict[str, Any] | None = None,
              *, attempt: int = 0) -> dict[str, Any]:
        token = self.auth.access_token()
        try:
            resp = self._session.post(
                MONDAY_API_URL, timeout=HTTP_TIMEOUT,
                headers={"Authorization": token, "Content-Type": "application/json",
                         "API-Version": API_VERSION},
                json={"query": query, "variables": variables or {}})
        except requests.RequestException as exc:
            raise wrap_network(exc, "Monday.com") from exc

        if resp.status_code == 429 or (resp.status_code == 200 and _is_rate_limited(resp)):
            wait = _retry_after(resp, attempt)
            if attempt < 3:
                log.warning("Monday.com rate limit reached; waiting %.1fs then retrying", wait)
                time.sleep(wait)
                return self._post(query, variables, attempt=attempt + 1)
            raise RateLimitError(
                "Monday.com is limiting requests right now. Please wait a minute and try again.",
                f"HTTP {resp.status_code} after {attempt} retries")
        if resp.status_code in (401, 403):
            raise AuthError("Your Monday authentication has expired. Please reconnect your account.",
                            f"HTTP {resp.status_code}", service="Monday.com")
        if resp.status_code >= 500 and attempt < 2:
            wait = 1.5 * (attempt + 1)
            log.warning("Monday.com returned HTTP %d; retrying in %.1fs", resp.status_code, wait)
            time.sleep(wait)
            return self._post(query, variables, attempt=attempt + 1)
        if resp.status_code != 200:
            raise http_to_error(resp.status_code, "Monday.com", resp.text)

        try:
            payload = resp.json()
        except ValueError as exc:
            raise MondayError("Monday.com returned a response the app could not read.",
                              f"non-JSON body: {resp.text[:300]}") from exc
        if payload.get("errors"):
            raise _graphql_error(payload["errors"])
        data = payload.get("data")
        if data is None:
            raise MondayError("Monday.com returned no data for that request.", str(payload)[:400])
        return data

    # -------------------------------------------------------------- metadata #
    def whoami(self) -> str:
        data = self._post("{ me { name email } account { name } }")
        me = data.get("me") or {}
        acct = (data.get("account") or {}).get("name") or ""
        who = me.get("name") or me.get("email") or ""
        return f"{who} ({acct})" if who and acct else who or acct or "connected"

    def workspaces(self) -> list[MondayWorkspace]:
        data = self._post("{ workspaces(limit: 200) { id name } }")
        out = [MondayWorkspace(str(w["id"]), w.get("name") or f"Workspace {w['id']}")
               for w in (data.get("workspaces") or []) if w and w.get("id")]
        out.sort(key=lambda w: w.name.lower())
        log.info("Fetched %d Monday workspaces", len(out))
        return out

    def boards(self, workspace_id: str | None = None) -> list[MondayBoard]:
        """Boards visible to the credential, optionally one workspace only."""
        out: list[MondayBoard] = []
        page = 1
        while page <= 20:                                  # 20 * 100 boards is plenty
            if workspace_id:
                query = ("query($p:Int!,$w:[ID!]) { boards(limit:100, page:$p, "
                         "workspace_ids:$w, state:active, order_by:used_at) "
                         "{ id name items_count workspace { id name } } }")
                variables = {"p": page, "w": [str(workspace_id)]}
            else:
                query = ("query($p:Int!) { boards(limit:100, page:$p, state:active, "
                         "order_by:used_at) { id name items_count workspace { id name } } }")
                variables = {"p": page}
            data = self._post(query, variables)
            batch = data.get("boards") or []
            for b in batch:
                if not b or not b.get("id"):
                    continue
                ws = b.get("workspace") or {}
                out.append(MondayBoard(
                    id=str(b["id"]), name=b.get("name") or f"Board {b['id']}",
                    workspace_id=str(ws.get("id") or ""), workspace_name=ws.get("name") or "",
                    item_count=int(b.get("items_count") or 0)))
            if len(batch) < 100:
                break
            page += 1
        log.info("Fetched %d Monday boards%s", len(out),
                 f" in workspace {workspace_id}" if workspace_id else "")
        return out

    def board(self, board_id: str) -> MondayBoard:
        data = self._post(
            "query($b:[ID!]) { boards(ids:$b) { id name items_count workspace { id name } } }",
            {"b": [str(board_id)]})
        boards = data.get("boards") or []
        if not boards or not boards[0]:
            raise NotFoundError(
                f"Monday.com board {board_id} could not be found. It may have been deleted, or "
                "this account may not have access to it. Please choose the board again in Settings.",
                f"boards(ids:[{board_id}]) returned empty")
        b = boards[0]
        ws = b.get("workspace") or {}
        return MondayBoard(str(b["id"]), b.get("name") or "", str(ws.get("id") or ""),
                           ws.get("name") or "", int(b.get("items_count") or 0))

    def columns(self, board_id: str) -> list[MondayColumn]:
        """Board columns, with the item title first as a selectable pseudo column."""
        data = self._post(
            "query($b:[ID!]) { boards(ids:$b) { id name columns { id title type } } }",
            {"b": [str(board_id)]})
        boards = data.get("boards") or []
        if not boards or not boards[0]:
            raise NotFoundError(
                f"Monday.com board {board_id} could not be found. Please choose it again in Settings.",
                f"columns(): boards(ids:[{board_id}]) empty")
        cols = [MondayColumn(NAME_COLUMN_ID, NAME_COLUMN_TITLE, "name"),
                MondayColumn("__group__", "Group", "group"),
                MondayColumn("__created_at__", "Created at", "datetime"),
                MondayColumn("__updated_at__", "Last updated at", "datetime")]
        for c in boards[0].get("columns") or []:
            if not c or not c.get("id"):
                continue
            if c.get("type") in ("subtasks", "button", "doc"):
                continue                                   # nothing useful to write to a sheet
            cols.append(MondayColumn(str(c["id"]), c.get("title") or str(c["id"]),
                                     c.get("type") or ""))
        log.info("Board %s exposes %d usable columns", board_id, len(cols))
        return cols

    # ----------------------------------------------------------------- items #
    def items(self, board_id: str, *, since: datetime | None = None,
              column_ids: Iterable[str] | None = None,
              progress: Callable[[int], None] | None = None) -> tuple[list[MondayItem], int]:
        """Return (items changed since `since`, total items seen).

        Filtering happens on each item's own updated_at so nothing is missed when
        a board view would have hidden it.
        """
        wanted = [c for c in (column_ids or []) if c and c != NAME_COLUMN_ID]
        cursor: str | None = None
        changed: list[MondayItem] = []
        seen = 0
        pages = 0
        while True:
            pages += 1
            if pages > 500:                                # 50,000 items, a real ceiling
                log.warning("Stopped paging board %s after %d pages", board_id, pages)
                break
            if cursor:
                query = ("query($c:String!,$l:Int!,$ids:[String!]) { next_items_page(cursor:$c, "
                         "limit:$l) { cursor items { id name updated_at created_at "
                         "group { title } column_values(ids:$ids) { id text value } } } }")
                variables: dict[str, Any] = {"c": cursor, "l": MONDAY_PAGE_SIZE, "ids": wanted}
                data = self._post(query, variables)
                node = data.get("next_items_page") or {}
            else:
                query = ("query($b:[ID!],$l:Int!,$ids:[String!]) { boards(ids:$b) { "
                         "items_page(limit:$l) { cursor items { id name updated_at created_at "
                         "group { title } column_values(ids:$ids) { id text value } } } } }")
                variables = {"b": [str(board_id)], "l": MONDAY_PAGE_SIZE, "ids": wanted}
                data = self._post(query, variables)
                boards = data.get("boards") or []
                if not boards or not boards[0]:
                    raise NotFoundError(
                        f"Monday.com board {board_id} could not be found. Please choose the board "
                        "again in Settings.", "items(): boards() returned empty")
                node = boards[0].get("items_page") or {}

            batch = node.get("items") or []
            for raw in batch:
                seen += 1
                item = _to_item(raw, board_id)
                if since is None:
                    changed.append(item)
                else:
                    dt = item.updated_dt
                    # An item with no timestamp is treated as changed rather than
                    # skipped: better to re-check it than to lose it.
                    if dt is None or dt > since:
                        changed.append(item)
            if progress:
                progress(seen)
            cursor = node.get("cursor")
            if not cursor or not batch:
                break

        log.info("Board %s: %d items seen, %d changed since %s",
                 board_id, seen, len(changed), since.isoformat() if since else "the beginning")
        return changed, seen


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _to_item(raw: dict[str, Any], board_id: str) -> MondayItem:
    values: dict[str, str] = {}
    for cv in raw.get("column_values") or []:
        if not cv or not cv.get("id"):
            continue
        text = cv.get("text")
        if text is None or text == "":
            # `text` is null for some column types; fall back to the raw value so
            # a change in, say, a mirror or formula column is still visible.
            text = _from_raw_value(cv.get("value"))
        values[str(cv["id"])] = (text or "").strip()
    return MondayItem(
        id=str(raw.get("id") or ""), name=(raw.get("name") or "").strip(), board_id=str(board_id),
        updated_at=raw.get("updated_at") or "", created_at=raw.get("created_at") or "",
        group=((raw.get("group") or {}).get("title") or ""), values=values)


def _from_raw_value(value: Any) -> str:
    """Best-effort text for a column whose `text` field came back empty."""
    if value in (None, "", "null"):
        return ""
    if isinstance(value, str):
        import json
        try:
            parsed = json.loads(value)
        except ValueError:
            return value.strip('"')
        value = parsed
    if isinstance(value, dict):
        for key in ("text", "label", "name", "date", "value", "email", "phone", "url"):
            if value.get(key):
                return str(value[key])
        if "ids" in value and isinstance(value["ids"], list):
            return ", ".join(str(x) for x in value["ids"])
        return ""
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    return str(value)


def _is_rate_limited(resp: requests.Response) -> bool:
    """Monday answers 200 with a complexity/minute error when throttling."""
    try:
        payload = resp.json()
    except ValueError:
        return False
    text = str(payload.get("errors") or payload.get("error_message") or "").lower()
    return ("complexity" in text and "budget" in text) or "rate limit" in text \
        or "minute limit" in text


def _retry_after(resp: requests.Response, attempt: int) -> float:
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return min(60.0, float(header))
        except ValueError:
            pass
    return min(30.0, 2.0 * (2 ** attempt))


def _graphql_error(errors: list[Any]) -> AppError:
    first = ""
    if errors:
        node = errors[0]
        first = node.get("message", "") if isinstance(node, dict) else str(node)
    low = first.lower()
    if "unauthorized" in low or "not authenticated" in low or "authentication" in low:
        return AuthError("Your Monday authentication has expired. Please reconnect your account.",
                         f"GraphQL: {first}", service="Monday.com")
    if "permission" in low or "forbidden" in low:
        return PermissionError_(
            "This Monday.com account does not have permission to read that board. Please check "
            "with whoever owns the board.", f"GraphQL: {first}")
    if "not found" in low or "does not exist" in low or "invalid board" in low:
        return NotFoundError(
            "That Monday.com board no longer exists, or this account cannot see it. Please choose "
            "the board again in Settings.", f"GraphQL: {first}")
    if "complexity" in low or "rate" in low or "minute limit" in low:
        return RateLimitError("Monday.com is limiting requests right now. Please try again shortly.",
                              f"GraphQL: {first}")
    if "column" in low and ("not found" in low or "invalid" in low):
        return MondayError(
            "One of the Monday.com columns being monitored no longer exists. Please review the "
            "column mappings in Settings.", f"GraphQL: {first}")
    return MondayError(f"Monday.com reported a problem: {first[:200]}", f"GraphQL: {first}")
