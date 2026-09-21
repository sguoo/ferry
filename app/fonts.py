"""Font loading and typographic helpers.

Latin runs use Geist / Geist Mono, Hangul falls through to Pretendard.
Both are variable fonts shipped in assets/fonts. If a file is missing the
stack degrades to the Windows system fonts without failing.
"""

from PySide6.QtGui import QFont, QFontDatabase

from .theme import ASSETS

_FONT_FILES = {
    "sans": "GeistVariable.ttf",
    "hangul": "PretendardVariable.ttf",
    "mono": "GeistMonoVariable.ttf",
}

_loaded: dict[str, str] = {}

SANS_FALLBACK = ["Segoe UI Variable", "Segoe UI", "Malgun Gothic"]
MONO_FALLBACK = ["Cascadia Mono", "Consolas"]


def load_fonts() -> None:
    for key, filename in _FONT_FILES.items():
        path = ASSETS / "fonts" / filename
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            _loaded[key] = families[0]


def sans_families() -> list[str]:
    stack = []
    if "sans" in _loaded:
        stack.append(_loaded["sans"])
    if "hangul" in _loaded:
        stack.append(_loaded["hangul"])
    return stack + SANS_FALLBACK


def mono_families() -> list[str]:
    stack = []
    if "mono" in _loaded:
        stack.append(_loaded["mono"])
    if "hangul" in _loaded:
        stack.append(_loaded["hangul"])
    return stack + MONO_FALLBACK


_WEIGHTS = {
    400: QFont.Weight.Normal,
    500: QFont.Weight.Medium,
    600: QFont.Weight.DemiBold,
    700: QFont.Weight.Bold,
}


def sans(size: int = 13, weight: int = 400, tracking: float | None = None) -> QFont:
    f = QFont()
    f.setFamilies(sans_families())
    f.setPixelSize(size)
    f.setWeight(_WEIGHTS.get(weight, QFont.Weight.Normal))
    if tracking is not None:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, tracking)
    return f


def mono(size: int = 13, weight: int = 400) -> QFont:
    f = QFont()
    f.setFamilies(mono_families())
    f.setPixelSize(size)
    f.setWeight(_WEIGHTS.get(weight, QFont.Weight.Normal))
    return f
