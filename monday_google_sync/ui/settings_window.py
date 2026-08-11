"""Settings: choose the Monday board, the Google spreadsheet and the column map."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from database.models import ColumnMapping, SettingsStore, SyncConfig, SyncedItemRepo, Checkpoint
from services.auth_service import AuthService
from services.google_sheets_service import GoogleSheetsService, extract_spreadsheet_id
from services.monday_service import (ITEM_ID_COLUMN_ID, MondayColumn, MondayService,
                                     NAME_COLUMN_ID)
from ui.widgets import Card
from ui.workers import CallableWorker, start
from utils.config import TRACK_ITEM_ID, TRACKING_COLUMNS
from utils.logger import get_logger

log = get_logger("settings")

# Sensible starting map for a property pipeline, applied only when the user asks.
SUGGESTED = {
    "name": "Property Name",
    "status": "Status",
    "person": "Assigned To",
    "people": "Assigned To",
    "date": "Due Date",
    "numbers": "Amount",
    "text": "Notes",
    "long_text": "Notes",
    "phone": "Phone",
    "email": "Email",
    "location": "Address",
    "dropdown": "Category",
}


class SettingsPage(QWidget):
    config_saved = Signal()

    def __init__(self, auth: AuthService, settings: SettingsStore) -> None:
        super().__init__()
        self.auth = auth
        self.settings = settings
        self.cfg = SyncConfig.load(settings)
        self._columns: list[MondayColumn] = []
        self._boards: list[Any] = []
        self._worksheets: list[Any] = []
        self._threads: list[Any] = []
        self._monday_ok = False
        self._google_ok = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 20)
        outer.setSpacing(14)

        head = QLabel("Settings")
        head.setObjectName("SectionTitle")
        outer.addWidget(head)
        hint = QLabel("Choose the Monday.com board to read and the Google Sheet to update, then "
                      "map the columns. These are remembered, so this only needs doing once.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        outer.addWidget(self._build_monday())
        outer.addWidget(self._build_google())
        outer.addWidget(self._build_mapping())
        outer.addWidget(self._build_behaviour())
        outer.addWidget(self._build_maintenance())

        actions = QHBoxLayout()
        self.lbl_state = QLabel("")
        self.lbl_state.setObjectName("Hint")
        self.lbl_state.setWordWrap(True)
        actions.addWidget(self.lbl_state, 1)
        self.btn_save = QPushButton("Save Settings")
        self.btn_save.setObjectName("Primary")
        self.btn_save.clicked.connect(self._save)
        actions.addWidget(self.btn_save)
        outer.addLayout(actions)
        outer.addStretch(1)

        self.reload()

    # ------------------------------------------------------------------ build #
    def _build_monday(self) -> QWidget:
        card = Card("Monday.com")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        grid.addWidget(QLabel("Workspace"), 0, 0)
        self.cb_workspace = QComboBox()
        self.cb_workspace.setMinimumWidth(280)
        self.cb_workspace.currentIndexChanged.connect(self._on_workspace_changed)
        grid.addWidget(self.cb_workspace, 0, 1)
        self.btn_load_ws = QPushButton("Load workspaces")
        self.btn_load_ws.clicked.connect(self._load_workspaces)
        grid.addWidget(self.btn_load_ws, 0, 2)

        grid.addWidget(QLabel("Board"), 1, 0)
        self.cb_board = QComboBox()
        self.cb_board.setMinimumWidth(280)
        self.cb_board.currentIndexChanged.connect(self._on_board_changed)
        grid.addWidget(self.cb_board, 1, 1)
        self.btn_load_boards = QPushButton("Load boards")
        self.btn_load_boards.clicked.connect(self._load_boards)
        grid.addWidget(self.btn_load_boards, 1, 2)

        grid.addWidget(QLabel("Board ID"), 2, 0)
        self.ed_board_id = QLineEdit()
        self.ed_board_id.setPlaceholderText("e.g. 18421423765 - filled in automatically, or type it")
        grid.addWidget(self.ed_board_id, 2, 1)
        self.btn_load_cols = QPushButton("Load columns")
        self.btn_load_cols.clicked.connect(self._load_columns)
        grid.addWidget(self.btn_load_cols, 2, 2)
        card.add_layout(grid)
        return card

    def _build_google(self) -> QWidget:
        card = Card("Google Sheets")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        grid.addWidget(QLabel("Spreadsheet link or ID"), 0, 0)
        self.ed_sheet_id = QLineEdit()
        self.ed_sheet_id.setPlaceholderText("Paste the full https://docs.google.com/... link")
        self.ed_sheet_id.editingFinished.connect(self._normalise_sheet_id)
        grid.addWidget(self.ed_sheet_id, 0, 1)
        self.btn_load_tabs = QPushButton("Open spreadsheet")
        self.btn_load_tabs.clicked.connect(self._load_worksheets)
        grid.addWidget(self.btn_load_tabs, 0, 2)

        grid.addWidget(QLabel("Worksheet tab"), 1, 0)
        self.cb_worksheet = QComboBox()
        self.cb_worksheet.setEditable(True)
        self.cb_worksheet.setMinimumWidth(280)
        grid.addWidget(self.cb_worksheet, 1, 1)
        self.lbl_sheet_name = QLabel("")
        self.lbl_sheet_name.setObjectName("Hint")
        grid.addWidget(self.lbl_sheet_name, 1, 2)
        card.add_layout(grid)

        note = QLabel(
            "The connected Google account needs edit access to this spreadsheet. The app keeps "
            f"five tracking columns up to date so it can recognise rows it already wrote: "
            f"{', '.join(TRACKING_COLUMNS)}. They are added automatically if missing.")
        note.setObjectName("Hint")
        note.setWordWrap(True)
        card.add(note)
        return card

    def _build_mapping(self) -> QWidget:
        card = Card("Column mappings")
        info = QLabel("Tick the Monday.com columns to monitor and give each one the worksheet "
                      "heading it belongs in. A heading that does not exist yet is created.")
        info.setObjectName("Hint")
        info.setWordWrap(True)
        card.add(info)

        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["Monitor", "Monday.com column", "Type",
                                            "Google Sheets heading"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                 | QAbstractItemView.EditTrigger.SelectedClicked
                                 | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.tbl.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl.setMinimumHeight(240)
        self.tbl.itemChanged.connect(self._update_map_count)
        card.add(self.tbl)

        row = QHBoxLayout()
        b1 = QPushButton("Suggest headings")
        b1.setToolTip("Fill blank headings with a sensible name based on the column type.")
        b1.clicked.connect(self._suggest)
        b2 = QPushButton("Select all")
        b2.clicked.connect(lambda: self._set_all_checked(True))
        b3 = QPushButton("Select none")
        b3.clicked.connect(lambda: self._set_all_checked(False))
        for b in (b1, b2, b3):
            row.addWidget(b)
        row.addStretch(1)
        self.lbl_map_count = QLabel("")
        self.lbl_map_count.setObjectName("Hint")
        row.addWidget(self.lbl_map_count)
        card.add_layout(row)
        return card

    def _build_behaviour(self) -> QWidget:
        card = Card("When Refresh finds changes")
        self.chk_auto = QCheckBox("Automatically write detected updates to Google Sheets")
        self.chk_auto.setToolTip(
            "On: Refresh writes the changes straight away.\n"
            "Off: Refresh shows what it found and waits for you to approve it.")
        card.add(self.chk_auto)
        note = QLabel("Leaving this off is the safer choice while you are getting used to the app: "
                      "Refresh will list what it found and nothing is written until you press "
                      "Apply Updates.")
        note.setObjectName("Hint")
        note.setWordWrap(True)
        card.add(note)
        return card

    def _build_maintenance(self) -> QWidget:
        card = Card("Maintenance")
        row = QHBoxLayout()
        btn_reset = QPushButton("Re-check every item on the next refresh")
        btn_reset.setToolTip("Clears the saved sync point. Nothing is deleted from the "
                             "spreadsheet — rows are matched by Monday Item ID as usual.")
        btn_reset.clicked.connect(self._reset_checkpoint)
        row.addWidget(btn_reset)
        btn_forget = QPushButton("Forget local sync state for this board")
        btn_forget.setObjectName("Danger")
        btn_forget.setToolTip("Clears what the app remembers about items it has written. "
                              "The spreadsheet is not changed.")
        btn_forget.clicked.connect(self._forget_state)
        row.addWidget(btn_forget)
        row.addStretch(1)
        card.add_layout(row)
        self.lbl_state_count = QLabel("")
        self.lbl_state_count.setObjectName("Hint")
        card.add(self.lbl_state_count)
        return card

    # ------------------------------------------------------------------ state #
    def set_connection_state(self, monday_ok: bool, google_ok: bool) -> None:
        self._monday_ok, self._google_ok = monday_ok, google_ok
        for w in (self.btn_load_ws, self.btn_load_boards, self.btn_load_cols):
            w.setEnabled(monday_ok)
        self.btn_load_tabs.setEnabled(google_ok)
        bits = []
        if not monday_ok:
            bits.append("Connect Monday.com to load workspaces, boards and columns.")
        if not google_ok:
            bits.append("Connect Google Sheets to open a spreadsheet.")
        self.lbl_state.setText(" ".join(bits))

    def reload(self) -> None:
        self.cfg = SyncConfig.load(self.settings)
        cfg = self.cfg
        self.ed_board_id.setText(cfg.board_id)
        self.ed_sheet_id.setText(cfg.spreadsheet_id)
        self.lbl_sheet_name.setText(cfg.spreadsheet_name)
        if cfg.worksheet and self.cb_worksheet.findText(cfg.worksheet) < 0:
            self.cb_worksheet.addItem(cfg.worksheet)
        self.cb_worksheet.setCurrentText(cfg.worksheet)
        self.chk_auto.setChecked(cfg.auto_write)
        if cfg.board_name and self.cb_board.count() == 0:
            self.cb_board.addItem(f"{cfg.board_name} ({cfg.board_id})", cfg.board_id)
        if not self._columns and cfg.mappings:
            # Show the saved mapping even before the board columns are fetched.
            self._columns = [MondayColumn(m.monday_id, m.monday_title or m.monday_id, "")
                             for m in cfg.mappings]
        self._render_table()
        repo = SyncedItemRepo()
        self.lbl_state_count.setText(
            f"The app remembers {repo.count(cfg.board_id)} item(s) for this board "
            f"({repo.count()} across all boards).")

    # ------------------------------------------------------------- background #
    def _run(self, fn, on_ok, busy_widget: QPushButton | None = None,
             busy_text: str = "Working...") -> None:
        original = busy_widget.text() if busy_widget else ""
        if busy_widget:
            busy_widget.setEnabled(False)
            busy_widget.setText(busy_text)

        def restore() -> None:
            if busy_widget:
                busy_widget.setEnabled(True)
                busy_widget.setText(original)

        worker = CallableWorker(fn)
        worker.ok.connect(lambda payload: (restore(), on_ok(payload)))
        worker.failed.connect(lambda msg, _d: (restore(),
                                               QMessageBox.warning(self, "Could not load", msg)))
        thread = start(worker)
        self._threads.append(thread)
        thread.finished.connect(lambda: self._threads.remove(thread)
                                if thread in self._threads else None)

    def _monday(self) -> MondayService:
        return MondayService(self.auth.monday)

    def _sheets(self) -> GoogleSheetsService:
        return GoogleSheetsService(self.auth.google)

    # ------------------------------------------------------------ monday load #
    def _load_workspaces(self) -> None:
        holder: dict[str, Any] = {}

        def fetch() -> str:
            holder["items"] = self._monday().workspaces()
            return "ok"

        def done(_s: str) -> None:
            items = holder.get("items") or []
            self.cb_workspace.blockSignals(True)
            self.cb_workspace.clear()
            self.cb_workspace.addItem("All workspaces", "")
            for ws in items:
                self.cb_workspace.addItem(f"{ws.name}", ws.id)
            if self.cfg.workspace_id:
                idx = self.cb_workspace.findData(self.cfg.workspace_id)
                if idx >= 0:
                    self.cb_workspace.setCurrentIndex(idx)
            self.cb_workspace.blockSignals(False)
            self.lbl_state.setText(f"Loaded {len(items)} workspace(s).")

        self._run(fetch, done, self.btn_load_ws, "Loading...")

    def _on_workspace_changed(self) -> None:
        if self._monday_ok and self.cb_workspace.count():
            self._load_boards()

    def _load_boards(self) -> None:
        workspace = self.cb_workspace.currentData() or ""
        holder: dict[str, Any] = {}

        def fetch() -> str:
            holder["items"] = self._monday().boards(workspace or None)
            return "ok"

        def done(_s: str) -> None:
            self._boards = holder.get("items") or []
            self.cb_board.blockSignals(True)
            self.cb_board.clear()
            for b in self._boards:
                label = f"{b.name} ({b.item_count} items)" if b.item_count else b.name
                self.cb_board.addItem(label, b.id)
            idx = self.cb_board.findData(self.ed_board_id.text().strip())
            if idx >= 0:
                self.cb_board.setCurrentIndex(idx)
            self.cb_board.blockSignals(False)
            self.lbl_state.setText(f"Loaded {len(self._boards)} board(s). Choose one, then "
                                   "Load columns.")

        self._run(fetch, done, self.btn_load_boards, "Loading...")

    def _on_board_changed(self) -> None:
        board_id = self.cb_board.currentData()
        if board_id:
            self.ed_board_id.setText(str(board_id))

    def _load_columns(self) -> None:
        board_id = self.ed_board_id.text().strip()
        if not board_id:
            QMessageBox.information(self, "Board needed",
                                    "Choose a board, or type its Board ID, first.")
            return
        holder: dict[str, Any] = {}

        def fetch() -> str:
            holder["cols"] = self._monday().columns(board_id)
            holder["board"] = self._monday().board(board_id)
            return "ok"

        def done(_s: str) -> None:
            self._columns = holder.get("cols") or []
            board = holder.get("board")
            if board is not None:
                self.cfg.board_name = board.name
                self.cfg.workspace_id = board.workspace_id
                self.cfg.workspace_name = board.workspace_name
                if self.cb_board.findData(board.id) < 0:
                    self.cb_board.addItem(board.name, board.id)
                    self.cb_board.setCurrentIndex(self.cb_board.count() - 1)
            self._render_table()
            self.lbl_state.setText(f"Loaded {len(self._columns)} column(s) from "
                                   f"\"{board.name if board else board_id}\".")

        self._run(fetch, done, self.btn_load_cols, "Loading...")

    # ------------------------------------------------------------ google load #
    def _normalise_sheet_id(self) -> None:
        raw = self.ed_sheet_id.text().strip()
        if not raw:
            return
        found = extract_spreadsheet_id(raw)
        if found and found != raw:
            self.ed_sheet_id.setText(found)

    def _load_worksheets(self) -> None:
        self._normalise_sheet_id()
        sheet_id = self.ed_sheet_id.text().strip()
        if not sheet_id:
            QMessageBox.information(
                self, "Spreadsheet needed",
                "Paste the spreadsheet link from your browser's address bar, or its ID.")
            return
        holder: dict[str, Any] = {}

        def fetch() -> str:
            svc = self._sheets()
            holder["title"] = svc.spreadsheet_title(sheet_id)
            holder["tabs"] = svc.worksheets(sheet_id)
            return "ok"

        def done(_s: str) -> None:
            self._worksheets = holder.get("tabs") or []
            title = holder.get("title") or ""
            self.cfg.spreadsheet_name = title
            self.lbl_sheet_name.setText(title)
            current = self.cb_worksheet.currentText().strip()
            self.cb_worksheet.blockSignals(True)
            self.cb_worksheet.clear()
            for ws in self._worksheets:
                self.cb_worksheet.addItem(ws.title)
            if current:
                idx = self.cb_worksheet.findText(current)
                self.cb_worksheet.setCurrentIndex(idx if idx >= 0 else 0)
            self.cb_worksheet.blockSignals(False)
            self.lbl_state.setText(f'Opened "{title}" with {len(self._worksheets)} tab(s).')

        self._run(fetch, done, self.btn_load_tabs, "Opening...")

    # ---------------------------------------------------------------- mapping #
    def _render_table(self) -> None:
        # Signals are muted while rows are built so _update_map_count is not
        # called once per cell, and reconnected by the caller afterwards.
        self.tbl.blockSignals(True)
        saved = {m.monday_id: m for m in self.cfg.mappings}
        monitored = set(self.cfg.monitored_columns) or set(saved)
        self.tbl.setRowCount(0)
        for col in self._columns:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)

            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            on = col.id in monitored or col.id in saved
            if col.id == NAME_COLUMN_ID and not saved:
                on = True                              # the title is almost always wanted
            chk.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            self.tbl.setItem(r, 0, chk)

            name = QTableWidgetItem(col.title)
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            name.setData(Qt.ItemDataRole.UserRole, col.id)
            self.tbl.setItem(r, 1, name)

            typ = QTableWidgetItem(col.type or "")
            typ.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tbl.setItem(r, 2, typ)

            header_text = saved[col.id].sheet_header if col.id in saved else ""
            self.tbl.setItem(r, 3, QTableWidgetItem(header_text))

        self._ensure_item_id_row(saved)
        self.tbl.blockSignals(False)
        self._update_map_count()

    def _ensure_item_id_row(self, saved: dict[str, ColumnMapping]) -> None:
        """The Monday Item ID mapping is mandatory, so offer it as a fixed row."""
        for r in range(self.tbl.rowCount()):
            item = self.tbl.item(r, 1)
            if item and item.data(Qt.ItemDataRole.UserRole) == ITEM_ID_COLUMN_ID:
                return
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsEnabled)
        chk.setCheckState(Qt.CheckState.Checked)
        chk.setToolTip("Required: this is how the app recognises rows it already wrote.")
        self.tbl.setItem(r, 0, chk)
        name = QTableWidgetItem("Monday Item ID (required)")
        name.setFlags(Qt.ItemFlag.ItemIsEnabled)
        name.setData(Qt.ItemDataRole.UserRole, ITEM_ID_COLUMN_ID)
        self.tbl.setItem(r, 1, name)
        typ = QTableWidgetItem("tracking")
        typ.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.tbl.setItem(r, 2, typ)
        header = QTableWidgetItem(TRACK_ITEM_ID)
        header.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.tbl.setItem(r, 3, header)

    def _set_all_checked(self, on: bool) -> None:
        for r in range(self.tbl.rowCount()):
            item = self.tbl.item(r, 0)
            name = self.tbl.item(r, 1)
            if not item or not name:
                continue
            if name.data(Qt.ItemDataRole.UserRole) == ITEM_ID_COLUMN_ID:
                continue                               # required, cannot be turned off
            item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self._update_map_count()

    def _suggest(self) -> None:
        used: set[str] = set()
        for r in range(self.tbl.rowCount()):
            cell = self.tbl.item(r, 3)
            if cell and cell.text().strip():
                used.add(cell.text().strip().lower())
        for r in range(self.tbl.rowCount()):
            chk, name, typ, head = (self.tbl.item(r, c) for c in range(4))
            if not (chk and name and head):
                continue
            if chk.checkState() != Qt.CheckState.Checked or head.text().strip():
                continue
            col_id = name.data(Qt.ItemDataRole.UserRole)
            if col_id == ITEM_ID_COLUMN_ID:
                continue
            base = ("Property Name" if col_id == NAME_COLUMN_ID
                    else SUGGESTED.get((typ.text() if typ else "").strip(), name.text().strip()))
            candidate, n = base, 2
            while candidate.lower() in used:
                candidate, n = f"{base} {n}", n + 1
            used.add(candidate.lower())
            head.setText(candidate)
        self._update_map_count()

    def _collect(self) -> tuple[list[ColumnMapping], list[str], list[str]]:
        mappings: list[ColumnMapping] = []
        monitored: list[str] = []
        problems: list[str] = []
        seen_headers: dict[str, str] = {}
        for r in range(self.tbl.rowCount()):
            chk, name, _typ, head = (self.tbl.item(r, c) for c in range(4))
            if not (chk and name and head):
                continue
            if chk.checkState() != Qt.CheckState.Checked:
                continue
            col_id = str(name.data(Qt.ItemDataRole.UserRole) or "")
            header = head.text().strip()
            if col_id == ITEM_ID_COLUMN_ID:
                mappings.append(ColumnMapping(ITEM_ID_COLUMN_ID, "Monday Item ID", TRACK_ITEM_ID))
                continue
            if not header:
                problems.append(f'"{name.text()}" is ticked but has no Google Sheets heading.')
                continue
            key = header.lower()
            if key in seen_headers:
                problems.append(f'Two Monday columns both write to "{header}" '
                                f'("{seen_headers[key]}" and "{name.text()}").')
                continue
            seen_headers[key] = name.text()
            mappings.append(ColumnMapping(col_id, name.text(), header))
            monitored.append(col_id)
        return mappings, monitored, problems

    def _update_map_count(self, *_a: Any) -> None:
        mappings, _mon, problems = self._collect()
        text = f"{len(mappings)} column(s) mapped."
        if problems:
            text += "  " + problems[0]
        self.lbl_map_count.setText(text)

    # ------------------------------------------------------------------- save #
    def _save(self) -> None:
        self._normalise_sheet_id()
        mappings, monitored, problems = self._collect()
        if problems:
            QMessageBox.warning(self, "Check the mappings", "\n".join(problems[:6]))
            return

        board_id = self.ed_board_id.text().strip()
        sheet_id = self.ed_sheet_id.text().strip()
        worksheet = self.cb_worksheet.currentText().strip()

        missing = []
        if not board_id:
            missing.append("a Monday.com board")
        if not sheet_id:
            missing.append("a Google spreadsheet")
        if not worksheet:
            missing.append("a worksheet tab")
        if not any(m.sheet_header.strip().lower() == TRACK_ITEM_ID.lower() for m in mappings):
            missing.append(f'the "{TRACK_ITEM_ID}" column')
        real = [m for m in mappings if m.sheet_header.strip().lower() != TRACK_ITEM_ID.lower()]
        if not real:
            missing.append("at least one data column to copy across")
        if missing:
            QMessageBox.warning(self, "Not saved yet",
                                "Still needed: " + ", ".join(missing) + ".")
            return

        board_name = self.cfg.board_name
        for b in self._boards:
            if str(b.id) == board_id:
                board_name = b.name
                break

        cfg = SyncConfig(
            workspace_id=str(self.cb_workspace.currentData() or self.cfg.workspace_id or ""),
            workspace_name=self.cb_workspace.currentText() if self.cb_workspace.currentData() else
            self.cfg.workspace_name,
            board_id=board_id, board_name=board_name,
            monitored_columns=monitored,
            spreadsheet_id=sheet_id,
            spreadsheet_name=self.cfg.spreadsheet_name or self.lbl_sheet_name.text(),
            worksheet=worksheet, mappings=mappings,
            auto_write=self.chk_auto.isChecked())
        cfg.save(self.settings)
        self.cfg = cfg
        self.config_saved.emit()
        QMessageBox.information(
            self, "Settings saved",
            f"Reading board \"{board_name or board_id}\" into \"{worksheet}\".\n\n"
            + ("Refresh will write changes straight away." if cfg.auto_write else
               "Refresh will show what it finds and wait for your approval."))

    # ------------------------------------------------------------ maintenance #
    def _reset_checkpoint(self) -> None:
        if QMessageBox.question(
                self, "Re-check everything?",
                "The next refresh will look at every item on the board instead of only the ones "
                "changed since the last sync.\n\nRows are still matched by Monday Item ID, so this "
                "does not create duplicates. Continue?") != QMessageBox.StandardButton.Yes:
            return
        Checkpoint(self.settings).reset()
        self.config_saved.emit()
        QMessageBox.information(self, "Done", "The next refresh will re-check every item.")

    def _forget_state(self) -> None:
        cfg = SyncConfig.load(self.settings)
        if not cfg.board_id:
            return
        if QMessageBox.question(
                self, "Forget local sync state?",
                "This clears what the app remembers about the items it has written for this "
                "board. The spreadsheet itself is not changed, and the next refresh will compare "
                "against the worksheet again.\n\nContinue?") != QMessageBox.StandardButton.Yes:
            return
        SyncedItemRepo().clear_board(cfg.board_id)
        self.reload()
        QMessageBox.information(self, "Done", "Local sync state cleared for this board.")
