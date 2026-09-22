"""Right-click menu for downloaded media, shared by the library cards and the folder-playlist rows.

    media_menu.show(widget, [path, ...], global_pos)

Play · move to a playlist folder (or a new one) · subtitles · reveal · delete to the Recycle Bin.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu, QWidget

from .. import collections, context, local, subtitles
from ..icons import icon
from ..theme import C
from . import dialogs


def show(parent: QWidget, paths: list[Path], pos: QPoint) -> None:
    paths = [Path(p) for p in paths]
    if not paths:
        return
    many = len(paths) > 1
    what = f"{len(paths)}개 파일" if many else paths[0].name
    menu = QMenu(parent)

    play = menu.addAction(icon("play", C.TEXT2, 14), "재생" if not many else f"선택 {len(paths)}개 재생")
    play.triggered.connect(lambda: context.bus.play.emit([str(p) for p in paths], 0, [p.stem for p in paths]))
    if not many and subtitles.sidecars(paths[0]):
        subs = menu.addAction(icon("subtitles", C.TEXT2, 14), "자막 보기")
        subs.triggered.connect(lambda: context.bus.view_subtitles.emit(str(paths[0])))
    menu.addSeparator()

    root = collections.root()
    move_menu = menu.addMenu(icon("move", C.TEXT2, 14), "재생목록으로 이동")
    here = {p.parent for p in paths}
    if root.is_dir() and here != {root}:
        act = move_menu.addAction(icon("folder", C.TEXT2, 14), "(루트) 저장 폴더")
        act.triggered.connect(lambda: _move(parent, paths, root))
    for folder in collections.playlists():
        act = move_menu.addAction(icon("folder", C.TEXT2, 14), folder.name)
        if here == {folder}:
            act.setEnabled(False)  # already there
        act.triggered.connect(lambda _=False, f=folder: _move(parent, paths, f))
    move_menu.addSeparator()
    new = move_menu.addAction(icon("folder-plus", C.ACCENT, 14), "새 재생목록…")
    new.triggered.connect(lambda: _move_to_new(parent, paths))

    reveal = menu.addAction(icon("folder-open", C.TEXT2, 14), "탐색기에서 보기")
    reveal.triggered.connect(lambda: local.open_in_explorer(paths[0]))
    menu.addSeparator()
    delete = menu.addAction(icon("trash", C.ACCENT, 14), "삭제 (휴지통으로)")
    delete.triggered.connect(lambda: _trash(parent, paths, what))
    menu.exec(pos)


def _move(parent: QWidget, paths: list[Path], dest: Path) -> None:
    try:
        moved = collections.move(paths, dest)
    except OSError as exc:
        dialogs.confirm(parent, "옮기지 못했습니다", str(exc), ok_text="닫기")
        return
    context.bus.notify.emit("재생목록으로 이동", f"{len(moved)}개 → {dest.name if dest != collections.root() else '(루트)'}")


def _move_to_new(parent: QWidget, paths: list[Path]) -> None:
    name = dialogs.prompt(parent, "새 재생목록", "저장 폴더 아래에 같은 이름의 폴더가 만들어지고, 선택한 파일이 그리로 옮겨집니다.", placeholder="재생목록 이름")
    if not name:
        return
    try:
        dest = collections.create(name)
    except (ValueError, OSError) as exc:
        dialogs.confirm(parent, "재생목록을 만들지 못했습니다", str(exc), ok_text="닫기")
        return
    _move(parent, paths, dest)


def _trash(parent: QWidget, paths: list[Path], what: str) -> None:
    if not dialogs.confirm(parent, f"{what}을(를) 삭제할까요?", "파일과 딸린 자막이 휴지통으로 이동합니다. 휴지통에서 되돌릴 수 있습니다.", ok_text="삭제", danger=True):
        return
    n = collections.trash(paths)
    context.bus.notify.emit("삭제됨", f"{n}개 파일을 휴지통으로 보냈습니다.")
