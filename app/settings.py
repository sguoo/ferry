"""User preferences, persisted as JSON in the per-user data dir (%LOCALAPPDATA%/Ferry/settings.json)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .theme import DATA_DIR, ROOT
from .youtube import DEFAULT_OUTPUT_DIR, DownloadOptions

SETTINGS_PATH = DATA_DIR / "settings.json"
_LEGACY_SETTINGS_PATH = ROOT / "settings.json"  # pre-DATA_DIR location (dev checkouts)

QUALITY_CHOICES: list[tuple[str, int | None]] = [
    ("최고 화질 (bestvideo+bestaudio)", None),
    ("2160p (4K)", 2160),
    ("1440p (QHD)", 1440),
    ("1080p (FHD)", 1080),
    ("720p (HD)", 720),
]
CONTAINERS = ["mp4", "mkv", "webm"]
THREAD_CHOICES = [1, 2, 4, 6, 8]
COOKIE_BROWSERS: list[tuple[str, str]] = [("사용 안 함", ""), ("Chrome", "chrome"), ("Edge", "edge"), ("Firefox", "firefox"), ("Brave", "brave")]
LANGUAGES = ["한국어", "English", "日本語"]

# per-item output presets for batch downloads: (label, mode, max height)
PRESETS: list[tuple[str, str, int | None]] = [
    ("최고 화질 (MP4)", "video", None),
    ("2160p (4K)", "video", 2160),
    ("1440p (QHD)", "video", 1440),
    ("1080p (FHD)", "video", 1080),
    ("720p (HD)", "video", 720),
    ("MP3 음원 320k", "audio", None),
]
AUDIO_PRESET = len(PRESETS) - 1


def default_preset(quality: int | None) -> int:
    for i, (_, mode, h) in enumerate(PRESETS):
        if mode == "video" and h == quality:
            return i
    return 0


@dataclass
class Settings:
    save_path: str = str(DEFAULT_OUTPUT_DIR)
    filename_template: str = "%(title)s [%(id)s].%(ext)s"
    quality: int | None = None          # max height; None = best
    container: str = "mp4"
    threads: int = 4
    speed_limit: str = ""
    clipboard_watch: bool = True
    background_play: bool = True       # keep playing (with the mini bar) when switching screens
    notify: bool = True
    auto_update: bool = True           # check GitHub Releases on launch and fetch a newer build
    sound: bool = False
    info_cache: bool = True
    ffmpeg_path: str = ""
    cookies_browser: str = ""
    cookies_file: str = ""            # Netscape cookies.txt exported from the browser
    language: str = "한국어"
    playlist_save_path: str = ""       # empty = save_path / <playlist title>
    subtitle_langs: list[str] = field(default_factory=lambda: ["ko"])

    # ---- derived -----------------------------------------------------------
    @property
    def save_dir(self) -> Path:
        return Path(self.save_path).expanduser()

    def download_options(
        self,
        mode: str = "video",
        quality: int | None | str = "default",
        container: str | None = None,
        vcodec: str | None = None,
        subtitle_langs: list[str] | None = None,
        output_dir: Path | None = None,
        audio_format: str = "mp3",
    ) -> DownloadOptions:
        return DownloadOptions(
            mode=mode,  # type: ignore[arg-type]
            quality=self.quality if quality == "default" else quality,  # type: ignore[arg-type]
            container=container or self.container,  # type: ignore[arg-type]
            vcodec=vcodec,
            audio_format=audio_format,  # type: ignore[arg-type]
            subtitle_langs=list(subtitle_langs if subtitle_langs is not None else []),
            embed_subtitles=True,
            output_dir=output_dir or self.save_dir,
            filename_template=self.filename_template,
            rate_limit=self.speed_limit or None,
            concurrent_fragments=4,
            ffmpeg_location=self.ffmpeg_path or None,
            cookies_from_browser=self.cookies_browser or None,
            cookies_file=self.cookies_file or None,
        )


def load(path: Path = SETTINGS_PATH) -> Settings:
    s = Settings()
    if not path.exists() and path == SETTINGS_PATH and _LEGACY_SETTINGS_PATH.exists():
        path = _LEGACY_SETTINGS_PATH
    if not path.exists():
        return s
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return s
    known = {f.name for f in fields(Settings)}
    for k, v in data.items():
        if k in known:
            setattr(s, k, v)
    if "_MEI" in s.save_path and not s.save_dir.exists():
        s.save_path = Settings.save_path  # older builds pointed at PyInstaller's temp dir, gone by now
    return s


def save(s: Settings, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8")
