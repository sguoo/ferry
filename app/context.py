"""Process-wide objects shared by the screens. `init()` runs once in main()."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from . import settings as settings_mod
from .jobs import JobManager
from .settings import Settings


class _Bus(QObject):
    settings_changed = Signal()
    library_changed = Signal()   # a download finished / folder rescanned
    notify = Signal(str, str)    # title, body -> tray toast
    tool_status = Signal(str)    # first-run ffmpeg/deno download progress text; '' when idle
    update_status = Signal(str)  # self-update download progress text; '' when idle
    update_ready = Signal(str)   # a newer Ferry.exe is downloaded; version string
    play = Signal(list, int, list)  # items (paths or urls), start index, titles -> in-app player
    view_subtitles = Signal(str)    # a media file (shows its sidecars) or a .srt/.vtt -> subtitle viewer


settings: Settings = Settings()
jobs: JobManager | None = None
bus: _Bus | None = None


def init() -> None:
    global settings, jobs, bus
    settings = settings_mod.load()
    bus = _Bus()
    jobs = JobManager(settings.threads)
    jobs.finished.connect(_on_job_finished)


def save_settings() -> None:
    settings_mod.save(settings)
    if jobs is not None:
        jobs.set_max_parallel(settings.threads)
    bus.settings_changed.emit()


def _on_job_finished(job) -> None:
    bus.library_changed.emit()
    if settings.notify:
        bus.notify.emit("다운로드 완료", job.title)
