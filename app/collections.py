"""User playlists as folders under the save directory, plus moving and deleting downloaded files.

    collections.playlists()                      # [Path(...\\downloads\\즐겨찾기), ...] - one per sub-folder
    collections.create("즐겨찾기")               # mkdir under the save folder (name is validated)
    collections.move([path, ...], dest_folder)   # media + its subtitle sidecars, no clobbering
    collections.trash([path, ...])               # to the Recycle Bin (SHFileOperation), sidecars included

Every mutation ends with bus.library_changed so the library and the folder view rescan.
"""

from __future__ import annotations

import ctypes
import os
import re
from ctypes import wintypes
from pathlib import Path

from . import context, subtitles

_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def root() -> Path:
    return context.settings.save_dir


def playlists() -> list[Path]:
    base = root()
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name.casefold())


def create(name: str) -> Path:
    name = _BAD_NAME.sub("", name).strip(" .")
    if not name:
        raise ValueError("재생목록 이름을 입력하세요.")
    dest = root() / name
    dest.mkdir(parents=True, exist_ok=True)
    context.bus.library_changed.emit()
    return dest


def _with_sidecars(path: Path) -> list[Path]:
    return [path, *subtitles.sidecars(path)] if path.is_file() else []


def move(paths: list[Path], dest: Path) -> list[Path]:
    """Move media files (and their subtitles) into `dest`; returns the new media paths. Files already there stay."""
    dest.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for path in map(Path, paths):
        if path.parent == dest:
            moved.append(path)
            continue
        for f in _with_sidecars(path):
            target = dest / f.name
            n = 1
            while target.exists():  # a same-named file already lives there: keep both
                target = dest / f"{f.stem} ({n}){f.suffix}"
                n += 1
            os.replace(f, target)
            if f == path:
                moved.append(target)
    context.bus.library_changed.emit()
    return moved


def trash(paths: list[Path]) -> int:
    """Send media files and their subtitles to the Recycle Bin. Returns how many files went."""
    files = [f for p in map(Path, paths) for f in _with_sidecars(p)]
    if not files:
        return 0
    if not _shell_delete(files):
        for f in files:  # no shell (or it refused): plain delete
            try:
                f.unlink()
            except OSError:
                pass
    context.bus.library_changed.emit()
    return len(files)


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _shell_delete(files: list[Path]) -> bool:
    """Recycle Bin delete through the shell; False when unavailable so the caller can fall back."""
    if os.name != "nt":
        return False
    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
    op = _SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = "\0".join(str(f) for f in files) + "\0\0"  # double-NUL terminated list
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    try:
        return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted
    except (AttributeError, OSError):
        return False
