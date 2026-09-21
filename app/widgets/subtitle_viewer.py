"""Subtitle viewer overlay: every cue of a subtitle file as a list, searchable.

Opened for a downloaded .srt/.vtt (subtitles-only jobs) or for a media file's
sidecars. When the player is showing the same media, clicking a cue seeks to it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QFrame, QScrollArea, QWidget

from .. import subtitles
from ..subtitles import Cue
from ..theme import C
from .primitives import Badge, CompositeField, EmptyState, IconButton, IconLabel, Segmented, clear_layout, hbox, label, set_prop, vbox


class CueRow(QFrame):
    clicked = Signal(object)

    def __init__(self, cue: Cue, parent=None):
        super().__init__(parent)
        self.cue = cue
        self.setProperty("tile", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = hbox(self, gap=14, margins=(14, 10, 14, 10))
        stamp = label(cue.stamp, "caption-strong", color=C.ACCENT, mono=True)
        stamp.setFixedWidth(64)
        lay.addWidget(stamp, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(label(cue.text, "body", wrap=True), 1)
        dur = label(f"{(cue.end - cue.start) / 1000:.1f}s", "muted", mono=True)
        lay.addWidget(dur, 0, Qt.AlignmentFlag.AlignTop)

    def mousePressEvent(self, event):
        self.clicked.emit(self.cue)
        super().mousePressEvent(event)

    def set_current(self, on: bool) -> None:
        set_prop(self, "selected", on)


class SubtitleViewer(QWidget):
    seek_requested = Signal(str, int)   # media path, position ms
    closed = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("SubtitleViewer")
        self.setStyleSheet(f"#SubtitleViewer {{ background: {C.BG0}; }}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()
        parent.installEventFilter(self)
        self.files: list[Path] = []
        self.media: Path | None = None
        self.cues: list[Cue] = []
        self.rows: list[CueRow] = []
        self._current: CueRow | None = None

        root = vbox(self, gap=0)
        top = QFrame()
        top.setStyleSheet(f"background: {C.BG1}; border-bottom: 1px solid {C.BORDER};")
        tl = hbox(top, gap=12, margins=(20, 12, 12, 12))
        tl.addWidget(IconLabel("subtitles", C.ACCENT, 18))
        self.title = label("", "title", elide=True)
        tl.addWidget(self.title, 1)
        self.count_badge = Badge("", "neutral", mono=True)
        tl.addWidget(self.count_badge)
        close_btn = IconButton("x", "닫기 (Esc)", flat=True)
        close_btn.clicked.connect(self.close)
        tl.addWidget(close_btn)
        root.addWidget(top)

        tools = QWidget()
        trow = hbox(tools, gap=10, margins=(20, 14, 20, 0))
        self.lang_host = QWidget()
        self.lang_box = hbox(self.lang_host, gap=0)
        trow.addWidget(self.lang_host)
        self.search = CompositeField("search", "자막 내용 검색...")
        self.search.edit.textChanged.connect(self._filter)
        trow.addWidget(self.search, 1)
        root.addWidget(tools)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.viewport().setAutoFillBackground(False)
        self.list_host = QWidget()
        self.list_box = vbox(self.list_host, gap=6, margins=(20, 14, 20, 20))
        self.list_box.addStretch()
        self.scroll.setWidget(self.list_host)
        root.addWidget(self.scroll, 1)

        self.hint = label("", "muted", align=Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.hint)
        root.addSpacing(10)

    # ------------------------------------------------------------------ api
    def open(self, files: list[Path], media: Path | None = None) -> None:
        self.files = [Path(f) for f in files if Path(f).exists()]
        self.media = Path(media) if media else None
        if not self.files:
            return
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus()
        self.search.edit.blockSignals(True)
        self.search.edit.clear()
        self.search.edit.blockSignals(False)
        clear_layout(self.lang_box)
        if len(self.files) > 1:
            seg = Segmented([subtitles.lang_label(f) for f in self.files], 0)
            seg.changed.connect(lambda i: self._load(self.files[i]))
            self.lang_box.addWidget(seg)
        self.hint.setText("큐를 클릭하면 플레이어가 그 시점으로 이동합니다." if self.media else "자막 파일만 있는 항목입니다. 영상과 같은 폴더에 두면 재생 중 표시됩니다.")
        self._load(self.files[0])

    def close(self) -> None:
        self.hide()
        self.closed.emit()

    def highlight(self, position_ms: int) -> None:
        """Called by the player while playing the same media."""
        cue = subtitles.cue_at(self.cues, position_ms)
        row = next((r for r in self.rows if r.cue is cue), None) if cue else None
        if row is self._current:
            return
        if self._current is not None:
            self._current.set_current(False)
        self._current = row
        if row is not None:
            row.set_current(True)
            self.scroll.ensureWidgetVisible(row, 0, 80)

    # ------------------------------------------------------------- internal
    def _load(self, path: Path) -> None:
        self.cues = subtitles.load(path)
        self.title.setText(f"{path.name}")
        self.count_badge.setText(f"{len(self.cues)} CUES")
        self._render()

    def _render(self) -> None:
        while self.list_box.count() > 1:
            item = self.list_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows = []
        self._current = None
        q = self.search.edit.text().strip().casefold()
        shown = [c for c in self.cues if not q or q in c.text.casefold()]
        if not shown:
            self.list_box.insertWidget(0, EmptyState("subtitles", "표시할 자막이 없습니다", "검색어를 지우거나 다른 언어를 선택해 보세요." if q else "이 파일에는 큐가 없습니다."))
            return
        for i, cue in enumerate(shown):
            row = CueRow(cue)
            row.clicked.connect(self._on_cue)
            self.rows.append(row)
            self.list_box.insertWidget(i, row)

    def _filter(self, _text: str) -> None:
        self._render()

    def _on_cue(self, cue: Cue) -> None:
        if self.media is not None:
            self.seek_requested.emit(str(self.media), cue.start)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(self.parentWidget().rect())
        return super().eventFilter(obj, event)
