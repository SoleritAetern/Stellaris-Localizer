"""Current shortcut assignments, shared by actions and Settings help."""

from PySide6.QtCore import QT_TRANSLATE_NOOP

SHORTCUTS = {
    "save": "Ctrl+S", "undo": "Ctrl+Z", "redo": "Ctrl+Y",
    "find": "Ctrl+F", "replace": "Ctrl+H",
    "search_project": "Ctrl+Shift+F", "replace_project": "Ctrl+Shift+H",
    "sidebar": "Ctrl+B",
    "reopen_tab": "Ctrl+Shift+T",
}

SHORTCUT_LABELS = (
    ("save", QT_TRANSLATE_NOOP("shortcuts", "Сохранить")), ("undo", QT_TRANSLATE_NOOP("shortcuts", "Отменить")), ("redo", QT_TRANSLATE_NOOP("shortcuts", "Повторить")),
    ("find", QT_TRANSLATE_NOOP("shortcuts", "Найти")), ("replace", QT_TRANSLATE_NOOP("shortcuts", "Заменить")),
    ("search_project", QT_TRANSLATE_NOOP("shortcuts", "Поиск по проекту")), ("replace_project", QT_TRANSLATE_NOOP("shortcuts", "Замена по проекту")),
    ("sidebar", QT_TRANSLATE_NOOP("shortcuts", "Боковая панель")),
    ("reopen_tab", QT_TRANSLATE_NOOP("shortcuts", "Восстановить закрытую вкладку")),
)
