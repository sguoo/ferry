"""yt-dlp backend for Ferry.

Pure Python, no Qt: every call here blocks, so the UI runs them through
`app.workers`. Results are plain dataclasses shaped for the screens
(downloader, playlist, library), and progress is delivered through a callback.

    info = fetch_info("https://www.youtube.com/watch?v=...")
    hits = search("lofi jazz", count=10)
    pl   = fetch_playlist("https://www.youtube.com/playlist?list=...")
    path = download(info.url, DownloadOptions(quality=1080), on_progress=print)
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Literal

import yt_dlp
from yt_dlp.utils import DownloadCancelled

from .theme import DATA_DIR

DEFAULT_OUTPUT_DIR = DATA_DIR / "downloads"
THUMBNAIL_CACHE_DIR = DATA_DIR / "cache" / "thumbs"  # downloaded YouTube thumbnails, keyed by video id

Mode = Literal["video", "audio", "subtitles"]
Container = Literal["mp4", "mkv", "webm"]
AudioFormat = Literal["mp3", "flac", "m4a", "opus"]


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #


class YoutubeError(Exception):
    """User-facing failure. `message` is short and Korean; `detail` is the raw yt-dlp text."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


_ERROR_HINTS = [
    (r"confirm you.re not a bot", "YouTube가 봇 확인을 요구합니다. 환경설정 › 외부 도구에서 YouTube 쿠키를 연결하면 해결됩니다 (잠시 후 재시도해도 풀리기도 함)."),
    (r"Could not copy Chrome cookie database|Failed to decrypt with DPAPI|app.bound", "브라우저 쿠키를 읽지 못했습니다. Chrome/Edge는 실행 중이거나 최신 버전이면 직접 읽기가 막힙니다 — 브라우저를 완전히 닫고 다시 시도하거나, cookies.txt 파일을 지정하세요."),
    (r"could not find .* cookies database", "선택한 브라우저의 쿠키 데이터베이스를 찾을 수 없습니다 (설치되어 있지 않거나 프로필이 없음)."),
    (r"cookies? file .* not found|No such file or directory: .*cookies|Netscape format", "cookies.txt 파일을 열 수 없거나 형식이 맞지 않습니다 (Netscape 형식으로 내보내야 합니다)."),
    (r"Sign in to confirm your age|age.restricted", "연령 제한 영상입니다. 환경설정에서 브라우저 쿠키를 연결하면 받을 수 있습니다."),
    (r"Private video", "비공개 영상입니다."),
    (r"Video unavailable|This video is (not available|unavailable)", "존재하지 않거나 이 지역에서 볼 수 없는 영상입니다."),
    (r"HTTP Error 403", "HTTP 403 · YouTube가 요청을 거부했습니다. 잠시 후 다시 시도하거나 쿠키를 연결하세요."),
    (r"HTTP Error 429", "요청이 너무 많습니다(429). 잠시 후 다시 시도하세요."),
    (r"is not a valid URL|Unsupported URL", "지원하지 않는 주소입니다."),
    (r"playlist type is unviewable|Mixes are not|RD[A-Za-z0-9_-]{11}", "믹스(자동 생성 재생목록)는 열 수 없습니다. 일반 재생목록이나 채널 주소를 사용하세요."),
    (r"does not have a videos tab|This channel does not have", "이 채널에는 동영상 탭이 없습니다. 채널 주소에서 /videos를 빼고 다시 시도하세요."),
    (r"Requested format is not available", "이 영상은 합본(영상+음성) 스트림을 제공하지 않아 바로 재생할 수 없습니다. 다운로드 후 재생해 주세요."),
    (r"ffmpeg|ffprobe", "ffmpeg를 찾을 수 없습니다. 환경설정에서 경로를 지정하세요."),
    (r"getaddrinfo failed|Network is unreachable|Temporary failure in name resolution", "네트워크에 연결할 수 없습니다."),
]


