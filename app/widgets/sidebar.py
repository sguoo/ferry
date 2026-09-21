"""Workspace navigation and the machine-status block."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QPushButton, QWidget

from .. import context, fonts, youtube
from ..icons import icon
from ..theme import C, S
from ..youtube import fmt_size, fmt_speed
from .primitives import Badge, Divider, IconLabel, ProgressBar, hbox, label, micro, num, set_prop, vbox

NAV_ITEMS = [
    ("downloader", "다운로더", "download"),
    ("playlist", "재생목록 & 일괄", "playlist"),
    ("library", "라이브러리", "library"),
    ("settings", "환경설정", "gear"),
]


class NavButton(QPushButton):
    def __init__(self, key: str, text: str, icon_name: str, parent=None):
        super().__init__(text, parent)
        self.key = key
        self._icon_name = icon_name
        self.setProperty("nav", True)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(fonts.sans(14, 500))
        self.setIconSize(QSize(18, 18))
        self.setFixedHeight(42)
        self.toggled.connect(self._sync_icon)
        self._sync_icon(False)

    def _sync_icon(self, on: bool) -> None:
        self.setIcon(icon(self._icon_name, C.ACCENT if on else C.TEXT2, 18))


class StatLine(QWidget):
    def __init__(self, icon_name: str, text: str, parent=None):
        super().__init__(parent)
        lay = hbox(self, gap=8)
        lay.addWidget(IconLabel(icon_name, C.TEXT2, 15))
        self.text = label(text, "caption-strong", elide=True)
        lay.addWidget(self.text, 1)
        self.value = num("", 13, 600)
        lay.addWidget(self.value)
        self.badge = Badge("", "success", mono=True)
        self.badge.hide()
        lay.addWidget(self.badge)


class Sidebar(QFrame):
    navigated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(248)
        self.setStyleSheet(f"#Sidebar {{ background: {C.BG1}; border-right: 1px solid {C.BORDER}; }}")
        lay = vbox(self, gap=S.TIGHT, margins=(16, 20, 16, 16))

        head = hbox(gap=8)
        head.addWidget(micro("Workspaces"))
        head.addStretch()
        self.online = Badge("ONLINE", "success", mono=True)
        head.addWidget(self.online)
        lay.addLayout(head)
        lay.addSpacing(6)

        self._buttons: dict[str, NavButton] = {}
        for key, text, icon_name in NAV_ITEMS:
            b = NavButton(key, text, icon_name)
            b.clicked.connect(lambda _=False, k=key: self.select(k))
            self._buttons[key] = b
            lay.addWidget(b)

        lay.addStretch()

        lay.addWidget(Divider())
        lay.addSpacing(8)
        self.disk = StatLine("hdd", "저장 드라이브 여유")
        lay.addWidget(self.disk)
        self.disk_bar = ProgressBar(0.0, 4, "muted")
        lay.addWidget(self.disk_bar)
        foot = hbox()
        self.disk_total = label("", "muted", mono=True)
        self.disk_used = label("", "muted", mono=True)
        foot.addWidget(self.disk_total)
        foot.addStretch()
        foot.addWidget(self.disk_used)
        lay.addLayout(foot)
        lay.addSpacing(10)
        self.net = StatLine("gauge", "다운로드 속도")
        lay.addWidget(self.net)
        lay.addSpacing(4)
        self.tools = StatLine("cpu", f"yt-dlp {youtube.ytdlp_version()}")
        self.tools.badge.show()
        lay.addWidget(self.tools)

        context.jobs.speed_changed.connect(self._on_speed)
        context.bus.settings_changed.connect(self.refresh_env)
        self._disk_timer = QTimer(self)
        self._disk_timer.setInterval(30_000)
        self._disk_timer.timeout.connect(self.refresh_env)
        self._disk_timer.start()
        self.refresh_env()
        self._on_speed(0.0)

    def select(self, key: str) -> None:
        for k, b in self._buttons.items():
            b.setChecked(k == key)
        self.navigated.emit(key)

    # ---------------------------------------------------------------- stats
    def refresh_env(self) -> None:
        folder = Path(context.settings.save_path)
        probe = folder if folder.exists() else next((p for p in folder.parents if p.exists()), Path.home())
        try:
            usage = shutil.disk_usage(probe)
            used = usage.used / usage.total if usage.total else 0.0
            self.disk.value.setText(fmt_size(usage.free))
            self.disk_bar.set_value(used)
            self.disk_total.setText(f"전체 {fmt_size(usage.total)}")
            self.disk_used.setText(f"{used * 100:.1f}% 사용")
        except OSError:
            self.disk.value.setText("-")
        ver = youtube.ffmpeg_version(context.settings.ffmpeg_path or None)
        self.tools.badge.setText("READY" if ver else "NO FFMPEG")
        set_prop(self.tools.badge, "badge", "success" if ver else "accent")

    def _on_speed(self, bps: float) -> None:
        active = len(context.jobs.active)
        self.net.value.setText(fmt_speed(bps) if active else "유휴")
        self.net.text.setText(f"다운로드 속도 · {active}개 진행" if active else "다운로드 속도")
