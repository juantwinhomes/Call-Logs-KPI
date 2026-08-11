"""Review Changes: what a refresh would write, before anything is written."""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from services.sync_service import SyncPlan
from ui.style import GOOD, INK_3, STYLESHEET, WARN


class PreviewDialog(QDialog):
    """Modal review of a plan. `apply_requested` says which button was pressed."""

    def __init__(self, plan: SyncPlan, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.apply_requested = False

        self.setWindowTitle("Review detected changes")
        self.setMinimumSize(940, 600)
        self.setStyleSheet(STYLESHEET)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        head = QLabel(plan.summary_line())
        head.setObjectName("SectionTitle")
        outer.addWidget(head)

        detail = QLabel(
            f'Board "{plan.config.board_name or plan.config.board_id}" was read and '
            f"{plan.items_considered} of {plan.items_seen} item(s) had changed since the last "
            f'sync. The worksheet "{plan.config.worksheet}" holds {plan.sheet_rows} data row(s). '
            "Nothing below has been written yet.")
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        outer.addWidget(detail)

        for warning in plan.warnings:
            w = QLabel("Note: " + warning)
            w.setWordWrap(True)
            w.setStyleSheet(f"color: {WARN};")
            outer.addWidget(w)

        tabs = QTabWidget()
        tabs.addTab(self._creates_tab(), f"New rows ({len(plan.creates)})")
        tabs.addTab(self._updates_tab(), f"Updates ({len(plan.updates)})")
        tabs.addTab(self._skips_tab(), f"Unchanged ({len(plan.skips)})")
        # Open on the first tab that has something in it, so a run of pure updates
        # does not greet the user with an empty New rows table.
        if not plan.creates and plan.updates:
            tabs.setCurrentIndex(1)
        elif not plan.creates and not plan.updates:
            tabs.setCurrentIndex(2)
        outer.addWidget(tabs, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        btn_close = QPushButton("Close without writing")
        btn_close.clicked.connect(self.reject)
        buttons.addWidget(btn_close)
        btn_apply = QPushButton(f"Apply {plan.total_changes} change(s)")
        btn_apply.setObjectName("Primary")
        btn_apply.setEnabled(plan.has_work)
        btn_apply.clicked.connect(self._apply)
        buttons.addWidget(btn_apply)
        outer.addLayout(buttons)

    def _apply(self) -> None:
        self.apply_requested = True
        self.accept()

    # ------------------------------------------------------------------ tabs #
    def _table(self, headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        h = t.horizontalHeader()
        for c in range(len(headers)):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch if c == len(headers) - 1
                                   else QHeaderView.ResizeMode.ResizeToContents)
        return t

    def _creates_tab(self) -> QWidget:
        cfg = self.plan.config
        data_cols = [m for m in cfg.mappings
                     if m.sheet_header.strip().lower() != "monday item id"]
        headers = ["Monday Item ID", "Item"] + [m.sheet_header for m in data_cols]
        t = self._table(headers)
        for change in self.plan.creates:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, QTableWidgetItem(change.item.id))
            item = QTableWidgetItem(change.label)
            item.setForeground(QColor(GOOD))
            t.setItem(r, 1, item)
            for c, m in enumerate(data_cols, start=2):
                t.setItem(r, c, QTableWidgetItem(change.item.value_for(m.monday_id)))
        return self._wrap(t, "These items are not in the worksheet yet, so a new row is appended "
                             "for each one." if self.plan.creates else "Nothing to add.")

    def _updates_tab(self) -> QWidget:
        t = self._table(["Row", "Monday Item ID", "Item", "Column", "Currently in sheet",
                         "Will become"])
        for change in self.plan.updates:
            for fc in change.changes:
                r = t.rowCount()
                t.insertRow(r)
                t.setItem(r, 0, QTableWidgetItem(str(change.row_number or "")))
                t.setItem(r, 1, QTableWidgetItem(change.item.id))
                t.setItem(r, 2, QTableWidgetItem(change.label))
                t.setItem(r, 3, QTableWidgetItem(fc.header))
                old = QTableWidgetItem(fc.old or "(blank)")
                old.setForeground(QColor(INK_3))
                t.setItem(r, 4, old)
                new = QTableWidgetItem(fc.new or "(blank)")
                new.setForeground(QColor(GOOD))
                t.setItem(r, 5, new)
        note = ("One line per changed cell. Only these cells and the tracking columns are "
                "rewritten — anything else in the row is left exactly as it is."
                if self.plan.updates else "Nothing to update.")
        return self._wrap(t, note)

    def _skips_tab(self) -> QWidget:
        t = self._table(["Row", "Monday Item ID", "Item", "Why it is being skipped"])
        for change in self.plan.skips[:600]:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, QTableWidgetItem(str(change.row_number or "")))
            t.setItem(r, 1, QTableWidgetItem(change.item.id))
            t.setItem(r, 2, QTableWidgetItem(change.label))
            t.setItem(r, 3, QTableWidgetItem(change.reason))
        shown = min(len(self.plan.skips), 600)
        note = (f"Showing {shown} of {len(self.plan.skips)}. These items changed in Monday.com but "
                "none of the monitored columns did, so the worksheet already matches."
                if self.plan.skips else "Nothing was skipped.")
        return self._wrap(t, note)

    def _wrap(self, table: QTableWidget, note: str) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(8)
        lbl = QLabel(note)
        lbl.setObjectName("Hint")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        lay.addWidget(table, 1)
        return page