def _friendly(exc: Exception) -> YoutubeError:
    raw = str(exc)
    for pattern, message in _ERROR_HINTS:
        if re.search(pattern, raw, re.IGNORECASE):
            return YoutubeError(message, raw)
    cleaned = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?", "", raw).strip()
    return YoutubeError(cleaned or "알 수 없는 오류가 발생했습니다.", raw)


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #


@dataclass
class QualityOption:
    """One selectable resolution, with the best matching stream for it."""

    height: int
    fps: int
    label: str            # "2160p60"
    tag: str              # "4K", "QHD", "FHD", "HD", "SD"
    vcodec: str           # "AV1", "VP9", "H.264"
    est_size: int | None  # video + best audio, bytes
    format_id: str


@dataclass
class SubtitleTrack:
    lang: str
    name: str
    auto: bool


@dataclass
class VideoInfo:
    id: str
    url: str
    title: str
    channel: str
    channel_verified: bool
    subscribers: int | None
    views: int | None
    upload_date: date | None
    duration: int | None
    thumbnail_url: str | None
    is_live: bool
    qualities: list[QualityOption]
    audio_codec: str
    audio_bitrate: int | None
    audio_est_size: int | None
    subtitles: list[SubtitleTrack]
    stream_count: int

    @property
    def best(self) -> QualityOption | None:
        return self.qualities[0] if self.qualities else None


@dataclass
class SearchItem:
    id: str
    url: str
    title: str
    channel: str
    channel_verified: bool
    views: int | None
    duration: int | None
    thumbnail_url: str | None
    is_playlist: bool
    is_live: bool
    playlist_count: int | None = None


@dataclass
class PlaylistEntry:
    index: int
    id: str
    url: str
    title: str
    channel: str
    duration: int | None
    thumbnail_url: str | None


@dataclass
class PlaylistInfo:
    id: str
    url: str
    title: str
    channel: str
    count: int
    total_duration: int
    thumbnail_url: str | None
    entries: list[PlaylistEntry]


@dataclass
class DownloadOptions:
    mode: Mode = "video"
    quality: int | None = None            # max height; None = best available
    container: Container = "mp4"
    vcodec: str | None = None              # "AV1" / "VP9" / "H.264"; None = let yt-dlp pick
    audio_format: AudioFormat = "mp3"
    audio_bitrate: int = 320               # kbps, lossy formats only
    subtitle_langs: list[str] = field(default_factory=list)
    embed_subtitles: bool = True
    output_dir: Path = DEFAULT_OUTPUT_DIR
    filename_template: str = "%(title)s [%(id)s].%(ext)s"
    rate_limit: str | None = None          # "20M", "500K"
    concurrent_fragments: int = 4
    ffmpeg_location: str | None = None
    cookies_from_browser: str | None = None  # "chrome", "edge", "firefox", "brave"
    cookies_file: str | None = None          # Netscape cookies.txt; wins over cookies_from_browser


Phase = Literal["queued", "downloading", "merging", "converting", "finished", "error"]


@dataclass
class Progress:
    phase: Phase
    percent: float = 0.0
    downloaded: int = 0
    total: int | None = None
    speed: float | None = None     # bytes/s
    eta: int | None = None         # seconds
    filename: str = ""
    fragment: tuple[int, int] | None = None
    note: str = ""


ProgressCallback = Callable[[Progress], None]


# --------------------------------------------------------------------------- #
# environment
# --------------------------------------------------------------------------- #


def ytdlp_version() -> str:
    return yt_dlp.version.__version__


def find_ffmpeg(explicit: str | None = None) -> Path | None:
    """Explicit path, then PATH, then the copy ffmpeg_install fetched on first run."""
    candidates = [explicit] if explicit else []
    candidates += [shutil.which("ffmpeg"), DATA_DIR / "ffmpeg" / "ffmpeg.exe"]
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    return None


