"""What's-new popup with a GitHub release description, in two flavours:
- installed=False: a newer build has been downloaded; restart now or let the swap happen on exit (app.updater)
- installed=True: first launch after an update; just shows what changed in the version now running"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices, QTextDocument
from PySide6.QtWidgets import QApplication, QDialog, QTextBrowser, QWidget

from .. import APP_NAME, APP_VERSION, fonts, updater
from ..theme import C
from .primitives import Button, IconLabel, hbox, label, vbox


class UpdateDialog(QDialog):
    def __init__(self, version: str, notes: str, url: str = "", installed: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} v{version} 업데이트")
        self.setModal(True)
        self.resize(520, 480)
        self.setStyleSheet(f"QDialog {{ background: {C.BG0}; }}")

        lay = vbox(self, gap=14, margins=(24, 22, 24, 20))
        head = hbox(gap=12)
        head.addWidget(IconLabel("download", C.ACCENT, 22))
        col = vbox(gap=2)
        if installed:
            col.addWidget(label(f"v{version}으로 업데이트되었습니다", "subtitle"))
            col.addWidget(label("이번 버전에서 달라진 점입니다.", "muted"))
        else:
            col.addWidget(label(f"v{version} 업데이트가 준비되었습니다", "subtitle"))
            col.addWidget(label(f"현재 v{APP_VERSION} → v{version} · 아래는 이번 버전의 변경 내역입니다.", "muted"))
        head.addLayout(col, 1)
        lay.addLayout(head)

        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.notes.setFont(fonts.sans(13, 400))
        self.notes.document().setDefaultStyleSheet(
            f"body {{ color: {C.TEXT}; }} h1, h2, h3 {{ color: {C.TEXT}; }} a {{ color: {C.ACCENT}; }} "
            f"code {{ background: {C.BG2}; color: {C.TEXT}; }} li {{ margin-bottom: 4px; }}"
        )
        self.notes.setStyleSheet(
            f"QTextBrowser {{ background: {C.BG1}; border: 1px solid {C.BORDER}; border-radius: 8px; padding: 12px; color: {C.TEXT}; }}"
        )
        self.notes.document().setDocumentMargin(4)
        self.notes.document().setDefaultFont(fonts.sans(13, 400))
        if notes:
            # Qt's markdown importer hard-codes link colour (#0000ff) and drops the default font on anchors;
            # round-trip through HTML so the theme colour and the app font apply
            md = QTextDocument()
            md.setMarkdown(notes, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
            html = md.toHtml().replace("color:#0000ff;", f"color:{C.ACCENT};")
            self.notes.setHtml(html)
        else:
            self.notes.setPlainText("이 릴리스에는 변경 내역이 적혀 있지 않습니다.")
        lay.addWidget(self.notes, 1)

        row = hbox(gap=8)
        if url:
            link = Button("GitHub에서 보기", "ghost", size="sm")
            link.clicked.connect(lambda: QDesktopServices.openUrl(url))
            row.addWidget(link)
        row.addStretch(1)
        if installed:
            ok = Button("확인", "primary")
            ok.clicked.connect(self.accept)
            ok.setDefault(True)
            row.addWidget(ok)
        else:
            later = Button("나중에", "secondary")
            later.setToolTip("앱을 닫을 때 자동으로 적용됩니다")
            later.clicked.connect(self.reject)
            row.addWidget(later)
            now = Button("지금 재시작하여 적용", "primary", "refresh")
            now.clicked.connect(self._restart)
            now.setDefault(True)
            row.addWidget(now)
        lay.addLayout(row)

    def _restart(self) -> None:
        if updater.apply(restart=True):
            self.accept()
            QApplication.quit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)
