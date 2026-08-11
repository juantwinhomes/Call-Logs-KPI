"""Shows the self-check, and lets the user save or copy it to send on."""
from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from services.diagnostics import FAIL, OK, WARN, Report, run_diagnostics
from ui.style import BAD, GOOD, INK_2, STYLESHEET, WARN as WARN_COLOUR
from ui.workers import run_task
from utils.config import APP_SLUG, data_dir
from utils.logger import get_logger

log = get_logger("diagnostics-ui")


class DiagnosticsDialog(QDialog):
    """Runs every prerequisite check and explains what to do about each failure."""

    def __init__(self, auth: Any, settings: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.settings = settings
        self.report: Report | None = None
        self._threads: list[Any] = []

        self.setWindowTitle("Check my setup")
        self.setMinimumSize(900, 620)
        self.setStyleSheet(STYLESHEET)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        title = QLabel("Check my setup")
        title.setObjectName("SectionTitle")
        outer.addWidget(title)

        self.verdict = QLabel("Running the checks...")
        self.verdict.setObjectName("Timestamp")
        self.verdict.setWordWrap(True)
        outer.addWidget(self.verdict)

        hint = QLabel("Each line is one thing the app needs. Anything marked PROBLEM is stopping "
                      "it from working, and the line underneath says how to fix it. Hover a line "
                      "to read it in full, or use Copy to clipboard to send the whole report on.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Check", "Result"])
        self.tree.setRootIsDecorated(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        outer.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        self.btn_rerun = QPushButton("Run the checks again")
        self.btn_rerun.clicked.connect(self._run)
        buttons.addWidget(self.btn_rerun)
        self.btn_copy = QPushButton("Copy to clipboard")
        self.btn_copy.clicked.connect(self._copy)
        self.btn_copy.setEnabled(False)
        buttons.addWidget(self.btn_copy)
        self.btn_save = QPushButton("Save as a text file...")
        self.btn_save.clicked.connect(self._save)
        self.btn_save.setEnabled(False)
        buttons.addWidget(self.btn_save)
        buttons.addStretch(1)
        btn_close = QPushButton("Close")
        btn_close.setObjectName("Primary")
        btn_close.clicked.connect(self.accept)
        buttons.addWidget(btn_close)
        outer.addLayout(buttons)

        self._run()

    # ------------------------------------------------------------------- run #
    def _run(self) -> None:
        self.btn_rerun.setEnabled(False)
        self.verdict.setText("Running the checks...")
        self.verdict.setStyleSheet("")
        holder: dict[str, Report] = {}

        def work() -> str:
            holder["report"] = run_diagnostics(self.auth, self.settings)
            return "ok"

        def done(_msg: str) -> None:
            self.btn_rerun.setEnabled(True)
            self.report = holder.get("report")
            self._render()

        def failed(message: str, _detail: str) -> None:
            self.btn_rerun.setEnabled(True)
            self.verdict.setText("The checks could not be completed: " + message)
            self.verdict.setStyleSheet(f"color: {BAD};")

        thread = run_task(work, done, failed)
        self._threads.append(thread)
        thread.finished.connect(lambda: self._threads.remove(thread)
                                if thread in self._threads else None)

    def _render(self) -> None:
        report = self.report
        self.tree.clear()
        if report is None:
            return
        colour = BAD if report.problems else WARN_COLOUR if report.warnings else GOOD
        self.verdict.setText(report.verdict)
        self.verdict.setStyleSheet(f"color: {colour};")

        for check in report.checks:
            row = QTreeWidgetItem([check.name, check.state])
            tone = {OK: GOOD, WARN: WARN_COLOUR, FAIL: BAD}.get(check.state)
            if tone:
                row.setForeground(1, QColor(tone))
            if check.state != OK:
                font = QFont(row.font(0))
                font.setBold(True)
                row.setFont(0, font)
            # A tree cannot wrap, so the full sentence goes in a tooltip as well
            # as into the copyable report - a truncated instruction is no
            # instruction at all.
            row.setToolTip(0, "\n".join(x for x in (check.detail, check.fix) if x))
            if check.detail:
                child = QTreeWidgetItem([check.detail, ""])
                child.setForeground(0, QColor(INK_2))
                child.setToolTip(0, check.detail)
                row.addChild(child)
            if check.fix and check.state != OK:
                fix = QTreeWidgetItem(["\u2192 " + check.fix, ""])
                fix.setForeground(0, QColor(colour if check.state == FAIL else WARN_COLOUR))
                fix.setToolTip(0, check.fix)
                row.addChild(fix)
            self.tree.addTopLevelItem(row)
            if check.state != OK:
                row.setExpanded(True)

        self.btn_copy.setEnabled(True)
        self.btn_save.setEnabled(True)

    # ------------------------------------------------------------------ share #
    def _copy(self) -> None:
        if self.report is None:
            return
        QGuiApplication.clipboard().setText(self.report.as_text())
        self.btn_copy.setText("Copied")
        self.btn_copy.setEnabled(False)

    def _save(self) -> None:
        if self.report is None:
            return
        default = str(data_dir() / f"{APP_SLUG}-diagnostics.txt")
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save the diagnostic report", default, "Text files (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.report.as_text())
        except OSError as exc:
            QMessageBox.warning(self, "Could not save",
                                f"The file could not be written: {exc.strerror or exc}")
            return
        QMessageBox.information(self, "Saved",
                                f"Report written to:\n{path}\n\nIt contains no passwords or "
                                "tokens, so it is safe to email.")

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