def ffmpeg_version(explicit: str | None = None) -> str | None:
    exe = find_ffmpeg(explicit)
    if exe is None:
        return None
    try:
        out = subprocess.run([str(exe), "-version"], capture_output=True, text=True, timeout=5, creationflags=_no_window())
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"ffmpeg version (\S+)", out.stdout)
    if not m:
        return "unknown"
    # "7.0-essentials_build-www.gyan.dev" -> "7.0"; nightly "N-126732-g5d3cb3dc17-20260920" -> "N-126732"
    parts = re.split(r"[-_]", m.group(1))
    return "-".join(parts[:2]) if parts[0] == "N" and len(parts) > 1 else parts[0] or m.group(1)


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --------------------------------------------------------------------------- #
# yt-dlp option builders
# --------------------------------------------------------------------------- #


class _Silent:
    """Swallow yt-dlp console chatter; errors still surface as exceptions."""

    def debug(self, msg): ...
    def info(self, msg): ...
    def warning(self, msg): ...
    def error(self, msg): ...


def _base_opts(opts: DownloadOptions | None = None) -> dict:
    o = {
        "quiet": True,
        "no_warnings": True,
        "logger": _Silent(),
        "noprogress": True,
        "ignoreerrors": False,
        "retries": 3,
        "socket_timeout": 20,
    }
    if opts:
        if opts.ffmpeg_location:
            o["ffmpeg_location"] = opts.ffmpeg_location
        if opts.cookies_file:
            o["cookiefile"] = opts.cookies_file
        elif opts.cookies_from_browser:
            o["cookiesfrombrowser"] = (opts.cookies_from_browser,)
    return o


_CODEC_PREFIX = {"AV1": "av01", "VP9": "vp09", "H.264": "avc1", "H.265": "hvc1"}


def _format_selector(opts: DownloadOptions) -> str:
    if opts.mode == "audio":
        return "bestaudio/best"
    h = f"[height<={opts.quality}]" if opts.quality else ""
    codec = _CODEC_PREFIX.get(opts.vcodec or "")
    c = f"[vcodec^={codec}]" if codec else ""
    chain = []
    if opts.container == "mp4" and not codec:
        # prefer streams that merge without re-encoding
        chain.append(f"bestvideo{h}[ext=mp4]+bestaudio[ext=m4a]")
    if c:
        chain.append(f"bestvideo{h}{c}+bestaudio")
    chain += [f"bestvideo{h}+bestaudio", f"best{h}"]
    return "/".join(chain)


def _download_opts(opts: DownloadOptions, on_progress: ProgressCallback | None, cancel: threading.Event | None) -> dict:
    o = _base_opts(opts)
    o.update(
        {
            "outtmpl": str(Path(opts.output_dir) / opts.filename_template),
            "format": _format_selector(opts),
            "concurrent_fragment_downloads": max(1, opts.concurrent_fragments),
            "continuedl": True,
            "noplaylist": True,
            "postprocessors": [],
        }
    )
    if opts.rate_limit:
        o["ratelimit"] = _parse_rate(opts.rate_limit)

    if opts.mode == "video":
        o["merge_output_format"] = opts.container
    elif opts.mode == "audio":
        pp = {"key": "FFmpegExtractAudio", "preferredcodec": opts.audio_format}
        if opts.audio_format in ("mp3", "m4a", "opus"):
            pp["preferredquality"] = str(opts.audio_bitrate)
        o["postprocessors"].append(pp)
        o["postprocessors"].append({"key": "FFmpegMetadata"})
    elif opts.mode == "subtitles":
        o["skip_download"] = True

    if opts.subtitle_langs:
        o.update({"writesubtitles": True, "writeautomaticsub": True, "subtitleslangs": opts.subtitle_langs, "subtitlesformat": "srt/best"})
        o["postprocessors"].append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
        if opts.embed_subtitles and opts.mode == "video":
            # keep the .srt next to the video so the in-app player can render it itself
            o["postprocessors"].append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": True})

    def hook(d: dict) -> None:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("cancelled by user")
        if on_progress:
            on_progress(_progress_from_hook(d))

    def pp_hook(d: dict) -> None:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("cancelled by user")
        if on_progress and d.get("status") == "started":
            name = d.get("postprocessor", "")
            if "ExtractAudio" in name:
                on_progress(Progress("converting", 100.0, note="오디오 변환 중"))
            elif "Merger" in name or "EmbedSubtitle" in name:
                on_progress(Progress("merging", 100.0, note="스트림 병합 중"))
            # Metadata / MoveFiles are near-instant; not worth a UI phase

    o["progress_hooks"] = [hook]
    o["postprocessor_hooks"] = [pp_hook]
    return o


