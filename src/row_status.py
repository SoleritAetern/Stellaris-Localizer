"""User-facing row labels only; model statuses and confirmation stay unchanged."""

from PySide6.QtCore import QCoreApplication, QT_TRANSLATE_NOOP


_SOURCE_LABELS = {
    "NEW": (QT_TRANSLATE_NOOP("row_status", "Новая"),),
    "CHANGED": (QT_TRANSLATE_NOOP("row_status", "Изменено"),),
    "MOVED": (QT_TRANSLATE_NOOP("row_status", "Перемещено"),),
    "MOVED_CHANGED": (QT_TRANSLATE_NOOP("row_status", "Перемещено"), QT_TRANSLATE_NOOP("row_status", "Изменено")),
    "RENAMED": (QT_TRANSLATE_NOOP("row_status", "Переименовано"),),
    "RENAMED_CHANGED": (QT_TRANSLATE_NOOP("row_status", "Переименовано"), QT_TRANSLATE_NOOP("row_status", "Изменено")),
    "NO_HISTORY": (QT_TRANSLATE_NOOP("row_status", "Без истории"),),
    "REMOVED": (QT_TRANSLATE_NOOP("row_status", "Удалено"),),
    "NO_SOURCE": (QT_TRANSLATE_NOOP("row_status", "Нет источника"),),
}
_TRANSLATION_LABELS = {
    "NEED_UPDATE": QT_TRANSLATE_NOOP("row_status", "Требует проверки"),
    "NEEDS_REVIEW": QT_TRANSLATE_NOOP("row_status", "Требует проверки"),
    "MISSING": QT_TRANSLATE_NOOP("row_status", "Без перевода"),
    "UNTRANSLATED": QT_TRANSLATE_NOOP("row_status", "Без перевода"),
    "NO_RUSSIAN": QT_TRANSLATE_NOOP("row_status", "Без перевода"),
    "EDITED": QT_TRANSLATE_NOOP("row_status", "Перевод изменён"),
    "OFFICIAL": QT_TRANSLATE_NOOP("row_status", "Официальный перевод"),
}
_REVIEW_STATUSES = {"NEED_UPDATE", "NEEDS_REVIEW"}
_REVIEW_EXPLAINED_BY_SOURCE = {"NEW", "CHANGED", "MOVED_CHANGED", "RENAMED_CHANGED"}


def row_status(row):
    """Return a compact label and an existing semantic foreground role.

    Missing translations always remain explicit. New/changed source labels
    already explain review; their warning foreground carries that distinction.
    With a translation label, keep at most one source label. Full details are
    available in the tooltip without adding a third visible state.
    """
    source = row.get("source_status")
    translation = row.get("translation_status")
    labels = tuple(QCoreApplication.translate("row_status", label) for label in _SOURCE_LABELS.get(source, ()))
    if source in ("REMOVED", "NO_SOURCE"):
        # ORPHAN/NONE add no useful information to the missing source label.
        role = "diff.inline.removed" if source == "REMOVED" else "status.warning_text"
        return " · ".join(labels), role

    disabled = row.get("kind") == "DISABLED_ENTRY"
    if disabled:
        labels = (QCoreApplication.translate("row_status", "Отключено"),)
    detail = _TRANSLATION_LABELS.get(translation)
    review = translation in _REVIEW_STATUSES
    if review and source in _REVIEW_EXPLAINED_BY_SOURCE and not disabled:
        detail = None
    if detail:
        labels = (*labels[:1], QCoreApplication.translate("row_status", detail))
    return " · ".join(labels), "status.warning_text" if review else "text.secondary"


def row_status_tooltip(row):
    """Plain text; the caller escapes it before using a rich-text tooltip."""
    label, _ = row_status(row)
    lines = [label] if label else []
    lines.extend((QCoreApplication.translate("row_status", "Источник: {0}").format(row.get('source_status', '')),
                  QCoreApplication.translate("row_status", "Перевод: {0}").format(row.get('translation_status', ''))))
    if row.get("kind") == "DISABLED_ENTRY":
        lines.append(QCoreApplication.translate("row_status", "Тип записи: DISABLED_ENTRY"))
    return "\n".join(lines)
