"""Reusable building blocks shared by every screen."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import fonts
from ..icons import icon, pixmap
from ..theme import ASSETS, C, R, S

PLACEHOLDER_DIR = ASSETS / "placeholders"

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def set_prop(widget: QWidget, name: str, value) -> None:
    """Set a dynamic property and re-polish so the stylesheet picks it up."""
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def hbox(parent: QWidget | None = None, gap: int = S.GAP, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    layout = QHBoxLayout(parent) if parent else QHBoxLayout()
    layout.setSpacing(gap)
    layout.setContentsMargins(*margins)
    return layout


def vbox(parent: QWidget | None = None, gap: int = S.GAP, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    layout = QVBoxLayout(parent) if parent else QVBoxLayout()
    layout.setSpacing(gap)
    layout.setContentsMargins(*margins)
    return layout


def stretch_policy(widget: QWidget, h=QSizePolicy.Policy.Expanding, v=QSizePolicy.Policy.Preferred) -> QWidget:
    widget.setSizePolicy(h, v)
    return widget


# --------------------------------------------------------------------------- #
# typography
# --------------------------------------------------------------------------- #

_ROLES = {
    # role: (size, weight, colour, tracking)
    "display": (24, 600, C.TEXT, 97),
    "headline": (20, 600, C.TEXT, 98),
    "title": (16, 600, C.TEXT, None),
    "subtitle": (14, 600, C.TEXT, None),
    "body": (13, 400, C.TEXT, None),
    "body-strong": (13, 500, C.TEXT, None),
    "secondary": (13, 400, C.TEXT2, None),
    "caption": (12, 400, C.TEXT2, None),
    "caption-strong": (12, 500, C.TEXT2, None),
    "muted": (12, 400, C.TEXT3, None),
    "micro": (11, 600, C.TEXT3, 108),
}


class ElidedLabel(QLabel):
    """Single-line label that trims with an ellipsis instead of forcing width."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.text_color = C.TEXT
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def minimumSizeHint(self):
        return QSize(24, super().minimumSizeHint().height())

    def paintEvent(self, _):
        p = QPainter(self)
        elided = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        p.setPen(QColor(self.text_color))
        p.drawText(self.rect(), int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), elided)
        p.end()


def label(
    text: str = "",
    role: str = "body",
    color: str | None = None,
    mono: bool = False,
    size: int | None = None,
    weight: int | None = None,
    wrap: bool = False,
    align: Qt.AlignmentFlag | None = None,
    elide: bool = False,
) -> QLabel:
    lbl = ElidedLabel(text) if elide else QLabel(text)
    r_size, r_weight, r_color, tracking = _ROLES[role]
    size = size or r_size
    weight = weight or r_weight
    font = fonts.mono(size, weight) if mono else fonts.sans(size, weight, tracking)
    if role == "micro":
        font.setCapitalization(fonts.QFont.Capitalization.AllUppercase)
    lbl.setFont(font)
    lbl.setStyleSheet(f"color: {color or r_color}; background: transparent;")
    if elide:
        lbl.text_color = color or r_color
    lbl.setWordWrap(wrap)
    if align is not None:
        lbl.setAlignment(align)
    return lbl


def micro(text: str, color: str = C.TEXT3) -> QLabel:
    return label(text, "micro", color=color)


def num(text: str, size: int = 13, weight: int = 500, color: str = C.TEXT) -> QLabel:
    """Numbers are always monospace (VISUAL_DENSITY rule)."""
    return label(text, "body", color=color, mono=True, size=size, weight=weight)


# --------------------------------------------------------------------------- #
# badges / chips
# --------------------------------------------------------------------------- #


class Badge(QLabel):
    def __init__(self, text: str, tone: str = "neutral", mono: bool = False, max_px: int | None = None, parent=None):
        super().__init__(parent)
        self.setProperty("badge", tone)
        self.setFont(fonts.mono(11, 600) if mono else fonts.sans(11, 600))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._max_px = max_px
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        if self._max_px:
            self.setToolTip(text)
            text = self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, self._max_px)
        super().setText(text)


