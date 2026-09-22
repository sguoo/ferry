"""Stroke icon set rendered from inline SVG.

All glyphs share a 24px viewbox, 1.5 stroke width and round caps so the set
reads as one family. Colour is injected at render time.
"""

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_STROKE = 1.5

_PATHS: dict[str, str] = {
    "download": '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M4 19h16"/>',
    "playlist": '<path d="M4 6h12"/><path d="M4 12h12"/><path d="M4 18h7"/><path d="M15 14v8l6-4z"/>',
    "library": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
    "gear": (
        '<circle cx="12" cy="12" r="3.2"/><circle cx="12" cy="12" r="6.6"/>'
        '<path d="M18.6 12h2.9"/><path d="M15.3 17.7l1.5 2.6"/><path d="M8.7 17.7l-1.5 2.6"/>'
        '<path d="M5.4 12H2.5"/><path d="M8.7 6.3 7.2 3.7"/><path d="m15.3 6.3 1.5-2.6"/>'
    ),
    "clipboard": (
        '<rect x="6" y="4.5" width="12" height="16.5" rx="2"/>'
        '<rect x="9" y="2.5" width="6" height="3.5" rx="1"/><path d="M9 12h6"/><path d="M9 15.5h4"/>'
    ),
    "link": (
        '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.2 1.2"/>'
        '<path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.2-1.2"/>'
    ),
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.3-4.3"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "folder-open": (
        '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1.5"/>'
        '<path d="M3 11h17.2a1 1 0 0 1 .96 1.27l-1.7 6A1 1 0 0 1 18.5 19H5a2 2 0 0 1-2-2z"/>'
    ),
    "play": '<path d="M8 5.5v13l10-6.5z"/>',
    "pause": '<path d="M9 5v14"/><path d="M15 5v14"/>',
    "x": '<path d="M6 6l12 12"/><path d="M18 6 6 18"/>',
    "repeat": '<path d="m17 2 4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
    "repeat-1": '<path d="m17 2 4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/><path d="M11 10h1v4"/>',
    "shuffle": '<path d="M2 18h1.4c1.3 0 2.5-.6 3.3-1.7l6.1-8.6c.7-1.1 2-1.7 3.3-1.7H22"/><path d="m18 2 4 4-4 4"/><path d="M2 6h1.9c1.5 0 2.9.9 3.6 2.2"/><path d="M22 18h-5.9c-1.3 0-2.6-.7-3.3-1.8l-.5-.8"/><path d="m18 14 4 4-4 4"/>',
    "minus": '<path d="M5 12h14"/>',
    "square": '<rect x="5" y="5" width="14" height="14" rx="1.5"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "check-circle": '<circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.5 2.5 2.5 4.5-5"/>',
    "chevron-down": '<path d="m6 9.5 6 6 6-6"/>',
    "chevron-right": '<path d="m9.5 6 6 6-6 6"/>',
    "chevron-left": '<path d="m14.5 6-6 6 6 6"/>',
    "sliders": '<path d="M4 7h10"/><circle cx="17" cy="7" r="2"/><path d="M20 17H10"/><circle cx="7" cy="17" r="2"/>',
    "grid": (
        '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>'
        '<rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>'
    ),
    "table": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18"/><path d="M3 14.5h18"/><path d="M9 10v9"/>',
    "film": (
        '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 5v14"/><path d="M17 5v14"/>'
        '<path d="M3 12h4"/><path d="M17 12h4"/>'
    ),
    "music": '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
    "subtitles": (
        '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 14.5h4"/><path d="M13 14.5h4"/>'
        '<path d="M7 10.5h2"/><path d="M11 10.5h6"/>'
    ),
    "queue-plus": '<path d="M4 6h10"/><path d="M4 12h10"/><path d="M4 18h6"/><path d="M18 14v6"/><path d="M15 17h6"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v4.5h-4.5"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "eye": '<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z"/><circle cx="12" cy="12" r="2.5"/>',
    "hdd": '<rect x="3" y="7" width="18" height="10" rx="2"/><circle cx="17" cy="12" r="1"/><path d="M6 12h6"/>',
    "gauge": '<path d="M4 15a8 8 0 1 1 16 0"/><path d="M12 15l3.5-4.5"/><circle cx="12" cy="15" r="1"/>',
    "shield": '<path d="M12 3l7 3v5.5c0 4.5-3 7.8-7 9.5-4-1.7-7-5-7-9.5V6z"/><path d="m9.5 12 2 2 3.5-4"/>',
    "bell": '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    "trash": '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/><path d="M10 11v6"/><path d="M14 11v6"/>',
    "sparkle": (
        '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>'
        '<path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>'
    ),
    "more": (
        '<circle cx="12" cy="5" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/>'
        '<circle cx="12" cy="19" r="1.2" fill="currentColor"/>'
    ),
    "waveform": '<path d="M4 10v4"/><path d="M8 7v10"/><path d="M12 4v16"/><path d="M16 7v10"/><path d="M20 10v4"/>',
    "arrow-down-circle": '<circle cx="12" cy="12" r="8.5"/><path d="M12 8v8"/><path d="m8.5 12.5 3.5 3.5 3.5-3.5"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    "user": '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5"/><circle cx="12" cy="8" r=".9" fill="currentColor"/>',
    "alert": '<path d="M12 4 2.5 20h19z"/><path d="M12 10v5"/><circle cx="12" cy="17.5" r=".9" fill="currentColor"/>',
    "archive": '<path d="M3 8l9-4 9 4-9 4z"/><path d="M3 8v8l9 4 9-4V8"/><path d="M12 12v8"/>',
    "zap": '<path d="M13 3 5 13h6l-1 8 8-10h-6z"/>',
    "external": '<path d="M14 5h5v5"/><path d="M19 5l-8 8"/><path d="M19 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h4"/>',
    "globe": '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><path d="M12 3.5c3 3 3 14 0 17"/><path d="M12 3.5c-3 3-3 14 0 17"/>',
    "arrows-v": '<path d="M8 5v14"/><path d="m5 8 3-3 3 3"/><path d="M16 19V5"/><path d="m13 16 3 3 3-3"/>',
    "cpu": (
        '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/>'
        '<path d="M9 3v3"/><path d="M15 3v3"/><path d="M9 18v3"/><path d="M15 18v3"/>'
        '<path d="M3 9h3"/><path d="M3 15h3"/><path d="M18 9h3"/><path d="M18 15h3"/>'
    ),
    "filter": '<path d="M4 5h16l-6 8v5l-4 2v-7z"/>',
    "list": '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h10"/>',
    "headphones": '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="4" y="14" width="4" height="6" rx="1.5"/><rect x="16" y="14" width="4" height="6" rx="1.5"/>',
    "tag": '<path d="M3 12V4h8l9 9-8 8z"/><circle cx="7.5" cy="8.5" r="1.2" fill="currentColor"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8"/><path d="M12 16v4"/>',
    "wand": '<path d="M4 20 15 9"/><path d="m13 7 4 4"/><path d="M18 3v2"/><path d="M21 6h-2"/><path d="M18 9v2"/><path d="M15 6h-2"/>',
}


def svg(name: str, color: str, size: int = 24) -> str:
    body = _PATHS[name]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{_STROKE}" stroke-linecap="round" '
        f'stroke-linejoin="round">{body.replace("currentColor", color)}</svg>'
    )


@lru_cache(maxsize=512)
def pixmap(name: str, color: str, size: int = 18) -> QPixmap:
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg(name, color).encode()))
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px.width(), px.height()))
    painter.end()
    px.setDevicePixelRatio(dpr)
    return px


def icon(name: str, color: str, size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color, size))


def names() -> list[str]:
    return sorted(_PATHS)
