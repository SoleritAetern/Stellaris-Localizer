"""Shared Find interaction: caret tracking and query-driven reveal scheduling."""

from contextlib import contextmanager
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QLineEdit, QToolButton


@dataclass
class LocalFindState:
    query: str = ""
    replacement: str = ""
    case_sensitive: bool = False
    replace_mode: bool = False
    opened: bool = False


def local_find_state():
    """One session state, including when the active project window is replaced."""
    app = QCoreApplication.instance()
    if not hasattr(app, "_local_find_state"):
        app._local_find_state = LocalFindState()
    return app._local_find_state


class FindQueryEdit(QLineEdit):
    """Report actual paste operations, without guessing from query length."""
    pasted = Signal()

    def keyPressEvent(self, event):
        paste = event.matches(QKeySequence.StandardKey.Paste)
        super().keyPressEvent(event)
        if paste:
            self.pasted.emit()

    def paste(self):
        super().paste()
        self.pasted.emit()

    def createStandardContextMenu(self):
        menu = super().createStandardContextMenu()
        for action in menu.actions():
            if action.objectName() == "edit-paste":
                # Keep Qt's native paste implementation and enabled state.
                action.triggered.connect(self.pasted.emit)
        return menu

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        try:
            menu.exec(event.globalPos())
        finally:
            menu.deleteLater()


class FindAutoReveal(QObject):
    """Only query input requests reveal; document changes never schedule it."""
    def __init__(self, panel, field, reveal):
        super().__init__(panel)
        self.panel, self.field, self.reveal = panel, field, reveal
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(230)
        self.timer.timeout.connect(self.show_current)
        field.textEdited.connect(self.schedule)
        field.pasted.connect(self.paste)
        field.installEventFilter(self)
        panel.installEventFilter(self)

    def cancel(self):
        self.timer.stop()

    def schedule(self, text):
        self.cancel()
        if len(text) >= 3:
            self.timer.start()

    def paste(self):
        self.cancel()
        # A native context menu can still own focus while its Paste action
        # fires. The explicit operation itself is sufficient authorization.
        if not self.panel.isHidden():
            self.reveal()

    def show_current(self):
        if not self.panel.isHidden() and self.field.hasFocus() and len(self.field.text()) >= 3:
            self.reveal()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Hide, QEvent.Type.FocusOut):
            self.cancel()
        return False


def nearest_span(spans, selection):
    """Index of an intersecting/containing match, otherwise the nearest span.

    Spans and selection must use the same offsets (Python or Qt UTF-16).
    Ties are resolved in document order.
    """
    left, right = sorted(selection)

    def distance(span):
        start, end = span
        intersects = (start <= left < end if left == right
                      else start < right and end > left)
        return (not intersects, max(start - right, left - end, 0))

    return min(range(len(spans)), key=lambda i: distance(spans[i]), default=-1)


def find_button_cursors(panel):
    tracker = _ButtonCursors(panel)
    panel._find_button_cursors = tracker
    for button in panel.findChildren(QToolButton):
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.installEventFilter(tracker)


class _ButtonCursors(QObject):
    def eventFilter(self, button, event):
        # QLineEdit resets its built-in clear button's cursor on focus changes.
        if event.type() == QEvent.Type.CursorChange and button.cursor().shape() != Qt.CursorShape.PointingHandCursor:
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        return False


class FindCursorTracker(QObject):
    """Observe real editor interaction, suppress feedback during navigation."""

    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.navigating = False

    def attach(self, editor):
        editor.cursorPositionChanged.connect(self.changed)
        editor.selectionChanged.connect(self.changed)
        editor.installEventFilter(self)

    def changed(self):
        self.notify(self.sender())

    def notify(self, editor):
        if not self.navigating and editor.isVisible() and editor.hasFocus():
            self.callback(editor)

    def eventFilter(self, editor, event):
        if event.type() == QEvent.Type.FocusIn:
            self.notify(editor)
        return False

    @contextmanager
    def navigation(self):
        previous = self.navigating
        self.navigating = True
        try:
            yield
        finally:
            self.navigating = previous
