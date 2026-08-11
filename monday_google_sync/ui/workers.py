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


# Callers hold the QThread but usually let the worker fall out of scope as soon
# as start() returns. Nothing else references it, so Python is free to collect it
# and take the underlying C++ object with it - and then thread.started has nothing
# to invoke, so the task silently never runs and no signal ever arrives. Keeping a
# strong reference here until the thread finishes is what makes a backgrounded
# task reliable rather than a race against the garbage collector.
_LIVE: set[tuple[Any, Any]] = set()


def start(worker: _Base, on_finished: Callable[[], None] | None = None) -> QThread:
    """Move a worker onto a fresh thread and start it.

    The worker and its thread are retained until the thread finishes, so neither
    can be collected mid-run.
    """
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)          # type: ignore[arg-type]
    worker.finished.connect(thread.quit)
    if on_finished:
        worker.finished.connect(on_finished)

    handle = (worker, thread)
    _LIVE.add(handle)

    def release() -> None:
        _LIVE.discard(handle)

    thread.finished.connect(release)
    thread.finished.connect(thread.deleteLater)
    # The worker must outlive the thread's event loop, so it is deleted after.
    thread.finished.connect(worker.deleteLater)
    thread.start()
    return thread


def live_worker_count() -> int:
    """How many background tasks are still held. Used by the tests."""
    return len(_LIVE)


class _Deliverer(QObject):
    """Marshals a worker's result back onto the thread that created this object.

    A signal connected to a plain Python function has no receiver thread, so Qt
    invokes it directly in the *emitting* thread - the worker. Any UI touched by
    such a callback would then be built or mutated off the GUI thread, which is
    undefined behaviour and shows up as an occasional inexplicable crash.

    Connecting to a bound method of a QObject fixes it: the object carries thread
    affinity, so an automatic connection becomes a queued one and the callback
    runs where the object lives. This class exists to be that object.
    """

    def __init__(self, on_ok: Callable[[str], None],
                 on_failed: Callable[[str, str], None] | None = None) -> None:
        super().__init__()
        self._on_ok = on_ok
        self._on_failed = on_failed

    def ok(self, message: str) -> None:
        try:
            self._on_ok(message)
        except Exception:
            log.exception("A task's success handler raised")

    def failed(self, message: str, detail: str) -> None:
        if self._on_failed is None:
            return
        try:
            self._on_failed(message, detail)
        except Exception:
            log.exception("A task's failure handler raised")


def run_task(fn: Callable[[], Any], on_ok: Callable[[str], None],
             on_failed: Callable[[str, str], None] | None = None,
             success_text: str = "") -> QThread:
    """Run `fn` off the GUI thread and deliver the outcome back onto it.

    Call this rather than wiring CallableWorker by hand: it guarantees the
    handlers run on the thread that called it, which for the UI is the only safe
    place to touch a widget.
    """
    worker = CallableWorker(fn, success_text)
    deliverer = _Deliverer(on_ok, on_failed)
    worker.ok.connect(deliverer.ok)
    worker.failed.connect(deliverer.failed)
    thread = start(worker)
    # The deliverer must outlive the run, and nothing else refers to it.
    handle = (deliverer, thread)
    _LIVE.add(handle)
    thread.finished.connect(lambda: _LIVE.discard(handle))
    return thread
