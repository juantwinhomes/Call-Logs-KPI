"""About: version, where files live, and the app-update check (requirement 23)."""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import urllib.request

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from ui.widgets import Card
from ui.workers import run_task
from utils.config import (APP_NAME, APP_SLUG, APP_VERSION, data_dir, db_path, is_frozen, log_dir,
                          token_store_path)
from utils.logger import get_logger

log = get_logger("about")

# Optional: an internal endpoint returning {"version": "1.1.0", "url": "...", "notes": "..."}.
# Left blank so a fresh install never phones home. Set it in the environment as
# MGS_UPDATE_URL, or edit this constant when an internal endpoint exists.
import os
UPDATE_URL = os.environ.get("MGS_UPDATE_URL", "").strip()


class AboutPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._threads: list[object] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 20)
        outer.setSpacing(14)

        title = QLabel("About")
        title.setObjectName("SectionTitle")
        outer.addWidget(title)

        info = Card(APP_NAME)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        rows = [
            ("Version", APP_VERSION),
            ("Build", "packaged executable" if is_frozen() else "running from source"),
            ("Python", platform.python_version()),
            ("System", f"{platform.system()} {platform.release()}"),
        ]
        for r, (k, v) in enumerate(rows):
            key = QLabel(k)
            key.setObjectName("MetricLabel")
            grid.addWidget(key, r, 0)
            grid.addWidget(QLabel(v), r, 1)
        info.add_layout(grid)
        purpose = QLabel(
            "This application reads a Monday.com board and keeps a Google Sheet in step with it. "
            "It only ever reads from Monday.com and only ever writes to the one worksheet named in "
            "Settings.")
        purpose.setObjectName("Muted")
        purpose.setWordWrap(True)
        info.add(purpose)
        outer.addWidget(info)

        files = Card("Where your files are")
        for label, path in (("Settings and sync state", db_path()),
                            ("Saved credentials (encrypted)", token_store_path()),
                            ("Log files", log_dir()),
                            ("Application data folder", data_dir())):
            row = QHBoxLayout()
            key = QLabel(label)
            key.setObjectName("MetricLabel")
            key.setMinimumWidth(220)
            row.addWidget(key)
            value = QLabel(str(path))
            value.setObjectName("Muted")
            value.setWordWrap(True)
            value.setTextInteractionFlags(value.textInteractionFlags().TextSelectableByMouse)
            row.addWidget(value, 1)
            files.add_layout(row)
        btn_open = QPushButton("Open application data folder")
        btn_open.clicked.connect(self._open_data)
        files.add(btn_open)
        note = QLabel(
            "The Google client_secrets.json file belongs in the application data folder. "
            "Credentials are encrypted with a key held in the Windows Credential Manager.")
        note.setObjectName("Hint")
        note.setWordWrap(True)
        files.add(note)
        outer.addWidget(files)

        upd = Card("Application updates")
        explain = QLabel(
            "This checks whether a newer version of the application itself is available. It is not "
            "the same as Refresh / Check for Updates on the dashboard, which looks for changes in "
            "your Monday.com data.")
        explain.setObjectName("Muted")
        explain.setWordWrap(True)
        upd.add(explain)
        row = QHBoxLayout()
        self.btn_check = QPushButton("Check for App Updates")
        self.btn_check.clicked.connect(self._check_updates)
        row.addWidget(self.btn_check)
        row.addStretch(1)
        upd.add_layout(row)
        self.lbl_update = QLabel(
            "No update server is configured on this installation, so this will report that there "
            "is nothing to check." if not UPDATE_URL else f"Update source: {UPDATE_URL}")
        self.lbl_update.setObjectName("Hint")
        self.lbl_update.setWordWrap(True)
        upd.add(self.lbl_update)
        outer.addWidget(upd)
        outer.addStretch(1)

    def _open_data(self) -> None:
        path = data_dir()
        if sys.platform.startswith("win"):
            try:
                subprocess.Popen(["explorer", str(path)],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return
            except OSError:
                pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _check_updates(self) -> None:
        if not UPDATE_URL:
            QMessageBox.information(
                self, "No update server",
                "No application update server is configured on this installation.\n\n"
                f"You are running version {APP_VERSION}. Ask whoever supplied the application "
                "whether a newer build exists.")
            return
        self.btn_check.setEnabled(False)
        self.btn_check.setText("Checking...")

        def fetch() -> str:
            req = urllib.request.Request(UPDATE_URL, headers={
                "User-Agent": f"{APP_SLUG}/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=15) as resp:   # noqa: S310 - fixed URL
                payload = json.loads(resp.read().decode("utf-8"))
            latest = str(payload.get("version") or "").strip()
            if not latest:
                return "The update server did not report a version."
            if _newer(latest, APP_VERSION):
                url = payload.get("url") or ""
                notes = payload.get("notes") or ""
                return (f"Version {latest} is available (you have {APP_VERSION}).\n\n"
                        f"{notes}\n\n{url}".strip())
            return f"You are up to date. Version {APP_VERSION} is the latest."

        def restore() -> None:
            self.btn_check.setEnabled(True)
            self.btn_check.setText("Check for App Updates")

        def ok(message: str) -> None:
            restore()
            QMessageBox.information(self, "Application updates", message)

        def failed(_message: str, _detail: str) -> None:
            restore()
            QMessageBox.warning(self, "Could not check",
                                "The update server could not be reached. This does not affect "
                                "synchronising your data.")

        self._threads.append(run_task(fetch, ok, failed))


def _newer(candidate: str, current: str) -> bool:
    """Compare dotted versions numerically, ignoring any suffix."""
    def parts(v: str) -> list[int]:
        out: list[int] = []
        for chunk in v.split("."):
            digits = "".join(ch for ch in chunk if ch.isdigit())
            out.append(int(digits) if digits else 0)
        return out
    a, b = parts(candidate), parts(current)
    width = max(len(a), len(b))
    a += [0] * (width - len(a))
    b += [0] * (width - len(b))
    return a > b
