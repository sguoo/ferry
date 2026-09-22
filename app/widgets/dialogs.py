"""Small themed modals: a yes/no confirmation and a one-line text prompt (Qt's stock boxes ignore the QSS)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QWidget

from .. import fonts
from ..theme import C
from .primitives import Button, IconLabel, hbox, label, vbox


class _Modal(QDialog):
    def __init__(self, parent: QWidget | None, title: str, body: str, icon_name: str, tone: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(440)
        self.setStyleSheet(f"QDialog {{ background: {C.BG0}; }}")
        self.lay = vbox(self, gap=14, margins=(24, 22, 24, 20))
        head = hbox(gap=12)
        head.addWidget(IconLabel(icon_name, C.ACCENT if tone == "danger" else C.TEXT2, 22), 0, Qt.AlignmentFlag.AlignTop)
        col = vbox(gap=4)
        col.addWidget(label(title, "subtitle", wrap=True))
        if body:
            col.addWidget(label(body, "secondary", wrap=True))
        head.addLayout(col, 1)
        self.lay.addLayout(head)
        self.buttons = hbox(gap=8)
        self.buttons.addStretch(1)
        self.lay.addLayout(self.buttons)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)


def confirm(parent: QWidget | None, title: str, body: str = "", ok_text: str = "확인", danger: bool = False) -> bool:
    dlg = _Modal(parent, title, body, "trash" if danger else "help-circle", "danger" if danger else "neutral")
    cancel = Button("취소", "secondary")
    cancel.clicked.connect(dlg.reject)
    dlg.buttons.addWidget(cancel)
    ok = Button(ok_text, "primary")
    ok.clicked.connect(dlg.accept)
    ok.setDefault(True)
    dlg.buttons.addWidget(ok)
    result = dlg.exec() == QDialog.DialogCode.Accepted
    dlg.deleteLater()
    return result


def prompt(parent: QWidget | None, title: str, body: str = "", placeholder: str = "", ok_text: str = "만들기", text: str = "") -> str | None:
    dlg = _Modal(parent, title, body, "edit", "neutral")
    edit = QLineEdit(text)
    edit.setPlaceholderText(placeholder)
    edit.setFont(fonts.sans(13, 400))
    dlg.lay.insertWidget(1, edit)
    cancel = Button("취소", "secondary")
    cancel.clicked.connect(dlg.reject)
    dlg.buttons.addWidget(cancel)
    ok = Button(ok_text, "primary")
    ok.clicked.connect(dlg.accept)
    ok.setDefault(True)
    dlg.buttons.addWidget(ok)
    edit.returnPressed.connect(dlg.accept)
    edit.setFocus()
    edit.selectAll()
    value = edit.text().strip() if dlg.exec() == QDialog.DialogCode.Accepted else None
    dlg.deleteLater()
    return value or None
