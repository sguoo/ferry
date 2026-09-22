"""Design tokens and the global Qt stylesheet.

Rules carried over from reference/SKILL.md:
- one accent colour, saturation kept under 80%
- neutral base is a single cool-grey (zinc) family, never pure black
- no outer glows; depth comes from 1px borders and tinted surfaces
- cards only where elevation communicates hierarchy, lists use dividers
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # bundled files only: inside PyInstaller's temp _MEI dir when frozen
ASSETS = ROOT / "assets"
# user data (settings, downloads, caches) must live outside ROOT: the _MEI dir is wiped on every exit
DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Ferry"
ICON_DIR = ASSETS / "icons"


class C:
    """Colour tokens."""

    BG0 = "#0b0b0f"  # window
    BG1 = "#111116"  # sidebar / panel
    BG2 = "#16161d"  # input / raised surface
    BG3 = "#1d1d26"  # hover / chip
    BG4 = "#25252f"  # pressed / strong chip

    BORDER = "#23232c"
    BORDER2 = "#2f2f3a"

    TEXT = "#f2f2f4"
    TEXT2 = "#a3a3ad"
    TEXT3 = "#6e6e79"

    ACCENT = "#e05a72"
    ACCENT_HOVER = "#e8718a"
    ACCENT_DOWN = "#c94d64"
    ACCENT_SOFT = "rgba(224, 90, 114, 0.14)"
    ACCENT_LINE = "rgba(224, 90, 114, 0.32)"
    ACCENT_FG = "#fff4f6"

    SUCCESS = "#4cbf88"
    SUCCESS_SOFT = "rgba(76, 191, 136, 0.14)"
    SUCCESS_LINE = "rgba(76, 191, 136, 0.30)"

    WARN = "#d6a544"
    WARN_SOFT = "rgba(214, 165, 68, 0.14)"
    WARN_LINE = "rgba(214, 165, 68, 0.30)"


class R:
    """Radius tokens."""

    SM = 6
    MD = 8
    LG = 12


class S:
    """Spacing tokens (VISUAL_DENSITY 4 -> daily-app spacing)."""

    PAGE = 24
    SECTION = 20
    PANEL = 20
    GAP = 12
    TIGHT = 8


def _url(name: str) -> str:
    return (ICON_DIR / name).as_posix()


def build_qss() -> str:
    check = _url("check.svg")
    chevron = _url("chevron-down.svg")
    return f"""
    QWidget {{
        color: {C.TEXT};
        selection-background-color: {C.ACCENT_SOFT};
        selection-color: {C.TEXT};
    }}
    #Window {{ background: {C.BG0}; }}
    QScrollArea {{ background: transparent; border: none; }}

    /* ---------- surfaces ---------- */
    QFrame[panel="true"] {{
        background: {C.BG1};
        border: 1px solid {C.BORDER};
        border-radius: {R.LG}px;
    }}
    QFrame[surface="true"] {{
        background: {C.BG2};
        border: 1px solid {C.BORDER};
        border-radius: {R.MD}px;
    }}
    QFrame[divider="true"] {{
        background: {C.BORDER};
        border: none;
        max-height: 1px;
        min-height: 1px;
    }}
    QFrame[vdivider="true"] {{
        background: {C.BORDER};
        border: none;
        max-width: 1px;
        min-width: 1px;
    }}
    QFrame[tile="true"] {{
        background: {C.BG2};
        border: 1px solid {C.BORDER};
        border-radius: {R.MD}px;
    }}
    QFrame[tile="true"]:hover {{ border-color: {C.BORDER2}; }}
    QFrame[tile="true"][selected="true"] {{
        background: {C.ACCENT_SOFT};
        border: 1px solid {C.ACCENT_LINE};
    }}
    QFrame[card="true"] {{
        background: {C.BG1};
        border: 1px solid {C.BORDER};
        border-radius: {R.LG}px;
    }}
    QFrame[card="true"]:hover {{ border-color: {C.BORDER2}; }}
    QFrame[icontile="true"] {{
        background: {C.ACCENT_SOFT};
        border: 1px solid {C.ACCENT_LINE};
        border-radius: {R.MD}px;
    }}
    QFrame[icontile="neutral"] {{
        background: {C.BG3};
        border: 1px solid {C.BORDER2};
        border-radius: {R.MD}px;
    }}
    QFrame[banner="error"] {{
        background: {C.ACCENT_SOFT};
        border: 1px solid {C.ACCENT_LINE};
        border-radius: {R.MD}px;
    }}
    QFrame[empty="true"] {{
        background: transparent;
        border: 1px dashed {C.BORDER2};
        border-radius: {R.LG}px;
    }}
    QFrame[composite="true"] {{
        background: {C.BG2};
        border: 1px solid {C.BORDER};
        border-radius: {R.MD}px;
    }}
    QFrame[composite="true"][focus="true"] {{ border-color: {C.ACCENT}; }}
    QFrame[composite="true"][state="error"] {{ border-color: {C.ACCENT}; }}

    /* ---------- buttons ---------- */
    QPushButton {{
        background: {C.BG2};
        border: 1px solid {C.BORDER};
        border-radius: {R.MD}px;
        padding: 8px 14px;
        color: {C.TEXT};
    }}
    QPushButton:hover {{ background: {C.BG3}; border-color: {C.BORDER2}; }}
    QPushButton:pressed {{ background: {C.BG2}; padding-top: 9px; padding-bottom: 7px; }}
    QPushButton:disabled {{ color: {C.TEXT3}; background: {C.BG1}; }}

    QPushButton[variant="primary"] {{
        background: {C.ACCENT};
        color: {C.ACCENT_FG};
        border: 1px solid {C.ACCENT_DOWN};
    }}
    QPushButton[variant="primary"]:hover {{ background: {C.ACCENT_HOVER}; border-color: {C.ACCENT}; }}
    QPushButton[variant="primary"]:pressed {{ background: {C.ACCENT_DOWN}; }}

    QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; color: {C.TEXT2}; }}
    QPushButton[variant="ghost"]:hover {{ background: {C.BG3}; color: {C.TEXT}; }}

    QPushButton[variant="icon"] {{ padding: 6px; min-width: 20px; }}
    QPushButton[variant="icon"][flat="true"] {{ background: transparent; border-color: transparent; }}
    QPushButton[variant="icon"][flat="true"]:hover {{ background: {C.BG3}; }}

    QPushButton[variant="window"] {{
        background: transparent; border: none; border-radius: {R.SM}px; padding: 6px 10px;
    }}
    QPushButton[variant="window"]:hover {{ background: {C.BG3}; }}
    QPushButton[variant="window"][danger="true"]:hover {{ background: {C.ACCENT}; }}

    QPushButton[nav="true"] {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: {R.MD}px;
        padding: 9px 12px;
        text-align: left;
        color: {C.TEXT2};
    }}
    QPushButton[nav="true"]:hover {{ background: {C.BG3}; color: {C.TEXT}; }}
    QPushButton[nav="true"]:checked {{
        background: {C.ACCENT_SOFT};
        border-color: {C.ACCENT_LINE};
        color: {C.ACCENT};
    }}

    QPushButton[segment="true"] {{
        background: transparent; border: 1px solid transparent; border-radius: {R.SM}px;
        padding: 6px 12px; color: {C.TEXT2};
    }}
    QPushButton[segment="true"]:hover {{ color: {C.TEXT}; background: {C.BG3}; }}
    QPushButton[segment="true"]:checked {{ background: {C.BG4}; color: {C.TEXT}; border-color: {C.BORDER2}; }}
    QPushButton[segment="true"][tone="accent"]:checked {{
        background: {C.ACCENT_SOFT}; color: {C.ACCENT}; border-color: {C.ACCENT_LINE};
    }}
    QFrame[segmented="true"] {{
        background: {C.BG2}; border: 1px solid {C.BORDER}; border-radius: {R.MD}px;
    }}

    QFrame[chip="true"] {{
        background: {C.BG2}; border: 1px solid {C.BORDER}; border-radius: {R.MD}px;
    }}
    QFrame[chip="true"]:hover {{ border-color: {C.BORDER2}; }}
    QFrame[chip="true"][active="true"] {{
        background: {C.ACCENT_SOFT}; border-color: {C.ACCENT_LINE};
    }}

    /* ---------- inputs ---------- */
    QLineEdit {{
        background: {C.BG2};
        border: 1px solid {C.BORDER};
        border-radius: {R.MD}px;
        padding: 8px 12px;
        color: {C.TEXT};
    }}
    QLineEdit:hover {{ border-color: {C.BORDER2}; }}
    QLineEdit:focus {{ border-color: {C.ACCENT}; }}
    QLineEdit[state="error"] {{ border-color: {C.ACCENT}; }}
    QLineEdit[bare="true"], QLineEdit[bare="true"]:focus, QLineEdit[bare="true"]:hover {{
        background: transparent; border: none; padding: 0px;
    }}

    QComboBox {{
        background: {C.BG2};
        border: 1px solid {C.BORDER};
        border-radius: {R.MD}px;
        padding: 7px 30px 7px 12px;
        color: {C.TEXT};
    }}
    QComboBox:hover {{ border-color: {C.BORDER2}; }}
    QComboBox:focus {{ border-color: {C.ACCENT}; }}
    QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
    QComboBox::down-arrow {{ image: url("{chevron}"); width: 14px; height: 14px; }}
    QComboBox QAbstractItemView {{
        background: {C.BG2};
        border: 1px solid {C.BORDER2};
        border-radius: {R.MD}px;
        padding: 4px;
        outline: 0;
        selection-background-color: {C.BG3};
        selection-color: {C.TEXT};
    }}
    QComboBox QAbstractItemView::item {{ padding: 6px 8px; border-radius: {R.SM}px; min-height: 22px; }}

    QCheckBox {{ spacing: 8px; color: {C.TEXT}; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {C.BORDER2};
        border-radius: 4px;
        background: {C.BG2};
    }}
    QCheckBox::indicator:hover {{ border-color: {C.TEXT3}; }}
    QCheckBox::indicator:checked {{
        background: {C.ACCENT}; border-color: {C.ACCENT}; image: url("{check}");
    }}
    QCheckBox:disabled {{ color: {C.TEXT3}; }}
    QCheckBox::indicator:disabled {{ background: {C.BG1}; border-color: {C.BORDER}; }}

    /* ---------- scrollbars ---------- */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {C.BORDER2}; border-radius: 3px; min-height: 36px; }}
    QScrollBar::handle:vertical:hover {{ background: {C.TEXT3}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {C.BORDER2}; border-radius: 3px; min-width: 36px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

    /* ---------- sliders (player) ---------- */
    QSlider::groove:horizontal {{ height: 6px; background: {C.BG4}; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: {C.ACCENT}; border-radius: 3px; }}
    QSlider::handle:horizontal {{
        width: 14px; height: 14px; margin: -4px 0; border-radius: 7px;
        background: {C.TEXT}; border: 1px solid {C.BORDER2};
    }}
    QSlider::handle:horizontal:hover {{ background: #ffffff; }}

    /* ---------- misc ---------- */
    QMenu {{
        background: {C.BG2}; color: {C.TEXT};
        border: 1px solid {C.BORDER2}; border-radius: {R.SM}px; padding: 6px;
    }}
    QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {C.BG3}; }}
    QMenu::item:disabled {{ color: {C.TEXT3}; }}
    QMenu::separator {{ height: 1px; background: {C.BORDER}; margin: 6px 4px; }}
    QMenu::right-arrow {{ width: 10px; height: 10px; }}
    QToolTip {{
        background: {C.BG2}; color: {C.TEXT};
        border: 1px solid {C.BORDER2}; border-radius: {R.SM}px; padding: 6px 8px;
    }}
    QLabel[badge="neutral"] {{
        background: {C.BG3}; color: {C.TEXT2}; border: 1px solid {C.BORDER2};
        border-radius: 5px; padding: 2px 7px;
    }}
    QLabel[badge="accent"] {{
        background: {C.ACCENT_SOFT}; color: {C.ACCENT}; border: 1px solid {C.ACCENT_LINE};
        border-radius: 5px; padding: 2px 7px;
    }}
    QLabel[badge="success"] {{
        background: {C.SUCCESS_SOFT}; color: {C.SUCCESS}; border: 1px solid {C.SUCCESS_LINE};
        border-radius: 5px; padding: 2px 7px;
    }}
    QLabel[badge="warn"] {{
        background: {C.WARN_SOFT}; color: {C.WARN}; border: 1px solid {C.WARN_LINE};
        border-radius: 5px; padding: 2px 7px;
    }}
    QLabel[badge="solid"] {{
        background: {C.ACCENT}; color: {C.ACCENT_FG}; border: 1px solid {C.ACCENT_DOWN};
        border-radius: 5px; padding: 2px 7px;
    }}
    QLabel[badge][overlay="true"] {{ background: rgba(11, 11, 15, 0.84); }}
    QLabel[kbd="true"] {{
        background: {C.BG3}; color: {C.TEXT3}; border: 1px solid {C.BORDER2};
        border-radius: 4px; padding: 1px 6px;
    }}
    """
