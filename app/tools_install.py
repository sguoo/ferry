"""First-run bootstrap for the external tools yt-dlp needs: ffmpeg (merging/conversion) and deno (the JavaScript
runtime that solves YouTube's player challenges). Missing ones are fetched into DATA_DIR/<tool>/.

    tools_install.ensure()           # from the UI thread after context.init(); no-op when both are present

Progress goes out on context.bus.tool_status(str) ("" once done) and the usual notify toast.
"""

from __future__ import annotations

import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import context, workers, youtube
from .theme import DATA_DIR
from .youtube import Progress, YoutubeError


@dataclass(frozen=True)
class Tool:
    name: str
    url: str
    wanted: tuple[str, ...]             # files to pull out of the archive
    find: Callable[[], Path | None]     # None when the tool has to be fetched
    check: Callable[[Path], str | None]  # version string once installed, None when it does not run

    @property
    def dir(self) -> Path:
        return DATA_DIR / self.name


TOOLS = (
    # yt-dlp's own ffmpeg builds carry patches for the streams yt-dlp produces
    Tool("ffmpeg", "https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
         ("ffmpeg.exe", "ffprobe.exe"), lambda: youtube.find_ffmpeg(context.settings.ffmpeg_path or None), lambda exe: youtube.ffmpeg_version(str(exe))),
    # without a JS runtime yt-dlp cannot use the web clients, which are the only ones that work with a signed-in session
    Tool("deno", "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
         ("deno.exe",), youtube.find_deno, lambda exe: youtube.deno_version(str(exe))),
)

_task: workers.Task | None = None


def ensure() -> bool:
    """Fetch whatever is missing, in the background. True when a download was started."""
    global _task
    missing = [t for t in TOOLS if t.find() is None]
    if _task is not None or not missing:
        return False
    context.bus.tool_status.emit(f"{missing[0].name} 준비 중…")
    _task = workers.Task(_install_all, missing, _emit_progress)
    _task.on(finished=_on_done, failed=_on_failed, progress=_on_progress)
    return True


def _install_all(tools: list[Tool], on_progress) -> list[tuple[Tool, Path]]:
    return [(t, _install(t, on_progress)) for t in tools]


def _install(tool: Tool, on_progress) -> Path:
    tool.dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(tool.url, headers={"User-Agent": "Mozilla/5.0"})
    with tempfile.TemporaryDirectory(prefix=f"ferry-{tool.name}-") as tmp:
        archive = Path(tmp) / "download.zip"
        try:
            with urllib.request.urlopen(req, timeout=30) as resp, archive.open("wb") as out:
                total = int(resp.headers.get("Content-Length") or 0) or None
                done = 0
                while chunk := resp.read(1 << 18):
                    out.write(chunk)
                    done += len(chunk)
                    on_progress(Progress("downloading", done / total * 100 if total else 0.0, done, total, note=tool.name))
        except OSError as exc:
            raise YoutubeError(f"{tool.name} 다운로드 실패", f"{tool.url}\n{exc}") from exc
        on_progress(Progress("converting", 100.0, note=f"{tool.name} 압축 해제 중"))
        try:
            with zipfile.ZipFile(archive) as zf:
                found = {Path(n).name: n for n in zf.namelist() if Path(n).name in tool.wanted}
                if set(found) != set(tool.wanted):
                    raise YoutubeError(f"{tool.name} 압축 파일이 예상과 다릅니다", ", ".join(zf.namelist()[:10]))
                for name, member in found.items():
                    with zf.open(member) as src, (tool.dir / f"{name}.part").open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile as exc:
            raise YoutubeError(f"{tool.name} 압축 파일이 손상되었습니다", str(exc)) from exc
    for name in tool.wanted:
        (tool.dir / f"{name}.part").replace(tool.dir / name)
    exe = tool.dir / tool.wanted[0]
    if tool.check(exe) is None:
        raise YoutubeError(f"내려받은 {tool.name}를 실행할 수 없습니다", str(exe))
    return exe


def _emit_progress(p: Progress) -> None:
    if _task is not None:
        _task.signals.progress.emit(p)


def _on_progress(p: Progress) -> None:
    if p.phase == "downloading":
        mb = p.downloaded / 1e6
        text = f"{p.note} 내려받는 중 {p.percent:.0f}% · {mb:.0f}/{p.total / 1e6:.0f} MB" if p.total else f"{p.note} 내려받는 중 {mb:.0f} MB"
    else:
        text = p.note
    context.bus.tool_status.emit(text)


def _on_done(result: list[tuple[Tool, Path]]) -> None:
    context.bus.tool_status.emit("")
    context.bus.settings_changed.emit()  # screens re-run their tool detection
    names = ", ".join(f"{t.name} {t.check(exe) or ''}".strip() for t, exe in result)
    context.bus.notify.emit("외부 도구 설치 완료", names)


def _on_failed(err: YoutubeError) -> None:
    global _task
    _task = None  # a later ensure() may retry
    context.bus.tool_status.emit("")
    context.bus.notify.emit(err.message, "설정 > 외부 도구에서 경로를 직접 지정할 수도 있습니다.")