def _parse_rate(text: str) -> int:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmMgG]?)\s*", text)
    if not m:
        raise YoutubeError(f"속도 제한 값을 이해할 수 없습니다: {text!r}")
    mult = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[m.group(2).lower()]
    return int(float(m.group(1)) * mult)


def _progress_from_hook(d: dict) -> Progress:
    status = d.get("status")
    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    done = d.get("downloaded_bytes") or 0
    if status == "finished":
        return Progress("merging", 100.0, done, total, filename=d.get("filename", ""))
    if status == "error":
        return Progress("error", note=str(d.get("error", "")))
    frag = None
    if d.get("fragment_index") is not None and d.get("fragment_count"):
        frag = (int(d["fragment_index"]), int(d["fragment_count"]))
    percent = (done / total * 100) if total else 0.0
    return Progress("downloading", percent, done, total, d.get("speed"), d.get("eta"), d.get("filename", ""), frag)


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def fetch_info(url: str, opts: DownloadOptions | None = None) -> VideoInfo:
    """Metadata + selectable qualities for one video. No download."""
    o = _base_opts(opts)
    o["noplaylist"] = True
    try:
        with yt_dlp.YoutubeDL(o) as ydl:
            raw = ydl.extract_info(url, download=False)
    except Exception as exc:  # yt-dlp wraps most failures in DownloadError
        raise _friendly(exc) from exc
    if raw is None:
        raise YoutubeError("영상 정보를 가져오지 못했습니다.")
    if raw.get("_type") == "playlist":
        entries = [e for e in raw.get("entries") or [] if e]
        if not entries:
            raise YoutubeError("재생목록이 비어 있습니다.")
        raw = entries[0]
    return _video_info(raw)


def search(query: str, count: int = 10, kind: Literal["all", "video", "playlist", "channel"] = "all") -> list[SearchItem]:
    """YouTube search via yt-dlp's ytsearch extractor (flat, fast)."""
    q = query.strip()
    if not q:
        return []
    o = _base_opts()
    o["extract_flat"] = "in_playlist"
    # over-fetch a little so kind filters still return `count` rows
    n = count if kind == "all" else count * 2
    try:
        with yt_dlp.YoutubeDL(o) as ydl:
            raw = ydl.extract_info(f"ytsearch{n}:{q}", download=False)
    except Exception as exc:
        raise _friendly(exc) from exc
    items = [_search_item(e) for e in (raw or {}).get("entries") or [] if e]
    if kind == "video":
        items = [i for i in items if not i.is_playlist]
    elif kind == "playlist":
        items = [i for i in items if i.is_playlist]
    elif kind == "channel":
        items = [i for i in items if "/channel/" in i.url or "/@" in i.url]
    return items[:count]


