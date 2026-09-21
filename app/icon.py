"""App icon: drawn with QPainter so the tile, tray icon and .ico all share one source.

`write_icon_files()` regenerates assets/icon.png and assets/icon.ico (multi-size, PNG-compressed
entries, which Windows Vista+ accepts). Run `uv run python -m app.icon` after changing the drawing.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from .theme import ASSETS, C

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_PATH = ASSETS / "icon.png"
ICO_PATH = ASSETS / "icon.ico"


def render(size: int, dpr: float = 1.0) -> QPixmap:
    """Accent tile with a down-arrow landing on a bar (download), rounded like the UI."""
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(dpr, dpr)
    s = float(size)
    radius = s * 0.24

    tile = QPainterPath()
    tile.addRoundedRect(QRectF(0, 0, s, s), radius, radius)
    grad = QLinearGradient(0, 0, s, s)
    grad.setColorAt(0.0, QColor(C.ACCENT_HOVER))
    grad.setColorAt(1.0, QColor(C.ACCENT_DOWN))
    p.fillPath(tile, grad)

    # soft highlight along the top edge so the tile reads as a physical surface
    sheen = QLinearGradient(0, 0, 0, s * 0.55)
    sheen.setColorAt(0.0, QColor(255, 255, 255, 46))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setClipPath(tile)
    p.fillRect(QRectF(0, 0, s, s * 0.55), sheen)
    p.setClipping(False)

    stroke = max(1.5, s * 0.115)
    pen = QPen(QColor(C.ACCENT_FG), stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    cx = s / 2
    top, tip = s * 0.24, s * 0.62
    p.drawLine(QPointF(cx, top), QPointF(cx, tip))
    wing = s * 0.19
    p.drawLine(QPointF(cx - wing, tip - wing), QPointF(cx, tip))
    p.drawLine(QPointF(cx + wing, tip - wing), QPointF(cx, tip))
    p.drawLine(QPointF(s * 0.27, s * 0.78), QPointF(s * 0.73, s * 0.78))
    p.end()
    return px


def app_icon() -> QIcon:
    if ICO_PATH.exists():
        return QIcon(str(ICO_PATH))
    ico = QIcon()
    for size in ICO_SIZES:
        ico.addPixmap(render(size))
    return ico


def _png_bytes(size: int) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    render(size).save(buf, "PNG")
    return bytes(buf.data())


def write_icon_files(png: Path = PNG_PATH, ico: Path = ICO_PATH) -> tuple[Path, Path]:
    png.parent.mkdir(parents=True, exist_ok=True)
    render(512).save(str(png), "PNG")
    blobs = [(size, _png_bytes(size)) for size in ICO_SIZES]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, payload = b"", b""
    for size, data in blobs:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(payload))
        payload += data
    ico.write_bytes(header + entries + payload)
    return png, ico


if __name__ == "__main__":  # pragma: no cover
    from PySide6.QtGui import QGuiApplication

    QGuiApplication([])
    out = write_icon_files()
    print("wrote", *out)
