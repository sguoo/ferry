"""Subtitle files: parse SRT/WebVTT into cues, find sidecar files for a media file.

    cues = load(Path("clip.ko.srt"))
    cue  = cue_at(cues, position_ms)          # None between cues
    files = sidecars(Path("clip.mp4"))        # [clip.ko.srt, clip.en.vtt]
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

SUBTITLE_EXTS = {".srt", ".vtt"}

LANG_NAMES = {
    "ko": "한국어", "en": "English", "ja": "日本語", "zh": "中文", "es": "Español", "fr": "Français",
    "de": "Deutsch", "pt": "Português", "ru": "Русский", "vi": "Tiếng Việt", "id": "Indonesia", "th": "ไทย",
}

_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_TAGS = re.compile(r"<[^>]+>|\{\\[^}]*\}")


@dataclass
class Cue:
    start: int  # ms
    end: int    # ms
    text: str

    @property
    def stamp(self) -> str:
        s = self.start // 1000
        h, rem = divmod(s, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _ms(token: str) -> int | None:
    m = _TIME.search(token)
    if not m:
        return None
    h, mi, s, frac = m.groups()
    frac_ms = int(frac.ljust(3, "0")[:3])
    return (int(h or 0) * 3600 + int(mi) * 60 + int(s)) * 1000 + frac_ms


def parse(text: str) -> list[Cue]:
    """Parse SRT or WebVTT text (both use `start --> end` lines) into sorted cues."""
    cues: list[Cue] = []
    block: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n") + [""]:
        line = raw.rstrip()
        if line.strip():
            block.append(line)
            continue
        if block:
            cue = _cue_from_block(block)
            if cue is not None:
                cues.append(cue)
            block = []
    cues.sort(key=lambda c: c.start)
    return cues


def _cue_from_block(block: list[str]) -> Cue | None:
    for i, line in enumerate(block):
        if "-->" in line:
            left, _, right = line.partition("-->")
            start, end = _ms(left), _ms(right)
            if start is None or end is None:
                return None
            body = "\n".join(block[i + 1:]).strip()
            body = _TAGS.sub("", body)
            if not body:
                return None
            return Cue(start, end, body)
    return None


def load(path: Path | str) -> list[Cue]:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = p.read_text(encoding="cp949", errors="replace")
    return parse(text)


def cue_at(cues: list[Cue], position_ms: int) -> Cue | None:
    """Cue covering `position_ms`, using the sorted start times."""
    if not cues:
        return None
    starts = [c.start for c in cues]
    i = bisect_right(starts, position_ms) - 1
    if i >= 0 and cues[i].start <= position_ms <= cues[i].end:
        return cues[i]
    return None


def lang_of(path: Path | str) -> str:
    """'clip [id].ko.srt' -> 'ko'; 'clip.srt' -> ''."""
    parts = Path(path).name.split(".")
    if len(parts) >= 3:
        tag = parts[-2]
        if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]+)?", tag):
            return tag
    return ""


def lang_label(path: Path | str) -> str:
    tag = lang_of(path)
    base = tag.split("-")[0].lower()
    if not tag:
        return "자막"
    name = LANG_NAMES.get(base, tag)
    return f"{name} ({tag})" if name != tag else tag


def sidecars(media: Path | str) -> list[Path]:
    """Subtitle files that belong to a media file: same folder, name starts with the media stem."""
    media = Path(media)
    stem = media.stem
    out = []
    for p in media.parent.iterdir():
        if p.suffix.lower() in SUBTITLE_EXTS and p.is_file() and p.name.startswith(stem):
            out.append(p)
    order = {"ko": 0, "en": 1}
    return sorted(out, key=lambda p: (order.get(lang_of(p).split("-")[0].lower(), 9), p.name))


def media_for(subtitle: Path | str, media_exts: set[str]) -> Path | None:
    """The media file a subtitle belongs to ('clip.ko.srt' -> 'clip.mp4'), if it exists."""
    sub = Path(subtitle)
    stem = sub.stem
    if lang_of(sub):
        stem = stem.rsplit(".", 1)[0]
    for p in sub.parent.iterdir():
        if p.stem == stem and p.suffix.lower() in media_exts:
            return p
    return None
