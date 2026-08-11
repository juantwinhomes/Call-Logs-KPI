"""Shared test setup.

pytest imports conftest before any test module, so this is the one place that can
point the application at a throwaway data directory before `utils.config` is
first read. Setting it per module does not work: every module is imported before
the first test runs, so the last import would win and the other modules would be
reading a directory their fixtures do not clean.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Must happen before anything imports utils.config.
_DATA_DIR = Path(tempfile.mkdtemp(prefix="mgs-tests-"))
os.environ["MGS_DATA_DIR"] = str(_DATA_DIR)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Keep the real OS keyring out of the tests: the encrypted store falls back to a
# key file inside the throwaway directory instead of touching the developer's
# credential manager.
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return _DATA_DIR


@pytest.fixture(autouse=True)
def clean_db():
    """Every test starts from an empty database."""
    from database import database as db

    db.close_thread_connection()
    db._initialised = False
    base = _DATA_DIR / "sync_state.sqlite3"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(base) + suffix)
        if p.exists():
            p.unlink()
    db.ensure_schema()
    yield
    db.close_thread_connection()


@pytest.fixture
def pump(app):
    """Pump the Qt event loop until a condition holds, or give up.

    The dialogs do their Drive and diagnostic work on worker threads, so a tight
    processEvents() loop can spin without ever letting the worker finish. This
    sleeps between passes so the worker actually gets scheduled.
    """
    import time

    def wait_for(condition, timeout: float = 10.0, interval: float = 0.02) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if condition():
                app.processEvents()
                return True
            time.sleep(interval)
        app.processEvents()
        return bool(condition())

    return wait_for


@pytest.fixture(scope="session")
def app():
    """One QApplication for the whole session; Qt allows only one."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()
