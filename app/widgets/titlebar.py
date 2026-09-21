"""Custom window chrome: brand mark, clipboard watcher pill, window controls."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QSizePolicy, QWidget

from .. import APP_NAME, APP_VERSION, context, updater
from ..icon import render as render_icon
from ..icons import icon
from ..theme import C
from .primitives import Badge, Button, IconLabel, PulseDot, hbox, label


class BrandMark(QWidget):
    """The app icon, rendered at widget size (same drawing as assets/icon.ico)."""

    def __init__(self, size: int = 30, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        dpr = self.devicePixelRatioF()
        px = render_icon(self.width(), dpr)
        px.setDevicePixelRatio(dpr)
        p.drawPixmap(0, 0, px)
        p.end()


class WindowButton(QPushButton):
    def __init__(self, icon_name: str, danger: bool = False, parent=None):
        super().__init__(parent)
        self.setProperty("variant", "window")
        self.setProperty("danger", danger)
        self.setIcon(icon(icon_name, C.TEXT2, 14))
        self.setFixedSize(40, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


class TitleBar(QFrame):
    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(64)
        self.setStyleSheet(f"#TitleBar {{ background: {C.BG0}; border-bottom: 1px solid {C.BORDER}; }}")
        lay = hbox(self, gap=16, margins=(20, 0, 12, 0))

        # brand
        brand = hbox(gap=10)
        brand.addWidget(BrandMark())
        brand.addWidget(label(APP_NAME, "title", size=17))
        self.version_badge = Badge(f"v{APP_VERSION}", "neutral", mono=True)
        brand.addWidget(self.version_badge)
        # self-update: the badge shows download progress, then this button offers the restart
        self.update_btn = Button("", "primary", "refresh", "sm")
        self.update_btn.clicked.connect(lambda: updater.apply(restart=True) and QApplication.quit())
        self.update_btn.hide()
        brand.addWidget(self.update_btn)
        context.bus.update_status.connect(self._on_update_status)
        context.bus.update_ready.connect(self._on_update_ready)
        lay.addLayout(brand)
        lay.addSpacing(16)

        # clipboard watcher pill
        pill = QFrame()
        pill.setProperty("surface", True)
        pill.setFixedHeight(38)
        pill.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        pill.setMaximumWidth(720)
        prow = hbox(pill, gap=10, margins=(14, 0, 8, 0))
        prow.addWidget(IconLabel("clipboard", C.TEXT2, 16))
        self.clip_label = label("", "secondary", elide=True)
        prow.addWidget(self.clip_label, 1)
        self.clip_dot = PulseDot(C.SUCCESS, 6)
        prow.addWidget(self.clip_dot)
        self.clip_badge = Badge("AUTO", "success", mono=True)
        prow.addWidget(self.clip_badge)
        lay.addWidget(pill, 1)

        lay.addStretch()

        # window controls
        controls = hbox(gap=2)
        b_min = WindowButton("minus")
        b_max = WindowButton("square")
        b_close = WindowButton("x", danger=True)
        b_min.clicked.connect(self.minimize_requested)
        b_max.clicked.connect(self.maximize_requested)
        b_close.clicked.connect(self.close_requested)
        for b in (b_min, b_max, b_close):
            controls.addWidget(b)
        lay.addLayout(controls)

    def _on_update_status(self, text: str) -> None:
        self.version_badge.setText(text or f"v{APP_VERSION}")

    def _on_update_ready(self, version: str) -> None:
        self.version_badge.setText(f"v{APP_VERSION}")
        self.update_btn.setText(f"v{version} 재시작하여 적용")
        self.update_btn.setToolTip("지금 재시작하지 않아도 앱을 닫을 때 자동으로 적용됩니다")
        self.update_btn.show()

    def set_clipboard(self, enabled: bool, last_url: str | None = None) -> None:
        if not enabled:
            self.clip_label.setText("클립보드 감지 꺼짐 · 환경설정에서 켤 수 있습니다")
            self.clip_badge.setText("OFF")
        elif last_url:
            self.clip_label.setText(f"클립보드에서 링크 감지: {last_url}")
            self.clip_badge.setText("AUTO")
        else:
            self.clip_label.setText("클립보드 감지 활성화: YouTube 링크를 복사하면 자동으로 분석합니다")
            self.clip_badge.setText("AUTO")
        self.clip_dot.setVisible(enabled)
        self.clip_badge.setProperty("badge", "success" if enabled else "neutral")
        self.clip_badge.style().unpolish(self.clip_badge)
        self.clip_badge.style().polish(self.clip_badge)

    # --- native drag / double-click maximise -------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximize_requested.emit()
        super().mouseDoubleClickEvent(event)
