"""Background workers.

Every network call runs off the GUI thread so the window never freezes, and each
worker closes its thread-local SQLite connection when it finishes.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from database.database import close_thread_connection
from services.errors import AppError
from services.sync_service import Stage, SyncPlan, SyncResult
from utils.logger import get_logger

log = get_logger("worker")


class _Base(QObject):
    finished = Signal()

    def _cleanup(self) -> None:
        try:
            close_thread_connection()
        except Exception:
            pass


class CallableWorker(_Base):
    """Runs one function and reports success with a message, or a friendly error."""

    ok = Signal(str)
    failed = Signal(str, str)          # user message, technical detail

    def __init__(self, fn: Callable[[], Any], success_text: str = "") -> None:
        super().__init__()
        self._fn = fn
        self._success_text = success_text

    def run(self) -> None:
        try:
            result = self._fn()
            text = self._success_text or (result if isinstance(result, str) else "Done.")
            self.ok.emit(str(text))
        except AppError as exc:
            log.error("Task failed [%s/%s]: %s", exc.scope, exc.code, exc.detail)
            self.failed.emit(exc.message, exc.detail)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            log.exception("Task raised an unexpected error")
            self.failed.emit("An unexpected error occurred. The details were written to the log "
                             "file, which you can open from the Logs screen.", detail)
        finally:
            self._cleanup()
            self.finished.emit()


class StatusProbeWorker(_Base):
    """Checks both connections without blocking the window (requirement 12)."""

    got = Signal(object, object)        # ConnStatus monday, ConnStatus google

    def __init__(self, auth: Any) -> None:
        super().__init__()
        self._auth = auth

    def run(self) -> None:
        from services.auth_service import ConnState, ConnStatus
        try:
            monday, google = self._auth.statuses()
        except Exception as exc:
            log.exception("Connection probe failed")
            fallback = ConnStatus(ConnState.ERROR, "",
                                  "The connection could not be checked. See the Logs screen.")
            monday = google = fallback
            _ = exc
        try:
            self.got.emit(monday, google)
        finally:
            self._cleanup()
            self.finished.emit()


class SyncWorker(_Base):
    """Runs a refresh, emitting the staged progress text of requirement 5."""

    progress = Signal(str, str)        # stage text, detail
    completed = Signal(object)         # SyncResult

    def __init__(self, service_factory: Callable[[], Any], plan: SyncPlan | None = None,
                 force_preview: bool | None = None) -> None:
        super().__init__()
        self._factory = service_factory
        self._plan = plan
        self._force_preview = force_preview
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _emit(self, stage: Stage, detail: str = "") -> None:
        self.progress.emit(stage.value if isinstance(stage, Stage) else str(stage), detail)

    def run(self) -> None:
        try:
            service = self._factory()
            if self._plan is not None:
                result = service.apply_prepared(self._plan, self._emit, lambda: self._cancelled)
            else:
                result = service.run(self._emit, lambda: self._cancelled,
                                     force_preview=self._force_preview)
            self.completed.emit(result)
        except Exception as exc:                       # the service catches its own, this is belt
            detail = f"{type(exc).__name__}: {exc}"
            log.exception("Sync worker crashed")
            from database.models import RunTotals
            self.completed.emit(SyncResult(
                totals=RunTotals(errors=1), outcome="error",
                message="An unexpected error occurred during the refresh. See the Logs screen.",
                errors=[detail]))
        finally:
            self._cleanup()
            self.finished.emit()


def start(worker: _Base, on_finished: Callable[[], None] | None = None) -> QThread:
    """Move a worker onto a fresh thread and start it. Keeps a reference alive."""
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)          # type: ignore[arg-type]
    worker.finished.connect(thread.quit)
    if on_finished:
        worker.finished.connect(on_finished)
    thread.finished.connect(thread.deleteLater)
    # The worker must outlive the thread's event loop, so it is deleted after.
    thread.finished.connect(worker.deleteLater)
    thread.start()
    return thread
