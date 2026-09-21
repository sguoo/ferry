"""Self-update from GitHub Releases: on launch, fetch a newer Ferry.exe in the background, then swap it in.

    updater.check()             # from the UI thread after the window is up; no-op in a dev checkout
    updater.ready               # version string once the new exe has been downloaded and verified
    updater.apply(restart=True) # hand over to a helper script that replaces the exe once we exit

Progress goes out on context.bus.update_status(str) ("" when idle); update_ready(version) once downloaded.

Releases must carry the onefile build as an asset named Ferry.exe (or a .zip holding it); the tag is the
version ("v0.2.0"), compared numerically against APP_VERSION.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import APP_NAME, APP_VERSION, context, workers
from .theme import DATA_DIR
from .youtube import Progress, YoutubeError

REPO = "sguoo/ferry"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
UPDATE_DIR = DATA_DIR / "updates"
EXE_NAME = f"{APP_NAME}.exe"

ready: str | None = None        # version waiting in UPDATE_DIR
_task: workers.Task | None = None
_new_exe: Path | None = None
_applied = False               # helper already spawned (the restart button also triggers closeEvent)


def check(force: bool = False) -> bool:
    """Start the background check + download. True when started. Skipped outside a frozen build
    (nothing to replace) or when the setting is off, unless `force`."""
    global _task
    if _task is not None or ready is not None:
        return False
    if not force and (not getattr(sys, "frozen", False) or not context.settings.auto_update):
        return False
    _task = workers.Task(_fetch, _emit_progress)
    _task.on(finished=_on_done, failed=_on_failed, progress=_on_progress)
    return True


def apply(restart: bool = True) -> bool:
    """Spawn the swap helper and ask the app to quit. The helper waits for our exe to unlock,
    moves the new one over it, then relaunches it when `restart`."""
    global _applied
    if _applied:
        return True
    if ready is None or _new_exe is None or not _new_exe.exists():
        return False
    target = Path(sys.executable)
    script = UPDATE_DIR / "apply.bat"
    lines = [
        "@echo off",
        "set n=0",
        ":retry",
        f'move /y "{_new_exe}" "{target}" >nul 2>&1 && goto done',
        "set /a n+=1",
        "if %n% lss 60 (timeout /t 1 /nobreak >nul & goto retry)",
        "exit /b 1",
        ":done",
    ]
    if restart:
        lines.append(f'start "" "{target}"')
    lines.append('del "%~f0"')
    script.write_text("\r\n".join(lines) + "\r\n", encoding="mbcs", errors="replace")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags, close_fds=True, cwd=str(UPDATE_DIR))
    _applied = True
    return True


# --------------------------------------------------------------------------- #
# background work
# --------------------------------------------------------------------------- #


def _version_tuple(tag: str) -> tuple[int, ...] | None:
    m = re.match(r"v?(\d+(?:\.\d+)*)", tag.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _fetch(on_progress) -> tuple[str, Path] | None:
    """Return (version, exe path) when a newer release was downloaded, None when up to date."""
    req = urllib.request.Request(LATEST_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            release = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise YoutubeError("업데이트 확인 실패", f"{LATEST_URL}\n{exc}") from exc
    tag = release.get("tag_name") or ""
    latest, current = _version_tuple(tag), _version_tuple(APP_VERSION)
    if not latest or not current or latest <= current:
        return None
    version = tag.lstrip("v")
    assets = release.get("assets") or []
    asset = next((a for a in assets if a.get("name", "").lower() == EXE_NAME.lower()), None) or next(
        (a for a in assets if a.get("name", "").lower().endswith((".exe", ".zip"))), None
    )
    if asset is None:
        raise YoutubeError(f"v{version} 릴리스에 실행 파일이 없습니다", ", ".join(a.get("name", "") for a in assets))

    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    final = UPDATE_DIR / f"{APP_NAME}-{version}.exe"
    if final.exists() and final.stat().st_size == asset.get("size", -1):
        return version, final  # picked up on a previous launch that was closed before applying
    for stale in UPDATE_DIR.glob("*.exe"):
        stale.unlink(missing_ok=True)

    dl = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with tempfile.TemporaryDirectory(prefix="ferry-update-", dir=UPDATE_DIR) as tmp:
        blob = Path(tmp) / asset["name"]
        try:
            with urllib.request.urlopen(dl, timeout=30) as resp, blob.open("wb") as out:
                total = int(resp.headers.get("Content-Length") or 0) or asset.get("size") or None
                done = 0
                while chunk := resp.read(1 << 18):
                    out.write(chunk)
                    done += len(chunk)
                    on_progress(Progress("downloading", done / total * 100 if total else 0.0, done, total, note=version))
        except OSError as exc:
            raise YoutubeError(f"v{version} 다운로드 실패", f"{asset['browser_download_url']}\n{exc}") from exc
        if blob.suffix.lower() == ".zip":
            with zipfile.ZipFile(blob) as zf:
                member = next((n for n in zf.namelist() if Path(n).name.lower() == EXE_NAME.lower()), None)
                if member is None:
                    raise YoutubeError("릴리스 zip 안에 Ferry.exe가 없습니다", ", ".join(zf.namelist()[:10]))
                extracted = Path(tmp) / EXE_NAME
                with zf.open(member) as src, extracted.open("wb") as dst:
                    while chunk := src.read(1 << 18):
                        dst.write(chunk)
                blob = extracted
        if blob.stat().st_size < 1_000_000:
            raise YoutubeError("내려받은 파일이 실행 파일로 보이지 않습니다", str(blob))
        os.replace(blob, final)
    return version, final


def _emit_progress(p: Progress) -> None:
    if _task is not None:
        _task.signals.progress.emit(p)


def _on_progress(p: Progress) -> None:
    mb = p.downloaded / 1e6
    text = f"v{p.note} 내려받는 중 {p.percent:.0f}%" if p.total else f"v{p.note} 내려받는 중 {mb:.0f} MB"
    context.bus.update_status.emit(text)


def _on_done(result: tuple[str, Path] | None) -> None:
    global ready, _new_exe, _task
    context.bus.update_status.emit("")
    if result is None:
        _task = None
        return
    ready, _new_exe = result
    context.bus.update_ready.emit(ready)
    context.bus.notify.emit(f"{APP_NAME} v{ready} 준비 완료", "제목 표시줄의 버튼으로 지금 재시작하거나, 다음에 앱을 닫을 때 자동으로 적용됩니다.")


def _on_failed(err: YoutubeError) -> None:
    global _task
    _task = None
    context.bus.update_status.emit("")
    # a failed check is not worth a toast on every launch; the detail is there for the log
    print(f"[updater] {err.message}: {err.detail}", file=sys.stderr)
