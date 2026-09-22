"""Frameless main window: title bar, sidebar, one scrollable page at a time,
clipboard watching and tray notifications."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QScrollArea, QStackedWidget, QSystemTrayIcon, QWidget

from pathlib import Path

from . import APP_NAME, APP_VERSION, context, icon as app_icon, subtitles, tools_install, updater, workers
from .local import MEDIA_EXTS
from .screens import Screen
from .settings import SETTINGS_PATH
from .screens.downloader import YOUTUBE_URL, DownloaderScreen
from .screens.library import LibraryScreen
from .screens.playlist import PlaylistScreen
from .screens.settings import SettingsScreen
from .widgets.player import MiniPlayerBar, PlayerOverlay
from .widgets.primitives import hbox, reveal, vbox
from .widgets.sidebar import Sidebar
from .widgets.subtitle_viewer import SubtitleViewer
from .widgets.titlebar import TitleBar
from .widgets.update_dialog import UpdateDialog

RESIZE_MARGIN = 7  # logical px; scaled by DPI at hit-test time

# Win32 non-client hit-test codes
WM_NCHITTEST = 0x0084
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 10, 11, 12, 13, 14, 15, 16, 17


class MainWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Window")
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(1000, 660)
        self.resize(1280, 800)
        self.setWindowIcon(app_icon.app_icon())

        root = vbox(self, gap=0)
        self.titlebar = TitleBar()
        self.titlebar.minimize_requested.connect(self.showMinimized)
        self.titlebar.maximize_requested.connect(self._toggle_maximize)
        self.titlebar.close_requested.connect(self.close)
        root.addWidget(self.titlebar)

        body = hbox(gap=0)
        self.sidebar = Sidebar()
        self.sidebar.navigated.connect(self.navigate)
        body.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        self.screens: dict[str, Screen] = {
            "downloader": DownloaderScreen(),
            "playlist": PlaylistScreen(),
            "library": LibraryScreen(),
            "settings": SettingsScreen(),
        }
        self._scrollers: dict[str, QScrollArea] = {}
        for key, screen in self.screens.items():
            scroller = QScrollArea()
            scroller.setWidgetResizable(True)
            scroller.setFrameShape(QScrollArea.Shape.NoFrame)
            scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroller.viewport().setAutoFillBackground(False)
            scroller.setWidget(screen)
            self._scrollers[key] = scroller
            self.pages.addWidget(scroller)
        content = vbox(gap=0)
        content.addWidget(self.pages, 1)
        self.mini = None  # created after the overlay below
        body.addLayout(content, 1)
        root.addLayout(body, 1)

        self.screens["downloader"].open_playlist.connect(self.open_playlist)
        self.screens["downloader"].open_settings.connect(self.open_cookie_settings)
        self.screens["playlist"].open_settings.connect(self.open_cookie_settings)

        # in-app player covers the page area; fullscreen hides the chrome around it
        self.player = PlayerOverlay(self.pages)
        self.player.fullscreen_toggled.connect(self._set_fullscreen)
        context.bus.play.connect(self.player.play)
        self.mini = MiniPlayerBar(self.player)
        content.addWidget(self.mini)

        # subtitle viewer sits above the player; cue clicks seek the player
        self.subs = SubtitleViewer(self.pages)
        context.bus.view_subtitles.connect(self._view_subtitles)
        self.player.subtitles_requested.connect(self._view_subtitles)
        self.subs.seek_requested.connect(self._seek_to)
        self.player.player.positionChanged.connect(self._track_subtitles)

        # clipboard watcher
        self._last_clip = QApplication.clipboard().text().strip()
        QApplication.clipboard().dataChanged.connect(self._on_clipboard)
        context.bus.settings_changed.connect(self._sync_clip_pill)
        self._sync_clip_pill()

        # tray icon for completion toasts
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(app_icon.app_icon())
        self.tray.setToolTip(APP_NAME)
        self.tray.show()
        context.bus.notify.connect(self._notify)

        self._current = "downloader"
        self.sidebar.select("downloader")
        QTimer.singleShot(0, tools_install.ensure)  # first run without ffmpeg/deno: fetch them in the background
        QTimer.singleShot(1500, updater.check)  # newer GitHub release -> downloaded now, swapped in on exit
        context.bus.update_ready.connect(self._show_update)
        QTimer.singleShot(2500, self._whats_new)  # first launch after an update: this version's release notes

    # ------------------------------------------------------------ navigation
    def navigate(self, key: str) -> None:
        # QStackedWidget raises the new page above the overlays, so they cannot stay
        # open: the player either shrinks to the mini bar (setting) or stops
        if self.subs.isVisible():
            self.subs.close()
        if self.player.isVisible():
            if context.settings.background_play:
                self.player.minimize()
            else:
                self.player.close()
        self._current = key
        self.pages.setCurrentWidget(self._scrollers[key])
        self._scrollers[key].verticalScrollBar().setValue(0)
        reveal(self.screens[key].sections())

    def open_cookie_settings(self) -> None:
        """Jump straight to the YouTube-account section (bot check / age gate banners)."""
        self.sidebar.select("settings")
        screen = self.screens["settings"]

        def scroll():
            self._scrollers["settings"].ensureWidgetVisible(screen.cookie_section, 0, 40)
            screen.cookie_file.edit.setFocus()

        QTimer.singleShot(350, scroll)  # after the reveal animation has laid things out

    def open_playlist(self, url: str) -> None:
        self.sidebar.select("playlist")
        self.screens["playlist"].load_url(url)

    # ------------------------------------------------------------- clipboard
    def _sync_clip_pill(self) -> None:
        self.titlebar.set_clipboard(context.settings.clipboard_watch)

    def _on_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not context.settings.clipboard_watch or not text or text == self._last_clip:
            return
        self._last_clip = text
        if not YOUTUBE_URL.match(text):
            return
        self.titlebar.set_clipboard(True, text)
        if "list=" in text and "watch?v=" not in text:
            self.open_playlist(text)
        else:
            self.sidebar.select("downloader")
            self.screens["downloader"].load_url(text)

    # -------------------------------------------------------------- subtitles
    def _view_subtitles(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            return
        if p.suffix.lower() in subtitles.SUBTITLE_EXTS:
            self.subs.open([p], subtitles.media_for(p, MEDIA_EXTS))
        else:
            self.subs.open(subtitles.pick_per_language(subtitles.sidecars(p)), p)

    def _seek_to(self, media: str, ms: int) -> None:
        self.subs.close()
        self.player.play_at(media, ms)
        self.player.setFocus()

    def _track_subtitles(self, ms: int) -> None:
        if self.subs.isVisible() and self.subs.media and str(self.subs.media) == self.player.current_media():
            self.subs.highlight(ms)

    # ------------------------------------------------------------- updates
    def _show_update(self, version: str) -> None:
        """What's-new popup with the release notes; the title-bar button stays for anyone who picks 나중에."""
        UpdateDialog(version, updater.notes, updater.release_url, parent=self).open()

    def _whats_new(self) -> None:
        seen = context.settings.last_seen_version
        if seen == APP_VERSION:
            return
        if not seen and not SETTINGS_PATH.exists():  # fresh install: nothing to compare against, just remember
            context.settings.last_seen_version = APP_VERSION
            context.save_settings()
            return
        # (an existing settings.json without last_seen_version is an upgrade from a build before this popup)

        def show(result: tuple[str, str]) -> None:
            notes, url = result
            context.settings.last_seen_version = APP_VERSION
            context.save_settings()
            UpdateDialog(APP_VERSION, notes, url, installed=True, parent=self).open()

        # offline or no release for this tag: stay quiet and try again next launch
        workers.call(updater.fetch_release_notes, APP_VERSION, finished=show, failed=lambda _err: None)

    # ---------------------------------------------------------- notifications
    def _notify(self, title: str, body: str) -> None:
        if context.settings.sound:
            QApplication.beep()
        if self.tray.isSystemTrayAvailable():
            self.tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 4000)

    # ------------------------------------------------------- window chrome
    def _set_fullscreen(self, on: bool) -> None:
        self.titlebar.setVisible(not on)
        self.sidebar.setVisible(not on)
        if on:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        elif getattr(self, "_was_maximized", False):
            self.showMaximized()
        else:
            self.showNormal()

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def closeEvent(self, event):
        self.subs.close()
        self.player.close()
        for job in context.jobs.active:
            job.pause()
        self.tray.hide()
        if updater.ready:
            updater.apply(restart=False)
        super().closeEvent(event)

    def nativeEvent(self, event_type, message):
        """Let Windows own edge resizing (cursor, drag, snap) for the frameless window.

        Child widgets cover every edge, so Qt mouse events never reach this
        widget there; answering WM_NCHITTEST bypasses that entirely.
        """
        if sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_NCHITTEST and not self.isMaximized():
                hit = self._hit_test(msg.lParam)
                if hit is not None:
                    return True, hit
        return super().nativeEvent(event_type, message)

    def _hit_test(self, lparam: int) -> int | None:
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(int(self.winId()), ctypes.byref(rect))
        border = int(RESIZE_MARGIN * self.devicePixelRatioF())
        left = x < rect.left + border
        right = x >= rect.right - border
        top = y < rect.top + border
        bottom = y >= rect.bottom - border
        if top and left:
            return HTTOPLEFT
        if top and right:
            return HTTOPRIGHT
        if bottom and left:
            return HTBOTTOMLEFT
        if bottom and right:
            return HTBOTTOMRIGHT
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if top:
            return HTTOP
        if bottom:
            return HTBOTTOM
        return None
