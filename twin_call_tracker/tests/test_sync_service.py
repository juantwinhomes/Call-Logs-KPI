"""Tests for the synchronisation engine.

These cover the behaviours the specification is strictest about: no duplicate
rows, change detection, the checkpoint only advancing on success, and unrelated
spreadsheet columns surviving an update.
"""
from __future__ import annotations


from database.models import (Checkpoint, ColumnMapping, SettingsStore, SyncConfig,    # noqa: E402
                             SyncRunRepo, SyncedItemRepo)
from services.errors import SheetsError                          # noqa: E402
from services.sync_service import SyncService, content_hash      # noqa: E402
from tests.fakes import FakeAuth, FakeMonday, FakeSheets, iso, later   # noqa: E402
from utils.config import (TRACK_BOARD_ID, TRACK_ITEM_ID, TRACK_MODIFIED, TRACK_STATUS,  # noqa: E402
                          TRACK_SYNCED)

BOARD = "100"
SHEET = "sheet-abc"
TAB = "Leads"


def make_config(auto_write: bool = True) -> SyncConfig:
    return SyncConfig(
        board_id=BOARD, board_name="Test Board",
        spreadsheet_id=SHEET, spreadsheet_name="Test Spreadsheet", worksheet=TAB,
        monitored_columns=["__name__", "status", "owner"],
        mappings=[
            ColumnMapping("__item_id__", "Monday Item ID", TRACK_ITEM_ID),
            ColumnMapping("__name__", "Item Name", "Property Name"),
            ColumnMapping("status", "Status", "Status"),
            ColumnMapping("owner", "Owner", "Assigned To"),
        ],
        auto_write=auto_write)


def build(monday: FakeMonday, sheets: FakeSheets, auto_write: bool = True,
          auth: FakeAuth | None = None) -> SyncService:
    settings = SettingsStore()
    make_config(auto_write).save(settings)
    return SyncService(auth or FakeAuth(), settings=settings, monday=monday, sheets=sheets)


def empty_sheet() -> FakeSheets:
    return FakeSheets(headers=["Property Name", "Status", "Assigned To"], rows=[])


# --------------------------------------------------------------------------- #
# First run
# --------------------------------------------------------------------------- #

def test_first_run_appends_every_item_and_adds_tracking_columns():
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    monday.add("2", "9 Elm Rd", iso(2026, 8, 2), status="Working", owner="Bo")
    sheets = empty_sheet()
    svc = build(monday, sheets)

    result = svc.run()

    assert result.outcome == "success", result.message
    assert result.totals.rows_added == 2
    assert result.totals.rows_updated == 0
    assert len(sheets.appended) == 2
    # The five tracking columns were added to the header.
    for col in (TRACK_BOARD_ID, TRACK_ITEM_ID, TRACK_MODIFIED, TRACK_SYNCED, TRACK_STATUS):
        assert col in sheets.headers, col
    assert sorted(sheets.column(TRACK_ITEM_ID)) == ["1", "2"]
    assert sheets.column("Property Name") == ["12 Oak St", "9 Elm Rd"]
    assert sheets.column("Status") == ["New", "Working"]
    assert set(sheets.column(TRACK_STATUS)) == {"Synced"}


def test_second_run_with_no_changes_adds_nothing():
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    sheets = empty_sheet()
    svc = build(monday, sheets)
    svc.run()
    appended_before = len(sheets.appended)

    result = svc.run()

    assert result.outcome == "success"
    assert result.totals.rows_added == 0
    assert result.totals.rows_updated == 0
    assert len(sheets.appended) == appended_before
    assert result.message == "No new updates found."


def test_clicking_refresh_five_times_never_duplicates_a_row():
    """Requirement 8, stated as bluntly as the specification does."""
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    monday.add("2", "9 Elm Rd", iso(2026, 8, 1), status="New", owner="Bo")
    sheets = empty_sheet()
    svc = build(monday, sheets)

    for _ in range(5):
        svc.run()

    assert len(sheets.rows) == 2
    assert sorted(sheets.column(TRACK_ITEM_ID)) == ["1", "2"]