class Kbd(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setProperty("kbd", True)
        self.setFont(fonts.mono(11, 500))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class IconLabel(QLabel):
    def __init__(self, name: str, color: str = C.TEXT2, size: int = 16, parent=None):
        super().__init__(parent)
        self._name, self._color, self._size = name, color, size
        self.setPixmap(pixmap(name, color, size))
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        self._color = color
        self.setPixmap(pixmap(self._name, color, self._size))

    def set_icon(self, name: str) -> None:
        self._name = name
        self.setPixmap(pixmap(name, self._color, self._size))


def icon_text(name: str, text: str, role: str = "caption", icon_color: str | None = None, gap: int = 6, mono=False, elide=False) -> QWidget:
    """Inline icon + text pair used for metadata rows."""
    w = QWidget()
    lay = hbox(w, gap=gap)
    lay.addWidget(IconLabel(name, icon_color or _ROLES[role][2], 14))
    lay.addWidget(label(text, role, mono=mono, elide=elide), 1 if elide else 0)
    return w


class IconTile(QFrame):
    """Small square tile holding an icon; used as section markers."""

    def __init__(self, name: str, tone: str = "accent", size: int = 40, parent=None):
        super().__init__(parent)
        self.setProperty("icontile", True if tone == "accent" else "neutral")
        self.setFixedSize(size, size)
        color = C.ACCENT if tone == "accent" else C.TEXT2
        lay = hbox(self, gap=0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(IconLabel(name, color, 20))


# --------------------------------------------------------------------------- #
# buttons
# --------------------------------------------------------------------------- #


class Button(QPushButton):
    def __init__(
        self,
        text: str = "",
        variant: str = "secondary",
        icon_name: str | None = None,
        size: str = "md",
        parent=None,
    ):
        super().__init__(text, parent)
        self.setProperty("variant", variant)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        weight = 600 if variant == "primary" else 500
        font_size = {"sm": 12, "md": 13, "lg": 15}[size]
        self.setFont(fonts.sans(font_size, weight))
        if size == "lg":
            self.setMinimumHeight(52)
        elif size == "sm":
            self.setStyleSheet("padding: 5px 10px;")
        if icon_name:
            color = C.ACCENT_FG if variant == "primary" else (C.TEXT2 if variant == "ghost" else C.TEXT)
            self.setIcon(icon(icon_name, color, 16))
            self.setIconSize(QSize(16, 16))


class IconButton(QPushButton):
    def __init__(self, icon_name: str, tooltip: str = "", flat: bool = False, color: str = C.TEXT2, size: int = 16, parent=None):
        super().__init__(parent)
        self.setProperty("variant", "icon")
        self.setProperty("flat", flat)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIcon(icon(icon_name, color, size))
        self.setIconSize(QSize(size, size))
        self.setFixedSize(size + 16, size + 16)
        if tooltip:
            self.setToolTip(tooltip)


class Segmented(QFrame):
    """Pill group with one active option."""

    changed = Signal(int)

    def __init__(self, options: list[tuple[str, str | None]] | list[str], active: int = 0, tone: str = "neutral", parent=None):
        super().__init__(parent)
        self.setProperty("segmented", True)
        self._buttons: list[QPushButton] = []
        lay = hbox(self, gap=2, margins=(3, 3, 3, 3))
        for i, opt in enumerate(options):
            text, icon_name = (opt, None) if isinstance(opt, str) else opt
            b = QPushButton(text)
            b.setProperty("segment", True)
            b.setProperty("tone", tone)
            b.setCheckable(True)
            b.setChecked(i == active)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(fonts.sans(13, 500))
            if icon_name:
                b.setProperty("icon_name", icon_name)
                b.setIcon(icon(icon_name, C.TEXT2, 15))
                b.setIconSize(QSize(15, 15))
            b.clicked.connect(lambda _=False, idx=i: self.set_active(idx))
            self._buttons.append(b)
            lay.addWidget(b)
        self._sync_icons()

    def set_active(self, idx: int) -> None:
        for i, b in enumerate(self._buttons):
            b.setChecked(i == idx)
        self._sync_icons()
        self.changed.emit(idx)

    def active_index(self) -> int:
        for i, b in enumerate(self._buttons):
            if b.isChecked():
                return i
        return 0

    def _sync_icons(self) -> None:
        tone = self._buttons[0].property("tone") if self._buttons else "neutral"
        for b in self._buttons:
            if b.icon().isNull():
                continue
            name = b.property("icon_name")
            if not name:
                continue
            color = (C.ACCENT if tone == "accent" else C.TEXT) if b.isChecked() else C.TEXT2
            b.setIcon(icon(name, color, 15))


class Chip(QFrame):
    """Filter chip with optional icon and count badge."""

    clicked = Signal()

    def __init__(self, text: str, count: str | None = None, icon_name: str | None = None, active: bool = False, parent=None):
        super().__init__(parent)
        self.setProperty("chip", True)
        self.setProperty("active", active)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = hbox(self, gap=8, margins=(12, 7, 12, 7))
        self._icon = IconLabel(icon_name, C.ACCENT if active else C.TEXT2, 15) if icon_name else None
        if self._icon:
            lay.addWidget(self._icon)
        self._text = label(text, "body-strong", color=C.ACCENT if active else C.TEXT2)
        lay.addWidget(self._text)
        self._badge = Badge(count, "accent" if active else "neutral", mono=True) if count is not None else None
        if self._badge:
            lay.addWidget(self._badge)

    def set_active(self, active: bool) -> None:
        set_prop(self, "active", active)
        color = C.ACCENT if active else C.TEXT2
        self._text.setStyleSheet(f"color: {color}; background: transparent;")
        if self._icon:
            self._icon.set_color(color)
        if self._badge:
            set_prop(self._badge, "badge", "accent" if active else "neutral")

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class Toggle(QAbstractButton):
    """Animated switch (transform-only motion, spring-like easing)."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._anim.valueChanged.connect(self._on_value)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _on_value(self, v) -> None:
        self._pos = float(v)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = max(0.0, min(1.0, self._pos))
        track = QColor(C.BG4)
        accent = QColor(C.ACCENT)
        mix = QColor(
            int(track.red() + (accent.red() - track.red()) * t),
            int(track.green() + (accent.green() - track.green()) * t),
            int(track.blue() + (accent.blue() - track.blue()) * t),
        )
        p.setPen(QPen(QColor(C.BORDER2), 1))
        p.setBrush(mix)
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 11, 11)
        knob_x = 3 + self._pos * (self.width() - 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C.TEXT))
        p.drawEllipse(QRectF(knob_x, 3, 16, 16))
        p.end()


class Check(QCheckBox):
    def __init__(self, text: str = "", checked: bool = False, parent=None):
        super().__init__(text, parent)
        self.setChecked(checked)
        self.setFont(fonts.sans(13, 500))
        self.setCursor(Qt.CursorShape.PointingHandCursor)


# --------------------------------------------------------------------------- #
# surfaces
# --------------------------------------------------------------------------- #


class Panel(QFrame):
    def __init__(self, padding: int = S.PANEL, gap: int = S.GAP, parent=None):
        super().__init__(parent)
        self.setProperty("panel", True)
        self.body = vbox(self, gap=gap, margins=(padding, padding, padding, padding))


class Divider(QFrame):
    def __init__(self, vertical: bool = False, parent=None):
        super().__init__(parent)
        self.setProperty("vdivider" if vertical else "divider", True)
        if vertical:
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class CompositeField(QFrame):
    """Bordered field that hosts an icon, a bare QLineEdit and trailing widgets."""

    def __init__(self, icon_name: str, placeholder: str = "", value: str = "", mono: bool = False, parent=None):
        super().__init__(parent)
        self.setProperty("composite", True)
        self.setMinimumHeight(44)
        lay = hbox(self, gap=10, margins=(12, 0, 8, 0))
        self.icon = IconLabel(icon_name, C.TEXT3, 16)
        lay.addWidget(self.icon)
        self.edit = QLineEdit(value)
        self.edit.setProperty("bare", True)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFont(fonts.mono(13, 500) if mono else fonts.sans(14, 500))
        self.edit.setStyleSheet(f"QLineEdit {{ color: {C.TEXT}; }} QLineEdit::placeholder {{ color: {C.TEXT3}; }}")
        self.edit.setCursorPosition(0)
        lay.addWidget(self.edit, 1)
        self.trailing = hbox(gap=6)
        lay.addLayout(self.trailing)
        self.edit.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.edit:
            if event.type() == event.Type.FocusIn:
                set_prop(self, "focus", True)
                self.icon.set_color(C.ACCENT)
            elif event.type() == event.Type.FocusOut:
                set_prop(self, "focus", False)
                self.icon.set_color(C.TEXT3)
        return super().eventFilter(obj, event)

    def add_trailing(self, w: QWidget) -> None:
        self.trailing.addWidget(w)

    def set_value(self, text: str) -> None:
        """Set text and keep the start visible (long URLs otherwise scroll to the end)."""
        self.edit.setText(text)
        self.edit.setCursorPosition(0)


class FormField(QWidget):
    """Label above input, helper below, optional inline error (Rule 6)."""

    def __init__(self, title: str, control: QWidget, helper: str = "", error: str = "", parent=None):
        super().__init__(parent)
        self.control = control
        lay = vbox(self, gap=8)
        lay.addWidget(label(title, "body-strong"))
        lay.addWidget(control)
        self.helper = label(helper, "muted", wrap=True)
        self.helper.setVisible(bool(helper))
        lay.addWidget(self.helper)
        self._error_row = QWidget()
        row = hbox(self._error_row, gap=6)
        row.addWidget(IconLabel("alert", C.ACCENT, 14), 0, Qt.AlignmentFlag.AlignTop)
        self._error_label = label("", "caption", color=C.ACCENT, wrap=True)
        row.addWidget(self._error_label, 1)
        lay.addWidget(self._error_row)
        self.set_error(error)

    def set_helper(self, text: str) -> None:
        self.helper.setText(text)
        self.helper.setVisible(bool(text))

    def set_error(self, text: str | None) -> None:
        self._error_label.setText(text or "")
        self._error_row.setVisible(bool(text))
        if isinstance(self.control, (QLineEdit, CompositeField)):
            set_prop(self.control, "state", "error" if text else "")


def clear_layout(layout) -> None:
    """Delete every widget / sub-layout in a layout (used to rebuild dynamic sections)."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


# --------------------------------------------------------------------------- #
# data display
# --------------------------------------------------------------------------- #


class ProgressBar(QWidget):
    def __init__(self, value: float = 0.0, height: int = 6, tone: str = "accent", parent=None):
        super().__init__(parent)
        self._value = max(0.0, min(1.0, value))
        self._tone = tone
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, v: float) -> None:
        self._value = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C.BG4))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), r, r)
        fill = {"accent": C.ACCENT, "success": C.SUCCESS, "warn": C.WARN, "muted": C.TEXT3}[self._tone]
        w = self.width() * self._value
        if w > 0:
            p.setBrush(QColor(fill))
            p.drawRoundedRect(QRectF(0, 0, max(w, self.height()), self.height()), r, r)
        p.end()