def fetch_playlist(url: str, opts: DownloadOptions | None = None) -> PlaylistInfo:
    """Flat listing of a playlist or channel. Entries are cheap; call fetch_info per item for formats."""
    o = _base_opts(opts)
    o["extract_flat"] = "in_playlist"
    try:
        with yt_dlp.YoutubeDL(o) as ydl:
            raw = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise _friendly(exc) from exc
    if raw is None:
        raise YoutubeError("재생목록을 열 수 없습니다.")
    if raw.get("_type") != "playlist":
        raise YoutubeError("재생목록이나 채널 주소가 아닙니다.")
    entries = []
    for i, e in enumerate(raw.get("entries") or [], start=1):
        if not e:
            continue
        entries.append(
            PlaylistEntry(
                index=i,
                id=e.get("id", ""),
                url=e.get("url") or e.get("webpage_url") or f"https://www.youtube.com/watch?v={e.get('id', '')}",
                title=e.get("title") or "(제목 없음)",
                channel=e.get("channel") or e.get("uploader") or "",
                duration=_int(e.get("duration")),
                thumbnail_url=_best_thumb(e),
            )
        )
    return PlaylistInfo(
        id=raw.get("id", ""),
        url=raw.get("webpage_url") or url,
        title=raw.get("title") or "(제목 없음)",
        channel=raw.get("channel") or raw.get("uploader") or "",
        count=len(entries),
        total_duration=sum(e.duration or 0 for e in entries),
        thumbnail_url=_best_thumb(raw) or (entries[0].thumbnail_url if entries else None),
        entries=entries,
    )


