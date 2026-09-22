"""In-app player: an overlay over the content area with a queue of local files or stream URLs.

    overlay.play([Path("a.mp4"), Path("b.mp3")], index=0)
    overlay.play(["https://...googlevideo.com/..."], titles=["Preview"])

Keys: Space play/pause · ←/→ seek 5s · ↑/↓ volume · M mute · N/P next/prev · R repeat · X shuffle · S subtitles
      · F fullscreen · Esc leaves fullscreen, otherwise drops to the mini bar
"""

from __future__ import annotations

import random
import re

from pathlib import Path

from PySide6.QtCore import QEvent, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QTextBlockFormat, QTextCursor, QTextDocument, QTextOption
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaMetaData, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import QApplication, QComboBox, QFrame, QGraphicsScene, QGraphicsView, QGridLayout, QSlider, QStackedWidget, QWidget

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


_CJK = re.compile(r"[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿一-鿿가-힯]")  # takes the CJK fallback font


class CaptionWidget(QWidget):
    """One subtitle window: rich text with an outline pass, anchored like YouTube's srv3 windows."""

    PAD = 8
    EDGE_OFFSETS = ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, -1), (0, 1), (-1, 0), (1, 0))
    # measured off YouTube's desktop player: styled windows land at 2% + 0.96 * (ah, av) of the picture and may
    # overflow its edges; each line is as tall as its biggest segment, 1.2 * font-size for Latin/blank segments
    # and 1.45 for ones that use the CJK fallback font. Styled files position text with huge invisible spacer
    # lines, so these ratios decide where the visible text ends up.
    YT_INSET, YT_SPAN = 0.02, 0.96
    LINE_LATIN, LINE_CJK = 1.2, 1.45

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.doc = QTextDocument(self)
        self.edge_doc = QTextDocument(self)
        for d in (self.doc, self.edge_doc):
            d.setDocumentMargin(0)
        self.doc.setDefaultStyleSheet("body { color: #ffffff; }")  # plain cues: white like YouTube's default
        self.cue: subtitles.Cue | None = None
        self._edge_px = 0
        self.hide()

    def set_cue(self, cue: subtitles.Cue, elapsed_ms: int, base_px: int, max_width: int) -> None:
        self.cue = cue
        font = fonts.sans(base_px, 400 if cue.styled else 600)  # styled pens say b="1" themselves
        opt = QTextOption()
        opt.setAlignment({0: Qt.AlignmentFlag.AlignLeft, 1: Qt.AlignmentFlag.AlignRight}.get(cue.justify, Qt.AlignmentFlag.AlignHCenter))
        opt.setWrapMode(QTextOption.WrapMode.WordWrap)
        for d in (self.doc, self.edge_doc):
            d.setDefaultFont(font)
            d.setDefaultTextOption(opt)
            d.setTextWidth(-1)
        self.doc.setHtml(cue.html(elapsed_ms, base_px=base_px))
        if cue.has_edge:
            self.edge_doc.setHtml(cue.html(elapsed_ms, edge=True, base_px=base_px))
        if cue.styled:
            self._apply_line_heights(base_px)
        # alignment only applies once the document has a width; with none set, every line hugs the left edge
        width = min(self.doc.idealWidth(), max_width)
        self.doc.setTextWidth(width)
        self.edge_doc.setTextWidth(width)
        self._edge_px = max(1, round(base_px / 14))
        size = self.doc.size()
        self.resize(int(size.width()) + 2 * self.PAD, int(size.height()) + 2 * self.PAD)
        self.update()

    def _apply_line_heights(self, base_px: int) -> None:
        heights = []
        block = self.doc.firstBlock()
        while block.isValid():
            tallest = 0.0
            for it in block:
                frag = it.fragment()
                px = frag.charFormat().font().pixelSize()
                ratio = self.LINE_CJK if _CJK.search(frag.text()) else self.LINE_LATIN
                tallest = max(tallest, (px if px > 0 else base_px) * ratio)
            heights.append(tallest or base_px * self.LINE_LATIN)
            block = block.next()
        for d in (self.doc, self.edge_doc):
            block = d.firstBlock()
            for h in heights:
                if not block.isValid():
                    break
                fmt = QTextBlockFormat()
                fmt.setLineHeight(h, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
                QTextCursor(block).mergeBlockFormat(fmt)
                block = block.next()

    def place(self, video: QRect) -> None:
        """Anchor point ap (0..8 = TL..BR) at (ah%, av%) of the picture; plain cues sit bottom-centre."""
        cue = self.cue
        w, h = self.width(), self.height()
        col, row = cue.anchor % 3, cue.anchor // 3
        if cue.styled:
            x = video.x() + video.width() * (self.YT_INSET + self.YT_SPAN * cue.ah / 100)
            y = video.y() + video.height() * (self.YT_INSET + self.YT_SPAN * cue.av / 100)
            x -= (0, w / 2, w)[col]
            y -= (0, h / 2, h)[row]
            # no clamping: authors rely on windows hanging past the picture edge (the host widget clips them)
        else:
            x = video.x() + video.width() * cue.ah / 100 - (0, w / 2, w)[col]
            y = min(video.y() + video.height() * cue.av / 100 - (0, h / 2, h)[row], video.bottom() - 28 - h)
            x = max(video.x(), min(x, video.right() - w))
            y = max(video.y(), min(y, video.bottom() - h))
        self.move(int(x), int(y))

    def paintEvent(self, _):
        if self.cue is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if not self.cue.styled:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(11, 11, 15, 184))
            p.drawRoundedRect(self.rect(), 8, 8)
        p.translate(self.PAD, self.PAD)
        if self.cue.styled and self.cue.has_edge:
            for dx, dy in self.EDGE_OFFSETS:
                p.save()
                p.translate(dx * self._edge_px, dy * self._edge_px)
                self.edge_doc.drawContents(p)
                p.restore()
        self.doc.drawContents(p)
        p.end()


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
        self.repeat = context.settings.player_repeat if context.settings.player_repeat in ("off", "all", "one") else "off"
        self.shuffle = context.settings.player_shuffle
        self._order: list[int] = []  # play order over queue indices while shuffling
        self._fullscreen = False
        self.cues: list[subtitles.Cue] = []
        self.sidecars: list[Path] = []
        self._pending_seek: int | None = None
        self._minimized = False

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        # follow the system default output: Qt keeps the device it opened with, so a headset plugged in or a
        # new default picked in Windows sound settings would otherwise leave the audio on the old device
        self.devices = QMediaDevices(self)
        self.devices.audioOutputsChanged.connect(self._follow_default_output)
        self.device_timer = QTimer(self)  # a default change with no plug/unplug fires no signal; poll for it
        self.device_timer.setInterval(2000)
        self.device_timer.timeout.connect(self._follow_default_output)
        # QVideoWidget is a native window that would sit above any overlay, so the
        # frames go through a QGraphicsVideoItem inside a plain QGraphicsView instead
        self.scene = QGraphicsScene(self)
        self.video_item = QGraphicsVideoItem()
        self.video_item.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.scene.addItem(self.video_item)
        self.video = QGraphicsView(self.scene)
        self.video.setFrameShape(QFrame.Shape.NoFrame)
        self.video.setStyleSheet("background: #000000; border: none;")
        self.video.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.video.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.video.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.video.installEventFilter(self)
        self.player.setVideoOutput(self.video_item)  # embedded subtitle tracks are drawn by the item itself
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.errorOccurred.connect(self._on_error)
        self.player.tracksChanged.connect(self._on_tracks)

        root = vbox(self, gap=0)

        # ---- top bar
        top = self.top = QFrame()
        top.setStyleSheet(f"background: {C.BG1}; border-bottom: 1px solid {C.BORDER};")
        tl = hbox(top, gap=12, margins=(20, 12, 12, 12))
        tl.addWidget(IconLabel("play", C.ACCENT, 18))
        self.title = label("", "title", elide=True)
        tl.addWidget(self.title, 1)
        self.pos_badge = Badge("", "neutral", mono=True)
        tl.addWidget(self.pos_badge)
        self.kind_badge = Badge("", "success", mono=True)
        tl.addWidget(self.kind_badge)
        close_btn = IconButton("x", "재생 종료", flat=True)
        close_btn.clicked.connect(self.close)
        tl.addWidget(close_btn)
        root.addWidget(top)

        # ---- stage: video (+ subtitle overlay) or an audio card
        self.stage = QStackedWidget()
        self.video_host = QWidget()
        vg = QGridLayout(self.video_host)
        vg.setContentsMargins(0, 0, 0, 0)
        vg.addWidget(self.video, 0, 0)
        # captions are free-floating children of the host (one per visible subtitle window),
        # positioned by _update_subtitle so styled srv3 files keep their on-screen placement
        self.captions: list[CaptionWidget] = []
        self.cue_index: subtitles.CueIndex | None = None
        self._caption_key: tuple = ()
        self._active_track = -1
        self.caption_timer = QTimer(self)
        self.caption_timer.setInterval(33)
        self.caption_timer.timeout.connect(lambda: self._update_subtitle())
        self.video_host.installEventFilter(self)
        self.stage.addWidget(self.video_host)
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
        bar = self.bar = QFrame()
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
        ctl.addSpacing(4)
        self.repeat_btn = IconButton("repeat", "", flat=True)
        self.repeat_btn.clicked.connect(self.cycle_repeat)
        ctl.addWidget(self.repeat_btn)
        self.shuffle_btn = IconButton("shuffle", "", flat=True)
        self.shuffle_btn.clicked.connect(self.toggle_shuffle)
        ctl.addWidget(self.shuffle_btn)
        self._sync_mode_buttons()
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

        self.hint = label("Space 재생/정지 · ←→ 5초 · ↑↓ 음량 · M 음소거 · N/P 다음/이전 · R 반복 · X 셔플 · S 자막 · F 전체 화면 · Esc 전체 화면 해제 / 미니 플레이어로", "muted", align=Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self.hint)

        # fullscreen: the bars and the cursor fade out after 3s without mouse movement
        self.idle_timer = QTimer(self)
        self.idle_timer.setInterval(3000)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.timeout.connect(self._hide_chrome)
        self._chrome_hidden = False

    # ------------------------------------------------------------------ api
    def play(self, items: list, index: int = 0, titles: list[str] | None = None) -> None:
        self.queue = [str(i) for i in items]
        self.titles = list(titles) if titles else [Path(i).stem if not str(i).startswith("http") else str(i) for i in items]
        if not self.queue:
            return
        self.restore()
        index = max(0, min(index, len(self.queue) - 1))
        self._reshuffle(index)
        self._load(index)

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
        self.prev_btn.setEnabled(self._step(-1) is not None or index > 0)
        self.next_btn.setEnabled(self._step(1) is not None)
        self.player.setSource(QUrl(src) if is_url else QUrl.fromLocalFile(src))
        self._setup_subtitles(Path(src) if not is_url else None)
        self.player.play()

    # ------------------------------------------------------------ subtitles
    def _setup_subtitles(self, media: Path | None) -> None:
        """Offer one sidecar per language (styled .srv3 preferred over .srt) plus embedded tracks."""
        self._clear_cues()
        self.sidecars = subtitles.pick_per_language(subtitles.sidecars(media)) if media else []
        self.sub_combo.blockSignals(True)
        self.sub_combo.clear()
        self.sub_combo.addItem("자막 끄기", None)
        for f in self.sidecars:
            name = subtitles.lang_label(f) + (" · 원본 스타일" if subtitles.is_styled(f) else "")
            self.sub_combo.addItem(name, str(f))
        self.sub_combo.blockSignals(False)
        self.sub_combo.setVisible(bool(self.sidecars))
        self.subs_btn.setVisible(bool(self.sidecars))
        self._active_track = -1
        if self.sidecars:
            wanted = [x.lower() for x in context.settings.subtitle_langs]
            pick = next((i for i, f in enumerate(self.sidecars) if subtitles.lang_of(f).split("-")[0].lower() in wanted), 0)
            self.sub_combo.setCurrentIndex(pick + 1)
        else:
            self.sub_combo.setCurrentIndex(0)

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

    def _set_track(self, index: int) -> None:
        # only touch the decoder when the embedded track really changes (it re-syncs and stutters)
        if index != self._active_track:
            self._active_track = index
            self.player.setActiveSubtitleTrack(index)

    def _on_sub_changed(self, idx: int) -> None:
        data = self.sub_combo.itemData(idx)
        self._clear_cues()
        if data is None:
            self._set_track(-1)
        elif str(data).startswith("track:"):
            self._set_track(int(str(data)[6:]))
        else:
            self._set_track(-1)
            try:
                self.cues = subtitles.load(Path(data))
            except OSError:
                self.cues = []
            self.cue_index = subtitles.CueIndex(self.cues)
            self._update_subtitle(self.player.position())
            self._sync_caption_timer()

    def _clear_cues(self) -> None:
        self.cues = []
        self.cue_index = None
        for cap in self.captions:
            cap.hide()
        self.caption_timer.stop()

    def cycle_subtitles(self) -> None:
        if self.sub_combo.count() > 1:
            self.sub_combo.setCurrentIndex((self.sub_combo.currentIndex() + 1) % self.sub_combo.count())

    def _sync_caption_timer(self) -> None:
        """Styled files change every ~33 ms; positionChanged alone is too coarse for them."""
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if self.cues and playing and self.stage.currentIndex() == 0:
            self.caption_timer.start()
        else:
            self.caption_timer.stop()

    def _fit_video(self) -> None:
        size = self.video.viewport().size()
        self.scene.setSceneRect(0, 0, size.width(), size.height())
        self.video_item.setPos(0, 0)
        self.video_item.setSize(size)
        self._update_subtitle(self.player.position(), force=True)

    def _video_rect(self) -> QRect:
        """Where the picture actually is inside the (letterboxed) view."""
        W, H = self.video_host.width(), self.video_host.height()
        native = self.video_item.nativeSize()
        if native.isEmpty() or W <= 0 or H <= 0:
            return QRect(0, 0, W, H)
        scale = min(W / native.width(), H / native.height())
        w, h = int(native.width() * scale), int(native.height() * scale)
        return QRect((W - w) // 2, (H - h) // 2, w, h)

    def _update_subtitle(self, ms: int | None = None, force: bool = False) -> None:
        if not self.cues or self.cue_index is None:
            return
        if ms is None:
            ms = self.player.position()
        if self.cues[0].styled:
            active = self.cue_index.active(ms)
        else:
            one = subtitles.cue_at(self.cues, ms)
            active = [one] if one else []
        key = tuple((id(c), (ms - c.start) if any(s.offset for s in c.segments) else 0) for c in active)
        if key == self._caption_key and not force:
            return
        self._caption_key = key
        rect = self._video_rect()
        base_px = max(16, int(rect.height() / 22.5))  # YouTube's default caption size
        while len(self.captions) < len(active):
            self.captions.append(CaptionWidget(self.video_host))
        for cap, cue in zip(self.captions, active):
            cap.set_cue(cue, ms - cue.start, base_px, int(rect.width() * 0.9))
            cap.place(rect)
            cap.show()
            cap.raise_()
        for cap in self.captions[len(active):]:
            cap.hide()
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
        nxt = self._step(1)
        if nxt is not None:
            self._load(nxt)

    def previous(self) -> None:
        prev = self._step(-1)
        if self.player.position() > 3000 or prev is None:
            self.player.setPosition(0)
        else:
            self._load(prev)

    # ------------------------------------------------------ repeat / shuffle
    def _reshuffle(self, first: int) -> None:
        """New random order that starts with `first` (the track already playing)."""
        rest = [i for i in range(len(self.queue)) if i != first]
        random.shuffle(rest)
        self._order = [first] + rest

    def _step(self, delta: int) -> int | None:
        """Queue index `delta` (+1/-1) steps away in play order; None at the end unless repeating all."""
        if not self.queue:
            return None
        order = self._order if self.shuffle and len(self._order) == len(self.queue) else list(range(len(self.queue)))
        pos = order.index(self.index) if self.index in order else 0
        pos += delta
        if 0 <= pos < len(order):
            return order[pos]
        if self.repeat == "all":
            return order[pos % len(order)]
        return None

    def cycle_repeat(self) -> None:
        self.repeat = {"off": "all", "all": "one", "one": "off"}[self.repeat]
        context.settings.player_repeat = self.repeat
        context.save_settings()
        self._sync_mode_buttons()

    def toggle_shuffle(self) -> None:
        self.shuffle = not self.shuffle
        if self.shuffle and self.index >= 0:
            self._reshuffle(self.index)
        context.settings.player_shuffle = self.shuffle
        context.save_settings()
        self._sync_mode_buttons()

    def set_shuffle(self, on: bool) -> None:
        if on != self.shuffle:
            self.toggle_shuffle()

    def _sync_mode_buttons(self) -> None:
        on = self.repeat != "off"
        self.repeat_btn.setIcon(icon("repeat-1" if self.repeat == "one" else "repeat", C.ACCENT if on else C.TEXT2, 16))
        self.repeat_btn.setToolTip({"off": "반복 없음 (R)", "all": "전체 반복 (R)", "one": "한 곡 반복 (R)"}[self.repeat])
        self.shuffle_btn.setIcon(icon("shuffle", C.ACCENT if self.shuffle else C.TEXT2, 16))
        self.shuffle_btn.setToolTip("셔플 켜짐 (X)" if self.shuffle else "셔플 꺼짐 (X)")
        if self.index >= 0:
            self.prev_btn.setEnabled(self._step(-1) is not None or self.index > 0)
            self.next_btn.setEnabled(self._step(1) is not None)

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
        self.fs_btn.setToolTip("전체 화면 해제 (F / Esc)" if self._fullscreen else "전체 화면 (F)")
        app = QApplication.instance()
        if self._fullscreen:
            app.installEventFilter(self)  # mouse moves anywhere over the player count as activity
            self.idle_timer.start()
        else:
            app.removeEventFilter(self)
            self.idle_timer.stop()
            self._show_chrome()
        self.fullscreen_toggled.emit(self._fullscreen)

    def _hide_chrome(self) -> None:
        if not self._fullscreen or self._chrome_hidden:
            return
        self._chrome_hidden = True
        self.top.hide()
        self.bar.hide()
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.video.viewport().setCursor(Qt.CursorShape.BlankCursor)

    def _show_chrome(self) -> None:
        if self._chrome_hidden:
            self._chrome_hidden = False
            self.top.show()
            self.bar.show()
            self.unsetCursor()
            self.video.viewport().unsetCursor()
        if self._fullscreen:
            self.idle_timer.start()  # (re)arm: another 3 quiet seconds hide it again

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
        self._sync_caption_timer()
        if playing:
            self.device_timer.start()
        else:
            self.device_timer.stop()

    def _follow_default_output(self) -> None:
        default = QMediaDevices.defaultAudioOutput()
        if not default.isNull() and default.id() != self.audio.device().id():
            self.audio.setDevice(default)

    def _on_status(self, status) -> None:
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) and self._pending_seek is not None:
            self.player.setPosition(self._pending_seek)
            self._pending_seek = None
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            nxt = self.index if self.repeat == "one" else self._step(1)
            if nxt is None:
                self.player.setPosition(0)
                self.player.pause()
            elif nxt == self.index:
                self.player.setPosition(0)
                self.player.play()
            else:
                self._load(nxt)
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
            if self._fullscreen:
                self.toggle_fullscreen()
            else:
                self.minimize()
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
        elif k == Qt.Key.Key_R:
            self.cycle_repeat()
        elif k == Qt.Key.Key_X:
            self.toggle_shuffle()
        elif k == Qt.Key.Key_F:
            self.toggle_fullscreen()
        elif k == Qt.Key.Key_S:
            self.cycle_subtitles()
        else:
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.toggle_fullscreen()

    def eventFilter(self, obj, event):
        if self._fullscreen and event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel, QEvent.Type.KeyPress):
            self._show_chrome()
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(self.parentWidget().rect())
        elif obj is self.video and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            QTimer.singleShot(0, self._fit_video)  # after the viewport has taken its new size
        elif obj is self.video_host and event.type() == QEvent.Type.Resize and self.cues:
            QTimer.singleShot(0, lambda: self._update_subtitle(force=True))
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
