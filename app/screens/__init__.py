from PySide6.QtWidgets import QWidget

from ..theme import S
from ..widgets.primitives import vbox


class Screen(QWidget):
    """A page: vertical stack of sections (revealed with a stagger on navigation)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.body = vbox(self, gap=S.SECTION, margins=(S.PAGE, S.PAGE, S.PAGE, S.PAGE))
        self._sections: list[QWidget] = []

    def add_section(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._sections.append(widget)
        self.body.addWidget(widget, stretch)
        return widget

    def sections(self) -> list[QWidget]:
        return list(self._sections)
