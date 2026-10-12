"""Viewport-only literal occurrence overlays; never change document formats."""

from collections import OrderedDict
from weakref import WeakSet

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QTextCursor, QTextFormat, QTextLayout
from PySide6.QtWidgets import QApplication, QTextEdit
from shiboken6 import isValid

from . import theme


_OCCURRENCE_MARKER = int(QTextFormat.Property.UserProperty) + 182


def uncovered(spans, protected):
    """Give existing diff backgrounds and native selection priority."""
    protected = sorted(protected)
    first = 0
    for start, end in spans:
        while first < len(protected) and protected[first][1] <= start:
            first += 1
        for number in range(first, len(protected)):
            left, right = protected[number]
            if left >= end:
                break
            if left > start:
                yield start, left
            start = max(start, right)
            if start >= end:
                break
        if start < end:
            yield start, end


class SelectionOccurrences(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.query = ""
        self.source = None
        self.scroll_source = None
        self.editors = WeakSet()
        self.cache = OrderedDict()
        view.viewport().installEventFilter(self)
        view.installEventFilter(self)
        QApplication.instance().focusChanged.connect(self.focus_changed)

    def clear(self):
        if self.query or self.source is not None:
            self.set_query("", None)

    def retain_for_scroll(self, editor):
        # The existing EN overlay dismisses on scroll. Retain its last actual
        # text selection for newly visible rows until the next interaction.
        if editor is self.source:
            self.scroll_source = editor

    def focus_changed(self, old, current):
        if (isValid(self.view) and current is not None and current is not self.view
                and not self.view.isAncestorOf(current)):
            self.clear()

    def attach(self, editor):
        self.editors.add(editor)
        editor.installEventFilter(self)
        editor.selectionChanged.connect(lambda: self.selection_changed(editor))
        editor.textChanged.connect(lambda: self.selection_changed(editor))

    def eventFilter(self, editor, event):
        if not isValid(self.view):
            return False
        if editor is self.view or editor is self.view.viewport():
            if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.Hide):
                self.clear()
        elif event.type() == QEvent.Type.FocusIn:
            self.scroll_source = None
            self.selection_changed(editor)
        elif (event.type() == QEvent.Type.Hide and editor is self.source
              and editor is not self.scroll_source):
            self.set_query("", None)
        return False

    def selection_changed(self, editor):
        if editor is not self.source and (not editor.isVisible() or not editor.hasFocus()):
            return
        text = editor.textCursor().selectedText().replace("\u2029", "\n")
        self.set_query(text.casefold() if text.strip() else "", editor)

    def set_query(self, query, source):
        changed = query != self.query
        self.source = source
        if not query or source is not self.scroll_source:
            self.scroll_source = None
        if changed:
            self.query = query
            self.cache.clear()
            self.view.viewport().update()
        # Even an unchanged query may have moved its native selection.
        for editor in tuple(self.editors):
            if isValid(editor):
                self.update_editor(editor)

    def spans(self, text):
        if not self.query or not text:
            return ()
        if text in self.cache:
            self.cache.move_to_end(text)
            return self.cache[text]
        # Case folding can expand characters. Map folded offsets back to Qt's
        # UTF-16 positions so Cyrillic, emoji and expansions retain exact ranges.
        parts, starts, ends = [], [], []
        offset = 0
        for char in text:
            folded = char.casefold()
            next_offset = offset + len(char.encode("utf-16-le")) // 2
            parts.append(folded)
            starts.extend([offset] * len(folded))
            ends.extend([next_offset] * len(folded))
            offset = next_offset
        folded = "".join(parts)
        spans, position = [], 0
        while (position := folded.find(self.query, position)) >= 0:
            start, end = starts[position], ends[position + len(self.query) - 1]
            if spans and start <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
            else:
                spans.append((start, end))
            position += 1  # Include overlapping literal occurrences too.
        self.cache[text] = tuple(spans)
        if len(self.cache) > 128:
            self.cache.popitem(last=False)
        return self.cache[text]

    def formats(self, layout):
        spans = self.spans(layout.text())
        if not spans:
            return []
        protected = [(item.start, item.start + item.length) for item in layout.formats()
                     if item.format.hasProperty(QTextFormat.Property.BackgroundBrush)]
        result = []
        for start, end in uncovered(spans, protected):
            item = QTextLayout.FormatRange()
            item.start, item.length = start, end - start
            item.format.setBackground(theme.color("selection.occurrence.bg"))
            result.append(item)
        return result

    def update_editor(self, editor):
        others = [item for item in editor.extraSelections()
                  if not item.format.property(_OCCURRENCE_MARKER)]
        selections = []
        if editor.isVisible() and self.query:
            cursor = editor.textCursor()
            protected = [(cursor.selectionStart(), cursor.selectionEnd())] if cursor.hasSelection() else []
            for start, end in uncovered(self.spans(editor.toPlainText()), protected):
                item = QTextEdit.ExtraSelection()
                item.cursor = QTextCursor(editor.document())
                item.cursor.setPosition(start)
                item.cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                item.format.setBackground(theme.color("selection.occurrence.bg"))
                item.format.setProperty(_OCCURRENCE_MARKER, True)
                selections.append(item)
        # Other overlays (including spellcheck underline) retain their formats.
        editor.setExtraSelections(selections + others)
