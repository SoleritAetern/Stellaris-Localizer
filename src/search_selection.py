"""Capture a real editor selection before Qt activates a Find/Replace action."""

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication


def editor_selection(editor):
    cursor = editor.textCursor()
    return cursor.selectedText().replace("\u2029", "\n") if cursor.hasSelection() else None


class SearchSelectionCapture(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        editor.setProperty("searchTextSource", True)
        editor.installEventFilter(self)

    def eventFilter(self, editor, event):
        if (event.type() == QEvent.Type.ShortcutOverride
                and QApplication.focusWidget() in (editor, editor.viewport())
                and event.key() in (Qt.Key.Key_F, Qt.Key.Key_H)
                and event.modifiers() in (Qt.KeyboardModifier.ControlModifier,
                                         Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
            window = editor.window()
            snapshot = {"text": editor_selection(editor)}
            window._search_selection_snapshot = snapshot

            def expire():
                # A shortcut snapshot is valid only for this event dispatch,
                # never for a later Find invocation from another field.
                if getattr(window, "_search_selection_snapshot", None) is snapshot:
                    window._search_selection_snapshot = None

            QTimer.singleShot(0, window, expire)
        return False


def selected_search_text(window):
    snapshot = getattr(window, "_search_selection_snapshot", None)
    if snapshot is not None:
        window._search_selection_snapshot = None
        return snapshot["text"]
    # Menu/programmatic invocations can still read a currently focused editor.
    # Only registered EN/RU/Glossary controls qualify, never query fields or rows.
    focused = QApplication.focusWidget()
    if focused is not None and not focused.property("searchTextSource"):
        parent = focused.parentWidget()
        # Find is itself a descendant of the editor's viewport. Walking all
        # ancestors would incorrectly reuse the editor's old match selection.
        focused = (parent if parent is not None and parent.property("searchTextSource")
                   and parent.viewport() is focused else None)
    if focused is not None and focused.isVisible() and focused.window() is window:
        return editor_selection(focused)
    return None
