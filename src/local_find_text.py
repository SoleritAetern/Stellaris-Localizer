"""QTextDocument target for the single LocalFind panel/controller."""

from PySide6.QtCore import QObject, QEvent
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import QTextEdit
from shiboken6 import isValid

from . import theme
from .find_current import nearest_span


class GlossaryFindTarget(QObject):
    def __init__(self, page, panel):
        super().__init__(page)
        self.page, self.panel = page, panel
        for editor in page.editors.values():
            panel.cursor_tracker.attach(editor)
            editor.installEventFilter(panel)
            editor.viewport().installEventFilter(self)
            editor.textChanged.connect(self.text_changed)
            editor.updateRequest.connect(self.viewport_updated)

    @property
    def editor(self):
        return self.page.current_editor()

    def active(self):
        return isValid(self.panel) and self.panel._filters_ready and self.panel.text_target is self

    def attach(self):
        if self.active():
            self.panel.set_text_target(self, force=True)

    def text_changed(self):
        if self.active() and self.sender() is self.editor:
            self.panel.scope_updated()

    def viewport_updated(self, *args):
        if self.active() and self.panel.state.opened:
            self.panel.reposition()

    def eventFilter(self, watched, event):
        if self.active() and event.type() == QEvent.Type.Resize and watched is self.editor.viewport():
            self.panel.reposition()
        return False

    def nearest(self):
        cursor = self.editor.textCursor()
        return nearest_span(self.panel.matches, (cursor.selectionStart(), cursor.selectionEnd()))

    def rebuild(self):
        panel = self.panel
        document = self.editor.document()
        panel.matches = []
        flags = (QTextDocument.FindFlag.FindCaseSensitively if panel.state.case_sensitive
                 else QTextDocument.FindFlag(0))
        if panel.state.query:
            cursor = document.find(panel.state.query, 0, flags)
            while not cursor.isNull():
                panel.matches.append((cursor.selectionStart(), cursor.selectionEnd()))
                cursor = document.find(panel.state.query, cursor.selectionEnd(), flags)
        panel.current = self.nearest()
        self.highlight()

    def clear(self):
        for editor in self.page.editors.values():
            editor.setExtraSelections([])  # NotebookEdit preserves its current-line layer.

    def highlight(self):
        panel = self.panel
        selections = []
        if panel.state.opened and self.active():
            for number, (start, end) in enumerate(panel.matches):
                selection = QTextEdit.ExtraSelection()
                selection.cursor = QTextCursor(self.editor.document())
                selection.cursor.setPosition(start)
                selection.cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                selection.format.setBackground(theme.color(
                    'search.active_match.bg' if number == panel.current else 'search.match.bg'))
                selections.append(selection)
        self.editor.setExtraSelections(selections)
        panel.update_counter()

    def navigate(self):
        start, end = self.panel.matches[self.panel.current]
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.reveal()
        self.highlight()

    def reveal(self):
        if 0 <= self.panel.current < len(self.panel.matches):
            self.editor.center_range(*self.panel.matches[self.panel.current])

    def replace(self, spans, replacement):
        if self.editor.isReadOnly():
            return
        cursor = QTextCursor(self.editor.document())
        changes = []
        for start, end in spans:
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            if cursor.selectedText().replace("\u2029", "\n") != replacement:
                changes.append((start, end))
        if not changes:
            return
        with self.panel.cursor_tracker.navigation():
            cursor.beginEditBlock()
            try:
                for start, end in reversed(changes):
                    cursor.setPosition(start)
                    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                    cursor.insertText(replacement)  # Literal text, native single Undo step.
            finally:
                cursor.endEditBlock()
        self.panel._replacement_undo_active = True