class PulseDot(QWidget):
    """Breathing status dot (perpetual micro-interaction, opacity-only)."""

    def __init__(self, color: str = C.SUCCESS, size: int = 8, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._t = 0.0
        self.setFixedSize(size * 3, size * 3)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1900)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.valueChanged.connect(self._tick)
        self._anim.start()

    def _tick(self, v):
        self._t = float(v)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        base = self.width() / 6
        ring = QColor(self._color)
        ring.setAlphaF(0.35 * (1 - self._t))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ring)
        p.drawEllipse(c, base * (1 + self._t * 1.6), base * (1 + self._t * 1.6))
        p.setBrush(self._color)
        p.drawEllipse(c, base, base)
        p.end()


class Thumbnail(QWidget):
    """Video frame stand-in: a shipped placeholder photo keyed by seed
    (assets/placeholders/<seed>.jpg), falling back to a seeded gradient."""

    def __init__(self, seed: str, width: int = 320, duration: str = "", badges: list[tuple[str, str]] | None = None, radius: int = R.MD, image=None, parent=None):
        super().__init__(parent)
        self._seed = seed
        self._duration = duration
        self._badges = badges or []
        self._radius = radius
        self._image_cache: QPixmap | None = None
        self._image_path = image
        self.setFixedSize(width, int(width * 9 / 16))
        self._badge_widgets: list[Badge] = []
        if self._badges:
            row = hbox(gap=6, margins=(8, 8, 8, 8))
            row.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            holder = QWidget(self)
            holder.setLayout(row)
            for text, tone in self._badges:
                badge = Badge(text, tone, mono=True)
                badge.setProperty("overlay", True)  # opaque backing over photos
                row.addWidget(badge)
            holder.adjustSize()
            holder.move(0, 0)

    def _hue_pair(self) -> tuple[float, float]:
        h = hashlib.sha1(self._seed.encode()).digest()
        h1 = h[0] / 255
        h2 = (h1 + 0.08 + (h[1] / 255) * 0.12) % 1.0
        return h1, h2

    def set_image(self, path) -> None:
        """Swap in a real frame (e.g. a fetched YouTube thumbnail) once it is available."""
        if not path:
            return
        self._image_path = path
        self._image_cache = None
        self.update()

    def set_duration(self, text: str) -> None:
        self._duration = text
        self.update()

    def fetch(self, url: str | None, key: str) -> None:
        """Download (or reuse cached) YouTube thumbnail in the background, then swap it in."""
        if not url:
            return
        import shiboken6

        from .. import workers

        def done(path):
            if path and shiboken6.isValid(self):
                self.set_image(path)

        workers.fetch_thumbnail_async(url, key, done)

    def _image(self) -> QPixmap | None:
        """Explicit image, else placeholder photo for this seed, scaled to cover; None if neither exists."""
        if self._image_cache is not None:
            return self._image_cache or None
        path = Path(self._image_path) if self._image_path else PLACEHOLDER_DIR / f"{self._seed}.jpg"
        px = QPixmap(str(path)) if path.exists() else QPixmap()
        if px.isNull():
            self._image_cache = QPixmap()
            return None
        dpr = self.devicePixelRatioF()
        target = QSize(int(self.width() * dpr), int(self.height() * dpr))
        scaled = px.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        # centre-crop to the widget's aspect
        x = (scaled.width() - target.width()) // 2
        y = (scaled.height() - target.height()) // 2
        cropped = scaled.copy(x, y, target.width(), target.height())
        cropped.setDevicePixelRatio(dpr)
        self._image_cache = cropped
        return cropped

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        p.setClipPath(path)
        image = self._image()
        if image is not None:
            p.drawPixmap(0, 0, image)
            # darken the lower edge so badges and duration stay legible
            shade = QLinearGradient(0, self.height() * 0.45, 0, self.height())
            shade.setColorAt(0.0, QColor(11, 11, 15, 0))
            shade.setColorAt(1.0, QColor(11, 11, 15, 150))
            p.fillRect(rect, QBrush(shade))
        else:
            h1, h2 = self._hue_pair()
            g = QLinearGradient(0, 0, self.width(), self.height())
            g.setColorAt(0.0, QColor.fromHslF(h1, 0.28, 0.24))
            g.setColorAt(1.0, QColor.fromHslF(h2, 0.30, 0.12))
            p.fillRect(rect, QBrush(g))
            glow = QLinearGradient(0, self.height(), self.width(), 0)
            glow.setColorAt(0.35, QColor(255, 255, 255, 0))
            glow.setColorAt(0.5, QColor(255, 255, 255, 14))
            glow.setColorAt(0.65, QColor(255, 255, 255, 0))
            p.fillRect(rect, QBrush(glow))
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), self._radius, self._radius)
        if self._duration:
            f = fonts.mono(11, 600)
            p.setFont(f)
            metrics = p.fontMetrics()
            tw = metrics.horizontalAdvance(self._duration)
            box = QRectF(self.width() - tw - 18, self.height() - 28, tw + 12, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(11, 11, 15, 200))
            p.drawRoundedRect(box, 5, 5)
            p.setPen(QColor(C.TEXT))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, self._duration)
        p.end()


