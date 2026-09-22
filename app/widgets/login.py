"""YouTube sign-in through the user's real Chrome/Edge (see app.chrome): a small modal that waits for the
browser window the user signs in with, then reports the exported cookies.

    login.open_login(parent, on_done)   # on_done(cookie_count) once cookies.txt is written
    login.is_logged_in() / login.status() / login.logout()
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QWidget

from .. import chrome
from ..chrome import COOKIES_PATH, is_logged_in, logout  # noqa: F401 - re-exported for the settings screen
from ..theme import C
from .primitives import Button, IconLabel, hbox, label, vbox


def status() -> str:
    """Short line for the settings screen."""
    if not is_logged_in():
        return "로그인되어 있지 않습니다."
    when = datetime.fromtimestamp(COOKIES_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    count = sum(1 for line in COOKIES_PATH.read_text(encoding="utf-8", errors="replace").splitlines() if line and not line.startswith("# "))
    return f"로그인됨 · 쿠키 {count}개 · {when} 저장"


def open_login(parent: QWidget | None, on_done: Callable[[int], None]) -> None:
    dlg = LoginDialog(parent)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        on_done(dlg.count)
    dlg.deleteLater()


class LoginDialog(QDialog):
    """Launches the browser and polls it every second; closes itself once the session is exported."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("YouTube 로그인")
        self.setModal(True)
        self.setFixedWidth(420)
        self.setStyleSheet(f"QDialog {{ background: {C.BG0}; }}")
        self.count = 0
        self.session: chrome.LoginSession | None = None

        lay = vbox(self, gap=14, margins=(24, 24, 24, 20))
        head = hbox(gap=12)
        head.addWidget(IconLabel("user", C.ACCENT, 22))
        self.title = label("브라우저 창에서 로그인해 주세요", "subtitle")
        head.addWidget(self.title, 1)
        lay.addLayout(head)
        self.body = label("", "secondary", wrap=True)
        lay.addWidget(self.body)
        row = hbox(gap=8)
        row.addStretch(1)
        self.cancel_btn = Button("취소", "secondary")
        self.cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.cancel_btn)
        lay.addLayout(row)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._poll)
        QTimer.singleShot(0, self._start)

    def _start(self) -> None:
        try:
            self.session = chrome.start_login()
        except (chrome.LoginError, OSError) as exc:
            self._fail(str(exc))
            return
        name = chrome.browser_label(self.session.exe)
        self.body.setText(
            f"{name} 창이 열렸습니다. 그 창에서 Google 계정으로 로그인하면 이 창은 자동으로 닫힙니다.\n"
            "평소 쓰는 브라우저 프로필과는 분리된 Ferry 전용 창이라 기존 로그인 상태에는 영향이 없습니다."
        )
        self.timer.start()

    def _poll(self) -> None:
        if self.session is None:
            return
        try:
            result = self.session.poll()
        except chrome.LoginError as exc:
            self._fail(str(exc))
            return
        if result is not None:
            self.timer.stop()
            self.count = result
            self.accept()

    def _fail(self, message: str) -> None:
        self.timer.stop()
        self.title.setText("로그인하지 못했습니다")
        self.body.setText(message)
        self.body.setStyleSheet(f"color: {C.ACCENT}; background: transparent;")
        self.cancel_btn.setText("닫기")

    def reject(self) -> None:
        self.timer.stop()
        if self.session is not None:
            self.session.cancel()
        super().reject()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)
