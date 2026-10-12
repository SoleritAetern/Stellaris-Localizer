"""Literal replacement in working RU only, applied through the project's model."""

from PySide6.QtCore import QCoreApplication

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)


@dataclass(frozen=True)
class ReplaceMatch:
    row: int
    start: int
    text: str


def find_matches(rows, query, file_path=None, *, include_untranslated=True, row_indexes=None):
    """Offsets use Python characters; escapes such as literal \\n are untouched."""
    if not query:
        return []
    matches = []
    for index, row in enumerate(rows):
        if row_indexes is not None and index not in row_indexes:
            continue
        if file_path is not None and (row["new_file"] or row["old_file"]) != file_path:
            continue
        if not include_untranslated and (
            row.get("translation_origin") == "en_fallback"
            or row.get("translation_state") == "untranslated"
        ):
            continue
        text = row["ru_text"] or ""
        start = text.find(query)
        while start >= 0:
            matches.append(ReplaceMatch(index, start, text))
            start = text.find(query, start + len(query))
    return matches


class ReplacePanel(QWidget):
    previewRequested = Signal(int)

    def __init__(self, model, current_file, finish_editing, parent=None, *, selected_rows=None, query_box=None):
        super().__init__(parent)
        self.embedded = query_box is not None
        self.model = model  # Only the current project's working model is passed here.
        self.current_file = current_file
        self.finish_editing = finish_editing
        self.selected_rows = selected_rows or (lambda: frozenset())
        self.row_indexes = None
        self.matches = []
        self.query = None
        self.file_path = None
        self._applying = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scope = QComboBox(self)
        self.scope.addItem(QCoreApplication.translate("replacement", "Текущий файл"), "file")
        self.scope.addItem(QCoreApplication.translate("replacement", "Весь проект"), "project")
        self.scope.addItem(QCoreApplication.translate("replacement", "Выделенные записи"), "selected")
        if self.embedded:
            self.scope.setCurrentIndex(self.scope.findData("project"))
            self.scope.hide()
        else:
            layout.addWidget(self.scope)
        self.include_untranslated_box = QCheckBox(QCoreApplication.translate("replacement", "Включать непереведённые строки"))
        self.include_untranslated_box.setChecked(True)
        self.context_label = QLabel(self)
        self.context_label.setWordWrap(True)
        self.context_label.setToolTip(QCoreApplication.translate("replacement", "Файл или выделенные записи основной панели рабочего перевода"))
        if self.embedded:
            self.context_label.hide()
        else:
            layout.addWidget(self.include_untranslated_box)
            layout.addWidget(self.context_label)
        self.query_box = query_box if self.embedded else QLineEdit()
        if not self.embedded:
            self.query_box.setPlaceholderText(QCoreApplication.translate("replacement", "Найти в RU · Enter"))
            self.query_box.setAccessibleName(QCoreApplication.translate("replacement", "Найти в RU"))
            self.query_box.setToolTip(QCoreApplication.translate("replacement", "Буквальный поиск с учётом регистра. Только рабочий RU."))
        self.replacement_box = QLineEdit()
        self.replacement_box.setPlaceholderText(QCoreApplication.translate("replacement", "Заменить на (можно оставить пустым)"))
        self.replacement_box.setAccessibleName(QCoreApplication.translate("replacement", "Заменить на"))
        if not self.embedded:
            layout.addWidget(self.query_box)
        layout.addWidget(self.replacement_box)
        if self.embedded:
            target = QLabel("RU · Current Mod")
            target.setProperty("role", "muted")
            target.setToolTip(QCoreApplication.translate("replacement", "Буквальная замена с учётом регистра. Только рабочий RU текущего проекта."))
            layout.addWidget(target)
            layout.addWidget(self.include_untranslated_box)
        buttons = QHBoxLayout()
        self.replace_button = QPushButton(QCoreApplication.translate("replacement", "Заменить"))
        self.replace_all_button = QPushButton(QCoreApplication.translate("replacement", "Заменить всё"))
        buttons.addWidget(self.replace_button)
        buttons.addWidget(self.replace_all_button)
        layout.addLayout(buttons)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.results = QListWidget()
        self.results.setWordWrap(True)
        layout.addWidget(self.results)
        if not self.embedded:
            self.query_box.returnPressed.connect(self.search)
            self.replacement_box.returnPressed.connect(self.search)
        # Input changes invalidate a previous search; they never run a new one.
        self.query_box.textChanged.connect(self.invalidate)
        self.scope.currentIndexChanged.connect(self.invalidate)
        self.include_untranslated_box.toggled.connect(self.invalidate)
        self.model.dataChanged.connect(self._model_changed)
        self.replace_button.clicked.connect(self.replace_current)
        self.replace_all_button.clicked.connect(self.replace_all)
        self.results.currentRowChanged.connect(self._update_buttons)
        self.results.itemDoubleClicked.connect(
            lambda item: self.previewRequested.emit(item.data(Qt.ItemDataRole.UserRole).row)
        )
        self.invalidate()

    @property
    def include_untranslated(self):
        return self.include_untranslated_box.isChecked()

    def _scope_rows(self):
        return frozenset(self.selected_rows()) if self.scope.currentData() == "selected" else None

    def _scope_file(self):
        return self.current_file() if self.scope.currentData() == "file" else None

    def _update_context(self):
        if self.scope.currentData() == "selected":
            count = len(self._scope_rows())
            self.context_label.setText(
                QCoreApplication.translate("replacement", "Выделено записей: {0}").format(count) if count else
                QCoreApplication.translate("replacement", "Выделите записи рабочего перевода в основной панели")
            )
            return
        path = self._scope_file()
        self.context_label.setText(
            str(path) if path is not None else
            (QCoreApplication.translate("replacement", "Выберите файл или запись в основной панели") if self.scope.currentData() == "file"
             else QCoreApplication.translate("replacement", "Рабочий RU всего проекта"))
        )

    def context_changed(self, *args):
        if self._applying:
            return
        if self._scope_file() != self.file_path or self._scope_rows() != self.row_indexes:
            self.invalidate()

    def invalidate(self, *args):
        self.query = None
        self.matches = []
        self.results.clear()
        self._update_context()
        hint = "" if self.include_untranslated else QCoreApplication.translate("replacement", " Непереведённые исключены.")
        self.summary.setText((QCoreApplication.translate("replacement", "Введите запрос.") if self.embedded else QCoreApplication.translate("replacement", "Введите запрос и нажмите Enter.")) + hint)
        self._update_buttons()

    def _model_changed(self, *args):
        if not self._applying:
            self.invalidate()

    def _update_buttons(self, *args):
        self.replace_button.setEnabled(self.query is not None and self.results.currentRow() >= 0)
        self.replace_all_button.setEnabled(bool(self.matches))

    def search(self):
        self.finish_editing()
        self.file_path = self._scope_file()
        self.row_indexes = self._scope_rows()
        self.query = self.query_box.text()  # Spaces and literal escapes are significant.
        if (self.scope.currentData() == "file" and self.file_path is None) or self.row_indexes == frozenset():
            self.invalidate()
            return
        self._refresh()

    def _refresh(self, next_position=None):
        if self._scope_rows() != self.row_indexes or self._scope_file() != self.file_path:
            self.invalidate()
            return
        self.matches = find_matches(self.model.rows, self.query or "", self.file_path,
                                    include_untranslated=self.include_untranslated, row_indexes=self.row_indexes)
        self.results.clear()
        selected = 0
        for index, match in enumerate(self.matches):
            row = self.model.rows[match.row]
            path = row["new_file"] or row["old_file"] or ""
            # Each occurrence can be selected independently, including repeats in one RU.
            item = QListWidgetItem(QCoreApplication.translate("replacement", "{0} · символ {1}\nRU: {2}\n{3}").format(row['key'], match.start + 1, match.text, path))
            item.setData(Qt.ItemDataRole.UserRole, match)
            self.results.addItem(item)
        if next_position is not None:
            selected = next((i for i, match in enumerate(self.matches)
                             if (match.row, match.start) >= next_position), 0)
        if self.matches:
            self.results.setCurrentRow(selected)
        self._update_context()
        self.summary.setText(QCoreApplication.translate("replacement", "Совпадений: {0} · записей: {1}").format(len(self.matches), len({m.row for m in self.matches})))
        self._update_buttons()

    def _valid(self):
        self.finish_editing()
        if (self.query is None or self.query != self.query_box.text()
                or self._scope_file() != self.file_path or self._scope_rows() != self.row_indexes):
            self.invalidate()
            return False
        fresh = find_matches(self.model.rows, self.query, self.file_path,
                             include_untranslated=self.include_untranslated, row_indexes=self.row_indexes)
        if fresh != self.matches:
            self.invalidate()
            return False
        return bool(fresh)

    def _replacement(self):
        return (self.replacement_box.text().replace("\r\n", "\\n").replace("\r", "\\n")
                .replace("\n", "\\n").replace("\u2028", "\\n").replace("\u2029", "\\n"))

    def _apply(self, changes, *, macro=False):
        self._applying = True
        if macro:
            self.model.undo_stack.beginMacro(QCoreApplication.translate("replacement", "Замена всех совпадений в RU"))
        try:
            for row, text in changes:
                self.model.setData(self.model.index(row, 0), text, Qt.ItemDataRole.EditRole)
        finally:
            if macro:
                self.model.undo_stack.endMacro()
            self._applying = False

    def replace_current(self):
        if not self._valid() or self.results.currentRow() < 0:
            return
        match = self.matches[self.results.currentRow()]
        replacement = self._replacement()
        text = match.text[:match.start] + replacement + match.text[match.start + len(self.query):]
        if text != match.text:
            self._apply([(match.row, text)])
        self._refresh((match.row, match.start + len(replacement)))

    def replace_all(self):
        if not self._valid():
            return
        replacement = self._replacement()
        originals = {match.row: match.text for match in self.matches}
        changes = [(row, text.replace(self.query, replacement)) for row, text in originals.items()
                   if text.replace(self.query, replacement) != text]
        if not changes:
            return
        scope = str(self.file_path) if self.file_path is not None else self.scope.currentText()
        answer = QMessageBox.question(
            self, QCoreApplication.translate("replacement", "Заменить всё в рабочем RU?"),
            QCoreApplication.translate("replacement", "{0}\nСовпадений: {1}\nЗаписей: {2}\nВыполнить замену?").format(scope, len(self.matches), len(changes)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes or not self._valid():
            return
        self._apply(changes, macro=True)
        self._refresh()