def download(
    url: str,
    opts: DownloadOptions | None = None,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Download one video/audio/subtitle set. Blocks; returns the final file path.

    `cancel` may be set from another thread: the current fragment finishes,
    then DownloadCancelled is raised as YoutubeError("취소됨"). Partial files are
    kept so the same call later resumes (continuedl).
    """
    opts = opts or DownloadOptions()
    Path(opts.output_dir).mkdir(parents=True, exist_ok=True)
    if opts.mode != "subtitles" and find_ffmpeg(opts.ffmpeg_location) is None:
        raise YoutubeError("ffmpeg를 찾을 수 없습니다. 병합과 오디오 변환에 필요합니다.")
    o = _download_opts(opts, on_progress, cancel)
    try:
        with yt_dlp.YoutubeDL(o) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadCancelled as exc:
        raise YoutubeError("취소됨", str(exc)) from exc
    except Exception as exc:
        if on_progress:
            on_progress(Progress("error", note=str(exc)))
        raise _friendly(exc) from exc
    path = _final_path(ydl, info, opts)
    if opts.subtitle_langs and opts.mode != "audio":
        _fetch_styled_subtitles(url, opts)
    if on_progress:
        on_progress(Progress("finished", 100.0, filename=str(path)))
    return path


class _Capture:
    """Logger that keeps yt-dlp's lines so a cookie test can report what happened."""

    def __init__(self):
        self.lines: list[str] = []

    def debug(self, msg):
        self.lines.append(str(msg))

    def info(self, msg):
        self.lines.append(str(msg))

    def warning(self, msg):
        self.lines.append(str(msg))

    def error(self, msg):
        self.lines.append(str(msg))


def test_cookies(opts: DownloadOptions) -> str:
    """Check that the configured cookie source loads and YouTube accepts it. Returns a summary; raises YoutubeError."""
    if not (opts.cookies_file or opts.cookies_from_browser):
        raise YoutubeError("쿠키 소스가 없습니다. 브라우저를 고르거나 cookies.txt 파일을 지정하세요.")
    if opts.cookies_file and not Path(opts.cookies_file).is_file():
        raise YoutubeError(f"cookies.txt 파일을 찾을 수 없습니다: {opts.cookies_file}")
    o = _base_opts(opts)
    log = _Capture()
    o.update({"logger": log, "quiet": False, "verbose": False, "extract_flat": "in_playlist", "playlist_items": "1"})
    try:
        with yt_dlp.YoutubeDL(o) as ydl:
            jar = ydl.cookiejar
            count = sum(1 for c in jar if "youtube.com" in (c.domain or "") or "google.com" in (c.domain or ""))
            logged_in = any(c.name in ("SAPISID", "__Secure-3PAPISID", "LOGIN_INFO") for c in jar)
            # the "library" feed is only served to a signed-in account
            ydl.extract_info("https://www.youtube.com/feed/library", download=False)
    except Exception as exc:
        raw = str(exc)
        if "login" in raw.lower() or "sign in" in raw.lower():
            raise YoutubeError(f"쿠키는 읽었지만 로그인 세션이 아닙니다 (YouTube/Google 쿠키 {count}개). 브라우저에서 YouTube에 로그인한 뒤 다시 내보내세요.", raw) from exc
        raise _friendly(exc) from exc
    source = ("Ferry 로그인" if Path(opts.cookies_file).name == "cookies.txt" and Path(opts.cookies_file).parent == DATA_DIR else "cookies.txt") if opts.cookies_file else opts.cookies_from_browser
    if not logged_in:
        raise YoutubeError(f"{source}에서 쿠키 {count}개를 읽었지만 로그인 세션 쿠키(SAPISID/LOGIN_INFO)가 없습니다. 브라우저에서 YouTube에 로그인한 상태로 다시 내보내세요.")
    return f"{source}에서 YouTube/Google 쿠키 {count}개 읽음 · 로그인 세션 확인됨"


def _fetch_styled_subtitles(url: str, opts: DownloadOptions) -> None:
    """Second, download-free pass that saves YouTube's own srv3 captions next to the .srt.

    srv3 keeps colours, outlines, sizes and screen positions (the .srt loses them); the
    in-app player prefers it when present. Silently skipped for sites without it.
    """
    if "youtu" not in url:
        return
    o = _base_opts(opts)
    o.update(
        {
            "skip_download": True,
            "noplaylist": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": list(opts.subtitle_langs),
            "subtitlesformat": "srv3",
            "outtmpl": str(Path(opts.output_dir) / opts.filename_template),
            "ignoreerrors": True,
        }
    )
    try:
        with yt_dlp.YoutubeDL(o) as ydl:
            ydl.extract_info(url, download=True)
    except Exception:
        pass  # best effort: the plain .srt from the main pass is already there


def fetch_thumbnail(url: str | None, key: str, dest_dir: Path = THUMBNAIL_CACHE_DIR, timeout: float = 10) -> Path | None:
    """Cache a thumbnail as <dest_dir>/<key>.jpg (returns the path, or None on any failure)."""
    if not url:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{_safe_key(key)}.jpg"
    if target.exists():
        return target
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            target.write_bytes(resp.read())
    except Exception:
        return None
    return target


# --------------------------------------------------------------------------- #
# mapping helpers
# --------------------------------------------------------------------------- #

_CODEC_FAMILY = [("av01", "AV1"), ("vp09", "VP9"), ("vp9", "VP9"), ("avc1", "H.264"), ("h264", "H.264"), ("hev1", "H.265"), ("hvc1", "H.265")]


def _codec_family(vcodec: str | None) -> str:
    v = (vcodec or "").lower()
    for prefix, name in _CODEC_FAMILY:
        if v.startswith(prefix):
            return name
    return v.split(".")[0].upper() if v and v != "none" else ""


def _quality_tag(height: int) -> str:
    if height >= 2160:
        return "4K"
    if height >= 1440:
        return "QHD"
    if height >= 1080:
        return "FHD"
    if height >= 720:
        return "HD"
    return "SD"


def _video_info(raw: dict) -> VideoInfo:
    formats = [f for f in raw.get("formats") or [] if f.get("format_id")]
    audio_only = [f for f in formats if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
    video_only = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("height")]

    best_audio = max(audio_only, key=lambda f: (f.get("abr") or f.get("tbr") or 0), default=None)
    audio_size = _size(best_audio, _int(raw.get("duration"))) if best_audio else None

    duration = _int(raw.get("duration"))

    # one option per height: plain HTTPS over HLS (mergeable, sized), then higher fps, then bitrate
    def rank(f: dict) -> tuple:
        return (not str(f.get("protocol") or "").startswith("m3u8"), f.get("fps") or 0, f.get("tbr") or 0)

    per_height: dict[int, dict] = {}
    for f in video_only:
        h = int(f["height"])
        cur = per_height.get(h)
        if cur is None or rank(f) > rank(cur):
            per_height[h] = f
    qualities: list[QualityOption] = []
    for h in sorted(per_height, reverse=True):
        f = per_height[h]
        fps = int(round(f.get("fps") or 0))
        vsize = _size(f, duration)
        est = (vsize + audio_size) if (vsize is not None and audio_size is not None) else vsize
        qualities.append(
            QualityOption(
                height=h,
                fps=fps,
                label=f"{h}p{fps}" if fps > 30 else f"{h}p",
                tag=_quality_tag(h),
                vcodec=_codec_family(f.get("vcodec")),
                est_size=est,
                format_id=f["format_id"],
            )
        )

    # manual tracks as-is; auto captions only for the spoken language plus ko/en
    # (YouTube lists a machine translation for every language otherwise)
    subs: list[SubtitleTrack] = []
    for lang, tracks in (raw.get("subtitles") or {}).items():
        if lang == "live_chat":
            continue
        subs.append(SubtitleTrack(lang, _sub_name(tracks, lang), auto=False))
    spoken = (raw.get("language") or "").split("-")[0]
    wanted_auto = {l for l in (spoken, "ko", "en") if l}
    for lang, tracks in (raw.get("automatic_captions") or {}).items():
        base = lang.split("-")[0]
        if base not in wanted_auto or lang in {s.lang for s in subs} or "-orig" in lang:
            continue
        subs.append(SubtitleTrack(lang, _sub_name(tracks, lang), auto=True))
    subs.sort(key=lambda s: (s.auto, s.lang not in ("ko", "en"), s.lang))

    return VideoInfo(
        id=raw.get("id", ""),
        url=raw.get("webpage_url") or raw.get("original_url") or "",
        title=raw.get("title") or "(제목 없음)",
        channel=raw.get("channel") or raw.get("uploader") or "",
        channel_verified=bool(raw.get("channel_is_verified")),
        subscribers=_int(raw.get("channel_follower_count")),
        views=_int(raw.get("view_count")),
        upload_date=_date(raw.get("upload_date")),
        duration=_int(raw.get("duration")),
        thumbnail_url=_best_thumb(raw),
        is_live=bool(raw.get("is_live")),
        qualities=qualities,
        audio_codec=(best_audio or {}).get("acodec", "") or "",
        audio_bitrate=_int((best_audio or {}).get("abr")),
        audio_est_size=audio_size,
        subtitles=subs,
        stream_count=len(formats),
    )


def _search_item(e: dict) -> SearchItem:
    url = e.get("url") or e.get("webpage_url") or ""
    is_playlist = e.get("_type") == "playlist" or "list=" in url or (e.get("ie_key") or "") in ("YoutubePlaylist", "YoutubeTab")
    if not url and e.get("id"):
        url = f"https://www.youtube.com/watch?v={e['id']}"
    return SearchItem(
        id=e.get("id", ""),
        url=url,
        title=e.get("title") or "(제목 없음)",
        channel=e.get("channel") or e.get("uploader") or "",
        channel_verified=bool(e.get("channel_is_verified")),
        views=_int(e.get("view_count")),
        duration=_int(e.get("duration")),
        thumbnail_url=_best_thumb(e),
        is_playlist=is_playlist,
        is_live=e.get("live_status") == "is_live",
        playlist_count=_int(e.get("playlist_count")),
    )


def _final_path(ydl: yt_dlp.YoutubeDL, info: dict | None, opts: DownloadOptions) -> Path:
    if info:
        if opts.mode == "subtitles":
            # subtitles-only: the .srt files are the deliverable
            written = []
            for lang, sub in (info.get("requested_subtitles") or {}).items():
                p = (sub or {}).get("filepath")
                if p and Path(p).exists():
                    written.append((lang, Path(p)))
            if written:
                preferred = [p for lang, p in written if lang.split("-")[0] in opts.subtitle_langs]
                return preferred[0] if preferred else written[0][1]
            base = Path(ydl.prepare_filename(info)).with_suffix("")
            for cand in base.parent.glob(f"{base.name}*.srt"):
                return cand
            raise YoutubeError("자막이 저장되지 않았습니다. 선택한 언어의 자막이 이 영상에 없을 수 있습니다.")
        # yt-dlp records the post-processed file here in recent versions
        downloads = info.get("requested_downloads") or []
        for d in downloads:
            p = d.get("filepath") or d.get("_filename")
            if p and Path(p).exists():
                return Path(p)
        guess = Path(ydl.prepare_filename(info))
        if opts.mode == "audio":
            guess = guess.with_suffix(f".{opts.audio_format}")
        elif opts.mode == "video":
            guess = guess.with_suffix(f".{opts.container}")
        if guess.exists():
            return guess
        return guess
    return Path(opts.output_dir)


def _size(f: dict | None, duration: int | None = None) -> int | None:
    """Reported size, else bitrate x duration (yt-dlp omits sizes for many streams)."""
    if not f:
        return None
    known = _int(f.get("filesize") or f.get("filesize_approx"))
    if known:
        return known
    tbr = f.get("tbr") or f.get("vbr") or f.get("abr")
    if tbr and duration:
        return int(float(tbr) * 1000 / 8 * duration)
    return None


def _best_thumb(d: dict) -> str | None:
    if d.get("thumbnail"):
        return d["thumbnail"]
    thumbs = d.get("thumbnails") or []
    if not thumbs:
        return None
    best = max(thumbs, key=lambda t: (t.get("width") or 0, t.get("height") or 0))
    return best.get("url")


def _sub_name(tracks: list[dict], lang: str) -> str:
    for t in tracks or []:
        if t.get("name"):
            return t["name"]
    return lang


def _int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _date(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y%m%d").date()
    except ValueError:
        return None


def _safe_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:120]


# --------------------------------------------------------------------------- #
# display formatting (Korean, matching the mock copy)
# --------------------------------------------------------------------------- #


def fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "--:--"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_duration_long(seconds: int | None) -> str:
    if not seconds:
        return "0분"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}시간 {m}분" if h else f"{m}분"


