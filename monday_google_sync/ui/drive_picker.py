"""Browse Google Drive by folder and pick a spreadsheet.

Double-click a folder to go in, double-click a spreadsheet to choose it. The
breadcrumb across the top walks back out. Search looks across everything the
account can see, for when the folder tree is deep.

Every Drive call runs on a worker thread, so a slow folder never freezes the
dialog.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from services.auth_service import GoogleAuth
from services.google_drive_service import (PSEUDO_ROOTS, ROOT_MY_DRIVE, DriveItem,
                                           GoogleDriveService, Listing)
from ui.style import ACCENT, INK_2, INK_3, STYLESHEET, WARN
from ui.workers import run_task
from utils.logger import get_logger

log = get_logger("picker")

FOLDER_GLYPH = "\U0001F4C1"      # 📁
SHEET_GLYPH = "\U0001F4C4"       # 📄


class DrivePicker(QDialog):
    """Modal folder browser. After exec(), read `chosen_id` and `chosen_name`."""

    reconnect_requested = Signal()

    def __init__(self, auth: GoogleAuth, parent: QWidget | None = None,
                 start_folder: str = ROOT_MY_DRIVE,
                 drive: GoogleDriveService | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        # Injectable so the tests can drive a fake Drive, the same way SyncService
        # takes its two clients.
        self.drive = drive or GoogleDriveService(auth)
        self.chosen_id = ""
        self.chosen_name = ""
        self._folder = start_folder
        self._trail: list[DriveItem] = []
        self._threads: list[Any] = []
        self._busy = False
        self._searching = False

        self.setWindowTitle("Choose a spreadsheet from Google Drive")
        self.setMinimumSize(880, 600)
        self.setStyleSheet(STYLESHEET)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        title = QLabel("Google Drive")
        title.setObjectName("SectionTitle")
        outer.addWidget(title)

        # -- search
        search_row = QHBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("Search all spreadsheets by name, then press Enter")
        self.ed_search.returnPressed.connect(self._search)
        search_row.addWidget(self.ed_search, 1)
        self.btn_search = QPushButton("Search")
        self.btn_search.clicked.connect(self._search)
        search_row.addWidget(self.btn_search)
        self.btn_clear_search = QPushButton("Back to folders")
        self.btn_clear_search.clicked.connect(lambda: self._open(self._folder))
        self.btn_clear_search.setVisible(False)
        search_row.addWidget(self.btn_clear_search)
        outer.addLayout(search_row)

        # -- breadcrumb
        self.crumbs = QWidget()
        self.crumb_row = QHBoxLayout(self.crumbs)
        self.crumb_row.setContentsMargins(0, 0, 0, 0)
        self.crumb_row.setSpacing(4)
        outer.addWidget(self.crumbs)

        # -- two panes: the places on the left, the folder contents on the right
        panes = QHBoxLayout()
        panes.setSpacing(12)

        self.places = QListWidget()
        self.places.setMaximumWidth(210)
        self.places.itemClicked.connect(self._place_clicked)
        panes.addWidget(self.places)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Name", "Type", "Modified"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(False)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        h = self.tree.header()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemDoubleClicked.connect(self._activate)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        panes.addWidget(self.tree, 1)
        outer.addLayout(panes, 1)

        self.status = QLabel("")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # -- buttons
        buttons = QHBoxLayout()
        self.btn_up = QPushButton("Up one level")
        self.btn_up.clicked.connect(self._go_up)
        buttons.addWidget(self.btn_up)
        self.btn_reload = QPushButton("Reload")
        self.btn_reload.clicked.connect(lambda: self._open(self._folder, force=True))
        buttons.addWidget(self.btn_reload)
        buttons.addStretch(1)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(btn_cancel)
        self.btn_choose = QPushButton("Use this spreadsheet")
        self.btn_choose.setObjectName("Primary")
        self.btn_choose.setEnabled(False)
        self.btn_choose.clicked.connect(self._choose_selected)
        buttons.addWidget(self.btn_choose)
        outer.addLayout(buttons)

        if not self.drive.available():
            self._show_reconnect_needed()
        else:
            # The two initial loads have to be chained, not fired together: the
            # second would hit the busy guard set by the first and be dropped,
            # leaving the dialog open on an empty file pane.
            self._load_places(then=lambda: self._open(start_folder, force=True))

    # ------------------------------------------------------------- background #
    def _run(self, fn: Callable[[], Any], on_ok: Callable[[Any], None],
             busy_text: str = "Loading...") -> None:
        self._busy = True
        self._set_buttons_enabled(False)
        self.status.setText(busy_text)
        holder: dict[str, Any] = {}

        def work() -> str:
            holder["value"] = fn()
            return "ok"

        def done(_msg: str) -> None:
            self._busy = False
            self._set_buttons_enabled(True)
            on_ok(holder.get("value"))

        def failed(message: str, _detail: str) -> None:
            self._busy = False
            self._set_buttons_enabled(True)
            self.status.setText(message)
            self.status.setStyleSheet(f"color: {WARN};")

        thread = run_task(work, done, failed)
        self._threads.append(thread)
        thread.finished.connect(lambda: self._threads.remove(thread)
                                if thread in self._threads else None)

    def _set_buttons_enabled(self, on: bool) -> None:
        for b in (self.btn_up, self.btn_reload, self.btn_search, self.ed_search, self.places):
            b.setEnabled(on)
        self.btn_choose.setEnabled(on and bool(self._selected_sheet()))

    # ------------------------------------------------------------------ places #
    def _load_places(self, then: Callable[[], None] | None = None) -> None:
        def done(roots: list[DriveItem] | None) -> None:
            self.places.clear()
            for item in roots or []:
                row = QListWidgetItem(f"{FOLDER_GLYPH}  {item.name}")
                row.setData(Qt.ItemDataRole.UserRole, item.id)
                self.places.addItem(row)
            self.status.setText("")
            self.status.setStyleSheet("")
            if then:
                then()
        self._run(self.drive.roots, done, "Reading your Drive...")

    def _place_clicked(self, row: QListWidgetItem) -> None:
        folder_id = row.data(Qt.ItemDataRole.UserRole)
        if folder_id and not self._busy:
            self._open(str(folder_id))

    # ---------------------------------------------------------------- browsing #
    def _open(self, folder_id: str, force: bool = False) -> None:
        """Show one folder. `force` ignores the busy guard, for chained loads."""
        if self._busy and not force:
            return
        self._searching = False
        self.btn_clear_search.setVisible(False)
        self._folder = folder_id

        def done(payload: Any) -> None:
            listing, trail = payload
            self._trail = trail or []
            self._render_crumbs()
            self._render(listing)

        def work() -> tuple[Listing, list[DriveItem]]:
            listing = self.drive.list_folder(folder_id)
            trail = self.drive.breadcrumb(folder_id)
            return listing, trail

        self._run(work, done, "Opening folder...")

    def _render(self, listing: Listing | None) -> None:
        self.tree.clear()
        if listing is None:
            return
        for item in listing.folders:
            self._add_row(item, FOLDER_GLYPH, bold=True)
        for item in listing.sheets:
            self._add_row(item, SHEET_GLYPH)

        bits = []
        if listing.folders:
            bits.append(f"{len(listing.folders)} folder(s)")
        if listing.sheets:
            bits.append(f"{len(listing.sheets)} spreadsheet(s)")
        if listing.is_empty:
            text = "This folder holds no sub-folders and no Google Sheets."
        else:
            text = " and ".join(bits) + ". Double-click a folder to open it, or a spreadsheet to use it."
        if listing.truncated:
            text += " Only the first 1,000 items are shown - use search if what you need is missing."
        self.status.setText(text)
        self.status.setStyleSheet("")
        self.btn_up.setEnabled(len(self._trail) > 1)

    def _add_row(self, item: DriveItem, glyph: str, bold: bool = False) -> None:
        row = QTreeWidgetItem([f"{glyph}  {item.name}", item.kind, item.modified])
        row.setData(0, Qt.ItemDataRole.UserRole, item)
        if bold:
            font = QFont(row.font(0))
            font.setBold(True)
            row.setFont(0, font)
        else:
            row.setForeground(1, QColor(INK_2))
        row.setForeground(2, QColor(INK_3))
        self.tree.addTopLevelItem(row)

    def _render_crumbs(self) -> None:
        while self.crumb_row.count():
            child = self.crumb_row.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        if self._searching:
            label = QLabel("Search results")
            label.setStyleSheet(f"color: {INK_2}; font-weight: 600;")
            self.crumb_row.addWidget(label)
            self.crumb_row.addStretch(1)
            return
        for i, item in enumerate(self._trail):
            if i:
                sep = QLabel("›")           # ›
                sep.setStyleSheet(f"color: {INK_3};")
                self.crumb_row.addWidget(sep)
            last = i == len(self._trail) - 1
            if last:
                label = QLabel(item.name)
                label.setStyleSheet("font-weight: 600;")
                self.crumb_row.addWidget(label)
            else:
                btn = QPushButton(item.name)
                btn.setFlat(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet(
                    f"QPushButton {{ border: none; background: transparent; color: {ACCENT}; "
                    f"padding: 2px 4px; font-weight: 600; }}"
                    f"QPushButton:hover {{ text-decoration: underline; }}")
                btn.clicked.connect(lambda _=False, fid=item.id: self._open(fid))
                self.crumb_row.addWidget(btn)
        self.crumb_row.addStretch(1)

    def _go_up(self) -> None:
        if len(self._trail) > 1:
            self._open(self._trail[-2].id)
        elif self._trail and self._trail[0].id not in PSEUDO_ROOTS:
            self._open(ROOT_MY_DRIVE)

    # ------------------------------------------------------------------ search #
    def _search(self) -> None:
        text = self.ed_search.text().strip()
        if not text:
            return
        self._searching = True
        self.btn_clear_search.setVisible(True)

        def done(items: list[DriveItem] | None) -> None:
            self._render_crumbs()
            self.tree.clear()
            for item in items or []:
                self._add_row(item, SHEET_GLYPH)
            n = len(items or [])
            self.status.setText(
                f'No spreadsheet name contains "{text}".' if not n
                else f'{n} spreadsheet(s) match "{text}". Double-click one to use it.')
            self.status.setStyleSheet("")
            self.btn_up.setEnabled(False)

        self._run(lambda: self.drive.search_spreadsheets(text), done, "Searching Drive...")

    # ---------------------------------------------------------------- choosing #
    def _selected_sheet(self) -> DriveItem | None:
        rows = self.tree.selectedItems()
        if not rows:
            return None
        item = rows[0].data(0, Qt.ItemDataRole.UserRole)
        return item if isinstance(item, DriveItem) and item.is_spreadsheet else None

    def _selection_changed(self) -> None:
        self.btn_choose.setEnabled(not self._busy and bool(self._selected_sheet()))

    def _activate(self, row: QTreeWidgetItem, _column: int) -> None:
        item = row.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(item, DriveItem):
            return
        if item.is_folder:
            self._open(item.id)
        elif item.is_spreadsheet:
            self._accept_item(item)

    def _choose_selected(self) -> None:
        item = self._selected_sheet()
        if item:
            self._accept_item(item)

    def _accept_item(self, item: DriveItem) -> None:
        self.chosen_id = item.id
        self.chosen_name = item.name
        log.info("User chose spreadsheet '%s' from Drive", item.name)
        self.accept()

    # ------------------------------------------------------- missing scope UI #
    def _show_reconnect_needed(self) -> None:
        self.places.setEnabled(False)
        self.tree.setEnabled(False)
        self.ed_search.setEnabled(False)
        self.btn_search.setEnabled(False)
        self.btn_up.setEnabled(False)
        self.btn_reload.setEnabled(False)
        self.status.setText(
            "Browsing Drive needs one extra permission that this connection does not have yet: "
            "the ability to see folder and file names. It grants no access to what is inside your "
            "files.\n\nClose this, press Reconnect Google Sheets on the dashboard, and approve the "
            "request. You can also just paste the spreadsheet link instead - that needs nothing new.")
        self.status.setStyleSheet(f"color: {WARN};")
        self.btn_choose.setText("Reconnect Google Sheets")
        self.btn_choose.setEnabled(True)
        self.btn_choose.clicked.disconnect()
        self.btn_choose.clicked.connect(self._ask_reconnect)

    def _ask_reconnect(self) -> None:
        self.reconnect_requested.emit()
        self.reject()

    # ------------------------------------------------------------------ close #
    def done(self, result: int) -> None:                     # noqa: A003
        for thread in list(self._threads):
            try:
                if thread.isRunning():
                    thread.quit()
                    thread.wait(3000)
            except RuntimeError:
                pass
        self._threads.clear()
        super().done(result)
