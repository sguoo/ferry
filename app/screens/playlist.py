"""Playlist / batch: a YouTube playlist or channel, or a folder of media on disk."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFileDialog, QFrame, QLineEdit, QWidget

from .. import context, fonts, local, workers, youtube
from ..icons import icon
from ..jobs import STATUS_LABEL, Job
from ..local import LocalFile, LocalPlaylist
from ..theme import C, S
from ..widgets.primitives import (
    Badge,
    Button,
    Check,
    CompositeField,
    Divider,
    EmptyState,
    ErrorBanner,
    IconButton,
    IconLabel,
    Kbd,
    Panel,
    ProgressBar,
    Segmented,
    Skeleton,
    Thumbnail,
    clear_layout,
    hbox,
    icon_text,
    label,
    micro,
    num,
    set_prop,
    vbox,
)
from ..youtube import PlaylistEntry, PlaylistInfo, fmt_duration, fmt_duration_long, fmt_relative, fmt_size
from . import Screen
from .downloader import YOUTUBE_URL, alive

# column widths for the item table (px); title column is the elastic one
COL_CHECK, COL_INDEX, COL_THUMB, COL_CHANNEL, COL_DUR, COL_FMT, COL_STATUS = 26, 32, 96, 130, 48, 176, 96

# per-row output presets: (label, mode, max height)
PRESETS: list[tuple[str, str, int | None]] = [
    ("최고 화질 (MP4)", "video", None),
    ("2160p (4K)", "video", 2160),
    ("1440p (QHD)", "video", 1440),
    ("1080p (FHD)", "video", 1080),
    ("720p (HD)", "video", 720),
    ("MP3 음원 320k", "audio", None),
]
AUDIO_PRESET = len(PRESETS) - 1


def combo(items: list[str], current: int = 0) -> QComboBox:
    c = QComboBox()
    c.addItems(items)
    c.setCurrentIndex(current)
    c.setFont(fonts.sans(13, 500))
    c.setCursor(Qt.CursorShape.PointingHandCursor)
    return c


def _default_preset() -> int:
    q = context.settings.quality
    for i, (_, mode, h) in enumerate(PRESETS):
        if mode == "video" and h == q:
            return i
    return 0


# --------------------------------------------------------------------------- #
# rows
# --------------------------------------------------------------------------- #


class ItemRow(QWidget):
    """One playlist entry: checkbox, preset, and the job it spawns."""

    def __init__(self, entry: PlaylistEntry, preset: int, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.job: Job | None = None
        lay = hbox(self, gap=12, margins=(0, 12, 0, 12))

        self.check = Check("", True)
        self.check.setFixedWidth(COL_CHECK)
        self.check.toggled.connect(self._on_toggled)
        lay.addWidget(self.check)
        idx = num(f"{entry.index:02d}", 13, 500, C.TEXT3)
        idx.setFixedWidth(COL_INDEX)
        lay.addWidget(idx)
        self.thumb = Thumbnail(entry.id or entry.url, COL_THUMB, fmt_duration(entry.duration), radius=6)
        self.thumb.fetch(entry.thumbnail_url, entry.id or entry.url)
        lay.addWidget(self.thumb)

        col = vbox(gap=4)
        top = hbox(gap=8)
        self.title = label(entry.title, "title", size=15, elide=True)
        top.addWidget(self.title, 1)
        self.pct = Badge("", "accent", mono=True)
        self.pct.hide()
        top.addWidget(self.pct)
        col.addLayout(top)
        self.meta_row = hbox(gap=12)
        self.bar = ProgressBar(0.0, 5)
        self.bar.setFixedWidth(160)
        self.bar.hide()
        self.meta_row.addWidget(self.bar)
        self.meta = label(f"ID: {entry.id}" if entry.id else entry.url, "muted", mono=True, elide=True)
        self.meta_row.addWidget(self.meta, 1)
        col.addLayout(self.meta_row)
        cw = QWidget()
        cw.setLayout(col)
        lay.addWidget(cw, 1)

        ch = icon_text("user", entry.channel or "—", "secondary", elide=True)
        ch.setFixedWidth(COL_CHANNEL)
        lay.addWidget(ch)
        dur = num(fmt_duration(entry.duration), 13, 500)
        dur.setFixedWidth(COL_DUR)
        lay.addWidget(dur)
        self.preset = combo([p[0] for p in PRESETS], preset)
        self.preset.setFixedWidth(COL_FMT)
        lay.addWidget(self.preset)
        self.status = Badge("준비 완료", "success")
        sw = QWidget()
        sw.setFixedWidth(COL_STATUS)
        sl = hbox(sw, gap=0)
        sl.addStretch()
        sl.addWidget(self.status)
        lay.addWidget(sw)

    @property
    def checked(self) -> bool:
        return self.check.isChecked()

    def _on_toggled(self, on: bool) -> None:
        self.title.text_color = C.TEXT if on else C.TEXT3
        self.title.update()
        self.preset.setEnabled(on)
        if self.job is None:
            self.status.setText("준비 완료" if on else "제외됨")
            set_prop(self.status, "badge", "success" if on else "neutral")

    def bind(self, job: Job) -> None:
        self.job = job
        job.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        if not alive(self) or self.job is None:
            return
        j = self.job
        text, tone = STATUS_LABEL[j.status]
        if j.status == "downloading":
            text = f"다운로드 {int(j.percent)}%"
        self.status.setText(text)
        set_prop(self.status, "badge", tone)
        busy = j.active
        self.bar.setVisible(busy)
        self.pct.setVisible(busy)
        self.bar.set_value(j.percent / 100)
        self.pct.setText(f"{int(j.percent)}%")
        self.preset.setEnabled(not busy and j.status != "finished")
        p = j.progress
        if j.status == "downloading" and p:
            self.meta.setText(f"{fmt_size(p.downloaded)} / {fmt_size(p.total)} ({youtube.fmt_speed(p.speed)})")
        elif j.status == "finished" and j.path:
            self.meta.setText(str(j.path.name))
        elif j.status == "error":
            self.meta.setText(j.error)


class LocalRow(QWidget):
    def __init__(self, file: LocalFile, root: Path, parent=None):
        super().__init__(parent)
        self.file = file
        lay = hbox(self, gap=12, margins=(0, 12, 0, 12))
        self.check = Check("", True)
        self.check.setFixedWidth(COL_CHECK)
        self.check.toggled.connect(self._on_toggled)
        lay.addWidget(self.check)
        self.idx = num("", 13, 500, C.TEXT3)
        self.idx.setFixedWidth(COL_INDEX)
        lay.addWidget(self.idx)

        if file.is_audio:
            tile = QFrame()
            tile.setProperty("icontile", "neutral")
            tile.setFixedSize(COL_THUMB, int(COL_THUMB * 9 / 16))
            tl = hbox(tile, gap=0)
            tl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tl.addWidget(IconLabel("waveform", C.TEXT2, 20))
            lay.addWidget(tile)
        else:
            thumb = Thumbnail(file.name, COL_THUMB, fmt_duration(file.duration) if file.duration is not None else "", radius=6)
            workers.call(local.make_thumbnail, file.path, youtube.THUMBNAIL_CACHE_DIR / "local", context.settings.ffmpeg_path or None,
                         finished=lambda p, t=thumb: p and alive(t) and t.set_image(p))
            lay.addWidget(thumb)

        col = vbox(gap=4)
        top = hbox(gap=8)
        self.title = label(file.name, "title", size=15, elide=True)
        top.addWidget(self.title, 1)
        top.addWidget(Badge(file.ext.lstrip(".").upper(), "success" if file.is_audio else "neutral", mono=True))
        self.play_btn = IconButton("play", "이 파일부터 재생", flat=True, size=14)
        top.addWidget(self.play_btn)
        col.addLayout(top)
        note = f"{file.quality} · 수정 {fmt_relative(file.modified.date())}"
        if file.duration is None:
            note += " · 길이를 읽지 못함"
        col.addWidget(label(note, "muted", mono=True, elide=True))
        cw = QWidget()
        cw.setLayout(col)
        lay.addWidget(cw, 1)

        folder = icon_text("folder", file.subfolder(root) or "(루트)", "secondary", elide=True)
        folder.setFixedWidth(COL_CHANNEL)
        lay.addWidget(folder)
        dur = num(fmt_duration(file.duration) if file.duration is not None else "--:--", 13, 500)
        dur.setFixedWidth(COL_DUR)
        lay.addWidget(dur)
        size = num(fmt_size(file.size), 13, 500, C.TEXT2)
        size.setFixedWidth(COL_FMT)
        size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(size)
        self.status = Badge("재생 가능", "success")
        sw = QWidget()
        sw.setFixedWidth(COL_STATUS)
        sl = hbox(sw, gap=0)
        sl.addStretch()
        sl.addWidget(self.status)
        lay.addWidget(sw)

    @property
    def checked(self) -> bool:
        return self.check.isChecked()

    def set_index(self, i: int) -> None:
        self.idx.setText(f"{i:02d}")

    def _on_toggled(self, on: bool) -> None:
        self.title.text_color = C.TEXT if on else C.TEXT3
        self.title.update()
        self.status.setText("재생 가능" if on else "제외됨")
        set_prop(self.status, "badge", "success" if on else "neutral")


# --------------------------------------------------------------------------- #
# screen
# --------------------------------------------------------------------------- #


class PlaylistScreen(Screen):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.playlist: PlaylistInfo | None = None
        self.local: LocalPlaylist | None = None
        self.rows: list[ItemRow] = []
        self.local_rows: list[LocalRow] = []
        self.output_dir: Path | None = None
        self._request = 0
        self._source = "link"
        self._last_folder = context.settings.save_path

        # ---------------------------------------------------------- intake
        intake = Panel(padding=14)
        row = hbox(gap=10)
        self.source_toggle = Segmented([("링크", "link"), ("폴더", "folder")], 0)
        self.source_toggle.changed.connect(lambda i: self._apply_source("folder" if i == 1 else "link", show=True))
        row.addWidget(self.source_toggle)
        self.field = CompositeField("link", "재생목록 또는 채널 주소")
        self.field.edit.returnPressed.connect(self._submit)
        self.kbd_hint = Kbd("Enter")
        self.field.add_trailing(self.kbd_hint)
        self.browse_btn = Button("찾아보기", "secondary", "folder-open", "sm")
        self.browse_btn.clicked.connect(self._browse_folder)
        self.field.add_trailing(self.browse_btn)
        row.addWidget(self.field, 1)
        self.recursive_check = Check("하위 폴더 포함", True)
        row.addWidget(self.recursive_check)
        self.fetch_btn = Button("재생목록 분석", "primary", "refresh")
        self.fetch_btn.setMinimumHeight(44)
        self.fetch_btn.clicked.connect(self._submit)
        row.addWidget(self.fetch_btn)
        self.m3u_btn = Button(".m3u8 불러오기", "secondary", "external")
        self.m3u_btn.setMinimumHeight(44)
        self.m3u_btn.clicked.connect(self._load_m3u)
        row.addWidget(self.m3u_btn)
        intake.body.addLayout(row)
        self.add_section(intake)

        # ---------------------------------------------------------- content
        self.host = QWidget()
        self.box = vbox(self.host, gap=0)
        self.add_section(self.host)
        self.body.addStretch()
        self._apply_source("link", show=True)

    # ------------------------------------------------------------- helpers
    def _set(self, w: QWidget) -> None:
        clear_layout(self.box)
        self.box.addWidget(w)

    def _apply_source(self, source: str, show: bool = False) -> None:
        self._source = source
        folder = source == "folder"
        self.source_toggle.blockSignals(True)
        self.source_toggle.set_active(1 if folder else 0)
        self.source_toggle.blockSignals(False)
        self.field.icon.set_icon("folder" if folder else "link")
        self.field.edit.setPlaceholderText("미디어 파일이 들어 있는 폴더" if folder else "재생목록 또는 채널 주소")
        self.field.edit.setFont(fonts.mono(13, 500) if folder else fonts.sans(14, 500))
        self.field.set_value(self._last_folder if folder else (self.playlist.url if self.playlist else ""))
        self.kbd_hint.setVisible(not folder)
        self.browse_btn.setVisible(folder)
        self.recursive_check.setVisible(folder)
        self.m3u_btn.setVisible(folder)
        self.fetch_btn.setText("폴더 읽기" if folder else "재생목록 분석")
        self.fetch_btn.setIcon(icon("folder-open" if folder else "refresh", C.ACCENT_FG, 16))
        if show:
            if folder:
                self._show_local() if self.local else self._show_local_idle()
            else:
                self._show_playlist() if self.playlist else self._show_idle()

    def _submit(self) -> None:
        text = self.field.edit.text().strip()
        if not text:
            return
        if self._source == "folder":
            self._scan(Path(text))
        else:
            self.load_url(text)

    # ================================================================ YouTube
    def load_url(self, url: str) -> None:
        self._apply_source("link")
        self.field.set_value(url)
        if not YOUTUBE_URL.match(url):
            self._show_error("YouTube 주소가 아닙니다", "재생목록(list=...) 또는 채널 주소를 입력해 주세요.", lambda: None)
            return
        self._request += 1
        req = self._request
        set_prop(self.field, "state", "")
        self._set(self._build_loading())
        workers.call(
            youtube.fetch_playlist, url, context.settings.download_options(),
            finished=lambda pl, r=req: r == self._request and self._on_playlist(pl),
            failed=lambda err, r=req, u=url: r == self._request and self._show_error("재생목록을 열 수 없습니다", err.message, lambda: self.load_url(u)),
        )

    def _on_playlist(self, pl: PlaylistInfo) -> None:
        self.playlist = pl
        self.output_dir = None
        self._show_playlist()

    def _show_error(self, title: str, message: str, retry) -> None:
        set_prop(self.field, "state", "error")
        banner = ErrorBanner(title, message)
        banner.findChild(Button).clicked.connect(retry)
        self._set(banner)

    def _show_idle(self) -> None:
        self._set(EmptyState("playlist", "가져온 재생목록이 없습니다", "재생목록·채널 주소를 입력하면 항목을 표로 나열해 일괄 다운로드할 수 있고, 폴더 모드로 바꾸면 PC에 있는 미디어 파일로 .m3u8 재생목록을 만들 수 있습니다."))

    def _build_loading(self) -> QWidget:
        w = QWidget()
        lay = vbox(w, gap=S.SECTION)
        head = Panel()
        row = hbox(gap=20)
        row.addWidget(Skeleton(168, 95, 8))
        col = vbox(gap=10)
        col.addWidget(Skeleton(140, 12))
        col.addWidget(Skeleton(420, 26))
        col.addWidget(Skeleton(300, 12))
        col.addStretch()
        row.addLayout(col, 1)
        row.addWidget(Skeleton(320, 56, 8), 0, Qt.AlignmentFlag.AlignTop)
        head.body.addLayout(row)
        lay.addWidget(head)
        table = Panel(gap=14)
        for _ in range(5):
            r = hbox(gap=16)
            r.addWidget(Skeleton(16, 16, 4))
            r.addWidget(Skeleton(96, 54, 6))
            c = vbox(gap=6)
            c.addWidget(Skeleton(320, 16))
            c.addWidget(Skeleton(200, 12))
            r.addLayout(c, 1)
            r.addWidget(Skeleton(150, 14))
            r.addWidget(Skeleton(176, 32, 8))
            table.body.addLayout(r)
        lay.addWidget(table)
        return w

    def _save_dir(self) -> Path:
        if self.output_dir:
            return self.output_dir
        base = Path(context.settings.playlist_save_path) if context.settings.playlist_save_path else context.settings.save_dir
        safe = "".join(ch for ch in (self.playlist.title if self.playlist else "playlist") if ch not in '\\/:*?"<>|').strip() or "playlist"
        return base / safe

    def _show_playlist(self) -> None:
        pl = self.playlist
        w = QWidget()
        lay = vbox(w, gap=S.SECTION)

        head = Panel(gap=16)
        top = hbox(gap=20)
        cover = Thumbnail(pl.id or pl.url, 168, "LIST")
        cover.fetch(pl.thumbnail_url, pl.id or pl.url)
        top.addWidget(cover, 0, Qt.AlignmentFlag.AlignTop)
        ident = vbox(gap=8)
        badges = hbox(gap=8)
        badges.addWidget(Badge("분석 완료", "success", mono=True))
        badges.addWidget(label(pl.channel or pl.id, "muted", mono=True, elide=True), 1)
        ident.addLayout(badges)
        ident.addWidget(label(pl.title, "display", wrap=True))
        stats = hbox(gap=14)
        stats.addWidget(icon_text("library", f"총 {pl.count}개 영상", "secondary"))
        stats.addWidget(icon_text("clock", f"총 재생시간 {fmt_duration_long(pl.total_duration)}", "secondary"))
        stats.addStretch()
        ident.addLayout(stats)
        top.addLayout(ident, 1)
        action = vbox(gap=6)
        self.cta = Button("", "primary", "arrow-down-circle", "lg")
        self.cta.setMinimumWidth(300)
        self.cta.clicked.connect(self._start_batch)
        action.addWidget(self.cta)
        action.addWidget(label(f"최대 {context.settings.threads}개 병렬 스트림으로 처리됩니다.", "muted", align=Qt.AlignmentFlag.AlignRight))
        action.addStretch()
        top.addLayout(action)
        head.body.addLayout(top)
        head.body.addWidget(Divider())

        tools = hbox(gap=10)
        self.all_check = Check("전체 선택", True)
        self.all_check.toggled.connect(self._toggle_all)
        tools.addWidget(self.all_check)
        self.sel_badge = Badge("", "accent", mono=True)
        tools.addWidget(self.sel_badge)
        tools.addStretch()
        kind = Segmented(["비디오", "음원만"], 0)
        kind.changed.connect(lambda i: self._apply_preset_all(AUDIO_PRESET if i == 1 else _default_preset()))
        tools.addWidget(kind)
        head.body.addLayout(tools)

        batch = hbox(gap=10)
        batch.addWidget(IconLabel("sliders", C.TEXT2, 16))
        batch.addWidget(label("일괄 적용", "secondary"))
        self.batch_preset = combo([p[0] for p in PRESETS], _default_preset())
        self.batch_preset.setMinimumWidth(220)
        batch.addWidget(self.batch_preset, 1)
        apply_btn = Button("적용", "secondary", size="sm")
        apply_btn.clicked.connect(lambda: self._apply_preset_all(self.batch_preset.currentIndex(), selected_only=True))
        batch.addWidget(apply_btn)
        batch.addSpacing(8)
        batch.addWidget(IconLabel("filter", C.TEXT2, 16))
        batch.addWidget(label("범위", "secondary"))
        self.range_lo = QLineEdit("1")
        self.range_hi = QLineEdit(str(pl.count))
        for e in (self.range_lo, self.range_hi):
            e.setFont(fonts.mono(13, 500))
            e.setFixedWidth(52)
            e.setAlignment(Qt.AlignmentFlag.AlignCenter)
        batch.addWidget(self.range_lo)
        batch.addWidget(label("~", "muted"))
        batch.addWidget(self.range_hi)
        range_btn = Button("적용", "secondary", size="sm")
        range_btn.clicked.connect(self._apply_range)
        batch.addWidget(range_btn)
        head.body.addLayout(batch)
        lay.addWidget(head)

        table = Panel(padding=16, gap=0)
        table.body.addLayout(self._header(("채널명", COL_CHANNEL), ("길이", COL_DUR), ("출력 해상도 / 포맷", COL_FMT), title="동영상 제목 및 메타데이터"))
        table.body.addWidget(Divider())
        self.rows = []
        default = _default_preset()
        for i, entry in enumerate(pl.entries):
            if i:
                table.body.addWidget(Divider())
            row = ItemRow(entry, default)
            row.check.toggled.connect(lambda _on: self._refresh_selection())
            self.rows.append(row)
            table.body.addWidget(row)
        if not pl.entries:
            table.body.addWidget(EmptyState("playlist", "항목이 없습니다", "비어 있는 재생목록이거나 모든 영상이 비공개입니다."))
        table.body.addWidget(Divider())
        foot = hbox(gap=10, margins=(0, 12, 0, 0))
        self.foot_note = label("", "muted")
        foot.addWidget(self.foot_note)
        foot.addStretch()
        invert = Button("선택 반전", "secondary", size="sm")
        invert.clicked.connect(lambda: [r.check.setChecked(not r.checked) for r in self.rows])
        foot.addWidget(invert)
        audio_all = Button("오디오 전용 일괄 전환", "secondary", "music", "sm")
        audio_all.clicked.connect(lambda: self._apply_preset_all(AUDIO_PRESET))
        foot.addWidget(audio_all)
        table.body.addLayout(foot)
        lay.addWidget(table)

        strip = QWidget()
        sl = vbox(strip, gap=12)
        sl.addWidget(Divider())
        srow = hbox(gap=18, margins=(4, 2, 4, 0))
        srow.addWidget(IconLabel("folder", C.TEXT2, 16))
        srow.addWidget(label("저장 위치:", "secondary"))
        self.dir_badge = Badge(str(self._save_dir()), "neutral", mono=True, max_px=320)
        srow.addWidget(self.dir_badge)
        change = Button("변경", "ghost", size="sm")
        change.clicked.connect(self._pick_dir)
        srow.addWidget(change)
        srow.addStretch()
        srow.addWidget(IconLabel("sliders", C.TEXT2, 16))
        srow.addWidget(label("동시 다운로드:", "secondary"))
        srow.addWidget(Badge(f"{context.settings.threads}개", "accent", mono=True))
        pause = Button("전체 일시정지", "secondary", "pause", "sm")
        pause.clicked.connect(lambda: context.jobs.pause_all())
        srow.addWidget(pause)
        sl.addLayout(srow)
        lay.addWidget(strip)

        self._set(w)
        self._refresh_selection()

    def _header(self, *cols: tuple[str, int], title: str, right_last: bool = True) -> object:
        header = hbox(gap=12, margins=(0, 4, 0, 10))
        for text, width in (("", COL_CHECK), ("#", COL_INDEX), ("", COL_THUMB)):
            m = micro(text)
            m.setFixedWidth(width)
            header.addWidget(m)
        header.addWidget(micro(title), 1)
        for text, width in cols:
            m = micro(text)
            m.setFixedWidth(width)
            header.addWidget(m)
        st = micro("상태")
        st.setFixedWidth(COL_STATUS)
        st.setAlignment(Qt.AlignmentFlag.AlignRight)
        header.addWidget(st)
        return header

    def _toggle_all(self, on: bool) -> None:
        for r in self.rows:
            r.check.setChecked(on)

    def _apply_preset_all(self, idx: int, selected_only: bool = False) -> None:
        for r in self.rows:
            if r.job is None and (r.checked or not selected_only):
                r.preset.setCurrentIndex(idx)

    def _apply_range(self) -> None:
        try:
            lo, hi = int(self.range_lo.text()), int(self.range_hi.text())
        except ValueError:
            return
        for r in self.rows:
            r.check.setChecked(lo <= r.entry.index <= hi)

    def _refresh_selection(self) -> None:
        if not alive(self.sel_badge):
            return
        sel = [r for r in self.rows if r.checked]
        secs = sum(r.entry.duration or 0 for r in sel)
        self.sel_badge.setText(f"{len(self.rows)}개 중 {len(sel)}개 선택됨")
        self.cta.setText(f"선택 항목 일괄 다운로드 시작 ({len(sel)}개 · {fmt_duration_long(secs)})")
        self.cta.setEnabled(bool(sel))
        self.foot_note.setText(f"{len(self.rows)}개 항목 · 이미 시작한 항목은 다시 시작하지 않습니다.")

    def _pick_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", str(self._save_dir().parent))
        if chosen:
            self.output_dir = Path(chosen)
            self.dir_badge.setText(str(self.output_dir))

    def _start_batch(self) -> None:
        out = self._save_dir()
        for r in self.rows:
            if not r.checked or r.job is not None:
                continue
            _, mode, height = PRESETS[r.preset.currentIndex()]
            opts = context.settings.download_options(mode=mode, quality=height, output_dir=out)
            spec = f"{opts.container.upper()} · {height}p" if (mode == "video" and height) else ("MP3 · 320k" if mode == "audio" else f"{opts.container.upper()} · best")
            job = Job(r.entry.url, r.entry.title, opts, spec, fmt_duration(r.entry.duration), r.entry.thumbnail_url, r.entry.id or r.entry.url)
            r.bind(job)
            context.jobs.add(job)

    # ================================================================= local
    def _show_local_idle(self) -> None:
        self._set(EmptyState("folder-open", "읽어온 폴더가 없습니다", "미디어 파일이 들어 있는 폴더를 고르면 파일을 표로 나열하고, 선택한 파일로 .m3u8 재생목록을 만들 수 있습니다.", hint="지원 형식: MP4, MKV, WebM, MOV, MP3, FLAC, M4A, OPUS, WAV"))

    def _browse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "폴더 선택", self.field.edit.text() or self._last_folder)
        if chosen:
            self.field.set_value(chosen)
            self._scan(Path(chosen))

    def _scan(self, folder: Path) -> None:
        if not folder.is_dir():
            self._show_error("폴더를 찾을 수 없습니다", str(folder), lambda: None)
            return
        self._last_folder = str(folder)
        self._request += 1
        req = self._request
        set_prop(self.field, "state", "")
        self._set(self._build_loading())
        workers.call(
            local.scan_folder, folder, self.recursive_check.isChecked(), True, context.settings.ffmpeg_path or None,
            finished=lambda pl, r=req: r == self._request and self._on_local(pl),
            failed=lambda err, r=req: r == self._request and self._show_error("폴더를 읽지 못했습니다", err.message, lambda: self._scan(folder)),
        )

    def _load_m3u(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "재생목록 파일 열기", self._last_folder, "재생목록 (*.m3u8 *.m3u)")
        if not chosen:
            return
        src = Path(chosen)
        paths = local.read_m3u(src)
        self._set(self._build_loading())
        self._request += 1
        req = self._request
        workers.call(
            local.from_paths, paths, src.stem, True, context.settings.ffmpeg_path or None,
            finished=lambda pl, r=req: r == self._request and self._on_local(pl, m3u=src),
            failed=lambda err, r=req: r == self._request and self._show_error("재생목록을 읽지 못했습니다", err.message, lambda: None),
        )

    def _on_local(self, pl: LocalPlaylist, m3u: Path | None = None) -> None:
        self.local = pl
        self._m3u_target: Path = m3u or pl.m3u_path
        self.field.set_value(str(pl.folder))
        self._show_local()

    def _show_local(self) -> None:
        pl = self.local
        w = QWidget()
        lay = vbox(w, gap=S.SECTION)

        head = Panel(gap=16)
        top = hbox(gap=20)
        cover = QFrame()
        cover.setProperty("icontile", True)
        cover.setFixedSize(168, 95)
        cl = vbox(cover, gap=6)
        cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(IconLabel("folder-open", C.ACCENT, 28), 0, Qt.AlignmentFlag.AlignHCenter)
        cl.addWidget(label("LOCAL", "micro", color=C.ACCENT, align=Qt.AlignmentFlag.AlignHCenter))
        top.addWidget(cover, 0, Qt.AlignmentFlag.AlignTop)
        ident = vbox(gap=8)
        badges = hbox(gap=8)
        badges.addWidget(Badge("로컬 폴더", "neutral", mono=True))
        badges.addWidget(label(str(pl.folder), "muted", mono=True, elide=True), 1)
        ident.addLayout(badges)
        ident.addWidget(label(pl.name, "display", elide=True))
        stats = hbox(gap=14)
        stats.addWidget(icon_text("film", f"파일 {len(pl.files)}개", "secondary"))
        stats.addWidget(icon_text("clock", f"총 재생시간 {fmt_duration_long(pl.total_duration)}", "secondary"))
        stats.addWidget(icon_text("hdd", f"총 용량 {fmt_size(pl.total_size)}", "secondary"))
        stats.addStretch()
        ident.addLayout(stats)
        top.addLayout(ident, 1)
        action = vbox(gap=6)
        self.local_cta = Button("", "primary", "playlist", "lg")
        self.local_cta.setMinimumWidth(300)
        self.local_cta.clicked.connect(self._save_m3u)
        action.addWidget(self.local_cta)
        self.local_hint = label("상대 경로로 저장되어 폴더째 옮겨도 깨지지 않습니다.", "muted", wrap=True, align=Qt.AlignmentFlag.AlignRight)
        action.addWidget(self.local_hint)
        action.addStretch()
        aw = QWidget()
        aw.setLayout(action)
        aw.setMaximumWidth(360)
        top.addWidget(aw)
        head.body.addLayout(top)
        head.body.addWidget(Divider())

        tools = hbox(gap=10)
        all_check = Check("전체 선택", True)
        all_check.toggled.connect(lambda on: [r.check.setChecked(on) for r in self.local_rows])
        tools.addWidget(all_check)
        self.local_sel = Badge("", "accent", mono=True)
        tools.addWidget(self.local_sel)
        tools.addStretch()
        kind = Segmented(["전체", "비디오", "음원만"], 0)
        kind.changed.connect(self._filter_local_kind)
        tools.addWidget(kind)
        head.body.addLayout(tools)

        order = hbox(gap=10)
        order.addWidget(IconLabel("arrows-v", C.TEXT2, 16))
        order.addWidget(label("정렬", "secondary"))
        self.sort_combo = combo(["이름순 (자연 정렬)", "수정일 최신순", "길이 긴 순", "크기 큰 순"])
        self.sort_combo.setMinimumWidth(200)
        order.addWidget(self.sort_combo, 1)
        sort_btn = Button("적용", "secondary", size="sm")
        sort_btn.clicked.connect(self._sort_local)
        order.addWidget(sort_btn)
        order.addSpacing(8)
        play_sel = Button("선택 항목 재생", "secondary", "play", "sm")
        play_sel.clicked.connect(lambda: self._play_local())
        order.addWidget(play_sel)
        rescan = Button("폴더 다시 읽기", "secondary", "refresh", "sm")
        rescan.clicked.connect(lambda: self._scan(pl.folder))
        order.addWidget(rescan)
        open_btn = Button("탐색기에서 열기", "ghost", "external", "sm")
        open_btn.clicked.connect(lambda: local.open_in_explorer(pl.folder))
        order.addWidget(open_btn)
        head.body.addLayout(order)
        lay.addWidget(head)

        table = Panel(padding=16, gap=0)
        table.body.addLayout(self._header(("폴더", COL_CHANNEL), ("길이", COL_DUR), ("크기", COL_FMT), title="파일명 및 메타데이터"))
        table.body.addWidget(Divider())
        self.local_box = vbox(gap=0)
        table.body.addLayout(self.local_box)
        self.local_rows = []
        for f in pl.files:
            row = LocalRow(f, pl.folder)
            row.check.toggled.connect(lambda _on: self._refresh_local_selection())
            row.play_btn.clicked.connect(lambda _=False, r=row: self._play_local(start=r))
            self.local_rows.append(row)
        self._layout_local_rows()
        if not pl.files:
            table.body.addWidget(EmptyState("film", "미디어 파일이 없습니다", "이 폴더에는 지원하는 비디오/오디오 파일이 없습니다. 하위 폴더 포함을 켜고 다시 읽어 보세요."))
        table.body.addWidget(Divider())
        foot = hbox(gap=10, margins=(0, 12, 0, 0))
        note = f"미디어가 아닌 파일 {pl.skipped}개 건너뜀" if pl.skipped else "모든 파일을 읽었습니다"
        if pl.unknown_durations:
            note += f" · 길이 미상 {pl.unknown_durations}개"
        foot.addWidget(label(note, "muted"))
        foot.addStretch()
        invert = Button("선택 반전", "secondary", size="sm")
        invert.clicked.connect(lambda: [r.check.setChecked(not r.checked) for r in self.local_rows])
        foot.addWidget(invert)
        table.body.addLayout(foot)
        lay.addWidget(table)

        strip = QWidget()
        sl = vbox(strip, gap=12)
        sl.addWidget(Divider())
        srow = hbox(gap=18, margins=(4, 2, 4, 0))
        srow.addWidget(IconLabel("playlist", C.TEXT2, 16))
        srow.addWidget(label("저장 위치:", "secondary"))
        self.m3u_badge = Badge(str(self._m3u_target), "neutral", mono=True, max_px=320)
        srow.addWidget(self.m3u_badge)
        change = Button("변경", "ghost", size="sm")
        change.clicked.connect(self._pick_m3u_target)
        srow.addWidget(change)
        srow.addStretch()
        self.relative_check = Check("상대 경로로 저장", True)
        srow.addWidget(self.relative_check)
        sl.addLayout(srow)
        lay.addWidget(strip)

        self._set(w)
        self._refresh_local_selection()

    def _layout_local_rows(self) -> None:
        # rows are re-used across sorts, so detach them instead of deleting
        while self.local_box.count():
            item = self.local_box.takeAt(0)
            w = item.widget()
            if isinstance(w, Divider):
                w.deleteLater()
            elif w is not None:
                w.setParent(None)
        for i, row in enumerate(self.local_rows):
            if i:
                self.local_box.addWidget(Divider())
            row.set_index(i + 1)
            self.local_box.addWidget(row)

    def _play_local(self, start: LocalRow | None = None) -> None:
        """Play the checked files in table order; `start` picks the first one (it is included even if unchecked)."""
        rows = [r for r in self.local_rows if r.checked or r is start]
        if not rows:
            return
        index = rows.index(start) if start in rows else 0
        context.bus.play.emit([str(r.file.path) for r in rows], index, [r.file.name for r in rows])

    def _sort_local(self) -> None:
        key = self.sort_combo.currentIndex()
        funcs = [
            lambda r: local.natural_key(r.file.name),
            lambda r: -r.file.modified.timestamp(),
            lambda r: -(r.file.duration or 0),
            lambda r: -r.file.size,
        ]
        self.local_rows.sort(key=funcs[key])
        self._layout_local_rows()

    def _filter_local_kind(self, idx: int) -> None:
        for r in self.local_rows:
            r.check.setChecked(idx == 0 or (idx == 1 and not r.file.is_audio) or (idx == 2 and r.file.is_audio))

    def _refresh_local_selection(self) -> None:
        if not alive(self.local_sel):
            return
        sel = [r for r in self.local_rows if r.checked]
        size = sum(r.file.size for r in sel)
        self.local_sel.setText(f"{len(self.local_rows)}개 중 {len(sel)}개 선택됨")
        self.local_cta.setText(f"재생목록 파일로 저장 ({len(sel)}개 · {fmt_size(size)})")
        self.local_cta.setEnabled(bool(sel))

    def _pick_m3u_target(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(self, "재생목록 저장 위치", str(self._m3u_target), "M3U8 재생목록 (*.m3u8)")
        if chosen:
            self._m3u_target = Path(chosen)
            self.m3u_badge.setText(str(self._m3u_target))

    def _save_m3u(self) -> None:
        files = [r.file for r in self.local_rows if r.checked]
        try:
            path = local.write_m3u(self.local, self._m3u_target, files, self.relative_check.isChecked())
        except OSError as exc:
            self.local_hint.setText(f"저장 실패: {exc}")
            self.local_hint.setStyleSheet(f"color: {C.ACCENT}; background: transparent;")
            return
        self.local_hint.setText(f"저장됨 · {path}")
        self.local_hint.setStyleSheet(f"color: {C.SUCCESS}; background: transparent;")
        self.local_cta.setText(f"저장 완료 ({len(files)}개)")
        if context.settings.notify:
            context.bus.notify.emit("재생목록 저장됨", str(path))
