"""Local-folder playlists: scan a directory for media, save/load .m3u8.

Pure Python, no Qt (run through `app.workers` from the UI).

    pl = scan_folder(Path(r"D:\\Media\\Focus"))
    write_m3u(pl)                       # -> D:\\Media\\Focus\\Focus.m3u8
    paths = read_m3u(Path("mix.m3u8"))  # absolute Paths, missing files dropped
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .youtube import find_ffmpeg

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"}
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".opus", ".wav", ".ogg", ".aac"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

_VIDEO_ID = re.compile(r"\[([A-Za-z0-9_-]{11})\](?:\.[A-Za-z0-9-]+)*$")  # "title [id].ext" / "title [id].ko.srt"


def owned_video_ids(folder: Path | str) -> set[str]:
    """YouTube ids of media already saved under `folder` (any depth), read off the "[id]" the filename template
    keeps in every download's name. Cheap: names only, no probing."""
    root = Path(folder)
    ids: set[str] = set()
    if not root.is_dir():
        return ids
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            stem, ext = os.path.splitext(name)
            if ext.lower() in MEDIA_EXTS and (m := _VIDEO_ID.search(stem)):
                ids.add(m.group(1))
    return ids
PLAYLIST_EXTS = {".m3u", ".m3u8"}
SUBTITLE_EXTS = {".srt", ".vtt", ".srv3"}


@dataclass
class LocalFile:
    path: Path
    name: str            # file name without extension
    ext: str             # ".mp4"
    size: int
    modified: datetime
    is_audio: bool
    duration: int | None = None   # seconds, from ffprobe
    width: int | None = None
    height: int | None = None
    vcodec: str = ""
    acodec: str = ""
    bitrate: int | None = None    # kbps
    subtitles: list[Path] = field(default_factory=list)  # sidecar .srt/.vtt files

    def subfolder(self, root: Path) -> str:
        """'sub/dir' for files found recursively, '' for files directly in root."""
        rel = self.path.relative_to(root).parent
        return "" if str(rel) == "." else rel.as_posix()

    @property
    def quality(self) -> str:
        if self.is_audio:
            return f"{self.bitrate} kbps" if self.bitrate else (self.acodec.upper() or "오디오")
        return f"{self.height}p" if self.height else (self.vcodec.upper() or "비디오")


@dataclass
class LocalPlaylist:
    folder: Path
    name: str
    files: list[LocalFile] = field(default_factory=list)
    skipped: int = 0     # non-media files ignored
    orphan_subtitles: list[Path] = field(default_factory=list)  # .srt/.vtt without a matching media file

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def total_duration(self) -> int:
        return sum(f.duration or 0 for f in self.files)

    @property
    def unknown_durations(self) -> int:
        return sum(1 for f in self.files if f.duration is None)

    @property
    def m3u_path(self) -> Path:
        return self.folder / f"{self.name}.m3u8"


ProgressCallback = Callable[[int, int], None]  # (done, total) while probing


def natural_key(text: str) -> list:
    """'track 2' < 'track 10'."""
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", text)]


