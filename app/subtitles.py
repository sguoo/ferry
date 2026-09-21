"""Subtitle files: parse SRT/WebVTT and YouTube's styled srv3 into cues, find sidecar files.

    cues = load(Path("clip.ko.srv3"))         # styled: colours, outline, size, anchor position
    cue  = cue_at(cues, position_ms)          # one cue (plain use) / active_cues(...) for windows
    files = sidecars(Path("clip.mp4"))        # [clip.ko.srv3, clip.ko.srt, clip.en.vtt]
"""

from __future__ import annotations

import html as html_mod
import re
import xml.etree.ElementTree as ET
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from pathlib import Path

SUBTITLE_EXTS = {".srt", ".vtt", ".srv3"}
STYLED_EXTS = {".srv3"}

LANG_NAMES = {
    "ko": "한국어", "en": "English", "ja": "日本語", "zh": "中文", "es": "Español", "fr": "Français",
    "de": "Deutsch", "pt": "Português", "ru": "Русский", "vi": "Tiếng Việt", "id": "Indonesia", "th": "ไทย",
}

_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_TAGS = re.compile(r"<[^>]+>|\{\\[^}]*\}")
_ZW = "​"


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #


@dataclass
class Pen:
    """Text style (srv3 <pen>). Colours are '#RRGGBB'; alpha 0..255."""

    color: str = "#FFFFFF"
    alpha: int = 255
    bold: bool = False
    italic: bool = False
    underline: bool = False
    size: int = 100                 # percent of the base caption size
    edge_color: str | None = None   # outline / glow colour
    edge_type: int = 0              # 0 none, 1 hard shadow, 2 bevel, 3 glow/outline, 4 soft shadow
    bg_color: str | None = None
    bg_alpha: int = 0


@dataclass
class Segment:
    text: str
    pen: Pen
    offset: int = 0                 # ms after the cue start before this segment appears (karaoke)


