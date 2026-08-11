"""Logs screen: shows app.log and opens the folder it lives in."""
from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from ui.widgets import Card
from utils.config import log_dir
from utils.logger import LOG_FILE_NAME, read_log_tail


class LogsPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 20)
        outer.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("Logs")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.chk_errors = QCheckBox("Only warnings and errors")
        self.chk_errors.stateChanged.connect(self.reload)
        head.addWidget(self.chk_errors)
        btn_reload = QPushButton("Reload")
        btn_reload.clicked.connect(self.reload)
        head.addWidget(btn_reload)
        btn_open = QPushButton("Open log folder")
        btn_open.clicked.connect(self._open_folder)
        head.addWidget(btn_open)
        outer.addLayout(head)

        hint = QLabel(
            f"The technical record of everything the app does, written to {LOG_FILE_NAME} in the "
            "folder below. Access tokens and passwords are never written to it, so this file is "
            "safe to send to whoever supports the app.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self.path_label = QLabel(str(log_dir()))
        self.path_label.setObjectName("Hint")
        self.path_label.setTextInteractionFlags(
            self.path_label.textInteractionFlags().TextSelectableByMouse)
        outer.addWidget(self.path_label)

        card = Card("app.log")
        self.view = QPlainTextEdit()
        self.view.setObjectName("LogView")
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setMinimumHeight(420)
        card.add(self.view)
        outer.addWidget(card, 1)

    def reload(self) -> None:
        text = read_log_tail(1200)
        if self.chk_errors.isChecked():
            keep = [ln for ln in text.splitlines()
                    if " WARNING " in ln or " ERROR " in ln or " CRITICAL " in ln]
            text = "\n".join(keep) or "No warnings or errors in the recent log."
        self.view.setPlainText(text)
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _open_folder(self) -> None:
        path = log_dir()
        if sys.platform.startswith("win"):
            try:
                subprocess.Popen(["explorer", str(path)],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return
            except OSError:
                pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
