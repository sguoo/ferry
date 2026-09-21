"""Download queue: what is running, what is waiting, and progress for the UI.

`JobManager` owns the concurrency limit (settings 동시 다운로드). Screens create
`Job`s and hand them to `manager.add()`; rows subscribe to `job.changed`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from .workers import DownloadTask
from .youtube import DownloadOptions, Progress, YoutubeError

Status = Literal["queued", "downloading", "merging", "converting", "finished", "paused", "error", "cancelled"]

STATUS_LABEL = {
    "queued": ("대기 중", "neutral"),
    "downloading": ("DOWNLOADING", "success"),
    "merging": ("MERGING", "warn"),
    "converting": ("CONVERTING", "warn"),
    "finished": ("완료", "success"),
    "paused": ("일시중지", "neutral"),
    "error": ("오류", "accent"),
    "cancelled": ("취소됨", "neutral"),
}


class Job(QObject):
    changed = Signal()

    def __init__(self, url: str, title: str, options: DownloadOptions, spec: str = "", duration: str = "", thumb_url: str | None = None, thumb_key: str = ""):
        super().__init__()
        self.url = url
        self.title = title
        self.options = options
        self.spec = spec
        self.duration = duration
        self.thumb_url = thumb_url
        self.thumb_key = thumb_key or url
        self.status: Status = "queued"
        self.progress: Progress | None = None
        self.error: str = ""
        self.path: Path | None = None
        self.created = time.time()
        self._task: DownloadTask | None = None

    # ---------------------------------------------------------------- state
    @property
    def active(self) -> bool:
        return self.status in ("downloading", "merging", "converting")

    @property
    def speed(self) -> float:
        return (self.progress.speed or 0.0) if (self.progress and self.status == "downloading") else 0.0

    @property
    def percent(self) -> float:
        if self.status == "finished":
            return 100.0
        return self.progress.percent if self.progress else 0.0

    # -------------------------------------------------------------- control
    def _start(self) -> None:
        self.status = "downloading"
        self.error = ""
        self._task = DownloadTask(self.url, self.options)
        self._task.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self._task.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        self._task.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)
        self._task.start()
        self.changed.emit()

    def pause(self) -> None:
        """Stop after the current fragment; resume() picks the .part file back up."""
        if self.active and self._task:
            self.status = "paused"
            self._task.cancel()
            self.changed.emit()

    def cancel(self) -> None:
        if self.active and self._task:
            self._task.cancel()
        self.status = "cancelled"
        self.changed.emit()

    def _on_progress(self, p: Progress) -> None:
        if self.status in ("paused", "cancelled"):
            return
        self.progress = p
        if p.phase in ("merging", "converting", "downloading"):
            self.status = p.phase  # type: ignore[assignment]
        self.changed.emit()

    def _on_finished(self, path) -> None:
        self.status = "finished"
        self.path = Path(path)
        self.changed.emit()

    def _on_failed(self, err: YoutubeError) -> None:
        if self.status in ("paused", "cancelled"):
            self.changed.emit()
            return
        self.status = "error"
        self.error = err.message
        self.changed.emit()


class JobManager(QObject):
    added = Signal(object)
    removed = Signal(object)
    changed = Signal(object)      # any job's state changed
    finished = Signal(object)     # a job completed successfully
    speed_changed = Signal(float)  # bytes/s summed over active jobs

    def __init__(self, max_parallel: int = 4):
        super().__init__()
        self.jobs: list[Job] = []
        self.max_parallel = max(1, max_parallel)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._emit_speed)
        self._tick.start()

    # -------------------------------------------------------------- queries
    @property
    def active(self) -> list[Job]:
        return [j for j in self.jobs if j.active]

    @property
    def queued(self) -> list[Job]:
        return [j for j in self.jobs if j.status == "queued"]

    @property
    def total_speed(self) -> float:
        return sum(j.speed for j in self.jobs)

    # ------------------------------------------------------------- mutation
    def add(self, job: Job, immediately: bool = False) -> Job:
        job.changed.connect(lambda j=job: self._on_job_changed(j))
        self.jobs.insert(0, job)
        self.added.emit(job)
        if immediately or len(self.active) < self.max_parallel:
            job._start()
        return job

    def remove(self, job: Job) -> None:
        if job.active:
            job.cancel()
        if job in self.jobs:
            self.jobs.remove(job)
            self.removed.emit(job)
        self._pump()

    def resume(self, job: Job) -> None:
        if job.status in ("paused", "error", "cancelled"):
            job.status = "queued"
            job.changed.emit()
            self._pump()

    def start_now(self, job: Job) -> None:
        """Bypass the queue for one job (user pressed play on it)."""
        if job in self.jobs and not job.active and job.status != "finished":
            job._start()

    def pause_all(self) -> None:
        for j in self.active:
            j.pause()

    def clear_done(self) -> None:
        for j in [j for j in self.jobs if j.status in ("finished", "cancelled")]:
            self.remove(j)

    def set_max_parallel(self, n: int) -> None:
        self.max_parallel = max(1, n)
        self._pump()

    # ------------------------------------------------------------- internal
    def _pump(self) -> None:
        free = self.max_parallel - len(self.active)
        for j in reversed(self.queued):  # oldest first
            if free <= 0:
                break
            j._start()
            free -= 1

    def _on_job_changed(self, job: Job) -> None:
        self.changed.emit(job)
        if job.status == "finished":
            self.finished.emit(job)
        if not job.active:
            self._pump()

    def _emit_speed(self) -> None:
        self.speed_changed.emit(self.total_speed)