# --------------------------------------------------------------------------- #
# states: loading / empty / error
# --------------------------------------------------------------------------- #


class Skeleton(QWidget):
    """Shimmering placeholder block sized to the layout it replaces."""

    def __init__(self, width: int | None = None, height: int = 14, radius: int = 6, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._t = 0.0
        if width is not None:
            self.setFixedWidth(width)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(height)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(-0.6)
        self._anim.setEndValue(1.6)
        self._anim.setDuration(1500)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.valueChanged.connect(self._tick)
        self._anim.start()

    def _tick(self, v):
        self._t = float(v)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C.BG3))
        p.drawRoundedRect(rect, self._radius, self._radius)
        g = QLinearGradient(self.width() * (self._t - 0.4), 0, self.width() * (self._t + 0.4), 0)
        g.setColorAt(0.0, QColor(255, 255, 255, 0))
        g.setColorAt(0.5, QColor(255, 255, 255, 14))
        g.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(rect, self._radius, self._radius)
        p.end()


class EmptyState(QFrame):
    """Left-aligned composed empty state with a clear next step."""

    def __init__(self, icon_name: str, title: str, description: str, action: str | None = None, hint: str | None = None, parent=None):
        super().__init__(parent)
        self.setProperty("empty", True)
        lay = hbox(self, gap=20, margins=(28, 28, 28, 28))
        lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(IconTile(icon_name, "neutral", 56), 0, Qt.AlignmentFlag.AlignTop)
        col = vbox(gap=6)
        col.addWidget(label(title, "title"))
        col.addWidget(label(description, "secondary", wrap=True))
        if hint:
            col.addSpacing(4)
            row = hbox(gap=8)
            row.addWidget(label(hint, "muted"))
            row.addStretch()
            col.addLayout(row)
        if action:
            col.addSpacing(8)
            row = hbox()
            row.addWidget(Button(action, "secondary"))
            row.addStretch()
            col.addLayout(row)
        lay.addLayout(col, 1)