@dataclass
class Cue:
    start: int                      # ms
    end: int                        # ms
    text: str                       # plain text (viewer, search)
    segments: list[Segment] = field(default_factory=list)
    anchor: int = 7                 # srv3 ap: 0 TL 1 TC 2 TR 3 ML 4 MC 5 MR 6 BL 7 BC 8 BR
    ah: float = 50.0                # anchor x, percent of the video width
    av: float = 95.0                # anchor y, percent of the video height
    justify: int = 2                # 0 left, 1 right, 2 centre
    styled: bool = False            # True when segments carry srv3 styling

    @property
    def stamp(self) -> str:
        s = self.start // 1000
        h, rem = divmod(s, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def html(self, elapsed_ms: int | None = None, edge: bool = False, base_px: int | None = None) -> str:
        """Rich text for QTextDocument. edge=True recolours every span to its edge colour (outline pass);
        base_px turns srv3 percentage sizes into absolute pixels (Qt rich text ignores % font sizes)."""
        if not self.styled:
            return html_mod.escape(self.text).replace("\n", "<br>")
        parts = []
        for seg in self.segments:
            if elapsed_ms is not None and seg.offset > elapsed_ms:
                continue
            pen = seg.pen
            colour = (pen.edge_color or pen.color) if edge else pen.color
            alpha = pen.alpha
            if edge and pen.edge_color is None:
                alpha = 0
            css = [f"color: rgba({_r(colour)}, {_g(colour)}, {_b(colour)}, {alpha / 255:.2f})"]
            if pen.bold:
                css.append("font-weight: 700")
            if pen.italic:
                css.append("font-style: italic")
            if pen.underline:
                css.append("text-decoration: underline")
            if pen.size != 100 and base_px:
                css.append(f"font-size: {max(8, round(base_px * max(30, pen.size) / 100))}px")
            if pen.bg_color and pen.bg_alpha and not edge:
                css.append(f"background-color: rgba({_r(pen.bg_color)}, {_g(pen.bg_color)}, {_b(pen.bg_color)}, {pen.bg_alpha / 255:.2f})")
            text = html_mod.escape(seg.text).replace("\n", "<br>")
            parts.append(f'<span style="{"; ".join(css)}">{text}</span>')
        return "".join(parts)

    @property
    def has_edge(self) -> bool:
        return self.styled and any(s.pen.edge_color for s in self.segments)


def _r(c: str) -> int:
    return int(c[1:3], 16)


def _g(c: str) -> int:
    return int(c[3:5], 16)


def _b(c: str) -> int:
    return int(c[5:7], 16)


# --------------------------------------------------------------------------- #
# SRT / WebVTT
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# YouTube srv3 (timedtext format 3)
# --------------------------------------------------------------------------- #


def _hex(value: str | None, default: str | None) -> str | None:
    if not value:
        return default
    v = value.strip()
    if not v.startswith("#"):
        v = "#" + v
    return v.upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", v) else default


def parse_srv3(text: str) -> list[Cue]:
    root = ET.fromstring(text)
    pens: dict[str, Pen] = {}
    wps: dict[str, tuple[int, float, float]] = {}
    wss: dict[str, int] = {}
    head = root.find("head")
    if head is not None:
        for el in head.findall("pen"):
            pens[el.get("id", "")] = Pen(
                color=_hex(el.get("fc"), "#FFFFFF") or "#FFFFFF",
                alpha=int(el.get("fo", "254")),
                bold=el.get("b") == "1",
                italic=el.get("i") == "1",
                underline=el.get("u") == "1",
                size=int(el.get("sz", "100") or 100),
                edge_color=_hex(el.get("ec"), None),
                edge_type=int(el.get("et", "0") or 0),
                bg_color=_hex(el.get("bc"), None),
                bg_alpha=int(el.get("bo", "0") or 0),
            )
        for el in head.findall("wp"):
            wps[el.get("id", "")] = (int(el.get("ap", "7") or 7), float(el.get("ah", "50") or 50), float(el.get("av", "95") or 95))
        for el in head.findall("ws"):
            wss[el.get("id", "")] = int(el.get("ju", "2") or 2)
    default_pen = Pen()
    cues: list[Cue] = []
    body = root.find("body")
    for p in (body.findall("p") if body is not None else []):
        start = int(p.get("t", "0") or 0)
        dur = int(p.get("d", "0") or 0)
        base_pen = pens.get(p.get("p", ""), default_pen)
        segments: list[Segment] = []
        if p.text:
            segments.append(Segment(p.text, base_pen))
        for s in p:
            if s.tag == "s":
                pen = pens.get(s.get("p", ""), base_pen)
                segments.append(Segment(s.text or "", pen, int(s.get("t", "0") or 0)))
            elif s.tag == "br":
                segments.append(Segment("\n", base_pen))
            if s.tail:
                segments.append(Segment(s.tail, base_pen))
        plain = "".join(seg.text for seg in segments).replace(_ZW, "").strip()
        if not plain:
            continue
        ap, ah, av = wps.get(p.get("wp", ""), (7, 50.0, 95.0))
        cues.append(Cue(start, start + max(dur, 1), plain, segments, ap, ah, av, wss.get(p.get("ws", ""), 2), styled=True))
    cues.sort(key=lambda c: c.start)
    return cues


# --------------------------------------------------------------------------- #
# loading & lookup
# --------------------------------------------------------------------------- #


def load(path: Path | str) -> list[Cue]:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = p.read_text(encoding="cp949", errors="replace")
    if p.suffix.lower() == ".srv3":
        try:
            return parse_srv3(text)
        except ET.ParseError:
            return []
    return parse(text)


def is_styled(path: Path | str) -> bool:
    return Path(path).suffix.lower() in STYLED_EXTS


def cue_at(cues: list[Cue], position_ms: int) -> Cue | None:
    """Cue covering `position_ms`, using the sorted start times."""
    if not cues:
        return None
    starts = [c.start for c in cues]
    i = bisect_right(starts, position_ms) - 1
    if i >= 0 and cues[i].start <= position_ms <= cues[i].end:
        return cues[i]
    return None


class CueIndex:
    """Fast 'which cues are showing now' for dense styled files (hundreds of overlapping windows)."""

    def __init__(self, cues: list[Cue]):
        self.cues = cues
        self.starts = [c.start for c in cues]
        self.max_len = max((c.end - c.start for c in cues), default=0)

    def active(self, position_ms: int) -> list[Cue]:
        if not self.cues:
            return []
        lo = bisect_left(self.starts, position_ms - self.max_len)
        hi = bisect_right(self.starts, position_ms)
        return [c for c in self.cues[lo:hi] if c.start <= position_ms <= c.end]


def merge_runs(cues: list[Cue]) -> list[Cue]:
    """Collapse consecutive cues with the same text and window (srv3 colour fades) for list views."""
    out: list[Cue] = []
    for c in cues:
        last = out[-1] if out else None
        if last and last.text == c.text and (last.anchor, last.ah, last.av) == (c.anchor, c.ah, c.av) and c.start - last.end <= 120:
            last.end = max(last.end, c.end)
            continue
        out.append(Cue(c.start, c.end, c.text, c.segments, c.anchor, c.ah, c.av, c.justify, c.styled))
    return out


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #


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
    """Subtitle files that belong to a media file: same folder, name starts with the media stem.
    Ordered ko, en, others; styled (.srv3) before plain within a language."""
    media = Path(media)
    stem = media.stem
    out = []
    for p in media.parent.iterdir():
        if p.suffix.lower() in SUBTITLE_EXTS and p.is_file() and p.name.startswith(stem):
            out.append(p)
    order = {"ko": 0, "en": 1}
    return sorted(out, key=lambda p: (order.get(lang_of(p).split("-")[0].lower(), 9), lang_of(p), not is_styled(p), p.name))


def pick_per_language(files: list[Path]) -> list[Path]:
    """One file per language, preferring the styled srv3 when both exist."""
    seen: dict[str, Path] = {}
    for f in files:
        key = lang_of(f) or f.name
        if key not in seen or (is_styled(f) and not is_styled(seen[key])):
            seen[key] = f
    return list(seen.values())


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