def test_no_duplicates_even_when_the_checkpoint_is_cleared():
    """The worksheet's own Item ID column is the guard, not the checkpoint."""
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    sheets = empty_sheet()
    svc = build(monday, sheets)
    svc.run()

    Checkpoint(svc.settings).reset()
    SyncedItemRepo().clear_board(BOARD)          # forget everything local too
    result = svc.run()

    assert len(sheets.rows) == 1
    assert result.totals.rows_added == 0


# --------------------------------------------------------------------------- #
# Change detection
# --------------------------------------------------------------------------- #

def test_status_change_updates_the_existing_row():
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    sheets = empty_sheet()
    svc = build(monday, sheets)
    svc.run()

    monday.touch("1", later(1), status="Under Contract")
    result = svc.run()

    assert result.totals.rows_updated == 1
    assert result.totals.rows_added == 0
    assert len(sheets.rows) == 1
    assert sheets.cell(2, "Status") == "Under Contract"
    assert sheets.cell(2, TRACK_MODIFIED) == monday.items_by_id["1"].updated_at


def test_owner_and_date_changes_are_both_detected():
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    sheets = empty_sheet()
    svc = build(monday, sheets)
    svc.run()

    monday.touch("1", later(1), owner="Chris", status="Appointment")
    plan = svc.plan(SyncConfig.load(svc.settings))

    assert len(plan.updates) == 1
    headers = {c.header for c in plan.updates[0].changes}
    assert headers == {"Status", "Assigned To"}


def test_touched_in_monday_but_monitored_fields_unchanged_is_a_skip():
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    sheets = empty_sheet()
    svc = build(monday, sheets)
    svc.run()

    # Someone edited a column the app does not monitor: updated_at moves, values do not.
    monday.touch("1", later(1), notes="internal comment")
    result = svc.run()

    assert result.totals.rows_updated == 0
    assert result.totals.skipped == 1
    assert not sheets.updated


def test_a_new_item_and_a_changed_item_in_one_run():
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    sheets = empty_sheet()
    svc = build(monday, sheets)
    svc.run()

    monday.touch("1", later(1), status="Dead")
    monday.add("2", "9 Elm Rd", later(1), status="New", owner="Bo")
    result = svc.run()

    assert result.totals.rows_added == 1
    assert result.totals.rows_updated == 1
    assert len(sheets.rows) == 2


def test_only_items_changed_since_the_checkpoint_are_examined():
    monday = FakeMonday()
    monday.add("1", "old", iso(2026, 1, 1), status="New", owner="Ana")
    sheets = empty_sheet()
    svc = build(monday, sheets)
    svc.run()
    monday.calls.clear()

    monday.add("2", "new", later(1), status="New", owner="Bo")
    plan = svc.plan(SyncConfig.load(svc.settings))

    # The old item is filtered out by `since`, so only the new one is considered.
    assert plan.items_considered == 1
    assert plan.items_seen == 1 + 1
    assert [c.item.id for c in plan.creates] == ["2"]


# --------------------------------------------------------------------------- #
# Data safety
# --------------------------------------------------------------------------- #

def test_an_update_preserves_columns_the_app_does_not_manage():
    """A person's own notes column must survive a sync."""
    sheets = FakeSheets(
        headers=["Property Name", "Status", "Assigned To", "Internal Notes", TRACK_ITEM_ID],
        rows=[["12 Oak St", "New", "Ana", "spoke to seller Tuesday", "1"]])
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 5), status="Under Contract", owner="Ana")
    svc = build(monday, sheets)

    result = svc.run()

    assert result.totals.rows_updated == 1
    assert sheets.cell(2, "Status") == "Under Contract"
    assert sheets.cell(2, "Internal Notes") == "spoke to seller Tuesday"


