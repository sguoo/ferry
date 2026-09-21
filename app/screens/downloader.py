"""Single-video downloader: paste a link or search, pick a stream, watch the job."""

from __future__ import annotations

import re
from pathlib import Path

import shiboken6
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QSizePolicy, QWidget

from .. import context, fonts, local, workers, youtube
from ..icons import icon
from ..jobs import STATUS_LABEL, Job
from ..settings import AUDIO_PRESET, PRESETS, default_preset
from ..subtitles import SUBTITLE_EXTS
from ..theme import C
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
from ..youtube import QualityOption, SearchItem, VideoInfo, YoutubeError, fmt_duration, fmt_relative, fmt_size, fmt_speed, fmt_subscribers, fmt_views
from . import Screen

YOUTUBE_URL = re.compile(r"(https?://)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/\S+", re.IGNORECASE)
KINDS = [("전체", "all"), ("영상", "video"), ("재생목록", "playlist"), ("채널", "channel")]
SORTS = ["관련도순", "조회수순", "길이순"]
PLAYLIST_HINT = re.compile(r"[?&]list=|/playlist|/@[^/]+|/channel/|/c/|/user/", re.IGNORECASE)


def is_playlist_url(url: str) -> bool:
    return bool(PLAYLIST_HINT.search(url))


def single_video_url(url: str) -> str | None:
    """`watch?v=X&list=...&index=n` -> the bare video link, else None."""
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url)
    if not m or "list=" not in url:
        return None
    return f"https://www.youtube.com/watch?v={m.group(1)}"


def alive(w) -> bool:
    return shiboken6.isValid(w)


def needs_login(err: YoutubeError) -> bool:
    """Errors that cookies (a signed-in YouTube session) fix."""
    return any(k in err.message for k in ("봇 확인", "연령 제한", "로그인")) or "cookies" in err.detail.lower()


# --------------------------------------------------------------------------- #
# pieces
# --------------------------------------------------------------------------- #


