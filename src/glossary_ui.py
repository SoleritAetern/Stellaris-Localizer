"""Shared text glossary views and retained legacy structured entry dialogs."""

from PySide6.QtCore import QCoreApplication

from copy import deepcopy
from uuid import uuid4

from PySide6.QtCore import QObject, QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QMenu, QToolButton,
)

from .glossary import Glossary, GlossaryEntry, GlossarySection, SCOPES, normalized
from .glossary_workspace import GlossaryPage
from .glossary_text import TextGlossaryPage, TextGlossaryMirror


class EntryForm(QWidget):
    def __init__(self, glossary, parent=None):
        super().__init__(parent)
        self.glossary = glossary
        form = QFormLayout(self)
        self.english, self.code, self.russian = QLineEdit(), QLineEdit(), QLineEdit()
        self.alternatives, self.note = QPlainTextEdit(), QPlainTextEdit()
        self.alternatives.setPlaceholderText(QCoreApplication.translate("glossary_ui", "Каждый вариант с новой строки"))
        self.alternatives.setMaximumHeight(100)
        self.note.setMinimumHeight(120)
        self.russian.setPlaceholderText(QCoreApplication.translate("glossary_ui", "Можно оставить пустым"))
        self.scope, self.section = QComboBox(), QComboBox()
        for scope, label in SCOPES.items():
            self.scope.addItem(label, scope)
        for title, field in (("English", self.english), ("Code / key", self.code),
                             (QCoreApplication.translate("glossary_ui", "Основной RU"), self.russian), ("Alternatives", self.alternatives),
                             ("Note", self.note), ("Scope", self.scope), (QCoreApplication.translate("glossary_ui", "Раздел"), self.section)):
            form.addRow(title, field)
        self.scope.currentIndexChanged.connect(self.sections_changed)
        self.original = None

    def sections_changed(self):
        self.section.clear()
        for identity, label in self.glossary.section_choices(self.scope.currentData()):
            self.section.addItem(label, identity)

    def load(self, entry):
        self.identity = entry.glossary_entry_id
        self.english.setText(entry.english)
        self.russian.setText(entry.russian)
        self.code.setText(entry.code or "")
        self.alternatives.setPlainText("\n".join(entry.alternatives))
        self.note.setPlainText(entry.note)
        self.scope.setCurrentIndex(self.scope.findData(entry.scope))
        self.sections_changed()
        self.section.setCurrentIndex(max(0, self.section.findData(entry.section_id)))
        self.original = self.value()

    def value(self):
        return GlossaryEntry(self.identity, self.english.text(), self.russian.text(),
                             self.code.text() or None,
                             [line for line in self.alternatives.toPlainText().splitlines() if line.strip()],
                             self.note.toPlainText(), self.section.currentData(), self.scope.currentData())

    def dirty(self):
        return self.original is not None and self.value() != self.original


class EntryDialog(QDialog):
    def __init__(self, glossary, entry, parent):
        super().__init__(parent)
        self.setWindowTitle(QCoreApplication.translate("glossary_ui", "Добавить в глоссарий"))
        self.resize(560, 550)
        self.glossary = glossary
        layout = QVBoxLayout(self)
        self.form = EntryForm(glossary)
        self.form.load(entry)
        layout.addWidget(self.form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        try:
            self.glossary.save_entry(self.form.value())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, QCoreApplication.translate("glossary_ui", "Глоссарий не сохранён"), str(error))
            return
        self.accept()


class GlossaryController(QObject):
    def __init__(self, window, project):
        super().__init__(window)
        self.window, self.project = window, project
        self._model = self._page = self.context = None

    def model(self):
        if self._model is None:
            self._model = Glossary(self.project.root, self)
        return self._model

    def page(self):
        if self._page is None:
            self._page = TextGlossaryPage(self.project.root,
                                         syntax_ranges=self.window.list_view.itemDelegate().syntax_ranges)
            self.window.editor_stack.addWidget(self._page)
        return self._page

    def can_leave(self):
        return self._page is None or self._page.resolve_draft()

    def save(self):
        return self._page is None or self._page.save_entry()

    def prepare_close(self):
        return self._page is None or self._page.prepare_close()

    def cancel_close(self):
        if self._page is not None:
            self._page.cancel_close()

    def ensure_context(self):
        if self.context is None:
            self.context = TextGlossaryMirror(self.page(), self.window.secondary_widget)
            self.window.secondary_widget.layout().addWidget(self.context, 1)
            self.context.hide()
        return self.context

    def show_context(self):
        self.ensure_context().show()

    def open_context(self):
        w = self.window
        for i in range(w.secondary_tab_bar.count()):
            if (w.secondary_tab_bar.tabData(i) or {}).get("kind") == "glossary":
                w.secondary_tab_bar.setCurrentIndex(i)
                w.apply_secondary_tab(i)
                w.secondary_widget.show()
                w.workspace_splitter.setSizes([650, 250])
                return
        w.add_secondary_tab("Glossary", None, kind="glossary")

    def add_from_row(self, view, index, english=None):
        if not self.can_leave() or not index.isValid():
            return
        row = index.data(Qt.ItemDataRole.UserRole)
        if english is None:
            overlay = getattr(view, "en_selection", None)
            english = (overlay.textCursor().selectedText() if overlay is not None and overlay.isVisible()
                       and overlay.index == index else "")
        russian = ""
        delegate = view.itemDelegate()
        editor = getattr(delegate, "editor", None)
        if english and editor is not None and delegate.edit_index == index and editor.textCursor().hasSelection():
            russian = editor.textCursor().selectedText()
        entry = GlossaryEntry(str(uuid4()), english or row.get("new_text") or "", russian, code=row.get("key"))
        EntryDialog(self.model(), entry, self.window).exec()
