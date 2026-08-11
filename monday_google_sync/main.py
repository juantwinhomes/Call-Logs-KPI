"""Application entry point.

Startup order follows requirement 12: load configuration, load credentials, check
both authentications, show the statuses, restore the Last Checked timestamp, then
wait. Nothing is written to Google Sheets because the app opened.

A single-instance guard (requirement 21) uses a named local socket: a second copy
tells the first to come to the front, then exits.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# When frozen, PyInstaller puts the package root on sys.path already; running from
# source needs the project directory so `import ui...` and `import services...` work.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _excepthook(exc_type, exc, tb) -> None:
    """Log anything that escapes, and tell the user plainly instead of crashing."""
    import traceback
    from utils.logger import get_logger
    log = get_logger("fatal")
    log.critical("Unhandled exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            QMessageBox.critical(
                None, "Unexpected error",
                "Something went wrong that the application did not expect.\n\n"
                "It has been written to the log file, which you can open from the Logs screen. "
                "You can keep using the application; if the problem repeats, send the log to "
                "whoever supports it.")
    except Exception:
        pass


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication, QMessageBox

    from utils.config import APP_NAME, APP_VERSION, IPC_SOCKET_NAME, ORG_NAME, data_dir
    from utils.logger import setup_logging

    log = setup_logging(verbose=bool(os.environ.get("MGS_DEBUG")))
    sys.excepthook = _excepthook

    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationVersion(APP_VERSION)
    QApplication.setOrganizationName(ORG_NAME)
    if hasattr(Qt.ApplicationAttribute, "AA_DontShowIconsInMenus"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
    app = QApplication(sys.argv)

    icon_path = _HERE / "resources" / "app.ico"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    # ---- single instance -------------------------------------------------- #
    probe = QLocalSocket()
    probe.connectToServer(IPC_SOCKET_NAME)
    if probe.waitForConnected(400):
        log.info("Another instance is already running - asking it to come forward")
        probe.write(b"activate")
        probe.waitForBytesWritten(400)
        probe.disconnectFromServer()
        QMessageBox.information(None, APP_NAME,
                                "Monday + Google Sheets Sync is already running.")
        return 0
    # A stale socket file is left behind if a previous run was killed.
    QLocalServer.removeServer(IPC_SOCKET_NAME)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(IPC_SOCKET_NAME):
        log.warning("Could not claim the single-instance socket: %s", server.errorString())

    # ---- startup sequence -------------------------------------------------- #
    log.info("Data directory: %s", data_dir())
    from database.database import ensure_schema
    from database.models import SyncRunRepo
    ensure_schema()
    stale = SyncRunRepo().mark_stale_running()
    if stale:
        log.warning("%d previous run(s) had not finished and were marked interrupted", stale)

    from services.auth_service import AuthService
    from ui.main_window import MainWindow

    auth = AuthService()
    window = MainWindow(auth)

    def on_new_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.readyRead.connect(lambda: (conn.readAll(), window.activated.emit()))
        conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(on_new_connection)

    window.show()
    log.info("Window shown - waiting for the user to press Refresh")
    code = app.exec()
    log.info("Event loop finished with code %d", code)
    try:
        server.close()
        QLocalServer.removeServer(IPC_SOCKET_NAME)
    except Exception:
        pass
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
