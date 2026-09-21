"""Run blocking `app.youtube` calls off the UI thread and report back via signals.

    task = run(youtube.fetch_info, url)
    task.finished.connect(lambda info: screen.show_video(info))
    task.failed.connect(lambda err: screen.show_error(err.message))

    job = DownloadTask(url, options)
    job.progress.connect(row.update_progress)   # Progress dataclass
    job.finished.connect(row.mark_done)         # Path
    job.start()
    job.cancel()                                # from any thread
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal

from . import youtube
from .youtube import DownloadOptions, Progress, YoutubeError

_pool = QThreadPool.globalInstance()
_pool.setMaxThreadCount(12)  # downloads are throttled by JobManager, not the pool
_live: set["Task"] = set()


class _Signals(QObject):
    finished = Signal(object)   # result of fn
    failed = Signal(object)     # YoutubeError
    progress = Signal(object)   # youtube.Progress (downloads only)


class Task(QRunnable):
    """One blocking call on the global thread pool."""

    def __init__(self, fn: Callable[..., Any], *args, **kwargs):
        super().__init__()
        # kept alive in _live until its result has been delivered on the UI thread
        self.setAutoDelete(False)
        self._fn, self._args, self._kwargs = fn, args, kwargs
        self.signals = _Signals()
        self.finished = self.signals.finished
        self.failed = self.signals.failed
        self.progress = self.signals.progress

    def run(self) -> None:
        try:
            try:
                result = self._fn(*self._args, **self._kwargs)
            except YoutubeError as exc:
                self.signals.failed.emit(exc)
            except Exception as exc:  # keep the UI alive on unexpected backend errors
                self.signals.failed.emit(YoutubeError(str(exc) or exc.__class__.__name__, traceback.format_exc()))
            else:
                self.signals.finished.emit(result)
        except RuntimeError:
            pass  # app shut down while this task was still running; nobody is listening

    def start(self) -> "Task":
        _live.add(self)
        self.signals.finished.connect(self._release, Qt.ConnectionType.QueuedConnection)
        self.signals.failed.connect(self._release, Qt.ConnectionType.QueuedConnection)
        _pool.start(self)
        return self

    def _release(self, _result=None) -> None:
        _live.discard(self)

    def on(self, finished: Callable | None = None, failed: Callable | None = None, progress: Callable | None = None) -> "Task":
        """Connect callbacks that run on the UI thread (queued), then start."""
        if finished:
            self.signals.finished.connect(finished, Qt.ConnectionType.QueuedConnection)
        if failed:
            self.signals.failed.connect(failed, Qt.ConnectionType.QueuedConnection)
        if progress:
            self.signals.progress.connect(progress, Qt.ConnectionType.QueuedConnection)
        return self.start()


def run(fn: Callable[..., Any], *args, **kwargs) -> Task:
    """Start `fn(*args, **kwargs)` in the background. Connect signals before the pool picks it up
    (connect right after the call returns; the task cannot finish before control returns to the event loop)."""
    return Task(fn, *args, **kwargs).start()


def call(fn: Callable[..., Any], *args, finished: Callable | None = None, failed: Callable | None = None, **kwargs) -> Task:
    """`run` with UI-thread callbacks attached before start (the safe way to touch widgets)."""
    return Task(fn, *args, **kwargs).on(finished=finished, failed=failed)


class DownloadTask(Task):
    """A cancellable download that streams `Progress` updates to the UI thread."""

    def __init__(self, url: str, options: DownloadOptions | None = None):
        self._cancel = threading.Event()
        super().__init__(youtube.download, url, options, self._emit_progress, self._cancel)
        self.url = url
        self.options = options or DownloadOptions()

    def _emit_progress(self, p: Progress) -> None:
        self.signals.progress.emit(p)

    def cancel(self) -> None:
        """Stops after the current fragment; the partial file stays so a retry resumes."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()


def fetch_thumbnail_async(url: str | None, key: str, on_done: Callable[[Path | None], None]) -> Task | None:
    """Cache a YouTube thumbnail and hand its path to on_done on the UI thread."""
    if not url:
        return None
    return call(youtube.fetch_thumbnail, url, key, finished=on_done, failed=lambda _err: on_done(None))


def set_max_parallel(n: int) -> None:
    """Concurrent downloads (matches the settings screen's 동시 다운로드)."""
    _pool.setMaxThreadCount(max(1, n))
