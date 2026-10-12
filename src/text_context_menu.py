"""Localized captions for Qt-owned text actions; retain native slots and state."""

from PySide6.QtCore import QCoreApplication, QT_TRANSLATE_NOOP

from PySide6.QtGui import QKeySequence

from . import theme


_TEXT_ACTIONS = {
    "edit-undo": (QT_TRANSLATE_NOOP("text_context_menu", "Отменить"), QKeySequence.StandardKey.Undo),
    "edit-redo": (QT_TRANSLATE_NOOP("text_context_menu", "Повторить"), QKeySequence.StandardKey.Redo),
    "edit-cut": (QT_TRANSLATE_NOOP("text_context_menu", "Вырезать"), QKeySequence.StandardKey.Cut),
    "edit-copy": (QT_TRANSLATE_NOOP("text_context_menu", "Копировать"), QKeySequence.StandardKey.Copy),
    "edit-paste": (QT_TRANSLATE_NOOP("text_context_menu", "Вставить"), QKeySequence.StandardKey.Paste),
    "edit-delete": (QT_TRANSLATE_NOOP("text_context_menu", "Удалить"), "Del"),
    "select-all": (QT_TRANSLATE_NOOP("text_context_menu", "Выделить всё"), QKeySequence.StandardKey.SelectAll),
}


def set_action_caption(action, title, shortcut=None):
    # A tab uses QMenu's shortcut column without registering a second binding.
    suffix = QKeySequence(shortcut).toString(QKeySequence.SequenceFormat.NativeText) if shortcut is not None else ""
    action.setText(f"{title}\t{suffix}" if suffix else title)


def standard_text_menu(editor):
    menu = editor.createStandardContextMenu()
    for action in menu.actions():
        caption = _TEXT_ACTIONS.get(action.objectName())
        if caption is not None:
            set_action_caption(action, QCoreApplication.translate("text_context_menu", caption[0]), caption[1])
    theme.style_menu(menu)
    return menu
