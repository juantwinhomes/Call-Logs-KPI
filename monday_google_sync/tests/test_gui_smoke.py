"""Headless GUI smoke tests.

These build the real window and drive the real refresh path with the fake
services, so a broken signal, a bad object name or a crash in a page body is
caught here rather than on an office desktop.
"""
from __future__ import annotations


import pytest                                                    # noqa: E402

pytest.importorskip("PySide6")

from database.models import ColumnMapping, SettingsStore, SyncConfig   # noqa: E402
from services.sync_service import SyncService                     # noqa: E402
from tests.fakes import FakeAuth, FakeMonday, FakeSheets, iso, later    # noqa: E402
from ui.main_window import PAGES, MainWindow                      # noqa: E402
from utils.config import TRACK_ITEM_ID                            # noqa: E402


@pytest.fixture
def make_window(app):
    """Build windows and guarantee they are closed, so a failing assertion
    cannot leave a live worker thread behind to disturb the next test."""
    built: list[MainWindow] = []

    def factory(auth=None) -> MainWindow:
        window = MainWindow(auth or FakeAuth())
        built.append(window)
        window.show()
        app.processEvents()
        return window

    yield factory
    for window in built:
        try:
            window._stop_threads()
            window.close()
            window.deleteLater()
        except RuntimeError:
            pass
    app.processEvents()


def configure(auto_write: bool = True) -> SettingsStore:
    settings = SettingsStore()
    SyncConfig(
        board_id="100", board_name="Test Board", spreadsheet_id="sheet-abc",
        spreadsheet_name="Test Spreadsheet", worksheet="Leads",
        monitored_columns=["__name__", "status"],
        mappings=[ColumnMapping("__item_id__", "Monday Item ID", TRACK_ITEM_ID),
                  ColumnMapping("__name__", "Item Name", "Property Name"),
                  ColumnMapping("status", "Status", "Status")],
        auto_write=auto_write).save(settings)
    return settings


# --------------------------------------------------------------------------- #

def test_the_window_builds_and_shows(make_window):
    window = make_window()

    assert window.isVisible()
    assert window.windowTitle()
    assert window.btn_refresh.text() == "REFRESH / CHECK FOR UPDATES"
    assert window.stack.count() == len(PAGES)


def test_every_page_renders_without_error(app, make_window):
    window = make_window()
    for index, name in enumerate(PAGES):
        window._select_page(index)
        app.processEvents()
        assert window.stack.currentIndex() == index, name


def test_the_refresh_button_is_disabled_until_things_are_configured(app, make_window):
    window = make_window()
    window._on_status(*FakeAuth().statuses())      # pretend the probe finished
    app.processEvents()

    assert not window.btn_refresh.isEnabled()
    assert "not configured" in window.lbl_config.text().lower()


def test_the_refresh_button_enables_once_configured_and_connected(app, make_window):
    configure()
    window = make_window()
    window._on_status(*FakeAuth().statuses())
    window._reload_config_summary()
    app.processEvents()

    assert window.btn_refresh.isEnabled()
    assert "Test Board" in window.lbl_config.text()


def test_a_disconnected_service_keeps_refresh_disabled(app, make_window):
    from services.auth_service import ConnState
    configure()
    auth = FakeAuth(google_ok=False, google_state=ConnState.NOT_CONNECTED)
    window = make_window(auth)
    window._on_status(*auth.statuses())
    app.processEvents()

    assert not window.btn_refresh.isEnabled()
    assert window.row_google.pill.text() == ConnState.NOT_CONNECTED


def test_the_locked_button_shows_the_checking_label(app, make_window):
    configure()
    window = make_window()

    window._lock_refresh(True)
    assert window.btn_refresh.text() == "CHECKING FOR UPDATES..."
    assert not window.btn_refresh.isEnabled()
    assert not window.progress.isHidden()

    window._lock_refresh(False)
    assert window.btn_refresh.text() == "REFRESH / CHECK FOR UPDATES"


def test_the_result_panel_fills_in_from_a_real_sync_result(app, make_window):
    settings = configure()
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New")
    monday.add("2", "9 Elm Rd", iso(2026, 8, 1), status="New")
    sheets = FakeSheets(headers=["Property Name", "Status"], rows=[])
    service = SyncService(FakeAuth(), settings=settings, monday=monday, sheets=sheets)
    result = service.run()

    window = make_window()
    window._on_complete(result)
    app.processEvents()

    assert window.m_added._value.text() == "2"
    assert window.m_errors._value.text() == "0"
    assert "Sync complete" in window.lbl_result.text()
    assert window.lbl_status.text() == "Sync Complete"


def test_no_updates_is_not_shown_as_an_error(app, make_window):
    """Requirement 6 says explicitly that nothing found is not a failure."""
    settings = configure()
    monday = FakeMonday()
    sheets = FakeSheets(headers=["Property Name", "Status"], rows=[])
    result = SyncService(FakeAuth(), settings=settings, monday=monday, sheets=sheets).run()

    window = make_window()
    window._on_complete(result)
    app.processEvents()

    assert result.outcome == "success"
    assert "No new updates found" in window.lbl_result.text()
    assert window.lbl_status.text() == "No new updates found"
    assert window.m_errors._value.text() == "0"