def scan_folder(
    folder: Path | str,
    recursive: bool = False,
    probe: bool = True,
    ffmpeg_location: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> LocalPlaylist:
    """List media files under `folder` (natural order). `probe=True` fills duration/resolution via ffprobe."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"폴더를 찾을 수 없습니다: {root}")
    it = root.rglob("*") if recursive else root.iterdir()
    files, skipped, subs = [], 0, []
    for p in sorted((p for p in it if p.is_file()), key=lambda p: natural_key(str(p.relative_to(root)))):
        ext = p.suffix.lower()
        if ext in SUBTITLE_EXTS:
            subs.append(p)
            continue
        if ext not in MEDIA_EXTS:
            if ext not in PLAYLIST_EXTS:
                skipped += 1
            continue
        st = p.stat()
        files.append(LocalFile(p, p.stem, ext, st.st_size, datetime.fromtimestamp(st.st_mtime), ext in AUDIO_EXTS))
    pl = LocalPlaylist(root, root.name or str(root), files, skipped)
    _attach_subtitles(pl, subs)
    if probe:
        ffprobe = _find_ffprobe(ffmpeg_location)
        if ffprobe is not None:
            for i, f in enumerate(files, start=1):
                _probe(ffprobe, f)
                if on_progress:
                    on_progress(i, len(files))
    return pl


def _attach_subtitles(pl: LocalPlaylist, subs: list[Path]) -> None:
    """'clip [id].ko.srt' belongs to 'clip [id].mp4' in the same folder; the rest are orphans."""
    by_dir: dict[Path, list[LocalFile]] = {}
    for f in pl.files:
        by_dir.setdefault(f.path.parent, []).append(f)
    for s in subs:
        owners = [f for f in by_dir.get(s.parent, []) if s.name.startswith(f.name[: -len(f.ext)])]
        if owners:
            # longest stem wins when one name prefixes another
            max(owners, key=lambda f: len(f.name)).subtitles.append(s)
        else:
            pl.orphan_subtitles.append(s)
    order = {"ko": 0, "en": 1}
    for f in pl.files:
        f.subtitles.sort(key=lambda p: (order.get(_lang_tag(p), 9), p.name))


def _lang_tag(path: Path) -> str:
    parts = path.name.split(".")
    return parts[-2].split("-")[0].lower() if len(parts) >= 3 else ""


def write_m3u(playlist: LocalPlaylist, dest: Path | None = None, files: list[LocalFile] | None = None, relative: bool = True) -> Path:
    """Write an extended M3U (UTF-8). Paths are relative to the .m3u8's folder when possible."""
    dest = Path(dest) if dest else playlist.m3u_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U", f"#PLAYLIST:{playlist.name}"]
    for f in files if files is not None else playlist.files:
        lines.append(f"#EXTINF:{f.duration if f.duration is not None else -1},{f.name}")
        lines.append(_m3u_path(f.path, dest.parent, relative))
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def from_paths(paths: list[Path], name: str, probe: bool = True, ffmpeg_location: str | None = None) -> LocalPlaylist:
    """Build a playlist from explicit files (e.g. entries of an .m3u8). Folder = common parent."""
    files = []
    for p in paths:
        p = Path(p)
        if not p.is_file() or p.suffix.lower() not in MEDIA_EXTS:
            continue
        st = p.stat()
        files.append(LocalFile(p, p.stem, p.suffix.lower(), st.st_size, datetime.fromtimestamp(st.st_mtime), p.suffix.lower() in AUDIO_EXTS))
    root = Path(os.path.commonpath([str(f.path.parent) for f in files])) if files else Path.cwd()
    pl = LocalPlaylist(root, name, files)
    if probe:
        ffprobe = _find_ffprobe(ffmpeg_location)
        if ffprobe is not None:
            for f in files:
                _probe(ffprobe, f)
    return pl


def make_thumbnail(file: Path, dest_dir: Path, ffmpeg_location: str | None = None, at: float | None = None, width: int = 640) -> Path | None:
    """Grab one frame of a video into <dest_dir>/<hash>.jpg (cached). None for audio or on failure."""
    file = Path(file)
    if file.suffix.lower() in AUDIO_EXTS:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{file.resolve()}|{file.stat().st_mtime_ns}".encode()).hexdigest()[:20]
    target = dest_dir / f"{key}.jpg"
    if target.exists():
        return target
    ffmpeg = find_ffmpeg(ffmpeg_location)
    if ffmpeg is None:
        return None
    seek = at if at is not None else 5.0
    cmd = [str(ffmpeg), "-v", "error", "-y", "-ss", f"{seek:.1f}", "-i", str(file), "-frames:v", "1", "-vf", f"scale={width}:-2", str(target)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not target.exists() and seek > 0:
            # shorter than the seek point: take the first frame instead
            cmd[cmd.index("-ss") + 1] = "0"
            subprocess.run(cmd, capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return target if target.exists() else None


def open_in_explorer(path: Path) -> None:
    """Reveal a file (selected) or open a folder in Windows Explorer."""
    path = Path(path)
    if path.is_file():
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        os.startfile(str(path))  # type: ignore[attr-defined]


def read_m3u(path: Path | str) -> list[Path]:
    """Entries of an .m3u/.m3u8 as absolute paths; entries that no longer exist are dropped."""
    src = Path(path)
    out = []
    for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        p = Path(line)
        if not p.is_absolute():
            p = (src.parent / p).resolve()
        if p.exists():
            out.append(p)
    return out


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _m3u_path(target: Path, base: Path, relative: bool) -> str:
    if relative:
        try:
            return target.relative_to(base).as_posix()
        except ValueError:
            pass
    return str(target)


def _find_ffprobe(ffmpeg_location: str | None) -> Path | None:
    ffmpeg = find_ffmpeg(ffmpeg_location)
    if ffmpeg is None:
        return None
    probe = ffmpeg.with_name("ffprobe" + ffmpeg.suffix)
    return probe if probe.exists() else None


def _probe(ffprobe: Path, f: LocalFile) -> None:
    cmd = [
        str(ffprobe), "-v", "error", "-print_format", "json",
        "-show_entries", "format=duration,bit_rate:stream=codec_type,codec_name,width,height",
        str(f.path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(out.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return
    fmt = data.get("format") or {}
    if fmt.get("duration"):
        f.duration = int(float(fmt["duration"]))
    if fmt.get("bit_rate"):
        f.bitrate = int(int(fmt["bit_rate"]) / 1000)
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and s.get("codec_name") not in ("mjpeg", "png"):
            f.vcodec = s.get("codec_name") or ""
            f.width, f.height = s.get("width"), s.get("height")
            f.is_audio = False
        elif s.get("codec_type") == "audio":
            f.acodec = s.get("codec_name") or ""
