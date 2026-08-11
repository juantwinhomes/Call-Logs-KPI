"""The synchronisation engine.

Two phases, deliberately separated so Preview mode (requirement 22) and a real
run share exactly the same comparison code:

    plan(...)   reads Monday and the worksheet and decides CREATE / UPDATE / SKIP
    apply(...)  performs only the writes the plan called for

Data-safety rules this module enforces:

  * The Monday Item ID column in the worksheet is the deduplication key, so
    clicking Refresh twice can never append the same item twice (requirement 8).
  * An UPDATE rewrites only the mapped columns and the tracking columns. Every
    other cell in that row is carried through untouched, so notes a person keeps
    in a spare column survive a sync.
  * The checkpoint advances only after a run in which nothing failed
    (requirement 20), and it is deliberately rewound by a minute so an edit made
    during the run cannot fall through the gap.
  * Per-item state is committed as each item is written, so a crash halfway
    leaves the finished items marked done and the rest simply pending.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Iterable

from database.models import (Checkpoint, ErrorRepo, RunTotals, SettingsStore, SyncConfig,
                             SyncedItem, SyncedItemRepo, SyncRunRepo, parse_iso, utc_now_iso)
from services.auth_service import AuthService, ConnState
from services.errors import AppError, AuthError, ConfigError
from services.google_sheets_service import GoogleSheetsService, SheetTable
from services.monday_service import MondayItem, MondayService
from utils.config import (TRACK_BOARD_ID, TRACK_ITEM_ID, TRACK_MODIFIED, TRACK_STATUS,
                          TRACK_SYNCED, TRACKING_COLUMNS)
from utils.logger import get_logger

log = get_logger("sync")

# Rewind applied to the new checkpoint so an edit landing mid-run is not missed.
CHECKPOINT_SAFETY = timedelta(seconds=60)


class Stage(str, Enum):
    VERIFY_MONDAY = "Verifying Monday.com connection..."
    VERIFY_GOOGLE = "Verifying Google Sheets connection..."
    CONNECT_MONDAY = "Connecting to Monday.com..."
    READ_MONDAY = "Reading Monday.com updates..."
    READ_SHEET = "Checking Google Sheets..."
    COMPARE = "Comparing records..."
    WRITE = "Updating Google Sheets..."
    DONE = "Sync Complete"


class Action(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"


@dataclass
class FieldChange:
    header: str
    old: str
    new: str


@dataclass
class PlannedChange:
    action: Action
    item: MondayItem
    row_number: int | None = None          # 1-based sheet row for an UPDATE
    reason: str = ""
    changes: list[FieldChange] = field(default_factory=list)
    content_hash: str = ""
    duplicate_rows: list[int] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.item.name or f"Item {self.item.id}"


@dataclass
class SyncPlan:
    config: SyncConfig
    creates: list[PlannedChange] = field(default_factory=list)
    updates: list[PlannedChange] = field(default_factory=list)
    skips: list[PlannedChange] = field(default_factory=list)
    duplicate_row_count: int = 0
    items_seen: int = 0
    items_considered: int = 0
    started_at: str = field(default_factory=utc_now_iso)
    checkpoint_candidate: str = ""
    sheet_rows: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def has_work(self) -> bool:
        return bool(self.creates or self.updates)

    @property
    def total_changes(self) -> int:
        return len(self.creates) + len(self.updates)

    def summary_line(self) -> str:
        if not self.has_work:
            return "No new updates found."
        bits = []
        if self.creates:
            bits.append(f"{len(self.creates)} new record{'s' if len(self.creates) != 1 else ''}")
        if self.updates:
            bits.append(f"{len(self.updates)} update{'s' if len(self.updates) != 1 else ''}")
        return " and ".join(bits) + " detected"


@dataclass
class SyncResult:
    totals: RunTotals = field(default_factory=RunTotals)
    outcome: str = "success"                # success | warning | error | preview
    message: str = ""
    plan: SyncPlan | None = None
    checkpoint_committed: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome in ("success", "warning", "preview")


ProgressFn = Callable[[Stage, str], None]
CancelFn = Callable[[], bool]


def _noop(_stage: Stage, _detail: str = "") -> None:
    return


def content_hash(values: Iterable[str]) -> str:
    """Stable hash of the monitored values, so an unrelated Monday edit is a skip."""
    h = hashlib.sha256()
    for v in values:
        h.update((v or "").strip().encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:32]


class SyncService:
    def __init__(self, auth: AuthService, settings: SettingsStore | None = None,
                 monday: MondayService | None = None,
                 sheets: GoogleSheetsService | None = None) -> None:
        self.auth = auth
        self.settings = settings or SettingsStore()
        self.monday = monday or MondayService(auth.monday)
        self.sheets = sheets or GoogleSheetsService(auth.google)
        self.items_repo = SyncedItemRepo()
        self.runs = SyncRunRepo()
        self.errors = ErrorRepo()
        self.checkpoint = Checkpoint(self.settings)

    # ------------------------------------------------------------------ guard #
    def preflight(self, progress: ProgressFn = _noop) -> SyncConfig:
        """Requirement 19 steps 1-2: prove both connections, then the config."""
        cfg = SyncConfig.load(self.settings)
        missing = cfg.missing_reasons()
        if missing:
            raise ConfigError(
                "The synchronisation is not configured yet: " + "; ".join(missing) +
                ". Open Settings to finish setting it up.",
                f"incomplete config: {missing}")

        progress(Stage.VERIFY_MONDAY, "")
        m = self.auth.monday.check()
        if m.state == ConnState.NOT_CONNECTED:
            raise AuthError("Monday.com is not connected yet. Please connect it first.",
                            "monday not connected", service="Monday.com")
        if m.state == ConnState.EXPIRED:
            raise AuthError("Your Monday authentication has expired. Please reconnect your account.",
                            m.detail, service="Monday.com")
        if not m.ok:
            raise AppError(m.detail or "Unable to connect to Monday.com. Please check your internet "
                           "connection and try again.", m.detail, code="monday_unavailable",
                           scope="monday")

        progress(Stage.VERIFY_GOOGLE, "")
        g = self.auth.google.check()
        if g.state == ConnState.NOT_CONNECTED:
            raise AuthError("Google Sheets is not connected yet. Please connect it first.",
                            "google not connected", service="Google")
        if g.state == ConnState.EXPIRED:
            raise AuthError("Your Google authentication has expired. Please reconnect your account.",
                            g.detail, service="Google")
        if not g.ok:
            raise AppError(g.detail or "Unable to connect to Google Sheets. Please check your "
                           "internet connection and try again.", g.detail,
                           code="google_unavailable", scope="google")
        return cfg

    # ------------------------------------------------------------------- plan #
    def plan(self, cfg: SyncConfig, progress: ProgressFn = _noop,
             cancelled: CancelFn = lambda: False) -> SyncPlan:
        started = datetime.now(timezone.utc).replace(microsecond=0)
        plan = SyncPlan(config=cfg, started_at=started.isoformat())

        since = parse_iso(self.checkpoint.get())
        progress(Stage.CONNECT_MONDAY, "")
        board = self.monday.board(cfg.board_id)          # also proves it still exists
        log.info("Refresh starting - board '%s' (%s), sheet tab '%s', checkpoint %s",
                 board.name, board.id, cfg.worksheet, since.isoformat() if since else "none")

        progress(Stage.READ_MONDAY, "")
        column_ids = [m.monday_id for m in cfg.mappings]
        items, seen = self.monday.items(
            cfg.board_id, since=since, column_ids=column_ids,
            progress=lambda n: progress(Stage.READ_MONDAY, f"{n} items read"))
        plan.items_seen = seen
        plan.items_considered = len(items)
        if cancelled():
            raise AppError("The refresh was cancelled.", "cancelled during monday read",
                           code="cancelled")

        progress(Stage.READ_SHEET, "")
        table = self.sheets.read_table(cfg.spreadsheet_id, cfg.worksheet)
        required = [m.sheet_header for m in cfg.mappings] + list(TRACKING_COLUMNS)
        table = self.sheets.ensure_headers(cfg.spreadsheet_id, cfg.worksheet, table, required)
        plan.sheet_rows = len(table.rows)

        id_col = table.header_index(TRACK_ITEM_ID)
        if id_col is None:
            raise ConfigError(
                f'The worksheet has no "{TRACK_ITEM_ID}" column, which the app needs to recognise '
                "rows it has already written. Please re-save the settings so it can be added.",
                "tracking column missing after ensure_headers")

        # Existing rows indexed by Monday item id. A second row for the same id is
        # a duplicate the app will not touch, and it says so rather than guessing.
        by_item: dict[str, int] = {}
        duplicates: dict[str, list[int]] = {}
        for offset, row in enumerate(table.rows):
            raw = (row[id_col] if id_col < len(row) else "") or ""
            item_id = str(raw).strip()
            if not item_id:
                continue
            row_number = table.first_data_row + offset
            if item_id in by_item:
                duplicates.setdefault(item_id, []).append(row_number)
            else:
                by_item[item_id] = row_number
        plan.duplicate_row_count = sum(len(v) for v in duplicates.values())
        if duplicates:
            plan.warnings.append(
                f"{plan.duplicate_row_count} row(s) in the worksheet repeat a Monday Item ID that "
                "already appears earlier. The first row of each is kept up to date and the repeats "
                "are left alone.")
            log.warning("Worksheet has %d duplicate Monday Item ID row(s): %s",
                        plan.duplicate_row_count,
                        ", ".join(f"{k}->{v}" for k, v in list(duplicates.items())[:10]))

        known = self.items_repo.all_for_board(cfg.board_id)

        progress(Stage.COMPARE, "")
        for item in items:
            if cancelled():
                raise AppError("The refresh was cancelled.", "cancelled during compare",
                               code="cancelled")
            mapped = [(m, item.value_for(m.monday_id)) for m in cfg.mappings]
            digest = content_hash(v for _, v in mapped)
            row_number = by_item.get(item.id)
            dups = duplicates.get(item.id, [])

            if row_number is None:
                plan.creates.append(PlannedChange(
                    Action.CREATE, item, None,
                    "not yet in the worksheet", content_hash=digest, duplicate_rows=dups))
                continue

            existing = table.rows[row_number - table.first_data_row]
            changes: list[FieldChange] = []
            for m, new_value in mapped:
                if _is_tracking(m.sheet_header):
                    continue                       # maintained by the app, not compared
                old_value = table.cell(existing, m.sheet_header)
                if _differs(old_value, new_value):
                    changes.append(FieldChange(m.sheet_header, old_value, new_value))

            prior = known.get(item.id)
            if changes:
                reason = "; ".join(f"{c.header}: '{_short(c.old)}' -> '{_short(c.new)}'"
                                   for c in changes[:4])
                plan.updates.append(PlannedChange(
                    Action.UPDATE, item, row_number, reason, changes, digest, dups))
            elif prior is not None and prior.content_hash == digest:
                plan.skips.append(PlannedChange(
                    Action.SKIP, item, row_number, "already synchronised and unchanged",
                    content_hash=digest, duplicate_rows=dups))
            else:
                # The worksheet already matches Monday even though local state did
                # not know it. Record the state, do not rewrite the row.
                plan.skips.append(PlannedChange(
                    Action.SKIP, item, row_number,
                    "worksheet already matches Monday.com", content_hash=digest,
                    duplicate_rows=dups))

        # The checkpoint is the moment the read began, rewound slightly.
        plan.checkpoint_candidate = (started - CHECKPOINT_SAFETY).isoformat()
        log.info("Plan: %d create, %d update, %d skip (from %d changed of %d items, "
                 "%d worksheet rows)", len(plan.creates), len(plan.updates), len(plan.skips),
                 plan.items_considered, plan.items_seen, plan.sheet_rows)
        return plan

    # ------------------------------------------------------------------ apply #
    def apply(self, plan: SyncPlan, progress: ProgressFn = _noop,
              cancelled: CancelFn = lambda: False) -> SyncResult:
        cfg = plan.config
        totals = RunTotals(new_items=len(plan.creates), updated_items=len(plan.updates),
                           skipped=len(plan.skips), duplicates=plan.duplicate_row_count)
        result = SyncResult(totals=totals, plan=plan)
        failures: list[str] = []

        table = self.sheets.read_table(cfg.spreadsheet_id, cfg.worksheet)
        required = [m.sheet_header for m in cfg.mappings] + list(TRACKING_COLUMNS)
        table = self.sheets.ensure_headers(cfg.spreadsheet_id, cfg.worksheet, table, required)
        width = len(table.headers)
        now = utc_now_iso()

        # -- updates first: they address existing rows by number, and appending
        #    first would not move them, but doing updates first keeps the row
        #    numbers in the plan valid even if someone else appends concurrently.
        if plan.updates and not cancelled():
            progress(Stage.WRITE, f"updating {len(plan.updates)} row(s)")
            payload: list[tuple[int, list[str]]] = []
            for change in plan.updates:
                offset = change.row_number - table.first_data_row
                if offset < 0 or offset >= len(table.rows):
                    failures.append(f"Row {change.row_number} for '{change.label}' no longer exists.")
                    continue
                row = list(table.rows[offset])
                row += [""] * (width - len(row))
                row = self._fill_row(row, table, cfg, change, now)
                payload.append((change.row_number, row))
            if payload:
                try:
                    self.sheets.update_rows(cfg.spreadsheet_id, cfg.worksheet, payload)
                    totals.rows_updated = len(payload)
                    self._remember(cfg, [c for c in plan.updates
                                         if c.row_number in {p[0] for p in payload}],
                                   now, "updated")
                except AppError as exc:
                    failures.append(exc.message)
                    self.errors.add("google", exc.code, exc.detail)
                    log.error("Row update failed: %s", exc.detail)

        # -- creates
        if plan.creates and not cancelled():
            progress(Stage.WRITE, f"adding {len(plan.creates)} row(s)")
            rows: list[list[str]] = []
            for change in plan.creates:
                row = [""] * width
                rows.append(self._fill_row(row, table, cfg, change, now))
            try:
                first_row = self.sheets.append_rows(cfg.spreadsheet_id, cfg.worksheet, rows)
                totals.rows_added = len(rows)
                for i, change in enumerate(plan.creates):
                    change.row_number = (first_row + i) if first_row else None
                self._remember(cfg, plan.creates, now, "created")
            except AppError as exc:
                failures.append(exc.message)
                self.errors.add("google", exc.code, exc.detail)
                log.error("Row append failed: %s", exc.detail)

        # -- skips still get their state recorded, so the next run is cheap
        if plan.skips:
            self._remember(cfg, plan.skips, now, "unchanged", touch_synced=False)

        totals.errors = len(failures)
        result.errors = failures
        if failures:
            result.outcome = "error"
            result.message = failures[0]
            log.error("Refresh finished with %d failure(s)", len(failures))
            return result

        # Requirement 20: advance the checkpoint only on a clean run.
        if plan.checkpoint_candidate:
            self.checkpoint.commit(plan.checkpoint_candidate)
            result.checkpoint_committed = True

        if plan.warnings:
            result.outcome = "warning"
            result.message = plan.warnings[0]
        else:
            result.message = ("No new updates found." if not plan.has_work
                              else f"{totals.changes} change(s) synchronised.")
        progress(Stage.DONE, "")
        return result

    # ------------------------------------------------------------------- run  #
    def run(self, progress: ProgressFn = _noop, cancelled: CancelFn = lambda: False,
            force_preview: bool | None = None) -> SyncResult:
        """One click of Refresh. Returns a result; raises nothing for API faults."""
        run_id = self.runs.start()
        try:
            cfg = self.preflight(progress)
            preview = (not cfg.auto_write) if force_preview is None else force_preview
            plan = self.plan(cfg, progress, cancelled)

            if preview:
                totals = RunTotals(new_items=len(plan.creates), updated_items=len(plan.updates),
                                   skipped=len(plan.skips), duplicates=plan.duplicate_row_count)
                msg = (plan.summary_line() if plan.has_work else "No new updates found.")
                self.runs.finish(run_id, "preview", totals, msg)
                self.checkpoint.touch_last_checked()
                log.info("Preview complete: %s", msg)
                return SyncResult(totals=totals, outcome="preview", message=msg, plan=plan)

            result = self.apply(plan, progress, cancelled)
            self.runs.finish(run_id, result.outcome, result.totals, result.message)
            self.checkpoint.touch_last_checked()
            log.info("Refresh complete: outcome=%s added=%d updated=%d skipped=%d "
                     "duplicates=%d errors=%d", result.outcome, result.totals.rows_added,
                     result.totals.rows_updated, result.totals.skipped,
                     result.totals.duplicates, result.totals.errors)
            return result

        except AppError as exc:
            self.errors.add(exc.scope, exc.code, exc.detail, run_id)
            outcome = "cancelled" if exc.code == "cancelled" else "error"
            self.runs.finish(run_id, outcome, RunTotals(errors=1), exc.message)
            self.checkpoint.touch_last_checked()
            log.error("Refresh failed [%s/%s]: %s", exc.scope, exc.code, exc.detail)
            return SyncResult(totals=RunTotals(errors=1), outcome=outcome,
                              message=exc.message, errors=[exc.message])
        except Exception as exc:                       # never crash the GUI
            detail = f"{type(exc).__name__}: {exc}"
            self.errors.add("app", "unexpected", detail, run_id)
            self.runs.finish(run_id, "error", RunTotals(errors=1), "An unexpected error occurred.")
            self.checkpoint.touch_last_checked()
            log.exception("Unexpected error during refresh")
            return SyncResult(
                totals=RunTotals(errors=1), outcome="error",
                message="An unexpected error occurred. The technical details were written to the "
                        "log file, which you can open from the Logs screen.",
                errors=[detail])

    def apply_prepared(self, plan: SyncPlan, progress: ProgressFn = _noop,
                       cancelled: CancelFn = lambda: False) -> SyncResult:
        """Apply a plan the user reviewed in Preview mode."""
        run_id = self.runs.start()
        try:
            result = self.apply(plan, progress, cancelled)
            self.runs.finish(run_id, result.outcome, result.totals, result.message)
            self.checkpoint.touch_last_checked()
            return result
        except AppError as exc:
            self.errors.add(exc.scope, exc.code, exc.detail, run_id)
            self.runs.finish(run_id, "error", RunTotals(errors=1), exc.message)
            log.error("Applying reviewed changes failed: %s", exc.detail)
            return SyncResult(totals=RunTotals(errors=1), outcome="error",
                              message=exc.message, errors=[exc.message])
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            self.errors.add("app", "unexpected", detail, run_id)
            self.runs.finish(run_id, "error", RunTotals(errors=1), "An unexpected error occurred.")
            log.exception("Unexpected error applying reviewed changes")
            return SyncResult(totals=RunTotals(errors=1), outcome="error",
                              message="An unexpected error occurred. See the Logs screen.",
                              errors=[detail])

    # --------------------------------------------------------------- helpers #
    def _fill_row(self, row: list[str], table: SheetTable, cfg: SyncConfig,
                  change: PlannedChange, now: str) -> list[str]:
        """Write mapped and tracking cells, leaving every other cell as it was."""
        def put(header: str, value: str) -> None:
            idx = table.header_index(header)
            if idx is None:
                return
            while len(row) <= idx:
                row.append("")
            row[idx] = value

        for m in cfg.mappings:
            if _is_tracking(m.sheet_header):
                continue
            put(m.sheet_header, change.item.value_for(m.monday_id))
        put(TRACK_BOARD_ID, str(change.item.board_id))
        put(TRACK_ITEM_ID, str(change.item.id))
        put(TRACK_MODIFIED, change.item.updated_at or "")
        put(TRACK_SYNCED, now)
        put(TRACK_STATUS, "Synced")
        return row

    def _remember(self, cfg: SyncConfig, changes: Iterable[PlannedChange], now: str,
                  status: str, touch_synced: bool = True) -> None:
        records = [SyncedItem(
            board_id=cfg.board_id, item_id=c.item.id, item_name=c.item.name,
            monday_updated_at=c.item.updated_at, content_hash=c.content_hash,
            sheet_row=c.row_number, last_synced_at=now if touch_synced else None,
            sync_status=status) for c in changes]
        if records:
            self.items_repo.upsert_many(records)


def _is_tracking(header: str) -> bool:
    return header.strip().lower() in {h.lower() for h in TRACKING_COLUMNS}


def _differs(old: str, new: str) -> bool:
    """Compare as a person would: trimmed, and blank equals blank."""
    return (old or "").strip() != (new or "").strip()


def _short(text: str, width: int = 28) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 1] + "…"
