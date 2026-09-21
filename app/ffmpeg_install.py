"""First-run ffmpeg bootstrap: fetch a Windows build into DATA_DIR/ffmpeg when none is on PATH.

    ffmpeg_install.ensure()          # from the UI thread after context.init(); no-op when ffmpeg exists
    ffmpeg_install.MANAGED_DIR       # where the managed ffmpeg.exe / ffprobe.exe live (find_ffmpeg checks it)

Progress goes out on context.bus.ffmpeg_status(str) ("" once done) and the usual notify toast.
"""

from __future__ import annotations

import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import context, workers, youtube
from .theme import DATA_DIR
from .youtube import Progress, YoutubeError

# yt-dlp's own ffmpeg builds carry patches for the streams yt-dlp produces
DOWNLOAD_URL = "https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
MANAGED_DIR = DATA_DIR / "ffmpeg"
WANTED = ("ffmpeg.exe", "ffprobe.exe")

_task: workers.Task | None = None


def ensure() -> bool:
    """Start the download in the background if ffmpeg is missing. True when a download was started."""
    global _task
    if _task is not None or youtube.find_ffmpeg(context.settings.ffmpeg_path or None) is not None:
        return False
    context.bus.ffmpeg_status.emit("ffmpeg 준비 중…")
    _task = workers.Task(_install, _emit_progress)
    _task.on(finished=_on_done, failed=_on_failed, progress=_on_progress)
    return True


def _install(on_progress) -> Path:
    MANAGED_DIR.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "Mozilla/5.0"})
    with tempfile.TemporaryDirectory(prefix="ferry-ffmpeg-") as tmp:
        archive = Path(tmp) / "ffmpeg.zip"
        try:
            with urllib.request.urlopen(req, timeout=30) as resp, archive.open("wb") as out:
                total = int(resp.headers.get("Content-Length") or 0) or None
                done = 0
                while chunk := resp.read(1 << 18):
                    out.write(chunk)
                    done += len(chunk)
                    on_progress(Progress("downloading", done / total * 100 if total else 0.0, done, total))
        except OSError as exc:
            raise YoutubeError("ffmpeg 다운로드 실패", f"{DOWNLOAD_URL}\n{exc}") from exc
        on_progress(Progress("converting", 100.0, note="압축 해제 중"))
        try:
            with zipfile.ZipFile(archive) as zf:
                found = {Path(n).name: n for n in zf.namelist() if Path(n).name in WANTED}
                if set(found) != set(WANTED):
                    raise YoutubeError("ffmpeg 압축 파일이 예상과 다릅니다", ", ".join(zf.namelist()[:10]))
                for name, member in found.items():
                    with zf.open(member) as src, (MANAGED_DIR / f"{name}.part").open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile as exc:
            raise YoutubeError("ffmpeg 압축 파일이 손상되었습니다", str(exc)) from exc
    for name in WANTED:
        (MANAGED_DIR / f"{name}.part").replace(MANAGED_DIR / name)
    exe = MANAGED_DIR / "ffmpeg.exe"
    if youtube.ffmpeg_version(str(exe)) is None:
        raise YoutubeError("내려받은 ffmpeg를 실행할 수 없습니다", str(exe))
    return exe


def _emit_progress(p: Progress) -> None:
    if _task is not None:
        _task.signals.progress.emit(p)


def _on_progress(p: Progress) -> None:
    if p.phase == "downloading":
        mb = p.downloaded / 1e6
        text = f"ffmpeg 내려받는 중 {p.percent:.0f}% · {mb:.0f}/{p.total / 1e6:.0f} MB" if p.total else f"ffmpeg 내려받는 중 {mb:.0f} MB"
    else:
        text = f"ffmpeg {p.note}"
    context.bus.ffmpeg_status.emit(text)


def _on_done(exe: Path) -> None:
    context.bus.ffmpeg_status.emit("")
    context.bus.settings_changed.emit()  # screens re-run their ffmpeg detection
    context.bus.notify.emit("ffmpeg 설치 완료", f"ffmpeg {youtube.ffmpeg_version(str(exe)) or ''} · {MANAGED_DIR}")


def _on_failed(err: YoutubeError) -> None:
    global _task
    _task = None  # a later ensure() may retry
    context.bus.ffmpeg_status.emit("")
    context.bus.notify.emit(err.message, "설정 > 외부 도구에서 ffmpeg 경로를 직접 지정할 수도 있습니다.")