def fmt_size(n: int | None, approx: bool = False) -> str:
    if n is None:
        return "크기 미상"
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(n)
    for u in units:
        if v < 1024 or u == units[-1]:
            text = f"{v:.0f} {u}" if u in ("B", "KB") else f"{v:.1f} {u}".replace(".0 ", " ")
            return f"약 {text}" if approx else text
        v /= 1024
    return str(n)


def fmt_speed(bps: float | None) -> str:
    return "-- MB/s" if not bps else f"{bps / 1024 / 1024:.1f} MB/s"


def fmt_eta(seconds: int | None) -> str:
    if seconds is None:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_count(n: int | None, suffix: str = "") -> str:
    """87,413 -> '87,413', 214,000 -> '21.4만', 12,830,000 -> '1,283만'."""
    if n is None:
        return "-"
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}억{suffix}".replace(".0억", "억")
    if n >= 10_000:
        man = n / 10_000
        return (f"{man:,.1f}" if man < 100 else f"{man:,.0f}").replace(".0", "") + f"만{suffix}"
    return f"{n:,}{suffix}"


def fmt_views(n: int | None) -> str:
    return f"조회수 {fmt_count(n, '회')}"


def fmt_subscribers(n: int | None) -> str:
    return f"구독자 {fmt_count(n, '명')}"


def fmt_relative(d: date | None, today: date | None = None) -> str:
    if d is None:
        return ""
    today = today or date.today()
    days = (today - d).days
    if days <= 0:
        return "오늘"
    if days < 7:
        return f"{days}일 전"
    if days < 30:
        return f"{days // 7}주 전"
    if days < 365:
        return f"{days // 30}개월 전"
    return f"{days // 365}년 전"
