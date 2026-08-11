"""The main window: header, connections, refresh, results and bottom navigation."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QButtonGroup, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from database.models import (Checkpoint, SettingsStore, SyncConfig, SyncRunRepo, to_local_display)
from services.auth_service import AuthService, ConnStatus
from services.sync_service import SyncPlan, SyncResult, SyncService
from ui.about import AboutPage
from ui.logs_view import LogsPage
from ui.preview_dialog import PreviewDialog
from ui.settings_window import SettingsPage
from ui.sync_history import SyncHistoryPage
from ui.style import ACCENT, BAD, GOOD, STYLESHEET, WARN
from ui.widgets import Card, ConnectionRow, Metric, Pill, hline
from ui.workers import StatusProbeWorker, SyncWorker, run_task, start
from utils.config import APP_NAME, APP_VERSION
from utils.logger import get_logger

log = get_logger("ui")

PAGES = ("Dashboard", "Settings", "Sync History", "Logs", "About")


class MainWindow(QMainWindow):
    activated = Signal()

    def __init__(self, auth: AuthService) -> None:
        super().__init__()
        self.auth = auth
        self.settings = SettingsStore()
        self.checkpoint = Checkpoint(self.settings)
        self.runs = SyncRunRepo()

        self._threads: list[Any] = []
        self._sync_worker: SyncWorker | None = None
        self._busy = False
        self._pending_plan: SyncPlan | None = None
        self._monday_status = ConnStatus()
        self._google_status = ConnStatus()

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(940, 720)
        self.resize(1040, 800)
        self.setStyleSheet(STYLESHEET)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_dashboard())
        self.settings_page = SettingsPage(self.auth, self.settings)
        self.settings_page.config_saved.connect(self._on_config_saved)
        self.settings_page.reconnect_google_requested.connect(self._connect_google)
        self.stack.addWidget(self._scrolled(self.settings_page))
        self.history_page = SyncHistoryPage()
        self.stack.addWidget(self._scrolled(self.history_page))
        self.logs_page = LogsPage()
        self.stack.addWidget(self._scrolled(self.logs_page))
        self.about_page = AboutPage()
        self.stack.addWidget(self._scrolled(self.about_page))
        outer.addWidget(self.stack, 1)

        outer.addWidget(self._build_nav())

        self.activated.connect(self._raise_self)
        self.refresh_connection_status(initial=True)
        self._reload_config_summary()
        self.history_page.reload()

    # ------------------------------------------------------------------ build #
    def _scrolled(self, widget: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(widget)
        return area

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("Header")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(22, 14, 22, 14)
        lay.setSpacing(14)

        block = QVBoxLayout()
        block.setSpacing(1)
        title = QLabel("TWIN CALL TRACKER")
        title.setObjectName("HeaderTitle")
        sub = QLabel("Reads Monday.com, updates your Google Sheet")
        sub.setObjectName("HeaderSub")
        block.addWidget(title)
        block.addWidget(sub)
        lay.addLayout(block)
        lay.addStretch(1)
        badge = QLabel(f"v{APP_VERSION}")
        badge.setObjectName("HeaderBadge")
        lay.addWidget(badge)
        return bar

    def _build_dashboard(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(22, 20, 22, 20)
        outer.setSpacing(14)

        # -- connections
        conn = Card("Connections")
        self.row_monday = ConnectionRow("Monday.com")
        self.row_monday.connect_clicked.connect(self._connect_monday)
        self.row_monday.test_clicked.connect(self._test_monday)
        self.row_monday.disconnect_clicked.connect(self._disconnect_monday)
        conn.add(self.row_monday)
        conn.add(hline())
        self.row_google = ConnectionRow("Google Sheets")
        self.row_google.connect_clicked.connect(self._connect_google)
        self.row_google.test_clicked.connect(self._test_google)
        self.row_google.disconnect_clicked.connect(self._disconnect_google)
        conn.add(self.row_google)
        outer.addWidget(conn)

        # -- what is configured
        cfg_card = Card("Configuration")
        self.lbl_config = QLabel("Loading...")
        self.lbl_config.setObjectName("Muted")
        self.lbl_config.setWordWrap(True)
        cfg_card.add(self.lbl_config)
        row = QHBoxLayout()
        self.lbl_mode = Pill("Preview mode", "warn")
        row.addWidget(self.lbl_mode)
        row.addStretch(1)
        btn_diag = QPushButton("Check my setup")
        btn_diag.setToolTip("Run every prerequisite check and say what is missing.")
        btn_diag.clicked.connect(self._open_diagnostics)
        row.addWidget(btn_diag)
        btn_settings = QPushButton("Open Settings")
        btn_settings.clicked.connect(lambda: self._goto("Settings"))
        row.addWidget(btn_settings)
        cfg_card.add_layout(row)
        outer.addWidget(cfg_card)

        # -- sync
        sync = Card("Sync")
        stamp = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        cap = QLabel("LAST CHECKED")
        cap.setObjectName("MetricLabel")
        self.lbl_last_checked = QLabel("Never")
        self.lbl_last_checked.setObjectName("Timestamp")
        left.addWidget(cap)
        left.addWidget(self.lbl_last_checked)
        stamp.addLayout(left)
        stamp.addSpacing(30)
        right = QVBoxLayout()
        right.setSpacing(2)
        cap2 = QLabel("LAST SUCCESSFUL SYNC POINT")
        cap2.setObjectName("MetricLabel")
        self.lbl_checkpoint = QLabel("Never")
        self.lbl_checkpoint.setObjectName("Timestamp")
        right.addWidget(cap2)
        right.addWidget(self.lbl_checkpoint)
        stamp.addLayout(right)
        stamp.addStretch(1)
        sync.add_layout(stamp)

        self.btn_refresh = QPushButton("REFRESH / CHECK FOR UPDATES")
        self.btn_refresh.setObjectName("Refresh")
        self.btn_refresh.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_refresh.clicked.connect(self._start_refresh)
        sync.add(self.btn_refresh)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        sync.add(self.progress)

        status_row = QHBoxLayout()
        cap3 = QLabel("STATUS")
        cap3.setObjectName("MetricLabel")
        self.lbl_status = QLabel("Ready")
        self.lbl_status.setObjectName("Timestamp")
        status_row.addWidget(cap3)
        status_row.addWidget(self.lbl_status)
        status_row.addStretch(1)
        self.btn_apply = QPushButton("Apply Updates")
        self.btn_apply.setObjectName("Primary")
        self.btn_apply.setVisible(False)
        self.btn_apply.clicked.connect(self._apply_pending)
        self.btn_review = QPushButton("Review Changes")
        self.btn_review.setVisible(False)
        self.btn_review.clicked.connect(self._review_pending)
        status_row.addWidget(self.btn_review)
        status_row.addWidget(self.btn_apply)
        sync.add_layout(status_row)
        outer.addWidget(sync)

        # -- latest result
        res = Card("Latest Result")
        grid = QGridLayout()
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(8)
        self.m_new = Metric("New Monday Items")
        self.m_upd = Metric("Updated Monday Items")
        self.m_added = Metric("Sheet Rows Added")
        self.m_rowupd = Metric("Sheet Rows Updated")
        self.m_dupes = Metric("Duplicates Skipped")
        self.m_skipped = Metric("Unchanged Skipped")
        self.m_errors = Metric("Errors")
        for i, m in enumerate((self.m_new, self.m_upd, self.m_added, self.m_rowupd)):
            grid.addWidget(m, 0, i)
        for i, m in enumerate((self.m_dupes, self.m_skipped, self.m_errors)):
            grid.addWidget(m, 1, i)
        res.add_layout(grid)
        self.lbl_result = QLabel("No refresh has run yet in this session.")
        self.lbl_result.setObjectName("Muted")
        self.lbl_result.setWordWrap(True)
        res.add(self.lbl_result)
        outer.addWidget(res)

        outer.addStretch(1)
        return self._scrolled(page)

    def _build_nav(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("NavBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(6)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for i, name in enumerate(PAGES):
            btn = QPushButton(name)
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(lambda _=False, idx=i: self._select_page(idx))
            self._nav_group.addButton(btn, i)
            lay.addWidget(btn)
        lay.addStretch(1)
        return bar

    # ------------------------------------------------------------- navigation #
    def _select_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        name = PAGES[index]
        if name == "Sync History":
            self.history_page.reload()
        elif name == "Logs":
            self.logs_page.reload()
        elif name == "Settings":
            self.settings_page.reload()

    def _goto(self, name: str) -> None:
        if name in PAGES:
            idx = PAGES.index(name)
            btn = self._nav_group.button(idx)
            if btn:
                btn.setChecked(True)
            self._select_page(idx)

    def _raise_self(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # -------------------------------------------------------------- lifecycle #
    def _track(self, thread) -> None:
        self._threads.append(thread)
        thread.finished.connect(lambda: self._threads.remove(thread)
                               if thread in self._threads else None)

    def closeEvent(self, event: QCloseEvent) -> None:    # noqa: N802
        if self._busy:
            answer = QMessageBox.question(
                self, "Sync in progress",
                "A refresh is still running. Closing now may leave it unfinished — the next "
                "refresh will pick up anything that was missed.\n\nClose anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self._sync_worker:
                self._sync_worker.cancel()
        self._stop_threads()
        log.info("Application closing")
        event.accept()

    def _stop_threads(self) -> None:
        """Let each worker thread finish so Qt does not tear one down mid-run."""
        for thread in list(self._threads):
            try:
                if thread.isRunning():
                    thread.quit()
                    if not thread.wait(4000):
                        log.warning("A background thread did not stop in time")
            except RuntimeError:
                pass          # already deleted by Qt
        self._threads.clear()

    # ----------------------------------------------------- connection status #
    def refresh_connection_status(self, initial: bool = False) -> None:
        """Probe both services in the background (requirement 12)."""
        if initial:
            self._set_status("Checking connections...", "busy")
        self.row_monday.set_busy(True, "Checking...")
        self.row_google.set_busy(True, "Checking...")
        probe = StatusProbeWorker(self.auth)
        probe.got.connect(self._on_status)
        self._track(start(probe))

    def _on_status(self, monday: ConnStatus, google: ConnStatus) -> None:
        self._monday_status, self._google_status = monday, google
        self.row_monday.set_busy(False)
        self.row_google.set_busy(False)
        self.row_monday.set_status(monday)
        self.row_google.set_status(google)
        self.settings_page.set_connection_state(monday.ok, google.ok)
        self._update_refresh_enabled()
        self._reload_timestamps()
        log.info("Connection status - Monday: %s, Google: %s", monday.state, google.state)

    def _update_refresh_enabled(self) -> None:
        cfg = SyncConfig.load(self.settings)
        ready = self._monday_status.ok and self._google_status.ok and cfg.is_complete
        self.btn_refresh.setEnabled(ready and not self._busy)
        if self._busy:
            return
        if not self._monday_status.ok or not self._google_status.ok:
            self.btn_refresh.setToolTip("Connect both services first.")
            self._set_status("Waiting for both connections", "warn")
        elif not cfg.is_complete:
            self.btn_refresh.setToolTip("Finish the configuration in Settings first.")
            self._set_status("Configuration incomplete", "warn")
        else:
            self.btn_refresh.setToolTip("")
            if self.lbl_status.text() in ("Waiting for both connections",
                                          "Configuration incomplete", "Ready"):
                self._set_status("Ready", "idle")

    def _reload_timestamps(self) -> None:
        self.lbl_last_checked.setText(to_local_display(self.checkpoint.last_checked()))
        self.lbl_checkpoint.setText(to_local_display(self.checkpoint.get()))

    def _reload_config_summary(self) -> None:
        cfg = SyncConfig.load(self.settings)
        if cfg.is_complete:
            self.lbl_config.setText(
                f'Board "{cfg.board_name or cfg.board_id}" -> spreadsheet '
                f'"{cfg.spreadsheet_name or cfg.spreadsheet_id}", tab "{cfg.worksheet}", '
                f"{len(cfg.mappings)} column(s) mapped.")
        else:
            reasons = cfg.missing_reasons()
            self.lbl_config.setText("Not configured yet: " + "; ".join(reasons) + ".")
        if cfg.auto_write:
            self.lbl_mode.set_state("Writes changes automatically", "good")
        else:
            self.lbl_mode.set_state("Preview mode - changes need approval", "warn")
        self._update_refresh_enabled()

    def _on_config_saved(self) -> None:
        self._reload_config_summary()
        self._pending_plan = None
        self.btn_apply.setVisible(False)
        self.btn_review.setVisible(False)

    # --------------------------------------------------------- connect / test #
    def _run_task(self, fn, success: str, on_ok=None) -> None:
        def ok(message: str) -> None:
            self._set_status("Ready", "idle")
            if on_ok:
                on_ok()
            QMessageBox.information(self, "Success", message)
            self.refresh_connection_status()

        def failed(message: str, _detail: str) -> None:
            self._set_status("Connection error", "bad")
            QMessageBox.warning(self, "Not connected", message)
            self.refresh_connection_status()

        self._track(run_task(fn, ok, failed, success_text=success))

    def _connect_monday(self) -> None:
        mode_oauth = self.auth.monday.oauth_available
        if mode_oauth:
            choice = QMessageBox.question(
                self, "Connect Monday.com",
                "Sign in with your Monday.com account in a browser window?\n\n"
                "Choose No to paste a personal API token instead.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes)
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Yes:
                self.row_monday.set_busy(True, "Waiting for the browser...")
                self._run_task(self.auth.monday.begin_oauth, "Monday.com is connected.")
                return
        token, ok = QInputDialog.getText(
            self, "Connect Monday.com",
            "Paste your Monday.com API token.\n\n"
            "In Monday.com: click your avatar, choose Developers, then My access tokens.\n"
            "The token is stored encrypted on this computer and is never shown again.",
            QLineEdit.EchoMode.Password)
        if not ok or not token.strip():
            return
        self.row_monday.set_busy(True, "Checking the token...")
        self._run_task(lambda: self.auth.monday.save_personal_token(token),
                       "Monday.com is connected.")

    def _connect_google(self) -> None:
        if not self.auth.google.oauth_available:
            QMessageBox.warning(
                self, "Google setup needed",
                "Google Sheets is not set up on this installation yet.\n\n"
                "A client_secrets.json file from the Google Cloud console must be placed in the "
                "application data folder. The README section 'Google setup' has the steps, and "
                "the About screen shows the exact folder.")
            return
        self.row_google.set_busy(True, "Waiting for the browser...")
        self._run_task(self.auth.google.begin_oauth, "Google Sheets is connected.")

    def _test_monday(self) -> None:
        from services.monday_service import MondayService

        def probe() -> str:
            who = MondayService(self.auth.monday).whoami()
            return f"Connection Successful. Signed in as {who}."
        self.row_monday.set_busy(True, "Testing...")
        self._run_task(probe, "")

    def _test_google(self) -> None:
        from services.google_sheets_service import GoogleSheetsService

        cfg = SyncConfig.load(self.settings)

        def probe() -> str:
            svc = GoogleSheetsService(self.auth.google)
            if not cfg.spreadsheet_id:
                svc.auth.credentials()
                return ("Connection Successful. No spreadsheet is chosen yet, so nothing was "
                        "opened - pick one in Settings.")
            return "Connection Successful. " + svc.test(cfg.spreadsheet_id, cfg.worksheet)
        self.row_google.set_busy(True, "Testing...")
        self._run_task(probe, "")

    def _disconnect_monday(self) -> None:
        if QMessageBox.question(self, "Disconnect Monday.com",
                                "Remove the stored Monday.com credential from this computer?") \
                == QMessageBox.StandardButton.Yes:
            self.auth.monday.disconnect()
            self.refresh_connection_status()

    def _disconnect_google(self) -> None:
        if QMessageBox.question(self, "Disconnect Google Sheets",
                                "Remove the stored Google credential from this computer?") \
                == QMessageBox.StandardButton.Yes:
            self.auth.google.disconnect()
            self.refresh_connection_status()

    def _open_diagnostics(self) -> None:
        from ui.diagnostics_dialog import DiagnosticsDialog
        DiagnosticsDialog(self.auth, self.settings, self).exec()
        self.refresh_connection_status()

    # ----------------------------------------------------------------- refresh #
    def _service_factory(self) -> SyncService:
        """Built inside the worker thread so its SQLite handle belongs there."""
        return SyncService(self.auth)

    def _set_status(self, text: str, kind: str = "idle") -> None:
        self.lbl_status.setText(text)
        colour = {"good": GOOD, "warn": WARN, "bad": BAD, "busy": ACCENT}.get(kind)
        self.lbl_status.setStyleSheet(f"color: {colour};" if colour else "")

    def _lock_refresh(self, locked: bool) -> None:
        self._busy = locked
        self.btn_refresh.setEnabled(not locked)
        self.btn_refresh.setText("CHECKING FOR UPDATES..." if locked
                                 else "REFRESH / CHECK FOR UPDATES")
        self.progress.setVisible(locked)
        for row in (self.row_monday, self.row_google):
            row.set_busy(locked)
        if not locked:
            self._update_refresh_enabled()

    def _start_refresh(self, plan: SyncPlan | None = None) -> None:
        if self._busy:
            return
        cfg = SyncConfig.load(self.settings)
        if plan is None and not cfg.is_complete:
            QMessageBox.warning(self, "Not configured",
                                "The synchronisation is not configured yet: "
                                + "; ".join(cfg.missing_reasons()) + ".")
            self._goto("Settings")
            return
        self._pending_plan = None
        self.btn_apply.setVisible(False)
        self.btn_review.setVisible(False)
        self._lock_refresh(True)
        self._set_status("Starting...", "busy")
        log.info("Refresh requested by the user%s", " (applying reviewed changes)" if plan else "")

        worker = SyncWorker(self._service_factory, plan=plan)
        self._sync_worker = worker
        worker.progress.connect(self._on_progress)
        worker.completed.connect(self._on_complete)
        self._track(start(worker, on_finished=lambda: setattr(self, "_sync_worker", None)))

    def _on_progress(self, stage: str, detail: str) -> None:
        self._set_status(f"{stage} {detail}".strip() if detail else stage, "busy")

    def _on_complete(self, result: SyncResult) -> None:
        self._lock_refresh(False)
        self._reload_timestamps()
        self.history_page.reload()

        t = result.totals
        self.m_new.set_value(t.new_items)
        self.m_upd.set_value(t.updated_items)
        self.m_added.set_value(t.rows_added)
        self.m_rowupd.set_value(t.rows_updated)
        self.m_dupes.set_value(t.duplicates)
        self.m_skipped.set_value(t.skipped)
        self.m_errors.set_value(t.errors)
        self.m_errors.set_colour(BAD if t.errors else None)
        self.m_dupes.set_colour(WARN if t.duplicates else None)

        if result.outcome == "preview":
            self._pending_plan = result.plan
            has_work = bool(result.plan and result.plan.has_work)
            self.btn_review.setVisible(has_work)
            self.btn_apply.setVisible(has_work)
            if has_work:
                self._set_status("Changes detected - review or apply them", "warn")
                self.lbl_result.setText(
                    f"{result.message}. Preview mode is on, so nothing has been written to the "
                    "spreadsheet yet. Use Review Changes to see them, or Apply Updates to write "
                    "them now. Preview mode can be turned off in Settings.")
            else:
                self._set_status("No new updates found", "good")
                self.lbl_result.setText("No new updates found. Nothing needed writing.")
            return

        if result.outcome == "error":
            self._set_status("Error", "bad")
            self.lbl_result.setText(result.message)
            QMessageBox.warning(self, "Sync did not finish", result.message)
            return
        if result.outcome == "cancelled":
            self._set_status("Cancelled", "warn")
            self.lbl_result.setText(result.message)
            return

        if not t.changes:
            self._set_status("No new updates found", "good")
            self.lbl_result.setText(
                "No new updates found." + (f" {result.message}" if result.outcome == "warning"
                                           and result.message else ""))
        else:
            self._set_status("Sync Complete", "good")
            extra = ""
            if result.outcome == "warning" and result.message:
                extra = " " + result.message
            self.lbl_result.setText(
                f"Sync complete. {t.rows_added} row(s) added and {t.rows_updated} row(s) updated "
                f"in the spreadsheet.{extra}")

    # ------------------------------------------------------------- preview mode #
    def _review_pending(self) -> None:
        if not self._pending_plan:
            return
        dialog = PreviewDialog(self._pending_plan, self)
        if dialog.exec() and dialog.apply_requested:
            self._apply_pending()

    def _apply_pending(self) -> None:
        if not self._pending_plan:
            return
        plan = self._pending_plan
        answer = QMessageBox.question(
            self, "Apply updates",
            f"Write {len(plan.creates)} new row(s) and {len(plan.updates)} update(s) to the "
            f'worksheet "{plan.config.worksheet}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_refresh(plan=plan)
