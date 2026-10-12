"""Manage only persistent user words, never the bundled Hunspell dictionary."""

from PySide6.QtCore import QCoreApplication

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QVBoxLayout,
)

from .app_settings import spellcheck_changes


class UserDictionaryDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(QCoreApplication.translate("user_dictionary_dialog", "Пользовательский словарь"))
        self.resize(420, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText(QCoreApplication.translate("user_dictionary_dialog", "Поиск…"))
        self.search.setAccessibleName(QCoreApplication.translate("user_dictionary_dialog", "Поиск в пользовательском словаре"))
        layout.addWidget(self.search)
        self.words = QListWidget()
        self.words.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.words.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.words.setAccessibleName(QCoreApplication.translate("user_dictionary_dialog", "Добавленные слова"))
        layout.addWidget(self.words, 1)
        self.empty = QLabel()
        self.empty.setProperty("role", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.remove = QPushButton(QCoreApplication.translate("user_dictionary_dialog", "Удалить"))
        self.remove.setAutoDefault(False)
        self.remove.setEnabled(False)
        buttons.addWidget(self.remove)
        layout.addLayout(buttons)
        self.search.textChanged.connect(self.filter_words)
        self.words.itemSelectionChanged.connect(self.update_remove)
        self.remove.clicked.connect(self.remove_selected)
        spellcheck_changes.words_changed.connect(self.reload_words)
        self.reload_words()

    def reload_words(self, removed=None):
        self.words.clear()
        self.words.addItems(sorted(self.settings.spellcheck_words))
        self.filter_words()

    def filter_words(self, text=None):
        query = self.search.text().strip().casefold()
        visible = 0
        for row in range(self.words.count()):
            item = self.words.item(row)
            matches = query in item.text().casefold()
            if not matches:
                item.setSelected(False)
            item.setHidden(not matches)
            visible += matches
        self.words.setVisible(bool(visible))
        self.empty.setVisible(not visible)
        self.empty.setText(QCoreApplication.translate("user_dictionary_dialog", "Ничего не найдено.") if self.words.count()
                           else QCoreApplication.translate("user_dictionary_dialog", "Пользовательский словарь пуст."))
        self.update_remove()

    def selected_words(self):
        return [item.text() for item in self.words.selectedItems() if not item.isHidden()]

    def update_remove(self):
        self.remove.setEnabled(bool(self.selected_words()))

    def remove_selected(self):
        words = self.selected_words()
        if not words:
            return
        try:
            self.settings.remove_spellcheck_words(words)
        except OSError as error:
            QMessageBox.warning(self, QCoreApplication.translate("user_dictionary_dialog", "Не удалось удалить слова"), str(error))