def test_preview_mode_offers_review_and_apply(app, make_window):
    settings = configure(auto_write=False)
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New")
    sheets = FakeSheets(headers=["Property Name", "Status"], rows=[])
    result = SyncService(FakeAuth(), settings=settings, monday=monday, sheets=sheets).run()

    window = make_window()
    window._on_complete(result)
    app.processEvents()

    assert result.outcome == "preview"
    # isHidden() is the reliable check: a child of a window that has not been
    # mapped by the window manager reports isVisible() False regardless.
    assert not window.btn_review.isHidden(), "Review Changes should be offered"
    assert not window.btn_apply.isHidden(), "Apply Updates should be offered"
    assert window._pending_plan is not None
    assert not sheets.appended, "preview must not have written anything"


def test_the_preview_dialog_lists_the_changes(app):
    from ui.preview_dialog import PreviewDialog

    settings = configure(auto_write=False)
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New")
    monday.add("2", "9 Elm Rd", iso(2026, 8, 1), status="Working")
    sheets = FakeSheets(headers=["Property Name", "Status"], rows=[])
    service = SyncService(FakeAuth(), settings=settings, monday=monday, sheets=sheets)
    plan = service.plan(SyncConfig.load(settings))

    dialog = PreviewDialog(plan)
    app.processEvents()

    from PySide6.QtWidgets import QLabel, QTabWidget
    labels = " ".join(w.text() for w in dialog.findChildren(QLabel))
    assert "2 new records detected" in labels
    assert "Nothing below has been written yet" in labels

    tabs = dialog.findChildren(QTabWidget)
    assert tabs, "the dialog should hold a tab widget"
    assert tabs[0].tabText(0) == "New rows (2)"
    assert tabs[0].tabText(1) == "Updates (0)"

    # The two new items and their mapped values are listed for the user to check.
    from PySide6.QtWidgets import QTableWidget
    table = tabs[0].widget(0).findChild(QTableWidget)
    assert table.rowCount() == 2
    shown = {table.item(r, 1).text() for r in range(2)}
    assert shown == {"12 Oak St", "9 Elm Rd"}

    assert not dialog.apply_requested, "opening the dialog must not imply approval"
    dialog.close()


def test_an_error_result_shows_the_friendly_message_only(app, make_window):
    from services.errors import SheetsError
    settings = configure()
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New")
    sheets = FakeSheets(headers=["Property Name", "Status"], rows=[])
    sheets.fail_on_append = SheetsError(
        "Google Sheets is currently unavailable. Please try again shortly.",
        "HttpError 503 at 0xdeadbeef with a stack trace")
    result = SyncService(FakeAuth(), settings=settings, monday=monday, sheets=sheets).run()

    # _on_complete would raise a modal dialog, so this asserts on the text it
    # would show rather than on the dialog itself.
    assert result.outcome == "error"
    assert "0xdeadbeef" not in result.message
    assert "stack trace" not in result.message
    assert "unavailable" in result.message.lower()


def test_settings_page_reflects_saved_configuration(app):
    settings = configure()
    from ui.settings_window import SettingsPage
    page = SettingsPage(FakeAuth(), settings)
    app.processEvents()

    assert page.ed_board_id.text() == "100"
    assert page.ed_sheet_id.text() == "sheet-abc"
    assert page.cb_worksheet.currentText() == "Leads"
    assert page.chk_auto.isChecked()
    page.close()


def test_sync_history_shows_the_runs_that_happened(app):
    settings = configure()
    monday = FakeMonday()
    monday.add("1", "12 Oak St", iso(2026, 8, 1), status="New")
    sheets = FakeSheets(headers=["Property Name", "Status"], rows=[])
    service = SyncService(FakeAuth(), settings=settings, monday=monday, sheets=sheets)
    service.run()
    monday.touch("1", later(1), status="Won")
    service.run()

    from ui.sync_history import SyncHistoryPage
    page = SyncHistoryPage()
    page.reload()
    app.processEvents()

    assert page.tbl.rowCount() == 2
    outcomes = {page.tbl.item(r, 1).text() for r in range(2)}
    assert outcomes == {"Success"}
    page.close()


def test_logs_page_reads_the_log_file(app):
    from ui.logs_view import LogsPage
    page = LogsPage()
    page.reload()
    app.processEvents()
    assert page.view.toPlainText()          # either content or the "no log yet" line
    page.close()


def test_about_page_lists_the_data_locations(app):
    from ui.about import AboutPage
    from utils.config import APP_VERSION
    page = AboutPage()
    app.processEvents()
    from PySide6.QtWidgets import QLabel
    text = " ".join(w.text() for w in page.findChildren(QLabel))
    assert APP_VERSION in text
    assert "credentials" in text.lower()
    page.close()