def test_checkpoint_is_not_advanced_when_a_write_fails():
    """Requirement 20."""
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    sheets = empty_sheet()
    sheets.fail_on_append = SheetsError("Google Sheets is currently unavailable.", "test")
    svc = build(monday, sheets)

    before = Checkpoint(svc.settings).get()
    result = svc.run()

    assert result.outcome == "error"
    assert result.totals.errors == 1
    assert Checkpoint(svc.settings).get() == before, "checkpoint must not move after a failure"


def test_the_item_is_retried_on_the_next_run_after_a_failure():
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    sheets = empty_sheet()
    sheets.fail_on_append = SheetsError("Google Sheets is currently unavailable.", "test")
    svc = build(monday, sheets)
    svc.run()

    sheets.fail_on_append = None
    result = svc.run()

    assert result.outcome == "success"
    assert result.totals.rows_added == 1
    assert sheets.column(TRACK_ITEM_ID) == ["1"]


def test_checkpoint_advances_and_is_rewound_for_safety():
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    svc = build(monday, empty_sheet())

    assert Checkpoint(svc.settings).get() is None
    result = svc.run()

    assert result.checkpoint_committed
    stored = Checkpoint(svc.settings).get()
    assert stored is not None
    from datetime import datetime, timedelta, timezone
    from database.models import parse_iso
    gap = datetime.now(timezone.utc) - parse_iso(stored)
    # The rewind is 60s, so the stored point is a minute or so in the past.
    assert timedelta(seconds=55) <= gap <= timedelta(seconds=180)


def test_duplicate_item_ids_in_the_sheet_are_reported_not_rewritten():
    sheets = FakeSheets(
        headers=["Property Name", "Status", "Assigned To", TRACK_ITEM_ID],
        rows=[["12 Oak St", "New", "Ana", "1"],
              ["12 Oak St (copy)", "New", "Ana", "1"]])
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 5), status="Under Contract", owner="Ana")
    svc = build(monday, sheets)

    result = svc.run()

    assert result.totals.duplicates == 1
    assert result.outcome == "warning"
    assert sheets.cell(2, "Status") == "Under Contract"      # first row updated
    assert sheets.cell(3, "Status") == "New"                 # the copy left alone
    assert len(sheets.updated) == 1


def test_rows_without_an_item_id_are_left_completely_alone():
    """A sheet people already use may have hand-entered rows."""
    sheets = FakeSheets(
        headers=["Property Name", "Status", "Assigned To", TRACK_ITEM_ID],
        rows=[["hand entered row", "Maybe", "Someone", ""]])
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    svc = build(monday, sheets)

    result = svc.run()

    assert result.totals.rows_added == 1
    assert sheets.rows[0][:4] == ["hand entered row", "Maybe", "Someone", ""]
    assert len(sheets.rows) == 2


# --------------------------------------------------------------------------- #
# Preview mode
# --------------------------------------------------------------------------- #

def test_preview_mode_writes_nothing_but_reports_what_it_found():
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    monday.add("2", "9 Elm Rd", iso(2026, 8, 1), status="New", owner="Bo")
    sheets = empty_sheet()
    svc = build(monday, sheets, auto_write=False)

    result = svc.run()

    assert result.outcome == "preview"
    assert result.totals.new_items == 2
    assert not sheets.appended
    assert not sheets.updated
    assert Checkpoint(svc.settings).get() is None, "preview must not move the checkpoint"
    assert "2 new records detected" in result.message


def test_a_reviewed_plan_can_then_be_applied():
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    sheets = empty_sheet()
    svc = build(monday, sheets, auto_write=False)
    preview = svc.run()

    applied = svc.apply_prepared(preview.plan)

    assert applied.outcome == "success"
    assert applied.totals.rows_added == 1
    assert sheets.column(TRACK_ITEM_ID) == ["1"]
    assert Checkpoint(svc.settings).get() is not None