class QualityTile(QFrame):
    clicked = Signal(object)

    def __init__(self, opt: QualityOption | None, audio_size: int | None = None, parent=None):
        """opt=None builds the audio-extraction tile."""
        super().__init__(parent)
        self.opt = opt
        self.setProperty("tile", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(96)
        lay = vbox(self, gap=6, margins=(14, 12, 14, 12))
        top = hbox(gap=8)
        if opt is None:
            self._icon = IconLabel("music", C.TEXT2, 16)
            top.addWidget(self._icon)
            self._title = label("MP3 오디오 추출 (320kbps)", "title")
            size, note, tag = audio_size, "ID3 태그 자동 기록", "CBR"
        else:
            self._icon = None
            self._title = label(opt.label, "title")
            size, note, tag = opt.est_size, opt.vcodec or "", opt.tag
        top.addWidget(self._title)
        top.addStretch()
        self._check = IconLabel("check-circle", C.ACCENT, 18)
        self._tag = Badge(tag, "neutral", mono=True)
        top.addWidget(self._check)
        top.addWidget(self._tag)
        lay.addLayout(top)
        lay.addWidget(num(fmt_size(size, approx=True), 13, 500, C.TEXT2))
        bottom = hbox()
        self._best = Badge("최고 화질" if opt is not None else "음원만", "accent")
        self._note = label(note, "muted")
        bottom.addWidget(self._best)
        bottom.addWidget(self._note)
        bottom.addStretch()
        lay.addLayout(bottom)
        self.set_selected(False)

    def set_selected(self, on: bool) -> None:
        set_prop(self, "selected", on)
        self._title.setStyleSheet(f"color: {C.ACCENT if on else C.TEXT}; background: transparent;")
        if self._icon:
            self._icon.set_color(C.ACCENT if on else C.TEXT2)
        self._check.setVisible(on)
        self._tag.setVisible(not on)
        self._best.setVisible(on)
        self._note.setVisible(not on)

    def mousePressEvent(self, event):
        self.clicked.emit(self)
        super().mousePressEvent(event)


class JobRow(QWidget):
    """Live view of one download; refreshes on job.changed."""

    def __init__(self, job: Job, parent=None):
        super().__init__(parent)
        self.job = job
        lay = hbox(self, gap=16, margins=(0, 14, 0, 14))
        self.bar = QFrame()
        self.bar.setFixedWidth(3)
        lay.addWidget(self.bar)

        self.thumb = Thumbnail(job.thumb_key, 112, job.duration, radius=6)
        self.thumb.fetch(job.thumb_url, job.thumb_key)
        lay.addWidget(self.thumb)

        info = vbox(gap=6)
        head = hbox(gap=8)
        self.status = Badge("", "neutral", mono=True)
        head.addWidget(self.status)
        head.addWidget(label(job.spec, "muted", mono=True, elide=True), 1)
        info.addLayout(head)
        info.addWidget(label(job.title, "title", size=15, elide=True))
        meta = hbox(gap=14)
        self.sizes = num("", 13, 500, C.TEXT2)
        self.speed = num("", 15, 600, C.ACCENT)
        self.eta = label("", "caption", mono=True)
        meta.addWidget(self.sizes)
        meta.addWidget(self.speed)
        meta.addWidget(self.eta)
        meta.addStretch()
        info.addLayout(meta)
        info_w = QWidget()
        info_w.setLayout(info)
        info_w.setMinimumWidth(300)
        lay.addWidget(info_w, 5)

        prog = vbox(gap=6)
        top = hbox()
        top.addWidget(label("진행률", "caption-strong"))
        top.addStretch()
        self.percent = num("0%", 16, 600)
        top.addWidget(self.percent)
        prog.addLayout(top)
        self.progress = ProgressBar(0.0, 6)
        prog.addWidget(self.progress)
        notes = hbox()
        self.note_l = label("", "muted", elide=True)
        self.note_r = label("", "muted")
        notes.addWidget(self.note_l, 1)
        notes.addWidget(self.note_r)
        prog.addLayout(notes)
        prog_w = QWidget()
        prog_w.setLayout(prog)
        lay.addWidget(prog_w, 6)

        ctl = hbox(gap=6)
        self.pause_btn = IconButton("pause", "일시중지")
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.play_file_btn = IconButton("play", "앱에서 재생")
        self.play_file_btn.clicked.connect(self._play_file)
        self.play_file_btn.hide()
        folder_btn = IconButton("folder", "폴더 열기")
        folder_btn.clicked.connect(self._open_folder)
        remove_btn = IconButton("x", "취소 / 목록에서 제거", flat=True)
        remove_btn.clicked.connect(lambda: context.jobs.remove(self.job))
        for b in (self.play_file_btn, self.pause_btn, folder_btn, remove_btn):
            ctl.addWidget(b)
        lay.addLayout(ctl)

        job.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        if not alive(self):
            return
        j = self.job
        text, tone = STATUS_LABEL[j.status]
        self.status.setText(text)
        set_prop(self.status, "badge", tone)
        colour = {"success": C.SUCCESS, "warn": C.WARN, "accent": C.ACCENT}.get(tone, C.TEXT3)
        self.bar.setStyleSheet(f"background: {colour}; border-radius: 1px;")
        p = j.progress
        self.percent.setText(f"{int(j.percent)}%")
        self.progress.set_value(j.percent / 100)
        if j.status == "downloading" and p:
            self.sizes.setText(f"{fmt_size(p.downloaded)} / {fmt_size(p.total)}")
            self.speed.setText(fmt_speed(p.speed))
            self.eta.setText(f"남은 시간 {youtube.fmt_eta(p.eta)}")
            self.note_l.setText(f"조각 {p.fragment[0]}/{p.fragment[1]} 수신 중" if p.fragment else "스트림 수신 중")
            self.note_r.setText(Path(p.filename).suffix.lstrip(".").upper() if p.filename else "")
        elif j.status in ("merging", "converting"):
            self.speed.setText("")
            self.eta.setText("")
            self.note_l.setText(p.note if p else "")
            self.note_r.setText("ffmpeg")
        elif j.status == "finished":
            self.sizes.setText(fmt_size(p.total) if p and p.total else "")
            self.speed.setText("")
            self.eta.setText("")
            self.note_l.setText(str(j.path) if j.path else "")
            self.note_r.setText("완료")
        elif j.status == "error":
            self.speed.setText("")
            self.eta.setText("")
            self.note_l.setText(j.error)
            self.note_r.setText("다시 시도하려면 재생 버튼")
        else:  # queued / paused / cancelled
            self.speed.setText("")
            self.eta.setText("")
            self.note_l.setText({"queued": "빈 슬롯을 기다리는 중", "paused": "이어받기 가능 (.part 보존)", "cancelled": "취소됨"}.get(j.status, ""))
            self.note_r.setText("")
        resumable = j.status in ("paused", "error", "cancelled", "queued")
        self.pause_btn.setIcon(icon("play" if resumable else "pause", C.TEXT2, 16))
        self.pause_btn.setToolTip("재개" if resumable else "일시중지")
        done = j.status == "finished" and j.path is not None and j.path.exists()
        self.pause_btn.setVisible(not done)
        self.play_file_btn.setVisible(done)
        if done:
            is_sub = j.path.suffix.lower() in SUBTITLE_EXTS
            self.play_file_btn.setIcon(icon("subtitles" if is_sub else "play", C.TEXT2, 16))
            self.play_file_btn.setToolTip("자막 보기" if is_sub else "앱에서 재생")

    def _toggle_pause(self) -> None:
        j = self.job
        if j.active:
            j.pause()
        else:
            context.jobs.start_now(j)

    def _play_file(self) -> None:
        if not (self.job.path and self.job.path.exists()):
            return
        if self.job.path.suffix.lower() in SUBTITLE_EXTS:
            context.bus.view_subtitles.emit(str(self.job.path))
        else:
            context.bus.play.emit([str(self.job.path)], 0, [self.job.title])

    def _open_folder(self) -> None:
        target = self.job.path if (self.job.path and self.job.path.exists()) else Path(self.job.options.output_dir)
        if target.exists():
            local.open_in_explorer(target)


class SearchResultRow(QWidget):
    def __init__(self, item: SearchItem, on_download, on_details, parent=None):
        super().__init__(parent)
        self.item = item
        lay = hbox(self, gap=16, margins=(0, 12, 0, 12))
        thumb = Thumbnail(item.id or item.url, 168, "LIVE" if item.is_live else ("LIST" if item.is_playlist else fmt_duration(item.duration)), radius=6)
        thumb.fetch(item.thumbnail_url, item.id or item.url)
        lay.addWidget(thumb)

        info = vbox(gap=6)
        info.addWidget(label(item.title, "title", size=15, elide=True))
        meta = hbox(gap=8)
        meta.addWidget(icon_text("check-circle" if item.channel_verified else "user", item.channel or "채널 정보 없음", "body-strong", C.SUCCESS if item.channel_verified else C.TEXT2, elide=True), 1)
        if item.views is not None:
            meta.addWidget(label("·", "muted"))
            meta.addWidget(label(fmt_views(item.views), "caption", mono=True))
        info.addLayout(meta)
        badges = hbox(gap=6)
        if item.is_playlist:
            badges.addWidget(Badge(f"PLAYLIST{f' · {item.playlist_count}개' if item.playlist_count else ''}", "accent", mono=True))
        if item.is_live:
            badges.addWidget(Badge("LIVE", "warn", mono=True))
        badges.addWidget(label(item.url, "muted", mono=True, elide=True), 1)
        info.addLayout(badges)
        info_w = QWidget()
        info_w.setLayout(info)
        lay.addWidget(info_w, 1)

        actions = hbox(gap=6)
        dl = Button("다운로드", "secondary", "download", "sm")
        dl.clicked.connect(lambda: on_download(item))
        actions.addWidget(dl)
        self.copy_btn = Button("링크 복사", "secondary", "copy", "sm")
        self.copy_btn.clicked.connect(self._copy_link)
        actions.addWidget(self.copy_btn)
        details = IconButton("sliders", "화질·자막을 골라서 받기")
        details.clicked.connect(lambda: on_details(item))
        actions.addWidget(details)
        lay.addLayout(actions)

    def _copy_link(self) -> None:
        QApplication.clipboard().setText(self.item.url)
        self.copy_btn.setText("복사됨")
        self.copy_btn.setIcon(icon("check", C.SUCCESS, 16))
        QTimer.singleShot(1500, self._reset_copy)

    def _reset_copy(self) -> None:
        if alive(self):
            self.copy_btn.setText("링크 복사")
            self.copy_btn.setIcon(icon("copy", C.TEXT, 16))


# --------------------------------------------------------------------------- #
# screen
# --------------------------------------------------------------------------- #


class DownloaderScreen(Screen):
    open_playlist = Signal(str)  # a playlist/channel URL that belongs on the playlist screen
    open_settings = Signal()     # user asked to connect cookies from an error banner

    def __init__(self, parent=None):
        super().__init__(parent)
        self.info: VideoInfo | None = None
        self.playlist = None
        self.results: list[SearchItem] = []
        self.output_dir: Path = context.settings.save_dir
        self._request = 0
        self._selected: QualityTile | None = None
        self._tiles: list[QualityTile] = []
        self._sub_checks: list[tuple[Check, str]] = []
        self._kind = "all"
        self._sort = 0
        self._last_query = ""

        # ---------------------------------------------------------- intake
        intake = Panel(gap=14)
        row = hbox(gap=10)
        self.mode_toggle = Segmented([("링크", "link"), ("검색", "search")], 0)
        self.mode_toggle.changed.connect(self._on_mode_changed)
        row.addWidget(self.mode_toggle)
        self.field = CompositeField("link", "YouTube 영상 링크를 붙여넣으세요")
        self.field.add_trailing(Kbd("Enter"))
        clear_btn = IconButton("x", "지우기", flat=True)
        clear_btn.clicked.connect(self._clear_field)
        self.field.add_trailing(clear_btn)
        self.field.edit.returnPressed.connect(self._submit)
        row.addWidget(self.field, 1)
        self.paste_btn = Button("클립보드 붙여넣기", "secondary", "clipboard")
        self.paste_btn.setMinimumHeight(44)
        self.paste_btn.clicked.connect(self._paste)
        row.addWidget(self.paste_btn)
        self.search_btn = Button("검색하기", "primary", "search")
        self.search_btn.setMinimumHeight(44)
        self.search_btn.setMinimumWidth(118)
        self.search_btn.clicked.connect(self._submit)
        row.addWidget(self.search_btn)
        intake.body.addLayout(row)

        mode = hbox(gap=12)
        mode.addWidget(micro("Mode"))
        self.output_mode = Segmented([("비디오 (MP4/MKV)", "film"), ("음원 추출 (MP3)", "music"), ("자막만 (SRT)", "subtitles")], 0, "accent")
        self.output_mode.changed.connect(self._on_output_mode)
        mode.addWidget(self.output_mode)
        mode.addStretch()
        mode.addWidget(label(f"yt-dlp {youtube.ytdlp_version()}", "muted", mono=True))
        mode.addWidget(label("|", "muted"))
        self.ffmpeg_label = icon_text("cpu", "", "muted", C.SUCCESS, mono=True)
        mode.addWidget(self.ffmpeg_label)
        intake.body.addLayout(mode)
        self.add_section(intake)

        # ---------------------------------------------------------- result
        self.result_host = QWidget()
        self.result_box = vbox(self.result_host, gap=0)
        self.add_section(self.result_host)

        # ---------------------------------------------------------- jobs
        self.add_section(self._build_jobs())
        self.body.addStretch()

        self._apply_mode("link")
        self._show_idle()
        self._refresh_env()
        context.bus.settings_changed.connect(self._on_settings_saved)
        self._retry_after_settings = None

    # ------------------------------------------------------------- helpers
    def _refresh_env(self) -> None:
        ver = youtube.ffmpeg_version(context.settings.ffmpeg_path or None)
        lbl = self.ffmpeg_label.layout().itemAt(1).widget()
        ico = self.ffmpeg_label.layout().itemAt(0).widget()
        lbl.setText(f"ffmpeg {ver}" if ver else "ffmpeg 없음 · 병합/변환 불가")
        ico.set_color(C.SUCCESS if ver else C.ACCENT)
        self.output_dir = context.settings.save_dir

    def _set_result(self, widget: QWidget) -> None:
        clear_layout(self.result_box)
        self.result_box.addWidget(widget)

    def _clear_field(self) -> None:
        self.field.edit.clear()
        self.field.edit.setFocus()
        if self.mode_toggle_index() == 0:
            self.info = None
            self._show_idle()

    def mode_toggle_index(self) -> int:
        return 1 if self.search_btn.isVisible() else 0

    # ---------------------------------------------------------- intake mode
    def _on_mode_changed(self, idx: int) -> None:
        self._apply_mode("search" if idx == 1 else "link")
        if idx == 1:
            if self.results:
                self._show_results()
            else:
                self._show_search_idle()
        else:
            if self.info:
                self._show_info(self.info)
            else:
                self._show_idle()

    def _apply_mode(self, mode: str) -> None:
        searching = mode == "search"
        self.mode_toggle.blockSignals(True)
        self.mode_toggle.set_active(1 if searching else 0)
        self.mode_toggle.blockSignals(False)
        self.field.icon.set_icon("search" if searching else "link")
        self.field.edit.setPlaceholderText("검색어를 입력하세요 (제목, 채널, 키워드)" if searching else "YouTube 영상 링크를 붙여넣으세요")
        self.field.set_value(self._last_query if searching else (self.info.url if self.info else ""))
        self.search_btn.setVisible(searching)
        self.paste_btn.setVisible(not searching)

    def _submit(self) -> None:
        text = self.field.edit.text().strip()
        if not text:
            return
        if self.mode_toggle_index() == 1 and not YOUTUBE_URL.match(text):
            self._search(text)
        else:
            self._apply_mode("link")
            self.field.set_value(text)
            self._fetch(text)

    def _paste(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            return
        self.field.set_value(text)
        self._submit()

    # ---------------------------------------------------------------- link
    def load_url(self, url: str) -> None:
        """Entry point for clipboard detection and search-result details."""
        self._apply_mode("link")
        self.field.set_value(url)
        self._fetch(url)

    def _fetch(self, url: str) -> None:
        if not YOUTUBE_URL.match(url):
            banner = ErrorBanner("YouTube 링크가 아닙니다", "youtube.com/watch?v=... 또는 youtu.be/... 형식의 주소를 붙여넣어 주세요.", action="지우기")
            banner.findChild(Button).clicked.connect(self._clear_field)
            set_prop(self.field, "state", "error")
            self._set_result(banner)
            return
        if is_playlist_url(url):
            self._fetch_playlist(url)
            return
        self._request += 1
        req = self._request
        set_prop(self.field, "state", "")
        self._set_result(self._build_loading())
        opts = context.settings.download_options()
        workers.call(
            youtube.fetch_info, url, opts,
            finished=lambda info, r=req: r == self._request and self._show_info(info),
            failed=lambda err, r=req, u=url: r == self._request and self._show_error(err, lambda: self._fetch(u)),
        )

    def _show_error(self, err: YoutubeError, retry, title: str = "영상 정보를 가져오지 못했습니다") -> None:
        set_prop(self.field, "state", "error")
        if needs_login(err):
            # bot check / age gate: send the user to the cookie settings and retry once they save
            banner = ErrorBanner(title, err.message, action="쿠키 연결하러 가기")
            banner.findChild(Button).clicked.connect(self.open_settings.emit)
            self._retry_after_settings = retry
        else:
            banner = ErrorBanner(title, err.message)
            banner.findChild(Button).clicked.connect(retry)
            self._retry_after_settings = None
        self._set_result(banner)

    def _on_settings_saved(self) -> None:
        self._refresh_env()
        retry = getattr(self, "_retry_after_settings", None)
        if retry and (context.settings.cookies_file or context.settings.cookies_browser):
            self._retry_after_settings = None
            retry()

    # ------------------------------------------------------- playlist links
    def _fetch_playlist(self, url: str) -> None:
        self._request += 1
        req = self._request
        set_prop(self.field, "state", "")
        self._set_result(self._build_loading())
        workers.call(
            youtube.fetch_playlist, url, context.settings.download_options(),
            finished=lambda pl, r=req, u=url: r == self._request and self._show_playlist(pl, u),
            failed=lambda err, r=req, u=url: r == self._request and self._show_error(err, lambda: self._fetch_playlist(u), "재생목록을 열 수 없습니다"),
        )

    def _show_playlist(self, pl, url: str) -> None:
        """A playlist/channel link: download everything from here, or pick items on the playlist screen."""
        self.info = None
        self.playlist = pl
        panel = Panel(gap=16)
        top = hbox(gap=20)
        cover = Thumbnail(pl.id or pl.url, 240, f"{pl.count}개")
        cover.fetch(pl.thumbnail_url, pl.id or pl.url)
        top.addWidget(cover, 0, Qt.AlignmentFlag.AlignTop)
        ident = vbox(gap=8)
        badges = hbox(gap=8)
        badges.addWidget(Badge("재생목록 감지", "accent", mono=True))
        badges.addWidget(label(pl.channel or pl.id, "muted", mono=True, elide=True), 1)
        ident.addLayout(badges)
        ident.addWidget(label(pl.title, "headline", wrap=True))
        stats = hbox(gap=14)
        stats.addWidget(icon_text("library", f"총 {pl.count}개 영상", "secondary"))
        stats.addWidget(icon_text("clock", f"총 재생시간 {youtube.fmt_duration_long(pl.total_duration)}", "secondary"))
        stats.addStretch()
        ident.addLayout(stats)
        ident.addStretch()
        preview = ", ".join(e.title for e in pl.entries[:3])
        if pl.count > 3:
            preview += f" 외 {pl.count - 3}개"
        ident.addWidget(label(preview, "muted", elide=True))
        top.addLayout(ident, 1)
        panel.body.addLayout(top)
        panel.body.addWidget(Divider())

        opts_row = hbox(gap=12)
        opts_row.addWidget(IconLabel("sliders", C.TEXT2, 16))
        opts_row.addWidget(label("전체 항목 출력", "secondary"))
        self.pl_preset = QComboBox()
        self.pl_preset.addItems([p[0] for p in PRESETS])
        self.pl_preset.setCurrentIndex(AUDIO_PRESET if self.output_mode_index() == 1 else default_preset(context.settings.quality))
        self.pl_preset.setFont(fonts.sans(13, 500))
        self.pl_preset.setMinimumWidth(220)
        opts_row.addWidget(self.pl_preset)
        opts_row.addSpacing(8)
        opts_row.addWidget(IconLabel("folder-open", C.TEXT2, 16))
        safe = "".join(ch for ch in pl.title if ch not in '\\/:*?"<>|').strip() or "playlist"
        self.pl_dir = self.output_dir / safe
        self.pl_dir_badge = Badge(str(self.pl_dir), "neutral", mono=True, max_px=360)
        opts_row.addWidget(self.pl_dir_badge)
        change = Button("변경", "ghost", size="sm")
        change.clicked.connect(self._pick_playlist_dir)
        opts_row.addWidget(change)
        opts_row.addStretch()
        panel.body.addLayout(opts_row)

        cta = hbox(gap=10)
        all_btn = Button(f"재생목록 전체 다운로드 ({pl.count}개)", "primary", "arrow-down-circle", "lg")
        all_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        all_btn.clicked.connect(self._enqueue_playlist)
        cta.addWidget(all_btn, 1)
        pick = Button("항목 골라서 받기", "secondary", "playlist", "lg")
        pick.clicked.connect(lambda: self.open_playlist.emit(url))
        cta.addWidget(pick)
        single = single_video_url(url)
        if single:
            only = Button("이 영상만", "secondary", "film", "lg")
            only.clicked.connect(lambda: self.load_url(single))
            cta.addWidget(only)
        panel.body.addLayout(cta)
        self._set_result(panel)

    def _pick_playlist_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", str(self.pl_dir.parent))
        if chosen:
            self.pl_dir = Path(chosen)
            self.pl_dir_badge.setText(str(self.pl_dir))

    def _enqueue_playlist(self) -> None:
        pl = self.playlist
        if not pl or not pl.entries:
            return
        _, mode, height = PRESETS[self.pl_preset.currentIndex()]
        opts = context.settings.download_options(mode=mode, quality=height, output_dir=self.pl_dir)
        spec = f"{opts.container.upper()} · {height}p" if (mode == "video" and height) else ("MP3 · 320k" if mode == "audio" else f"{opts.container.upper()} · best")
        for e in reversed(pl.entries):  # jobs list shows newest first; keep playlist order on screen
            job = Job(e.url, e.title, opts, spec, fmt_duration(e.duration), e.thumbnail_url, e.id or e.url)
            context.jobs.add(job)
        self._set_result(EmptyState("check-circle", f"{pl.count}개 항목을 대기열에 넣었습니다", f"아래 '진행 중인 작업'에서 진행 상황을 볼 수 있습니다. 동시에 {context.jobs.max_parallel}개씩 내려받고, 저장 위치는 {self.pl_dir} 입니다."))

    def _show_idle(self) -> None:
        self._set_result(
            EmptyState(
                "link",
                "분석할 영상이 없습니다",
                "YouTube 링크를 붙여넣으면 바로 영상 정보를 불러옵니다. 검색 모드로 전환하면 제목·채널·키워드로 영상을 찾아 다운로드하거나 링크를 복사할 수 있습니다.",
                hint="클립보드 감지가 켜져 있으면 링크를 복사하는 즉시 자동으로 채워집니다." if context.settings.clipboard_watch else None,
            )
        )

    def _build_loading(self) -> QWidget:
        panel = Panel()
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        left = vbox(gap=12)
        left.addWidget(Skeleton(340, 191, 8))
        left.addWidget(Skeleton(300, 20))
        left.addWidget(Skeleton(220, 20))
        left.addWidget(Skeleton(180, 14))
        left.addStretch()
        lw = QWidget()
        lw.setLayout(left)
        grid.addWidget(lw, 0, 0)
        right = vbox(gap=12)
        right.addWidget(Skeleton(160, 12))
        tiles = QGridLayout()
        tiles.setSpacing(10)
        for i in range(3):
            tiles.addWidget(Skeleton(None, 96, 8), 0, i)
        tiles.addWidget(Skeleton(None, 96, 8), 1, 0, 1, 3)
        right.addLayout(tiles)
        right.addWidget(Skeleton(None, 40, 8))
        right.addWidget(Skeleton(None, 52, 8))
        rw = QWidget()
        rw.setLayout(right)
        grid.addWidget(rw, 0, 1)
        grid.setColumnStretch(1, 1)
        panel.body.addLayout(grid)
        return panel

    def _show_info(self, info: VideoInfo) -> None:
        self.info = info
        self.field.set_value(info.url)
        panel = Panel()
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(0)

        # left: identity
        left = vbox(gap=12)
        badges = []
        if info.best:
            badges.append((info.best.tag, "accent"))
            if info.best.fps > 30:
                badges.append((f"{info.best.fps} FPS", "neutral"))
        if info.is_live:
            badges.append(("LIVE", "warn"))
        thumb = Thumbnail(info.id, 340, fmt_duration(info.duration), badges)
        thumb.fetch(info.thumbnail_url, info.id)
        left.addWidget(thumb)
        left.addSpacing(4)
        left.addWidget(label(info.title, "headline", wrap=True))
        ch = hbox(gap=10)
        ch.addWidget(icon_text("check-circle" if info.channel_verified else "user", info.channel, "body-strong", C.SUCCESS if info.channel_verified else C.TEXT2))
        if info.subscribers:
            ch.addWidget(label("·", "muted"))
            ch.addWidget(label(fmt_subscribers(info.subscribers), "caption"))
        if info.upload_date:
            ch.addWidget(label("·", "muted"))
            ch.addWidget(label(f"{fmt_relative(info.upload_date)} 업로드", "caption"))
        ch.addStretch()
        left.addLayout(ch)
        left.addWidget(icon_text("eye", fmt_views(info.views), "caption", mono=True))
        left.addStretch()
        audio = QFrame()
        audio.setProperty("surface", True)
        arow = hbox(audio, gap=8, margins=(12, 8, 12, 8))
        arow.addWidget(IconLabel("waveform", C.TEXT2, 16))
        arow.addWidget(label(f"{info.audio_codec or '오디오'} {info.audio_bitrate or '?'}k · {fmt_size(info.audio_est_size, approx=True)}", "caption-strong", mono=True, elide=True), 1)
        arow.addWidget(Badge(f"{info.stream_count} STREAMS", "neutral", mono=True))
        left.addWidget(audio)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(340)
        grid.addWidget(lw, 0, 0)

        # right: output spec
        right = vbox(gap=14)
        head = hbox(gap=8)
        head.addWidget(IconLabel("sliders", C.TEXT2, 16))
        head.addWidget(micro("Output specifications & encoding"))
        head.addStretch()
        head.addWidget(label(f"화질 {len(info.qualities)}종 감지", "micro", color=C.ACCENT))
        right.addLayout(head)
        right.addWidget(label("해상도 및 화질 선택", "title"))

        tiles = QGridLayout()
        tiles.setSpacing(10)
        self._tiles = []
        for i, q in enumerate(info.qualities[:6]):
            t = QualityTile(q)
            t.clicked.connect(self._select_tile)
            self._tiles.append(t)
            tiles.addWidget(t, i // 3, i % 3)
        self.audio_tile = QualityTile(None, info.audio_est_size)
        self.audio_tile.clicked.connect(self._select_tile)
        rows = (len(self._tiles) + 2) // 3
        tiles.addWidget(self.audio_tile, rows, 0, 1, 3)
        for i in range(3):
            tiles.setColumnStretch(i, 1)
        right.addLayout(tiles)

        two = hbox(gap=24)
        codec_col = vbox(gap=8)
        codec_col.addWidget(label("비디오 코덱", "subtitle"))
        families = []
        for q in info.qualities:
            if q.vcodec and q.vcodec not in families:
                families.append(q.vcodec)
        self._codecs = ["자동"] + families
        self.codec_seg = Segmented(self._codecs, 0)
        codec_col.addWidget(self.codec_seg)
        codec_col.addStretch()
        two.addLayout(codec_col, 1)
        subs_col = vbox(gap=8)
        subs_col.addWidget(label("자막 스트림 포함", "subtitle"))
        self._sub_checks = []
        if info.subtitles:
            for track in info.subtitles[:4]:
                chk = Check(f"{track.name} ({track.lang}){' · 자동 생성' if track.auto else ''}", track.lang.split("-")[0] in context.settings.subtitle_langs)
                self._sub_checks.append((chk, track.lang))
                subs_col.addWidget(chk)
        else:
            subs_col.addWidget(label("이 영상에는 자막 트랙이 없습니다.", "muted"))
        subs_col.addStretch()
        two.addLayout(subs_col, 1)
        right.addLayout(two)

        path = QFrame()
        path.setProperty("surface", True)
        prow = hbox(path, gap=12, margins=(12, 10, 10, 10))
        prow.addWidget(IconLabel("folder-open", C.TEXT2, 18))
        pcol = vbox(gap=2)
        pcol.addWidget(micro("저장 경로"))
        self.path_label = label(str(self.output_dir), "body-strong", mono=True, elide=True)
        pcol.addWidget(self.path_label)
        prow.addLayout(pcol, 1)
        change = Button("경로 변경", "secondary", size="sm")
        change.clicked.connect(self._pick_output_dir)
        prow.addWidget(change)
        right.addWidget(path)

        cta = hbox(gap=10)
        self.cta = Button("지금 다운로드", "primary", "arrow-down-circle", "lg")
        self.cta.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cta.clicked.connect(lambda: self._enqueue(True))
        cta.addWidget(self.cta, 1)
        queue = Button("대기열 추가", "secondary", "queue-plus", "lg")
        queue.clicked.connect(lambda: self._enqueue(False))
        cta.addWidget(queue)
        right.addLayout(cta)

        rw = QWidget()
        rw.setLayout(right)
        grid.addWidget(rw, 0, 1)
        grid.setColumnStretch(1, 1)
        panel.body.addLayout(grid)
        self._set_result(panel)

        # default selection follows the output mode + settings quality
        if self.output_mode_index() == 1 or not self._tiles:
            self._select_tile(self.audio_tile)
        else:
            wanted = context.settings.quality
            pick = self._tiles[0]
            if wanted:
                fitting = [t for t in self._tiles if t.opt.height <= wanted]
                if fitting:
                    pick = fitting[0]
            self._select_tile(pick)

    def output_mode_index(self) -> int:
        return self.output_mode.active_index()

    def _select_tile(self, tile: QualityTile) -> None:
        self._selected = tile
        for t in self._tiles + [self.audio_tile]:
            t.set_selected(t is tile)
        if tile.opt is None:
            self.output_mode.blockSignals(True)
            self.output_mode.set_active(1)
            self.output_mode.blockSignals(False)
        elif self.output_mode_index() == 1:
            self.output_mode.blockSignals(True)
            self.output_mode.set_active(0)
            self.output_mode.blockSignals(False)
        self._update_cta()

    def _on_output_mode(self, idx: int) -> None:
        if not self.info or not alive(self.audio_tile):
            return
        if idx == 1:
            self._select_tile(self.audio_tile)
        elif idx == 0 and self._selected is self.audio_tile and self._tiles:
            self._select_tile(self._tiles[0])
        else:
            self._update_cta()

    def _update_cta(self) -> None:
        if not alive(self.cta):
            return
        if self.output_mode_index() == 2:
            self.cta.setText("자막만 다운로드")
            return
        size = None
        if self._selected is not None:
            size = self.info.audio_est_size if self._selected.opt is None else self._selected.opt.est_size
        self.cta.setText(f"지금 다운로드 ({fmt_size(size, approx=True)})" if size else "지금 다운로드")

    def _pick_output_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", str(self.output_dir))
        if chosen:
            self.output_dir = Path(chosen)
            self.path_label.setText(str(self.output_dir))

    def _current_options(self):
        mode_idx = self.output_mode_index()
        mode = ["video", "audio", "subtitles"][mode_idx]
        quality = None
        if mode == "video" and self._selected is not None and self._selected.opt is not None:
            quality = self._selected.opt.height
        codec = None
        if hasattr(self, "codec_seg") and alive(self.codec_seg) and self.codec_seg.active_index() > 0:
            codec = self._codecs[self.codec_seg.active_index()]
        langs = [lang for chk, lang in self._sub_checks if chk.isChecked()]
        if mode == "subtitles" and not langs:
            langs = [t.lang for t in self.info.subtitles[:1]] if self.info and self.info.subtitles else ["ko"]
        return context.settings.download_options(mode=mode, quality=quality, vcodec=codec, subtitle_langs=langs, output_dir=self.output_dir)

    def _enqueue(self, immediately: bool) -> None:
        if not self.info:
            return
        opts = self._current_options()
        if opts.mode == "video":
            q = self._selected.opt if (self._selected and self._selected.opt) else self.info.best
            codec = opts.vcodec or (q.vcodec if q else "")
            spec = f"{opts.container.upper()} · {q.label if q else 'best'}" + (f" · {codec}" if codec else "")
        elif opts.mode == "audio":
            spec = f"MP3 · {opts.audio_bitrate}k"
        else:
            spec = "SRT · " + ", ".join(opts.subtitle_langs)
        job = Job(self.info.url, self.info.title, opts, spec, fmt_duration(self.info.duration), self.info.thumbnail_url, self.info.id)
        context.jobs.add(job, immediately=immediately)

    # -------------------------------------------------------------- search
    def _show_search_idle(self) -> None:
        self._set_result(EmptyState("search", "검색어를 입력하세요", "제목, 채널, 키워드로 YouTube를 검색합니다. 결과에서 바로 다운로드하거나 링크를 복사할 수 있고, 화질을 직접 고르려면 슬라이더 아이콘을 누르세요."))

    def _search(self, query: str, kind: str | None = None) -> None:
        self._last_query = query
        if kind is not None:
            self._kind = kind
        self._request += 1
        req = self._request
        panel = Panel(gap=14)
        for _ in range(4):
            r = hbox(gap=16)
            r.addWidget(Skeleton(168, 95, 6))
            c = vbox(gap=8)
            c.addWidget(Skeleton(360, 18))
            c.addWidget(Skeleton(220, 12))
            c.addWidget(Skeleton(140, 12))
            r.addLayout(c, 1)
            r.addWidget(Skeleton(200, 32, 8))
            panel.body.addLayout(r)
        self._set_result(panel)
        workers.call(
            youtube.search, query, 12, self._kind,
            finished=lambda items, r=req: r == self._request and self._on_results(items),
            failed=lambda err, r=req, q=query: r == self._request and self._show_search_error(err, q),
        )

    def _show_search_error(self, err: YoutubeError, query: str) -> None:
        self._show_error(err, lambda: self._search(query), "검색에 실패했습니다")

    def _on_results(self, items: list[SearchItem]) -> None:
        self.results = items
        self._show_results()

    def _sorted_results(self) -> list[SearchItem]:
        if self._sort == 1:
            return sorted(self.results, key=lambda i: i.views or 0, reverse=True)
        if self._sort == 2:
            return sorted(self.results, key=lambda i: i.duration or 0, reverse=True)
        return list(self.results)

    def _show_results(self) -> None:
        panel = Panel(gap=4)
        head = hbox(gap=10)
        head.addWidget(IconLabel("search", C.TEXT2, 18))
        head.addWidget(label(f"'{self._last_query}' 검색 결과", "title", elide=True), 1)
        head.addWidget(Badge(f"결과 {len(self.results)}개", "neutral", mono=True))
        head.addSpacing(8)
        kinds = Segmented([k for k, _ in KINDS], [v for _, v in KINDS].index(self._kind))
        kinds.changed.connect(lambda i: self._search(self._last_query, KINDS[i][1]))
        head.addWidget(kinds)
        sorts = Segmented(SORTS, self._sort)
        sorts.changed.connect(self._on_sort)
        head.addWidget(sorts)
        panel.body.addLayout(head)
        panel.body.addSpacing(6)
        items = self._sorted_results()
        if not items:
            panel.body.addWidget(EmptyState("search", "결과가 없습니다", "다른 검색어나 종류 필터로 다시 시도해 보세요."))
        for i, item in enumerate(items):
            if i:
                panel.body.addWidget(Divider())
            panel.body.addWidget(SearchResultRow(item, self._quick_download, self._details))
        panel.body.addWidget(Divider())
        foot = hbox(gap=10, margins=(0, 12, 0, 0))
        foot.addWidget(label("다운로드 버튼은 현재 Mode와 환경설정의 기본 화질로 즉시 시작합니다.", "muted"))
        foot.addStretch()
        panel.body.addLayout(foot)
        self._set_result(panel)

    def _on_sort(self, idx: int) -> None:
        self._sort = idx
        self._show_results()

    def _quick_download(self, item: SearchItem) -> None:
        if item.is_playlist:
            self.open_playlist.emit(item.url)
            return
        mode = ["video", "audio", "subtitles"][self.output_mode_index()]
        opts = context.settings.download_options(mode=mode, subtitle_langs=context.settings.subtitle_langs if mode == "subtitles" else [], output_dir=self.output_dir)
        spec = {"video": f"{opts.container.upper()} · {opts.quality or 'best'}p" if opts.quality else f"{opts.container.upper()} · best", "audio": f"MP3 · {opts.audio_bitrate}k", "subtitles": "SRT"}[mode]
        job = Job(item.url, item.title, opts, spec, fmt_duration(item.duration), item.thumbnail_url, item.id or item.url)
        context.jobs.add(job)

    def _details(self, item: SearchItem) -> None:
        if item.is_playlist:
            self.open_playlist.emit(item.url)
        else:
            self.load_url(item.url)

    # ---------------------------------------------------------------- jobs
    def _build_jobs(self) -> QWidget:
        panel = Panel(gap=4)
        head = hbox(gap=10)
        head.addWidget(IconLabel("arrows-v", C.TEXT2, 18))
        head.addWidget(label("진행 중인 작업", "title"))
        self.jobs_badge = Badge("0개 활성", "neutral", mono=True)
        head.addWidget(self.jobs_badge)
        head.addStretch()
        pause_all = Button("모두 일시중지", "secondary", "pause", "sm")
        pause_all.clicked.connect(lambda: context.jobs.pause_all())
        head.addWidget(pause_all)
        clear_done = Button("완료 항목 정리", "ghost", "check", "sm")
        clear_done.clicked.connect(lambda: context.jobs.clear_done())
        head.addWidget(clear_done)
        panel.body.addLayout(head)
        panel.body.addSpacing(6)
        self.jobs_box = vbox(gap=0)
        panel.body.addLayout(self.jobs_box)
        self.jobs_empty = label("아직 시작한 다운로드가 없습니다.", "muted")
        panel.body.addWidget(self.jobs_empty)
        self._rows: dict[Job, JobRow] = {}
        context.jobs.added.connect(self._on_job_added)
        context.jobs.removed.connect(self._on_job_removed)
        context.jobs.changed.connect(lambda _j: self._refresh_jobs_badge())
        for j in reversed(context.jobs.jobs):
            self._on_job_added(j)
        return panel

    def _on_job_added(self, job: Job) -> None:
        row = JobRow(job)
        self._rows[job] = row
        if self.jobs_box.count():
            self.jobs_box.insertWidget(0, Divider())
        self.jobs_box.insertWidget(0, row)
        self._refresh_jobs_badge()

    def _on_job_removed(self, job: Job) -> None:
        row = self._rows.pop(job, None)
        if row is None:
            return
        idx = self.jobs_box.indexOf(row)
        row.setParent(None)
        row.deleteLater()
        # drop the divider that followed (or preceded) it
        if self.jobs_box.count() > idx:
            item = self.jobs_box.itemAt(idx)
            if item and isinstance(item.widget(), Divider):
                w = self.jobs_box.takeAt(idx).widget()
                w.deleteLater()
        elif idx > 0:
            item = self.jobs_box.itemAt(idx - 1)
            if item and isinstance(item.widget(), Divider):
                w = self.jobs_box.takeAt(idx - 1).widget()
                w.deleteLater()
        self._refresh_jobs_badge()

    def _refresh_jobs_badge(self) -> None:
        active = len(context.jobs.active)
        queued = len(context.jobs.queued)
        text = f"{active}개 활성" + (f" · {queued}개 대기" if queued else "")
        self.jobs_badge.setText(text)
        set_prop(self.jobs_badge, "badge", "accent" if active else "neutral")
        self.jobs_empty.setVisible(not context.jobs.jobs)
