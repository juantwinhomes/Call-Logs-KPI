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


def test_the_drive_picker_lists_folders_and_lets_one_be_chosen(app, pump):
    """The folder browser is the new way to choose a spreadsheet."""
    from tests.test_drive_and_diagnostics import FakeDriveApi, FakeGoogleAuth, folder, sheet
    from ui.drive_picker import DrivePicker

    from services.google_drive_service import GoogleDriveService
    drive = GoogleDriveService(FakeGoogleAuth())
    drive._svc = FakeDriveApi([
        folder("f1", "2026 Call Logs", ["root"]),
        sheet("s1", "Lead Tracker", ["root"]),
    ])
    picker = DrivePicker(FakeGoogleAuth(), drive=drive)
    assert pump(lambda: picker.tree.topLevelItemCount() >= 2), "the folder never listed"

    rows = [picker.tree.topLevelItem(i) for i in range(picker.tree.topLevelItemCount())]
    names = [r.text(0) for r in rows]
    assert any("2026 Call Logs" in n for n in names), names
    assert any("Lead Tracker" in n for n in names), names
    kinds = {r.text(1) for r in rows}
    assert kinds == {"Folder", "Google Sheet"}

    # Double-clicking the spreadsheet chooses it and closes the dialog.
    sheet_row = next(r for r in rows if r.text(1) == "Google Sheet")
    picker._activate(sheet_row, 0)
    assert picker.chosen_id == "s1"
    assert picker.chosen_name == "Lead Tracker"
    picker.close()


def test_the_drive_picker_explains_itself_when_the_scope_is_missing(app):
    from PySide6.QtWidgets import QLabel
    from tests.test_drive_and_diagnostics import FakeGoogleAuth
    from utils.config import GOOGLE_SCOPE_SHEETS
    from ui.drive_picker import DrivePicker

    picker = DrivePicker(FakeGoogleAuth([GOOGLE_SCOPE_SHEETS]))
    app.processEvents()

    text = " ".join(w.text() for w in picker.findChildren(QLabel))
    assert "extra permission" in text
    assert "paste the spreadsheet link" in text, "must offer the way around it"
    assert picker.btn_choose.text() == "Reconnect Google Sheets"
    assert not picker.tree.isEnabled()
    picker.close()


def test_the_diagnostics_dialog_lists_the_checks(app, pump):
    from PySide6.QtWidgets import QLabel
    from tests.test_drive_and_diagnostics import _DiagAuth, _settings_with_config
    from ui.diagnostics_dialog import DiagnosticsDialog

    settings = _settings_with_config(False)
    dialog = DiagnosticsDialog(_DiagAuth(), settings)
    assert pump(lambda: dialog.report is not None), "the checks never finished"
    assert dialog.tree.topLevelItemCount() >= 8
    labels = [dialog.tree.topLevelItem(i).text(0)
              for i in range(dialog.tree.topLevelItemCount())]
    assert "Configuration" in labels
    assert "Monday.com connection" in labels
    assert "Google Sheets connection" in labels
    verdict = " ".join(w.text() for w in dialog.findChildren(QLabel))
    assert "stopping the app" in verdict or "needs setting up" in verdict
    dialog.close()


def test_settings_shows_a_browse_drive_button(app):
    from ui.settings_window import SettingsPage
    settings = configure()
    page = SettingsPage(FakeAuth(), settings)
    page.set_connection_state(True, True)
    app.processEvents()

    assert page.btn_browse.text() == "Browse Google Drive..."
    assert page.btn_browse.isEnabled()
    page.set_connection_state(True, False)
    assert not page.btn_browse.isEnabled(), "browsing needs Google connected"
    page.close()


def test_a_task_result_is_delivered_on_the_calling_thread(app, pump):
    """Regression: handlers connected as bare closures ran in the worker thread.

    Anything touching a widget from there is undefined behaviour, and it showed up
    as the folder browser opening empty and occasional hard crashes. run_task
    marshals the result back, so this records which thread the handler saw.
    """
    import threading
    from ui.workers import run_task

    main_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def work() -> str:
        seen["worker"] = threading.get_ident()
        return "done"

    def ok(_message: str) -> None:
        seen["handler"] = threading.get_ident()

    run_task(work, ok)
    assert pump(lambda: "handler" in seen), "the handler never ran"

    assert seen["worker"] != main_thread, "the work should be off the GUI thread"
    assert seen["handler"] == main_thread, "the handler must be back on the GUI thread"


def test_a_task_is_not_lost_to_garbage_collection(app, pump):
    """Regression: start() kept no reference, so the worker could be collected
    before the thread invoked it and the task silently never ran."""
    import gc
    from ui.workers import run_task

    done: list[str] = []
    run_task(lambda: "value", lambda msg: done.append(msg), success_text="finished")
    gc.collect()                       # the worker is unreferenced by the caller
    assert pump(lambda: bool(done)), "the task was collected before it ran"
    assert done == ["finished"]


# --------------------------------------------------------------------------- #
# Filing away the Google credential file from the connection screen
# --------------------------------------------------------------------------- #