def test_preview_says_nothing_found_when_there_is_nothing():
    monday = FakeMonday()
    sheets = empty_sheet()
    svc = build(monday, sheets, auto_write=False)

    result = svc.run()

    assert result.outcome == "preview"
    assert result.message == "No new updates found."


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #

def test_a_disconnected_service_is_reported_not_raised():
    from services.auth_service import ConnState
    monday = FakeMonday()
    sheets = empty_sheet()
    auth = FakeAuth(google_ok=False, google_state=ConnState.EXPIRED)
    svc = build(monday, sheets, auth=auth)

    result = svc.run()

    assert result.outcome == "error"
    assert "Google" in result.message and "expired" in result.message.lower()
    assert not sheets.appended


def test_incomplete_configuration_is_refused_with_a_readable_reason():
    settings = SettingsStore()
    SyncConfig(board_id="", spreadsheet_id="", worksheet="", mappings=[]).save(settings)
    svc = SyncService(FakeAuth(), settings=settings, monday=FakeMonday(), sheets=empty_sheet())

    result = svc.run()

    assert result.outcome == "error"
    assert "not configured yet" in result.message
    assert "no Monday.com board is chosen" in result.message


def test_an_api_failure_is_recorded_in_history_and_the_error_log():
    monday = FakeMonday()
    monday.fail_with = SheetsError("Monday.com is currently unavailable.", "boom")
    svc = build(monday, empty_sheet())

    result = svc.run()

    assert result.outcome == "error"
    runs = SyncRunRepo().recent(5)
    assert runs and runs[0].outcome == "error"
    from database.models import ErrorRepo
    assert ErrorRepo().count() >= 1


def test_every_run_is_recorded_in_history():
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    svc = build(monday, empty_sheet())

    svc.run()
    svc.run()

    runs = SyncRunRepo().recent(10)
    assert len(runs) == 2
    assert runs[0].outcome == "success" and runs[1].outcome == "success"
    assert runs[1].totals.rows_added == 1          # oldest first run added the row
    assert runs[0].totals.rows_added == 0


def test_last_checked_is_updated_even_when_nothing_changed():
    monday = FakeMonday()
    svc = build(monday, empty_sheet())

    assert Checkpoint(svc.settings).last_checked() is None
    svc.run()
    assert Checkpoint(svc.settings).last_checked() is not None


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #

def test_content_hash_ignores_surrounding_whitespace_only():
    assert content_hash(["a", "b"]) == content_hash([" a ", "b "])
    assert content_hash(["a", "b"]) != content_hash(["a", "c"])
    assert content_hash(["a", ""]) != content_hash(["", "a"])


def test_progress_reports_every_stage_in_order():
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New", owner="Ana")
    svc = build(monday, empty_sheet())
    seen: list[str] = []

    svc.run(progress=lambda stage, detail: seen.append(stage.value))

    for expected in ("Verifying Monday.com connection...", "Verifying Google Sheets connection...",
                     "Connecting to Monday.com...", "Reading Monday.com updates...",
                     "Checking Google Sheets...", "Comparing records...",
                     "Updating Google Sheets...", "Sync Complete"):
        assert expected in seen, f"missing stage: {expected}"
    assert seen.index("Verifying Monday.com connection...") < seen.index("Comparing records...")
    assert seen[-1] == "Sync Complete"


def test_header_matching_ignores_case_and_extra_spaces():
    sheets = FakeSheets(headers=["property name", "  STATUS ", "Assigned To", TRACK_ITEM_ID],
                        rows=[["12 Oak St", "New", "Ana", "1"]])
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 5), status="Won", owner="Ana")
    svc = build(monday, sheets)

    result = svc.run()

    assert result.totals.rows_updated == 1
    assert sheets.cell(2, "Status") == "Won"
    assert len(sheets.header_writes) == 1                    # only tracking columns added
    assert "Property Name" not in sheets.header_writes[0]
