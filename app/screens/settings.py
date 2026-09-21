"""Preferences: two-column sections (explanation left, controls right), saved to settings.json."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFileDialog, QLineEdit, QWidget

from .. import context, fonts, workers, youtube
from ..settings import CONTAINERS, COOKIE_BROWSERS, LANGUAGES, QUALITY_CHOICES, THREAD_CHOICES, Settings
from ..youtube import DownloadOptions
from ..theme import C
from ..widgets.primitives import (
    Button,
    CompositeField,
    Divider,
    FormField,
    Segmented,
    Toggle,
    hbox,
    label,
    vbox,
)
from . import Screen

LEFT_COL = 280


def _combo(items: list[str], current: int = 0) -> QComboBox:
    c = QComboBox()
    c.addItems(items)
    c.setCurrentIndex(current)
    c.setFont(fonts.sans(13, 500))
    c.setCursor(Qt.CursorShape.PointingHandCursor)
    return c


def _line(value: str = "", placeholder: str = "", mono: bool = False) -> QLineEdit:
    e = QLineEdit(value)
    e.setPlaceholderText(placeholder)
    e.setFont(fonts.mono(13, 500) if mono else fonts.sans(13, 500))
    e.setMinimumHeight(38)
    return e


class SwitchRow(QWidget):
    def __init__(self, title: str, description: str, on: bool, parent=None):
        super().__init__(parent)
        lay = hbox(self, gap=16)
        col = vbox(gap=3)
        col.addWidget(label(title, "body-strong"))
        col.addWidget(label(description, "muted", wrap=True))
        lay.addLayout(col, 1)
        self.toggle = Toggle(on)
        lay.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignTop)


class SettingsSection(QWidget):
    """Asymmetric 280px / 1fr split."""

    def __init__(self, title: str, description: str, parent=None):
        super().__init__(parent)
        lay = hbox(self, gap=40)
        left = vbox(gap=6)
        left.addWidget(label(title, "title"))
        left.addWidget(label(description, "secondary", wrap=True))
        left.addStretch()
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(LEFT_COL)
        lay.addWidget(lw, 0, Qt.AlignmentFlag.AlignTop)
        self.form = vbox(gap=18)
        fw = QWidget()
        fw.setLayout(self.form)
        fw.setMaximumWidth(720)
        lay.addWidget(fw, 1)


class SettingsScreen(Screen):
    def __init__(self, parent=None):
        super().__init__(parent)

        head = QWidget()
        hl = hbox(head, gap=12)
        col = vbox(gap=4)
        col.addWidget(label("환경설정", "headline"))
        self.status = label("변경 사항은 다음 다운로드부터 적용됩니다. 진행 중인 작업에는 영향을 주지 않습니다.", "secondary")
        col.addWidget(self.status)
        hl.addLayout(col, 1)
        reset = Button("기본값 복원", "ghost")
        reset.clicked.connect(self._reset)
        hl.addWidget(reset)
        save = Button("변경 사항 저장", "primary", "check")
        save.clicked.connect(self._save)
        hl.addWidget(save)
        self.add_section(head)

        # ---------------------------------------------------------- storage
        sec = SettingsSection("저장 위치와 파일명", "다운로드가 완료된 파일이 놓일 폴더와 파일명 규칙입니다. yt-dlp 출력 템플릿 문법을 그대로 사용합니다.")
        self.save_path = CompositeField("folder-open", "", "", mono=True)
        browse = Button("찾아보기", "secondary", size="sm")
        browse.clicked.connect(lambda: self._pick_dir(self.save_path))
        self.save_path.add_trailing(browse)
        self.save_path_field = FormField("기본 저장 폴더", self.save_path, "재생목록 다운로드는 이 폴더 아래에 재생목록 이름으로 하위 폴더를 만듭니다. 라이브러리도 이 폴더를 보여줍니다.")
        sec.form.addWidget(self.save_path_field)
        self.template = _line("", mono=True)
        self.template_field = FormField("파일명 템플릿", self.template, "사용 가능한 필드: %(title)s, %(id)s, %(uploader)s, %(upload_date)s, %(ext)s")
        sec.form.addWidget(self.template_field)
        self.add_section(sec)
        self.add_section(Divider())

        # ---------------------------------------------------------- quality
        sec = SettingsSection("기본 화질과 컨테이너", "분석 후 자동으로 선택될 스트림입니다. 영상마다 다운로더 화면에서 바꿀 수 있습니다.")
        self.quality = _combo([q[0] for q in QUALITY_CHOICES], 0)
        sec.form.addWidget(FormField("기본 화질", self.quality, "선택한 화질이 없으면 그 아래 가장 가까운 화질을 고릅니다."))
        containers = QWidget()
        crow = hbox(containers, gap=0)
        self.container = Segmented([c.upper() for c in CONTAINERS], 0)
        crow.addWidget(self.container)
        crow.addStretch()
        sec.form.addWidget(FormField("출력 컨테이너", containers, "MKV는 모든 코덱과 다중 자막 트랙을 담을 수 있고, MP4는 호환성이 가장 넓습니다."))
        self.subs = _line("", "예: ko, en", mono=True)
        sec.form.addWidget(FormField("기본 자막 언어", self.subs, "쉼표로 구분. 분석 화면에서 해당 언어 자막이 있으면 미리 체크됩니다."))
        self.add_section(sec)
        self.add_section(Divider())

        # ---------------------------------------------------------- network
        sec = SettingsSection("네트워크", "동시에 받을 작업 수와 대역폭 상한입니다. 값이 클수록 빠르지만 다른 프로그램의 인터넷 사용이 느려질 수 있습니다.")
        self.threads = _combo([f"{n}개" for n in THREAD_CHOICES], 2)
        sec.form.addWidget(FormField("동시 다운로드", self.threads))
        self.speed = _line("", "예: 20M (비워두면 제한 없음)", mono=True)
        self.speed_field = FormField("속도 제한", self.speed, "단위: K, M. 총 대역폭이 아닌 작업당 상한입니다.")
        sec.form.addWidget(self.speed_field)
        self.add_section(sec)
        self.add_section(Divider())

        # ---------------------------------------------------------- behaviour
        sec = SettingsSection("동작", "앱이 백그라운드에서 무엇을 감시하고 어떻게 알릴지 정합니다.")
        self.sw_clip = SwitchRow("클립보드 감지", "YouTube 링크를 복사하면 다운로더 입력창에 자동으로 채우고 바로 분석합니다.", True)
        self.sw_bg = SwitchRow("다른 화면에서도 계속 재생", "재생 중에 다른 화면으로 이동하면 플레이어를 닫지 않고 하단 미니 재생바로 줄여서 계속 재생합니다. 끄면 화면을 이동할 때 재생을 멈춥니다.", True)
        self.sw_notify = SwitchRow("완료 알림", "다운로드가 끝나면 트레이 알림을 표시합니다.", True)
        self.sw_sound = SwitchRow("알림 사운드", "완료 알림과 함께 시스템 효과음을 재생합니다.", False)
        for sw in (self.sw_clip, self.sw_bg, self.sw_notify, self.sw_sound):
            sec.form.addWidget(sw)
        self.add_section(sec)
        self.add_section(Divider())

        # ---------------------------------------------------------- tools
        sec = SettingsSection("외부 도구", "스트림 병합과 오디오 변환에 ffmpeg가 필요합니다. PATH에 있으면 자동으로 찾습니다.")
        self.ffmpeg = CompositeField("cpu", "비워두면 PATH에서 찾습니다", "", mono=True)
        fbrowse = Button("찾아보기", "secondary", size="sm")
        fbrowse.clicked.connect(self._pick_ffmpeg)
        self.ffmpeg.add_trailing(fbrowse)
        self.ffmpeg_field = FormField("ffmpeg 경로", self.ffmpeg)
        self.ffmpeg.edit.editingFinished.connect(self._check_ffmpeg)
        sec.form.addWidget(self.ffmpeg_field)
        self.add_section(sec)
        self.add_section(Divider())

        # ---------------------------------------------------------- youtube account
        sec = SettingsSection(
            "YouTube 계정 연동 (쿠키)",
            "\"봇이 아님을 확인\" 오류, 연령 제한·회원 전용 영상은 로그인 세션이 있어야 받을 수 있습니다. "
            "브라우저의 쿠키를 그대로 쓰거나, 로그인된 브라우저에서 내보낸 cookies.txt를 지정하세요.",
        )
        self.cookie_file = CompositeField("shield", "예: C:\\Users\\me\\Downloads\\youtube.com_cookies.txt", "", mono=True)
        cbrowse = Button("찾아보기", "secondary", size="sm")
        cbrowse.clicked.connect(self._pick_cookie_file)
        self.cookie_file.add_trailing(cbrowse)
        self.cookie_file_field = FormField(
            "cookies.txt 파일 (권장)",
            self.cookie_file,
            "가장 확실한 방법입니다. Chrome/Edge 확장 'Get cookies.txt LOCALLY'로 youtube.com에 로그인한 상태에서 내보낸 Netscape 형식 파일을 고르세요. 지정하면 아래 브라우저 설정보다 우선합니다.",
        )
        sec.form.addWidget(self.cookie_file_field)
        self.cookies = _combo([b[0] for b in COOKIE_BROWSERS], 0)
        self.cookies_field = FormField(
            "브라우저에서 직접 읽기",
            self.cookies,
            "브라우저 프로필의 쿠키 DB를 직접 읽습니다. Firefox는 잘 되지만 최신 Chrome/Edge는 실행 중이거나 암호화 방식 때문에 실패하는 경우가 많습니다 — 그럴 땐 위의 cookies.txt를 쓰세요.",
        )
        sec.form.addWidget(self.cookies_field)
        test_row = QWidget()
        trow = hbox(test_row, gap=10)
        self.cookie_test_btn = Button("연결 테스트", "secondary", "check")
        self.cookie_test_btn.clicked.connect(self._test_cookies)
        trow.addWidget(self.cookie_test_btn)
        self.cookie_status = label("아직 테스트하지 않았습니다.", "muted", wrap=True)
        trow.addWidget(self.cookie_status, 1)
        sec.form.addWidget(test_row)
        self.cookie_section = sec
        self.add_section(sec)
        self.add_section(Divider())

        # ---------------------------------------------------------- language
        sec = SettingsSection("표시", "인터페이스 언어입니다. (현재 버전은 한국어 UI만 제공합니다.)")
        self.language = _combo(LANGUAGES, 0)
        sec.form.addWidget(FormField("언어", self.language))
        self.add_section(sec)
        self.body.addStretch()

        self._load(context.settings)

    # ------------------------------------------------------------- binding
    def _load(self, s: Settings) -> None:
        self.save_path.set_value(s.save_path)
        self.template.setText(s.filename_template)
        self.quality.setCurrentIndex(next((i for i, q in enumerate(QUALITY_CHOICES) if q[1] == s.quality), 0))
        self.container.set_active(CONTAINERS.index(s.container) if s.container in CONTAINERS else 0)
        self.subs.setText(", ".join(s.subtitle_langs))
        self.threads.setCurrentIndex(THREAD_CHOICES.index(s.threads) if s.threads in THREAD_CHOICES else 2)
        self.speed.setText(s.speed_limit)
        self.sw_clip.toggle.setChecked(s.clipboard_watch)
        self.sw_bg.toggle.setChecked(s.background_play)
        self.sw_notify.toggle.setChecked(s.notify)
        self.sw_sound.toggle.setChecked(s.sound)
        self.ffmpeg.set_value(s.ffmpeg_path)
        self.cookies.setCurrentIndex(next((i for i, b in enumerate(COOKIE_BROWSERS) if b[1] == s.cookies_browser), 0))
        self.cookie_file.set_value(s.cookies_file)
        self.language.setCurrentIndex(LANGUAGES.index(s.language) if s.language in LANGUAGES else 0)
        self._check_ffmpeg()

    def _collect(self) -> Settings | None:
        s = context.settings
        path = self.save_path.edit.text().strip()
        if not path:
            self.save_path_field.set_error("저장 폴더를 입력해 주세요.")
            return None
        self.save_path_field.set_error(None)
        template = self.template.text().strip() or "%(title)s [%(id)s].%(ext)s"
        if "%(ext)s" not in template:
            self.template_field.set_error("템플릿에는 %(ext)s가 있어야 확장자가 붙습니다.")
            return None
        self.template_field.set_error(None)
        speed = self.speed.text().strip()
        if speed:
            try:
                youtube._parse_rate(speed)
            except youtube.YoutubeError as exc:
                self.speed_field.set_error(exc.message)
                return None
        self.speed_field.set_error(None)
        s.save_path = path
        s.filename_template = template
        s.quality = QUALITY_CHOICES[self.quality.currentIndex()][1]
        s.container = CONTAINERS[self.container.active_index()]
        s.subtitle_langs = [x.strip() for x in self.subs.text().split(",") if x.strip()]
        s.threads = THREAD_CHOICES[self.threads.currentIndex()]
        s.speed_limit = speed
        s.clipboard_watch = self.sw_clip.toggle.isChecked()
        s.background_play = self.sw_bg.toggle.isChecked()
        s.notify = self.sw_notify.toggle.isChecked()
        s.sound = self.sw_sound.toggle.isChecked()
        s.ffmpeg_path = self.ffmpeg.edit.text().strip()
        s.cookies_browser = COOKIE_BROWSERS[self.cookies.currentIndex()][1]
        cookie_path = self.cookie_file.edit.text().strip()
        if cookie_path and not Path(cookie_path).is_file():
            self.cookie_file_field.set_error("cookies.txt 파일을 찾을 수 없습니다.")
            return None
        self.cookie_file_field.set_error(None)
        s.cookies_file = cookie_path
        s.language = LANGUAGES[self.language.currentIndex()]
        return s

    def _save(self) -> None:
        s = self._collect()
        if s is None:
            self.status.setText("저장하지 못했습니다. 표시된 항목을 확인해 주세요.")
            self.status.setStyleSheet(f"color: {C.ACCENT}; background: transparent;")
            return
        Path(s.save_path).expanduser().mkdir(parents=True, exist_ok=True)
        context.save_settings()
        self.status.setText("저장했습니다. 다음 다운로드부터 적용됩니다.")
        self.status.setStyleSheet(f"color: {C.SUCCESS}; background: transparent;")

    def _reset(self) -> None:
        self._load(Settings())
        self.status.setText("기본값을 불러왔습니다. 저장 버튼을 눌러야 적용됩니다.")
        self.status.setStyleSheet(f"color: {C.TEXT2}; background: transparent;")

    def _pick_dir(self, field: CompositeField) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "폴더 선택", field.edit.text() or str(Path.home()))
        if chosen:
            field.set_value(chosen)

    def _pick_cookie_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "cookies.txt 선택", self.cookie_file.edit.text() or str(Path.home() / "Downloads"), "쿠키 파일 (*.txt);;모든 파일 (*)")
        if chosen:
            self.cookie_file.set_value(chosen)
            self.cookie_file_field.set_error(None)

    def _test_cookies(self) -> None:
        """Try the currently *entered* cookie source (not yet saved) against YouTube."""
        opts = DownloadOptions(
            cookies_file=self.cookie_file.edit.text().strip() or None,
            cookies_from_browser=COOKIE_BROWSERS[self.cookies.currentIndex()][1] or None,
        )
        self.cookie_test_btn.setEnabled(False)
        self.cookie_status.setText("확인 중... (브라우저 쿠키는 몇 초 걸릴 수 있습니다)")
        self.cookie_status.setStyleSheet(f"color: {C.TEXT2}; background: transparent;")
        workers.call(youtube.test_cookies, opts, finished=self._cookie_ok, failed=self._cookie_failed)

    def _cookie_ok(self, summary: str) -> None:
        self.cookie_test_btn.setEnabled(True)
        self.cookie_status.setText(f"연결됨 · {summary} · 저장 버튼을 눌러 적용하세요.")
        self.cookie_status.setStyleSheet(f"color: {C.SUCCESS}; background: transparent;")

    def _cookie_failed(self, err) -> None:
        self.cookie_test_btn.setEnabled(True)
        self.cookie_status.setText(f"실패 · {err.message}")
        self.cookie_status.setStyleSheet(f"color: {C.ACCENT}; background: transparent;")

    def _pick_ffmpeg(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "ffmpeg 실행 파일", self.ffmpeg.edit.text() or "C:\\", "ffmpeg (ffmpeg.exe ffmpeg);;모든 파일 (*)")
        if chosen:
            self.ffmpeg.set_value(chosen)
            self._check_ffmpeg()

    def _check_ffmpeg(self) -> None:
        explicit = self.ffmpeg.edit.text().strip() or None
        ver = youtube.ffmpeg_version(explicit)
        if ver:
            found = youtube.find_ffmpeg(explicit)
            self.ffmpeg_field.set_error(None)
            self.ffmpeg_field.set_helper(f"감지됨: ffmpeg {ver} · {found}")
        else:
            self.ffmpeg_field.set_helper("")
            self.ffmpeg_field.set_error("지정한 경로에서 ffmpeg.exe를 찾을 수 없습니다. 병합과 오디오 변환이 비활성화됩니다.")
