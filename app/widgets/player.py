"""In-app player: an overlay over the content area with a queue of local files or stream URLs.

    overlay.play([Path("a.mp4"), Path("b.mp3")], index=0)
    overlay.play(["https://...googlevideo.com/..."], titles=["Preview"])

Keys: Space play/pause · ←/→ seek 5s · ↑/↓ volume · M mute · N/P next/prev · S subtitles · F fullscreen · Esc close
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QLabel, QSlider, QStackedWidget, QWidget

from .. import context, fonts, subtitles
from ..icons import icon
from ..theme import C
from ..youtube import fmt_duration
from .primitives import Badge, Button, IconButton, IconLabel, hbox, label, vbox

SEEK_STEP_MS = 5_000
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".opus", ".wav", ".ogg", ".aac"}


class SeekSlider(QSlider):
    """Click anywhere to jump; emits `scrubbed(ms)` only on user interaction."""

    scrubbed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setRange(0, 0)
        self.setSingleStep(1000)
        self.setPageStep(10_000)
        self._dragging = False
        self.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.sliderReleased.connect(self._released)

    def _released(self) -> None:
        self._dragging = False
        self.scrubbed.emit(self.value())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > 0:
            ratio = event.position().x() / max(1, self.width())
            self.setValue(int(self.maximum() * min(1.0, max(0.0, ratio))))
            self.scrubbed.emit(self.value())
        super().mousePressEvent(event)

    @property
    def dragging(self) -> bool:
        return self._dragging


class PlayerOverlay(QWidget):
    """Covers its parent; hidden until `play()` is called."""

    closed = Signal()
    fullscreen_toggled = Signal(bool)
    subtitles_requested = Signal(str)  # media path -> open the subtitle viewer
    minimized = Signal(bool)           # True: playing in the background, show the mini bar

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Player")
        self.setStyleSheet(f"#Player {{ background: {C.BG0}; }}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()
        parent.installEventFilter(self)

        self.queue: list[str] = []
        self.titles: list[str] = []
        self.index = -1
        self._fullscreen = False
        self.cues: list[subtitles.Cue] = []
        self.sidecars: list[Path] = []
        self._pending_seek: int | None = None
        self._minimized = False

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget()
        self.video.setStyleSheet("background: #000000;")
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.errorOccurred.connect(self._on_error)
        self.player.tracksChanged.connect(self._on_tracks)

        root = vbox(self, gap=0)

        # ---- top bar
        top = QFrame()
        top.setStyleSheet(f"background: {C.BG1}; border-bottom: 1px solid {C.BORDER};")
        tl = hbox(top, gap=12, margins=(20, 12, 12, 12))
        tl.addWidget(IconLabel("play", C.ACCENT, 18))
        self.title = label("", "title", elide=True)
        tl.addWidget(self.title, 1)
        self.pos_badge = Badge("", "neutral", mono=True)
        tl.addWidget(self.pos_badge)
        self.kind_badge = Badge("", "success", mono=True)
        tl.addWidget(self.kind_badge)
        close_btn = IconButton("x", "닫기 (Esc)", flat=True)
        close_btn.clicked.connect(self.close)
        tl.addWidget(close_btn)
        root.addWidget(top)

        # ---- stage: video (+ subtitle overlay) or an audio card
        self.stage = QStackedWidget()
        video_host = QWidget()
        vg = QGridLayout(video_host)
        vg.setContentsMargins(0, 0, 0, 0)
        vg.addWidget(self.video, 0, 0)
        self.sub_label = QLabel()
        self.sub_label.setWordWrap(True)
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.sub_label.setStyleSheet(
            "QLabel { color: #ffffff; background: rgba(11, 11, 15, 0.72); border-radius: 8px; padding: 6px 14px; }"
        )
        self.sub_label.setFont(fonts.sans(22, 600))
        self.sub_label.hide()
        sub_wrap = QWidget()
        sub_wrap.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        sl = vbox(sub_wrap, gap=0, margins=(48, 0, 48, 40))
        sl.addStretch()
        sl.addWidget(self.sub_label, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        vg.addWidget(sub_wrap, 0, 0)
        sub_wrap.raise_()
        self.stage.addWidget(video_host)
        audio_card = QWidget()
        al = vbox(audio_card, gap=14)
        al.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile = QFrame()
        tile.setProperty("icontile", True)
        tile.setFixedSize(160, 160)
        tlay = hbox(tile, gap=0)
        tlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tlay.addWidget(IconLabel("waveform", C.ACCENT, 64))
        al.addWidget(tile, 0, Qt.AlignmentFlag.AlignHCenter)
        self.audio_title = label("", "headline", align=Qt.AlignmentFlag.AlignCenter, wrap=True)
        self.audio_title.setMaximumWidth(720)
        al.addWidget(self.audio_title, 0, Qt.AlignmentFlag.AlignHCenter)
        self.audio_sub = label("", "secondary", align=Qt.AlignmentFlag.AlignCenter)
        al.addWidget(self.audio_sub, 0, Qt.AlignmentFlag.AlignHCenter)
        self.stage.addWidget(audio_card)
        root.addWidget(self.stage, 1)

        # ---- error strip
        self.error = label("", "caption", color=C.ACCENT, align=Qt.AlignmentFlag.AlignCenter)
        self.error.hide()
        root.addWidget(self.error)

        # ---- controls
        bar = QFrame()
        bar.setStyleSheet(f"background: {C.BG1}; border-top: 1px solid {C.BORDER};")
        bl = vbox(bar, gap=8, margins=(20, 12, 20, 14))
        seek_row = hbox(gap=12)
        self.elapsed = label("0:00", "caption", mono=True)
        self.elapsed.setFixedWidth(64)
        seek_row.addWidget(self.elapsed)
        self.seek = SeekSlider()
        self.seek.scrubbed.connect(self.player.setPosition)
        seek_row.addWidget(self.seek, 1)
        self.total = label("0:00", "caption", mono=True, align=Qt.AlignmentFlag.AlignRight)
        self.total.setFixedWidth(64)
        seek_row.addWidget(self.total)
        bl.addLayout(seek_row)

        ctl = hbox(gap=8)
        self.prev_btn = IconButton("chevron-left", "이전 (P)", flat=True)
        self.prev_btn.clicked.connect(self.previous)
        ctl.addWidget(self.prev_btn)
        self.play_btn = IconButton("play", "재생/일시정지 (Space)", size=20)
        self.play_btn.setFixedSize(44, 44)
        self.play_btn.clicked.connect(self.toggle)
        ctl.addWidget(self.play_btn)
        self.next_btn = IconButton("chevron-right", "다음 (N)", flat=True)
        self.next_btn.clicked.connect(self.next)
        ctl.addWidget(self.next_btn)
        ctl.addSpacing(12)
        self.mute_btn = IconButton("waveform", "음소거 (M)", flat=True)
        self.mute_btn.clicked.connect(self.toggle_mute)
        ctl.addWidget(self.mute_btn)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(120)
        self.volume.setCursor(Qt.CursorShape.PointingHandCursor)
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        ctl.addWidget(self.volume)
        ctl.addStretch()
        self.sub_combo = QComboBox()
        self.sub_combo.setFont(fonts.sans(12, 500))
        self.sub_combo.setMinimumWidth(150)
        self.sub_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sub_combo.currentIndexChanged.connect(self._on_sub_changed)
        self.sub_combo.hide()
        ctl.addWidget(IconLabel("subtitles", C.TEXT2, 16))
        ctl.addWidget(self.sub_combo)
        self.subs_btn = IconButton("list", "자막 전체 보기", flat=True)
        self.subs_btn.clicked.connect(lambda: self.subtitles_requested.emit(self.current_media() or ""))
        self.subs_btn.hide()
        ctl.addWidget(self.subs_btn)
        ctl.addSpacing(8)
        self.rate_label = label("1.0x", "caption-strong", mono=True)
        rate_btn = IconButton("zap", "재생 속도 (0.5x → 2.0x)", flat=True)
        rate_btn.clicked.connect(self._cycle_rate)
        ctl.addWidget(rate_btn)
        ctl.addWidget(self.rate_label)
        ctl.addSpacing(8)
        self.fs_btn = IconButton("square", "전체 화면 (F)", flat=True)
        self.fs_btn.clicked.connect(self.toggle_fullscreen)
        ctl.addWidget(self.fs_btn)
        bl.addLayout(ctl)
        root.addWidget(bar)

        self.hint = label("Space 재생/정지 · ←→ 5초 · ↑↓ 음량 · M 음소거 · N/P 다음/이전 · S 자막 · F 전체 화면 · Esc 닫기", "muted", align=Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self.hint)

    # ------------------------------------------------------------------ api
    def play(self, items: list, index: int = 0, titles: list[str] | None = None) -> None:
        self.queue = [str(i) for i in items]
        self.titles = list(titles) if titles else [Path(i).stem if not str(i).startswith("http") else str(i) for i in items]
        if not self.queue:
            return
        self.restore()
        self._load(max(0, min(index, len(self.queue) - 1)))

    def close(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        if self._fullscreen:
            self.toggle_fullscreen()
        self.hide()
        self._set_minimized(False)
        self.closed.emit()

    def minimize(self) -> None:
        """Hide the overlay but keep playing; the window shows the mini bar instead."""
        if self._fullscreen:
            self.toggle_fullscreen()
        self.hide()
        self._set_minimized(True)

    def restore(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus()
        self._set_minimized(False)

    def _set_minimized(self, on: bool) -> None:
        if on != self._minimized:
            self._minimized = on
            self.minimized.emit(on)

    @property
    def is_minimized(self) -> bool:
        return self._minimized

    @property
    def current_title(self) -> str:
        return self.titles[self.index] if 0 <= self.index < len(self.titles) else ""

    # ------------------------------------------------------------- playback
    def _load(self, index: int) -> None:
        self.index = index
        src = self.queue[index]
        name = self.titles[index] if index < len(self.titles) else src
        is_url = src.startswith("http")
        is_audio = (not is_url) and Path(src).suffix.lower() in AUDIO_EXTS
        self.title.setText(name)
        self.pos_badge.setText(f"{index + 1} / {len(self.queue)}")
        self.pos_badge.setVisible(len(self.queue) > 1)
        self.kind_badge.setText("STREAM" if is_url else Path(src).suffix.lstrip(".").upper())
        self.stage.setCurrentIndex(1 if is_audio else 0)
        self.audio_title.setText(name)
        self.audio_sub.setText(str(Path(src).parent) if not is_url else "")
        self.error.hide()
        self.prev_btn.setEnabled(index > 0)
        self.next_btn.setEnabled(index < len(self.queue) - 1)
        self.player.setSource(QUrl(src) if is_url else QUrl.fromLocalFile(src))
        self._setup_subtitles(Path(src) if not is_url else None)
        self.player.play()

    # ------------------------------------------------------------ subtitles
    def _setup_subtitles(self, media: Path | None) -> None:
        """Offer sidecar .srt/.vtt files (rendered by us) and embedded tracks (rendered by Qt)."""
        self.cues = []
        self.sub_label.hide()
        self.sidecars = subtitles.sidecars(media) if media else []
        self.sub_combo.blockSignals(True)
        self.sub_combo.clear()
        self.sub_combo.addItem("자막 끄기", None)
        for f in self.sidecars:
            self.sub_combo.addItem(subtitles.lang_label(f), str(f))
        self.sub_combo.blockSignals(False)
        self.sub_combo.setVisible(bool(self.sidecars))
        self.subs_btn.setVisible(bool(self.sidecars))
        self.sub_combo.setCurrentIndex(0)
        if self.sidecars:
            wanted = [x.lower() for x in context.settings.subtitle_langs]
            pick = next((i for i, f in enumerate(self.sidecars) if subtitles.lang_of(f).split("-")[0].lower() in wanted), 0)
            self.sub_combo.setCurrentIndex(pick + 1)

    def _on_tracks(self) -> None:
        """Embedded subtitle tracks become available once the media is loaded."""
        for i in range(self.sub_combo.count() - 1, 0, -1):
            if str(self.sub_combo.itemData(i)).startswith("track:"):
                self.sub_combo.removeItem(i)
        tracks = self.player.subtitleTracks()
        for i, meta in enumerate(tracks):
            lang = meta.stringValue(QMediaMetaData.Key.Language) or ""
            title = meta.stringValue(QMediaMetaData.Key.Title) or ""
            name = " · ".join(x for x in (title, lang) if x) or f"트랙 {i + 1}"
            self.sub_combo.addItem(f"내장: {name}", f"track:{i}")
        has_any = bool(self.sidecars) or bool(tracks)
        self.sub_combo.setVisible(has_any)
        if tracks and not self.sidecars:
            self.sub_combo.setCurrentIndex(1)

    def _on_sub_changed(self, idx: int) -> None:
        data = self.sub_combo.itemData(idx)
        self.cues = []
        self.sub_label.hide()
        if data is None:
            self.player.setActiveSubtitleTrack(-1)
        elif str(data).startswith("track:"):
            self.player.setActiveSubtitleTrack(int(str(data)[6:]))
        else:
            self.player.setActiveSubtitleTrack(-1)
            try:
                self.cues = subtitles.load(Path(data))
            except OSError:
                self.cues = []
            self._update_subtitle(self.player.position())

    def cycle_subtitles(self) -> None:
        if self.sub_combo.count() > 1:
            self.sub_combo.setCurrentIndex((self.sub_combo.currentIndex() + 1) % self.sub_combo.count())

    def _update_subtitle(self, ms: int) -> None:
        if not self.cues:
            return
        cue = subtitles.cue_at(self.cues, ms)
        if cue is None:
            self.sub_label.hide()
            return
        if self.sub_label.text() != cue.text:
            self.sub_label.setText(cue.text)
        if not self.sub_label.isVisible():
            self.sub_label.show()

    def current_media(self) -> str | None:
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def play_at(self, path: str, position_ms: int) -> None:
        """Seek if this file is already loaded; otherwise start it and seek once it is."""
        if self.isVisible() and self.current_media() == path:
            self.player.setPosition(position_ms)
            self.player.play()
            return
        self._pending_seek = position_ms
        self.play([path])

    def toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def next(self) -> None:
        if self.index < len(self.queue) - 1:
            self._load(self.index + 1)

    def previous(self) -> None:
        if self.player.position() > 3000 or self.index == 0:
            self.player.setPosition(0)
        else:
            self._load(self.index - 1)

    def seek_by(self, delta_ms: int) -> None:
        self.player.setPosition(max(0, min(self.player.duration(), self.player.position() + delta_ms)))

    def toggle_mute(self) -> None:
        self.audio.setMuted(not self.audio.isMuted())
        self.mute_btn.setIcon(icon("x" if self.audio.isMuted() else "waveform", C.ACCENT if self.audio.isMuted() else C.TEXT2, 16))

    def _cycle_rate(self) -> None:
        rates = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
        cur = self.player.playbackRate()
        nxt = rates[(rates.index(cur) + 1) % len(rates)] if cur in rates else 1.0
        self.player.setPlaybackRate(nxt)
        self.rate_label.setText(f"{nxt:.2g}x" if nxt != 1.0 else "1.0x")

    def toggle_fullscreen(self) -> None:
        self._fullscreen = not self._fullscreen
        self.fs_btn.setIcon(icon("minus" if self._fullscreen else "square", C.TEXT2, 16))
        self.fullscreen_toggled.emit(self._fullscreen)

    # --------------------------------------------------------------- slots
    def _on_position(self, ms: int) -> None:
        if not self.seek.dragging:
            self.seek.setValue(ms)
        self.elapsed.setText(fmt_duration(ms // 1000))
        self._update_subtitle(ms)

    def _on_duration(self, ms: int) -> None:
        self.seek.setRange(0, max(0, ms))
        self.total.setText(fmt_duration(ms // 1000) if ms > 0 else "--:--")

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setIcon(icon("pause" if playing else "play", C.TEXT, 20))

    def _on_status(self, status) -> None:
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) and self._pending_seek is not None:
            self.player.setPosition(self._pending_seek)
            self._pending_seek = None
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.index < len(self.queue) - 1:
                self.next()
            else:
                self.player.setPosition(0)
                self.player.pause()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._show_error("이 파일을 재생할 수 없습니다 (지원하지 않는 코덱이거나 손상됨).")

    def _on_error(self, _err, message: str) -> None:
        self._show_error(message or "재생 오류")

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()

    # ----------------------------------------------------------- key/geom
    def keyPressEvent(self, event):
        k = event.key()
        if k == Qt.Key.Key_Space:
            self.toggle()
        elif k == Qt.Key.Key_Escape:
            self.close()
        elif k == Qt.Key.Key_Left:
            self.seek_by(-SEEK_STEP_MS)
        elif k == Qt.Key.Key_Right:
            self.seek_by(SEEK_STEP_MS)
        elif k == Qt.Key.Key_Up:
            self.volume.setValue(min(100, self.volume.value() + 5))
        elif k == Qt.Key.Key_Down:
            self.volume.setValue(max(0, self.volume.value() - 5))
        elif k == Qt.Key.Key_M:
            self.toggle_mute()
        elif k == Qt.Key.Key_N:
            self.next()
        elif k == Qt.Key.Key_P:
            self.previous()
        elif k == Qt.Key.Key_F:
            self.toggle_fullscreen()
        elif k == Qt.Key.Key_S:
            self.cycle_subtitles()
        else:
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.toggle_fullscreen()

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(self.parentWidget().rect())
        return super().eventFilter(obj, event)


class MiniPlayerBar(QFrame):
    """Docked strip under the pages while the overlay is minimized: keeps playback in reach."""

    def __init__(self, overlay: PlayerOverlay, parent=None):
        super().__init__(parent)
        self.overlay = overlay
        self.setObjectName("MiniPlayer")
        self.setStyleSheet(f"#MiniPlayer {{ background: {C.BG1}; border-top: 1px solid {C.BORDER}; }}")
        self.setFixedHeight(64)
        self.hide()
        lay = hbox(self, gap=12, margins=(16, 0, 12, 0))

        self.kind = IconLabel("waveform", C.ACCENT, 18)
        lay.addWidget(self.kind)
        col = vbox(gap=2)
        self.title = label("", "body-strong", elide=True)
        col.addWidget(self.title)
        self.sub = label("", "muted", mono=True, elide=True)
        col.addWidget(self.sub)
        cw = QWidget()
        cw.setLayout(col)
        cw.setMinimumWidth(160)
        lay.addWidget(cw, 2)

        self.prev_btn = IconButton("chevron-left", "이전", flat=True)
        self.prev_btn.clicked.connect(overlay.previous)
        lay.addWidget(self.prev_btn)
        self.play_btn = IconButton("pause", "재생/일시정지", size=18)
        self.play_btn.setFixedSize(38, 38)
        self.play_btn.clicked.connect(overlay.toggle)
        lay.addWidget(self.play_btn)
        self.next_btn = IconButton("chevron-right", "다음", flat=True)
        self.next_btn.clicked.connect(overlay.next)
        lay.addWidget(self.next_btn)

        self.elapsed = label("0:00", "caption", mono=True)
        self.elapsed.setFixedWidth(56)
        self.elapsed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.elapsed)
        self.seek = SeekSlider()
        self.seek.scrubbed.connect(overlay.player.setPosition)
        lay.addWidget(self.seek, 5)
        self.total = label("0:00", "caption", mono=True)
        self.total.setFixedWidth(56)
        lay.addWidget(self.total)

        self.mute_btn = IconButton("waveform", "음소거", flat=True)
        self.mute_btn.clicked.connect(overlay.toggle_mute)
        lay.addWidget(self.mute_btn)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(90)
        self.volume.setCursor(Qt.CursorShape.PointingHandCursor)
        self.volume.valueChanged.connect(overlay.volume.setValue)
        overlay.volume.valueChanged.connect(self.volume.setValue)
        lay.addWidget(self.volume)
        lay.addSpacing(4)
        expand = Button("플레이어 열기", "secondary", "square", "sm")
        expand.clicked.connect(overlay.restore)
        lay.addWidget(expand)
        close_btn = IconButton("x", "재생 종료", flat=True)
        close_btn.clicked.connect(overlay.close)
        lay.addWidget(close_btn)

        overlay.minimized.connect(self.setVisible)
        overlay.player.positionChanged.connect(self._on_position)
        overlay.player.durationChanged.connect(self._on_duration)
        overlay.player.playbackStateChanged.connect(self._on_state)
        overlay.audio.mutedChanged.connect(self._on_muted)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_identity()
        self.volume.setValue(self.overlay.volume.value())
        self._on_duration(self.overlay.player.duration())
        self._on_position(self.overlay.player.position())
        self._on_state(self.overlay.player.playbackState())

    def _refresh_identity(self) -> None:
        src = self.overlay.current_media() or ""
        is_audio = Path(src).suffix.lower() in AUDIO_EXTS
        self.kind.set_icon("waveform" if is_audio else "film")
        self.title.setText(self.overlay.current_title)
        n = len(self.overlay.queue)
        self.sub.setText(f"{self.overlay.index + 1} / {n} · {Path(src).suffix.lstrip('.').upper()}" if n else "")
        self.prev_btn.setEnabled(self.overlay.index > 0)
        self.next_btn.setEnabled(self.overlay.index < n - 1)

    def _on_position(self, ms: int) -> None:
        if not self.isVisible():
            return
        if not self.seek.dragging:
            self.seek.setValue(ms)
        self.elapsed.setText(fmt_duration(ms // 1000))

    def _on_duration(self, ms: int) -> None:
        self.seek.setRange(0, max(0, ms))
        self.total.setText(fmt_duration(ms // 1000) if ms > 0 else "--:--")
        if self.isVisible():
            self._refresh_identity()

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setIcon(icon("pause" if playing else "play", C.TEXT, 18))

    def _on_muted(self, muted: bool) -> None:
        self.mute_btn.setIcon(icon("x" if muted else "waveform", C.ACCENT if muted else C.TEXT2, 16))
