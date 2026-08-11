"""Sync History: recent runs and recent errors (requirements 14 and 15)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from database.models import ErrorRepo, SyncRunRepo, to_local_display
from ui.style import BAD, GOOD, INK_2, WARN
from ui.widgets import Card

OUTCOME_TEXT = {
    "success": ("Success", GOOD),
    "warning": ("Warning", WARN),
    "preview": ("Preview", INK_2),
    "error": ("Error", BAD),
    "cancelled": ("Cancelled", WARN),
    "interrupted": ("Interrupted", WARN),
    "running": ("Running", INK_2),
}


class SyncHistoryPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.runs = SyncRunRepo()
        self.errors = ErrorRepo()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 20)
        outer.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("Sync History")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        btn = QPushButton("Reload")
        btn.clicked.connect(self.reload)
        head.addWidget(btn)
        outer.addLayout(head)

        hint = QLabel("Every refresh is recorded here, including the ones that found nothing. "
                      "A run marked Preview detected changes but did not write them.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        runs_card = Card("Recent runs")
        self.tbl = QTableWidget(0, 8)
        self.tbl.setHorizontalHeaderLabels(
            ["When", "Outcome", "Summary", "Added", "Updated", "Skipped", "Duplicates", "Errors"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        h = self.tbl.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for c in range(3, 8):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setMinimumHeight(280)
        runs_card.add(self.tbl)
        outer.addWidget(runs_card)

        err_card = Card("Recent errors")
        self.tbl_err = QTableWidget(0, 3)
        self.tbl_err.setHorizontalHeaderLabels(["When", "Area", "Technical detail"])
        self.tbl_err.verticalHeader().setVisible(False)
        self.tbl_err.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        he = self.tbl_err.horizontalHeader()
        he.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        he.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        he.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tbl_err.setMinimumHeight(160)
        err_card.add(self.tbl_err)
        self.lbl_err = QLabel("")
        self.lbl_err.setObjectName("Hint")
        err_card.add(self.lbl_err)
        outer.addWidget(err_card)
        outer.addStretch(1)

    def reload(self) -> None:
        runs = self.runs.recent(100)
        self.tbl.setRowCount(0)
        for run in runs:
            r = self.tbl.rowCount()
            self.tbl.insertRow(r)
            label, colour = OUTCOME_TEXT.get(run.outcome, (run.outcome.title(), INK_2))
            t = run.totals
            summary = run.message or ""
            if not summary:
                summary = (f"{t.changes} change(s) synchronised." if t.changes
                           else "No updates.")
            cells = [to_local_display(run.started_at), label, summary,
                     str(t.rows_added), str(t.rows_updated), str(t.skipped),
                     str(t.duplicates), str(t.errors)]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c >= 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                if c == 1:
                    item.setForeground(QColor(colour))
                    item.setToolTip(run.message or "")
                elif c == 7 and t.errors:
                    item.setForeground(QColor(BAD))
                self.tbl.setItem(r, c, item)
        if not runs:
            self.tbl.setRowCount(1)
            empty = QTableWidgetItem("No refresh has run on this computer yet.")
            self.tbl.setItem(0, 2, empty)

        errors = self.errors.recent(60)
        self.tbl_err.setRowCount(0)
        for e in errors:
            r = self.tbl_err.rowCount()
            self.tbl_err.insertRow(r)
            self.tbl_err.setItem(r, 0, QTableWidgetItem(to_local_display(e.get("occurred_at"))))
            self.tbl_err.setItem(r, 1, QTableWidgetItem(
                f"{e.get('scope') or ''} / {e.get('code') or ''}"))
            detail = (e.get("message") or "").replace("\n", " ")
            item = QTableWidgetItem(detail[:300])
            item.setToolTip(detail)
            self.tbl_err.setItem(r, 2, item)
        self.lbl_err.setText(
            "No errors recorded." if not errors
            else f"{self.errors.count()} error(s) recorded in total. These are the technical "
                 "details kept for support; the messages shown on the dashboard are the plain-English "
                 "versions.")