class ErrorBanner(QFrame):
    def __init__(self, title: str, message: str, action: str = "다시 시도", parent=None):
        super().__init__(parent)
        self.setProperty("banner", "error")
        lay = hbox(self, gap=14, margins=(16, 14, 16, 14))
        lay.addWidget(IconLabel("alert", C.ACCENT, 18), 0, Qt.AlignmentFlag.AlignTop)
        col = vbox(gap=3)
        col.addWidget(label(title, "subtitle"))
        col.addWidget(label(message, "secondary", wrap=True))
        lay.addLayout(col, 1)
        lay.addWidget(Button(action, "secondary", "refresh"), 0, Qt.AlignmentFlag.AlignTop)


# --------------------------------------------------------------------------- #
# motion
# --------------------------------------------------------------------------- #

_active_animations: list[QPropertyAnimation] = []


def reveal(widgets: list[QWidget], step_ms: int = 60, duration_ms: int = 320) -> None:
    """Staggered opacity waterfall for section mounts (MOTION_INTENSITY 6)."""
    for i, w in enumerate(widgets):
        effect = QGraphicsOpacityEffect(w)
        effect.setOpacity(0.0)
        w.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", w)
        anim.setDuration(duration_ms)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        _active_animations.append(anim)

        def _cleanup(widget=w, a=anim):
            widget.setGraphicsEffect(None)
            if a in _active_animations:
                _active_animations.remove(a)

        anim.finished.connect(_cleanup)
        QTimer.singleShot(i * step_ms, anim.start)


# --------------------------------------------------------------------------- #
# layout: flow
# --------------------------------------------------------------------------- #


class FlowLayout(QLayout):
    """Wrapping grid; tiles keep their size and flow to the next line."""

    def __init__(self, parent=None, h_gap: int = 16, v_gap: int = 16):
        super().__init__(parent)
        self._items = []
        self._h, self._v = h_gap, v_gap
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect, test_only):
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            if x + w > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._v
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x += w + self._h
            line_h = max(line_h, h)
        return y + line_h - rect.y()
