"""Explicit Global Replace plans and a single undo command per operation."""

from PySide6.QtCore import QCoreApplication

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand

from .global_search import full_replacement_matches, is_working_ru
from .translation_baseline import refresh_baseline


TRANSLATION_FIELDS = ("ru_text", "translation_status", "translation_state", "translation_origin",
                      "needs_review", "source_diff", "source_comment_changed")


class ReplaceScope(str, Enum):
    OCCURRENCE = "OCCURRENCE"
    ENTRY = "ENTRY"
    FILE = "FILE"
    ALL = "ALL"


class StaleReplacementError(ValueError):
    pass


def file_path(row):
    return str(row.get("new_file") or row.get("old_file") or "").replace("\\", "/")


@dataclass(frozen=True)
class ReplacementChange:
    entry_id: str
    key: str
    file: str
    old_ru: str
    new_ru: str
    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class ReplacementPlan:
    scope: ReplaceScope
    changes: tuple[ReplacementChange, ...]
    occurrences: int
    _owner: object = field(repr=False, compare=False)
    _version: int = field(repr=False)

    @property
    def summary(self):
        # Occurrences includes selected matches even if replacement is identical;
        # entries/files count only actual changes. A no-op never enters Undo.
        return {"occurrences": self.occurrences, "entries": len(self.changes),
                "files": len({change.file for change in self.changes})}


class GlobalReplaceCommand(QUndoCommand):
    def __init__(self, model, snapshots, scope):
        super().__init__(QCoreApplication.translate("global_replace", "Замена RU · {0}").format(scope.value))
        self.model = model
        self.snapshots = snapshots  # (row, entry_id, before, after), already validated.

    def redo(self):
        self.model.apply_translation_batch([(row, identity, after) for row, identity, before, after in self.snapshots])

    def undo(self):
        self.model.apply_translation_batch([(row, identity, before) for row, identity, before, after in self.snapshots])


class GlobalReplacementEngine(QObject):
    applied = Signal()

    def __init__(self, model, parent=None):
        super().__init__(parent or model)
        self.model = model  # The current working model only; never a preview/Vanilla proxy.
        self._owner = object()
        self._version = 0
        for signal in (model.dataChanged, model.modelReset, model.rowsInserted,
                       model.rowsRemoved, model.layoutChanged):
            signal.connect(self._changed)
        # Redo and Undo need the same single Search refresh as initial apply.
        model.translationBatchChanged.connect(self.applied.emit)

    def _changed(self, *args):
        self._version += 1

    def prepare(self, query, replacement, scope=ReplaceScope.ALL, *, entry_id=None,
                occurrence=None, file=None, case_sensitive=False):
        """Build a read-only plan; occurrence is an exact (start, end) source span."""
        scope = ReplaceScope(scope)
        if not isinstance(replacement, str):
            raise TypeError(QCoreApplication.translate("global_replace", "Replacement должен быть строкой."))
        if scope in (ReplaceScope.OCCURRENCE, ReplaceScope.ENTRY) and not entry_id:
            raise ValueError(QCoreApplication.translate("global_replace", "Для замены в записи требуется entry_id."))
        if scope == ReplaceScope.OCCURRENCE and occurrence is None:
            raise ValueError(QCoreApplication.translate("global_replace", "Для одной замены требуется точный span совпадения."))
        if scope == ReplaceScope.FILE and not file:
            raise ValueError(QCoreApplication.translate("global_replace", "Для замены файла требуется relative path."))
        target_file = str(file).replace("\\", "/") if file is not None else None
        candidates = [row for row in self.model.rows
                      if (scope not in (ReplaceScope.OCCURRENCE, ReplaceScope.ENTRY) or row.get("entry_id") == entry_id)
                      and (scope != ReplaceScope.FILE or file_path(row) == target_file)]
        if scope in (ReplaceScope.OCCURRENCE, ReplaceScope.ENTRY) and len(candidates) != 1:
            raise StaleReplacementError(QCoreApplication.translate("global_replace", "Запись не найдена однозначно. Повторите поиск."))
        matches = full_replacement_matches(candidates, query, case_sensitive=case_sensitive)
        changes, occurrences, identities = [], 0, set()
        selected_occurrence = tuple(occurrence) if occurrence is not None else None
        for match in matches:
            row = candidates[match.row]
            identity = row.get("entry_id")
            if not identity or identity in identities:
                raise ValueError(QCoreApplication.translate("global_replace", "Запись не имеет уникального entry_id."))
            identities.add(identity)
            spans = match.spans
            if scope == ReplaceScope.OCCURRENCE:
                if selected_occurrence not in spans:
                    raise StaleReplacementError(QCoreApplication.translate("global_replace", "Совпадение изменилось. Повторите поиск."))
                spans = (selected_occurrence,)
            occurrences += len(spans)
            chunks, previous = [], 0
            for start, end in spans:
                chunks.extend((match.text[previous:start], replacement))
                previous = end
            chunks.append(match.text[previous:])
            new_ru = "".join(chunks)
            if new_ru != match.text:
                changes.append(ReplacementChange(identity, row.get("key") or "", file_path(row),
                                                 match.text, new_ru, spans))
        if scope == ReplaceScope.OCCURRENCE and not matches:
            raise StaleReplacementError(QCoreApplication.translate("global_replace", "Совпадение больше недоступно для замены. Повторите поиск."))
        return ReplacementPlan(scope, tuple(changes), occurrences, self._owner, self._version)

    def apply(self, plan):
        """Validate the entire plan, then push exactly one command. No dialogs/IO."""
        if plan._owner is not self._owner or plan._version != self._version:
            raise StaleReplacementError(QCoreApplication.translate("global_replace", "Проект изменился после подготовки замены. Повторите поиск."))
        if not plan.changes:
            return False
        numbers = {}
        for number, row in enumerate(self.model.rows):
            identity = row.get("entry_id")
            if not identity:
                continue
            if identity in numbers:
                raise StaleReplacementError(QCoreApplication.translate("global_replace", "Повторяющийся entry_id. Замена отменена."))
            numbers[identity] = number
        snapshots = []
        for change in plan.changes:
            number = numbers.get(change.entry_id)
            row = self.model.rows[number] if number is not None else None
            if (row is None or not is_working_ru(row) or row.get("ru_text") != change.old_ru
                    or row.get("key") != change.key or file_path(row) != change.file):
                raise StaleReplacementError(QCoreApplication.translate("global_replace", "Запись изменилась после подготовки замены. Повторите поиск."))
            before = {key: deepcopy(row[key]) for key in TRANSLATION_FIELDS if key in row}
            updated = dict(row)
            updated.update(ru_text=change.new_ru, translation_status="EDITED")
            if any(key in row for key in ("translation_state", "translation_origin", "needs_review")):
                updated.update(translation_state="edited", translation_origin="manual")
            if "accepted_source_text" in row:
                refresh_baseline(updated)
            if updated["ru_text"] != change.new_ru:
                raise ValueError(QCoreApplication.translate("global_replace", "Замена отменена: {0} ({1}) хранит обязательное whitespace-значение EN. Изменения не применены.").format(change.key, change.file))
            after = {key: deepcopy(updated[key]) for key in TRANSLATION_FIELDS if key in updated}
            snapshots.append((number, change.entry_id, before, after))
        self.model.undo_stack.push(GlobalReplaceCommand(self.model, snapshots, plan.scope))
        return True
