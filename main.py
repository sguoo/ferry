"""Ferry desktop entry point.

    uv run main.py
"""

import ctypes
import os
import sys

# keep the FFmpeg media backend from dumping stream info to the console
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from app import APP_NAME, context, fonts, icon
from app.theme import build_qss
from app.window import MainWindow


def main() -> int:
    if sys.platform == "win32":
        # own taskbar identity so Windows shows our icon instead of python.exe's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.Desktop")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(icon.app_icon())
    app.setQuitOnLastWindowClosed(True)

    fonts.load_fonts()
    base = fonts.sans(13, 400)
    base.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(base)
    app.setStyleSheet(build_qss())

    context.init()
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
