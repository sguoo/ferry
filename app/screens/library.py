"""Library: what is already in the download folder, searchable and filterable."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QSizePolicy, QWidget

from .. import context, local, subtitles, workers, youtube
from ..local import LocalFile, LocalPlaylist
from ..theme import C
from ..widgets.primitives import (
    Badge,
    Button,
    Chip,
    CompositeField,
    EmptyState,
    ErrorBanner,
    FlowLayout,
    IconButton,
    IconLabel,
    IconTile,
    Kbd,
    Panel,
    Skeleton,
    Thumbnail,
    Toggle,
    clear_layout,
    hbox,
    icon_text,
    label,
    num,
    set_prop,
    vbox,
)
from ..youtube import fmt_duration, fmt_relative, fmt_size
from . import Screen
from .downloader import alive

CARD_W = 372
FILTERS = [("전체 보기", "all", "list"), ("비디오", "video", "film"), ("음악", "audio", "music"), ("4K", "4k", "monitor"), ("자막", "subs", "subtitles")]


def _matches(f: LocalFile, kind: str) -> bool:
    if kind == "video":
        return not f.is_audio
    if kind == "audio":
        return f.is_audio
    if kind == "4k":
        return bool(f.height and f.height >= 2160)
    if kind == "subs":
        return bool(f.subtitles)
    return True


class LibraryCard(QFrame):
    def __init__(self, file: LocalFile, parent=None):
        super().__init__(parent)
        self.file = file
        self.setProperty("card", True)
        self.setFixedWidth(CARD_W)
        lay = vbox(self, gap=10, margins=(12, 12, 12, 12))
        badges = []
        if file.height:
            badges.append((f"{file.height}p", "accent" if file.height >= 2160 else "neutral"))
        if file.is_audio:
            badges.append((file.ext.lstrip(".").upper(), "success"))
            if file.bitrate:
                badges.append((f"{file.bitrate} kbps", "neutral"))
        thumb = Thumbnail(str(file.path), CARD_W - 26, fmt_duration(file.duration) if file.duration is not None else "", badges)
        if not file.is_audio:
            workers.call(local.make_thumbnail, file.path, youtube.THUMBNAIL_CACHE_DIR / "local", context.settings.ffmpeg_path or None,
                         finished=lambda p, t=thumb: p and alive(t) and t.set_image(p))
        lay.addWidget(thumb)
        head = hbox(gap=8)
        title = label(file.name, "title", size=15, wrap=True)
        title.setMinimumHeight(44)
        title.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        head.addWidget(title, 1)
        lay.addLayout(head)
        meta = hbox(gap=6)
        meta.addWidget(label(file.ext.lstrip(".").upper(), "caption-strong", color=C.ACCENT, mono=True))
        meta.addWidget(label("·", "muted"))
        meta.addWidget(num(fmt_size(file.size), 12, 500, C.TEXT2))
        meta.addWidget(label("·", "muted"))
        meta.addWidget(label(file.quality, "caption-strong", color=C.SUCCESS, mono=True))
        meta.addWidget(label("·", "muted"))
        meta.addWidget(label(fmt_relative(file.modified.date()), "muted"))
        meta.addStretch()
        lay.addLayout(meta)
        lay.addSpacing(2)
        foot = hbox(gap=6)
        codec = " / ".join(c for c in (file.vcodec.upper(), file.acodec.upper()) if c)
        foot.addWidget(icon_text("waveform" if file.is_audio else "film", codec or "코덱 정보 없음", "caption", elide=True), 1)
        if file.subtitles:
            langs = ", ".join(subtitles.lang_of(s) or "srt" for s in file.subtitles)
            subs = Button(f"자막 {langs}", "secondary", "subtitles", "sm")
            subs.setToolTip("자막 보기 · 재생 중에도 표시됩니다")
            subs.clicked.connect(lambda: context.bus.view_subtitles.emit(str(file.path)))
            foot.addWidget(subs)
        play = IconButton("play", "앱에서 재생")
        play.clicked.connect(lambda: context.bus.play.emit([str(file.path)], 0, [file.name]))
        foot.addWidget(play)
        ext_play = IconButton("external", "기본 플레이어로 열기", flat=True)
        ext_play.clicked.connect(lambda: os.startfile(str(file.path)))  # type: ignore[attr-defined]
        foot.addWidget(ext_play)
        reveal = IconButton("folder", "탐색기에서 보기")
        reveal.clicked.connect(lambda: local.open_in_explorer(file.path))
        foot.addWidget(reveal)
        lay.addLayout(foot)


class SubtitleCard(QFrame):
    """A subtitle file with no media next to it (subtitles-only download)."""

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self.setFixedWidth(CARD_W)
        lay = vbox(self, gap=10, margins=(12, 12, 12, 12))
        cover = QFrame()
        cover.setProperty("icontile", "neutral")
        cover.setFixedSize(CARD_W - 26, int((CARD_W - 26) * 9 / 16))
        cl = vbox(cover, gap=8)
        cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(IconLabel("subtitles", C.TEXT2, 40), 0, Qt.AlignmentFlag.AlignHCenter)
        cl.addWidget(Badge(subtitles.lang_label(path), "success", mono=True), 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(cover)
        title = label(path.name, "title", size=15, wrap=True)
        title.setMinimumHeight(44)
        title.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(title)
        st = path.stat()
        meta = hbox(gap=6)
        meta.addWidget(label(path.suffix.lstrip(".").upper(), "caption-strong", color=C.ACCENT, mono=True))
        meta.addWidget(label("·", "muted"))
        meta.addWidget(num(fmt_size(st.st_size), 12, 500, C.TEXT2))
        meta.addWidget(label("·", "muted"))
        meta.addWidget(label(fmt_relative(datetime.fromtimestamp(st.st_mtime).date()), "muted"))
        meta.addStretch()
        lay.addLayout(meta)
        lay.addSpacing(2)
        foot = hbox(gap=6)
        foot.addWidget(label("자막 파일만 있음", "caption"), 1)
        view = Button("자막 보기", "secondary", "subtitles", "sm")
        view.clicked.connect(lambda: context.bus.view_subtitles.emit(str(path)))
        foot.addWidget(view)
        reveal = IconButton("folder", "탐색기에서 보기")
        reveal.clicked.connect(lambda: local.open_in_explorer(path))
        foot.addWidget(reveal)
        lay.addLayout(foot)


class LibraryScreen(Screen):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.playlist: LocalPlaylist | None = None
        self._kind = "all"
        self._query = ""
        self._request = 0
        self._scanned_at: datetime | None = None

        # ---------------------------------------------------------- header
        head = QWidget()
        hl = hbox(head, gap=14)
        hl.addWidget(IconTile("archive", "accent", 44))
        ident = vbox(gap=4)
        trow = hbox(gap=10)
        trow.addWidget(label("보관함 (완료된 다운로드)", "headline"))
        self.sync_badge = Badge("SCANNING", "neutral", mono=True)
        trow.addWidget(self.sync_badge)
        trow.addStretch()
        ident.addLayout(trow)
        srow = hbox(gap=6)
        srow.addWidget(IconLabel("check-circle", C.SUCCESS, 14))
        self.count_label = label("", "caption")
        srow.addWidget(self.count_label)
        srow.addWidget(label("·", "muted"))
        self.size_label = num("", 12, 600)
        srow.addWidget(self.size_label)
        self.path_label = label("", "muted", mono=True, elide=True)
        srow.addWidget(self.path_label, 1)
        ident.addLayout(srow)
        hl.addLayout(ident, 1)
        open_btn = Button("폴더 열기 (Explorer)", "secondary", "folder-open")
        open_btn.clicked.connect(lambda: context.settings.save_dir.exists() and local.open_in_explorer(context.settings.save_dir))
        hl.addWidget(open_btn)
        rescan = Button("다시 스캔", "secondary", "refresh")
        rescan.clicked.connect(self.rescan)
        hl.addWidget(rescan)
        play_all = Button("전체 재생", "primary", "play")
        play_all.clicked.connect(self._play_visible)
        hl.addWidget(play_all)
        self.add_section(head)

        # ---------------------------------------------------------- search
        search = QWidget()
        sl = hbox(search, gap=10)
        self.field = CompositeField("search", "파일명으로 검색...")
        self.field.add_trailing(Kbd("Ctrl+F"))
        self.field.edit.textChanged.connect(self._on_query)
        sl.addWidget(self.field, 1)
        self._chips: list[tuple[Chip, str]] = []
        for text, kind, icon_name in FILTERS:
            chip = Chip(text, "0", icon_name, kind == "all")
            chip.clicked.connect(lambda k=kind: self._set_kind(k))
            self._chips.append((chip, kind))
            sl.addWidget(chip)
        self.add_section(search)

        # ---------------------------------------------------------- grid
        self.host = QWidget()
        self.box = vbox(self.host, gap=0)
        self.add_section(self.host)

        # ---------------------------------------------------------- status strip
        self.add_section(self._build_strip())
        self.body.addStretch()

        context.bus.library_changed.connect(self.rescan)
        context.bus.settings_changed.connect(self._on_settings_changed)
        self._scan_key = (context.settings.save_path, context.settings.ffmpeg_path)
        self.rescan()

    def _on_settings_changed(self) -> None:
        key = (context.settings.save_path, context.settings.ffmpeg_path)
        if key != self._scan_key:
            self._scan_key = key
            self.rescan()

    # ------------------------------------------------------------- scanning
    def rescan(self) -> None:
        folder = context.settings.save_dir
        self.path_label.setText(str(folder))
        self._request += 1
        req = self._request
        self.sync_badge.setText("SCANNING")
        set_prop(self.sync_badge, "badge", "neutral")
        if not folder.exists():
            self.playlist = None
            self._render()
            return
        self._set(self._build_loading())
        workers.call(
            local.scan_folder, folder, True, True, context.settings.ffmpeg_path or None,
            finished=lambda pl, r=req: r == self._request and self._on_scanned(pl),
            failed=lambda err, r=req: r == self._request and self._set(ErrorBanner("폴더를 읽지 못했습니다", err.message)),
        )

    def _on_scanned(self, pl: LocalPlaylist) -> None:
        self.playlist = pl
        self._scanned_at = datetime.now()
        self.sync_badge.setText("ARCHIVE SYNCED")
        set_prop(self.sync_badge, "badge", "success")
        self.count_label.setText(f"총 {len(pl.files)}개 파일")
        self.size_label.setText(fmt_size(pl.total_size))
        for chip, kind in self._chips:
            count = sum(1 for f in pl.files if _matches(f, kind)) + (len(pl.orphan_subtitles) if kind in ("all", "subs") else 0)
            chip._badge.setText(str(count))
        self.strip_note.setText(f"마지막 스캔 {self._scanned_at:%H:%M:%S} · 하위 폴더 포함 · ffprobe로 길이/해상도 읽음")
        self._render()

    def _set(self, w: QWidget) -> None:
        clear_layout(self.box)
        self.box.addWidget(w)

    def _set_kind(self, kind: str) -> None:
        self._kind = kind
        for chip, k in self._chips:
            chip.set_active(k == kind)
        self._render()

    def _on_query(self, text: str) -> None:
        self._query = text.strip().casefold()
        self._render()

    def _visible_files(self) -> list[LocalFile]:
        if self.playlist is None:
            return []
        files = [f for f in self.playlist.files if _matches(f, self._kind) and (not self._query or self._query in f.name.casefold())]
        return sorted(files, key=lambda f: -f.modified.timestamp())

    def _play_visible(self) -> None:
        files = self._visible_files()
        if files:
            context.bus.play.emit([str(f.path) for f in files], 0, [f.name for f in files])

    def _render(self) -> None:
        if self.playlist is None:
            empty = EmptyState("archive", "저장 폴더가 아직 없습니다", f"첫 다운로드가 끝나면 {context.settings.save_path} 폴더가 만들어지고 이곳에 정리됩니다.", action="다시 스캔")
            empty.findChild(Button).clicked.connect(self.rescan)
            self._set(empty)
            return
        files = self._visible_files()
        orphans = [p for p in self.playlist.orphan_subtitles if self._kind in ("all", "subs") and (not self._query or self._query in p.name.casefold())]
        if not self.playlist.files and not orphans:
            self._set(EmptyState("archive", "아직 저장된 파일이 없습니다", "다운로더에서 첫 영상을 내려받으면 이곳에 썸네일, 용량, 화질 정보와 함께 정리됩니다."))
            return
        if not files and not orphans:
            self._set(EmptyState("search", "일치하는 파일이 없습니다", "검색어를 지우거나 다른 필터를 선택해 보세요."))
            return
        w = QWidget()
        flow = FlowLayout(w, 16, 16)
        for f in files:
            flow.addWidget(LibraryCard(f))
        for p in orphans:
            flow.addWidget(SubtitleCard(p))
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._set(w)

    def _build_loading(self) -> QWidget:
        w = QWidget()
        flow = FlowLayout(w, 16, 16)
        for _ in range(6):
            card = QFrame()
            card.setProperty("card", True)
            card.setFixedWidth(CARD_W)
            cl = vbox(card, gap=10, margins=(12, 12, 12, 12))
            cl.addWidget(Skeleton(CARD_W - 26, int((CARD_W - 26) * 9 / 16), 8))
            cl.addWidget(Skeleton(260, 18))
            cl.addWidget(Skeleton(180, 12))
            cl.addWidget(Skeleton(140, 12))
            flow.addWidget(card)
        return w

    def _build_strip(self) -> QWidget:
        panel = Panel(padding=16)
        row = hbox(gap=14)
        row.addWidget(IconTile("hdd", "neutral", 44))
        col = vbox(gap=3)
        trow = hbox(gap=8)
        trow.addWidget(label("저장 폴더 상태", "subtitle"))
        trow.addStretch()
        col.addLayout(trow)
        self.strip_note = label("아직 스캔하지 않았습니다.", "secondary")
        col.addWidget(self.strip_note)
        row.addLayout(col, 1)
        toggle_row = hbox(gap=10)
        toggle_row.addWidget(IconLabel("bell", C.TEXT2, 16))
        toggle_row.addWidget(label("완료 알림", "body-strong"))
        notify = Toggle(context.settings.notify)
        notify.toggled.connect(self._set_notify)
        toggle_row.addWidget(notify)
        row.addLayout(toggle_row)
        panel.body.addLayout(row)
        return panel

    def _set_notify(self, on: bool) -> None:
        context.settings.notify = on
        context.save_settings()