DESKTOP_CLIENT_JSON = (
    '{"installed": {"client_id": "8134-abc.apps.googleusercontent.com",'
    ' "client_secret": "GOCSPX-fake", "redirect_uris": ["http://localhost"]}}')


@pytest.fixture
def picked(monkeypatch):
    """Stand in for the file dialog, and record the message boxes shown."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from services.google_setup import installed_path

    shown: dict[str, list[str]] = {"critical": [], "warning": []}
    chosen = {"path": ""}

    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (chosen["path"], "")))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda parent, title, text, *a, **k:
                                     shown["critical"].append(text)))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda parent, title, text, *a, **k:
                                     shown["warning"].append(text)))

    def choose(path) -> None:
        chosen["path"] = str(path)

    yield type("Picked", (), {"choose": staticmethod(choose), "shown": shown,
                              "target": staticmethod(installed_path)})
    installed_path().unlink(missing_ok=True)


def test_choosing_a_downloaded_credential_files_it_away(make_window, picked, tmp_path):
    window = make_window(FakeAuth(google_oauth_available=False))
    download = tmp_path / "client_secret_8134-abc.apps.googleusercontent.com.json"
    download.write_text(DESKTOP_CLIENT_JSON, encoding="utf-8")
    picked.choose(download)

    assert window._install_google_secrets() is True
    assert picked.target().is_file()
    assert picked.shown["critical"] == []
    assert picked.shown["warning"] == []


def test_the_wrong_file_is_refused_with_a_readable_reason(make_window, picked, tmp_path):
    window = make_window(FakeAuth(google_oauth_available=False))
    wrong = tmp_path / "key.json"
    wrong.write_text('{"type": "service_account", "private_key": "x"}', encoding="utf-8")
    picked.choose(wrong)

    assert window._install_google_secrets() is False
    assert not picked.target().exists()
    assert len(picked.shown["critical"]) == 1
    assert "service account key" in picked.shown["critical"][0]
    assert "Traceback" not in picked.shown["critical"][0]


def test_closing_the_file_dialog_changes_nothing(make_window, picked):
    window = make_window(FakeAuth(google_oauth_available=False))
    picked.choose("")

    assert window._install_google_secrets() is False
    assert not picked.target().exists()
    assert picked.shown["critical"] == []


def test_a_web_client_is_saved_but_the_user_is_told(make_window, picked, tmp_path):
    window = make_window(FakeAuth(google_oauth_available=False))
    web = tmp_path / "web.json"
    web.write_text('{"web": {"client_id": "8134-web.apps.googleusercontent.com"}}',
                   encoding="utf-8")
    picked.choose(web)

    assert window._install_google_secrets() is True
    assert picked.target().is_file()
    assert len(picked.shown["warning"]) == 1
    assert "Desktop app" in picked.shown["warning"][0]


def test_sign_in_starts_once_the_credential_is_in_place(app, make_window, monkeypatch):
    window = make_window(FakeAuth(google_oauth_available=False))
    started: list[str] = []
    monkeypatch.setattr(window, "_run_task",
                        lambda fn, text="", **k: started.append(text))
    monkeypatch.setattr(window, "_offer_google_setup", lambda: True)

    window._connect_google()

    assert started == ["Google Sheets is connected."]


def test_sign_in_is_not_attempted_when_the_setup_is_declined(app, make_window, monkeypatch):
    window = make_window(FakeAuth(google_oauth_available=False))
    started: list[str] = []
    monkeypatch.setattr(window, "_run_task",
                        lambda fn, text="", **k: started.append(text))
    monkeypatch.setattr(window, "_offer_google_setup", lambda: False)

    window._connect_google()

    assert started == []


def test_a_configured_installation_goes_straight_to_sign_in(app, make_window, monkeypatch):
    window = make_window(FakeAuth(google_oauth_available=True))
    started: list[str] = []
    monkeypatch.setattr(window, "_run_task",
                        lambda fn, text="", **k: started.append(text))
    monkeypatch.setattr(window, "_offer_google_setup",
                        lambda: pytest.fail("should not ask when already set up"))

    window._connect_google()

    assert started == ["Google Sheets is connected."]


def test_dismissing_the_setup_prompt_reports_declined(app, make_window, monkeypatch):
    """No button clicked - QMessageBox.clickedButton() is None - must not proceed."""
    from PySide6.QtWidgets import QMessageBox

    window = make_window(FakeAuth(google_oauth_available=False))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    assert window._offer_google_setup() is False


def test_the_console_button_opens_the_credentials_page(app, make_window, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QMessageBox

    window = make_window(FakeAuth(google_oauth_available=False))
    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url.toString())))

    def click_the_console_button(box) -> int:
        for button in box.buttons():
            if "console" in button.text().lower():
                box.setDefaultButton(button)
                button.click()
                break
        return 0

    monkeypatch.setattr(QMessageBox, "exec", click_the_console_button)

    assert window._offer_google_setup() is False
    assert opened == ["https://console.cloud.google.com/apis/credentials"]
