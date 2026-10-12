"""Translate persisted diagnostic/review text only at the presentation boundary."""

from PySide6.QtCore import QCoreApplication, QT_TRANSLATE_NOOP

# These source strings remain unchanged in parser results and project metadata.
_MESSAGES = (
    QT_TRANSLATE_NOOP("StoredMessage", "Не закрыта конструкция £...£."),
    QT_TRANSLATE_NOOP("StoredMessage", "Не закрыта конструкция $...$."),
    QT_TRANSLATE_NOOP("StoredMessage", "Не закрыта конструкция [...]."),
    QT_TRANSLATE_NOOP("StoredMessage", "Закрывающая ] без открывающей [."),
    QT_TRANSLATE_NOOP("StoredMessage", "Нет закрывающей кавычки текста локализации."),
    QT_TRANSLATE_NOOP("StoredMessage", "Не удалось распознать запись локализации."),
    QT_TRANSLATE_NOOP("StoredMessage", "Некорректный языковой заголовок."),
    QT_TRANSLATE_NOOP("StoredMessage", 'Повреждённая запись; ожидается key:0 "Text".'),
    QT_TRANSLATE_NOOP("StoredMessage", "Нераспознанная строка локализации."),
    QT_TRANSLATE_NOOP("StoredMessage", "Низкая уверенность совпадения"),
    QT_TRANSLATE_NOOP("StoredMessage", "Несколько близких кандидатов"),
    QT_TRANSLATE_NOOP("StoredMessage", "Вернувшийся ключ — текст сильно изменён"),
    QT_TRANSLATE_NOOP("StoredMessage", "Возможное возвращение удалённой записи: несколько записей с этим ключом"),
    QT_TRANSLATE_NOOP("StoredMessage", "Возможное возвращение удалённой записи"),
)

_READ_ERROR = QT_TRANSLATE_NOOP("StoredMessage", "Не удалось прочитать диагностику снимка: {0}")
_DUPLICATE = QT_TRANSLATE_NOOP("StoredMessage", "Ключ встречается {0} раз. Первое вхождение: {1}:{2}.")


def stored_message(text):
    # Older diagnostics already contain formatted Russian messages. Recover
    # only their known presentation parameters; never modify the stored value.
    prefix = _READ_ERROR.split("{0}", 1)[0]
    if text.startswith(prefix):
        return QCoreApplication.translate("StoredMessage", _READ_ERROR).format(text[len(prefix):])
    prefix, rest = _DUPLICATE.split("{0}", 1)
    separator = rest.split("{1}", 1)[0]
    if text.startswith(prefix) and text.endswith("."):
        count, found, location = text[len(prefix):-1].partition(separator)
        file, colon, line = location.rpartition(":")
        if found and count.isdigit() and colon and line.isdigit():
            return QCoreApplication.translate("StoredMessage", _DUPLICATE).format(count, file, line)
    return QCoreApplication.translate("StoredMessage", text)
