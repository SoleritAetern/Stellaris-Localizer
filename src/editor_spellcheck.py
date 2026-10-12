"""Spellcheck presentation owned by a single live RussianTextEdit."""

from PySide6.QtCore import QCoreApplication

import logging
from threading import Event

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QContextMenuEvent, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QMessageBox, QTextEdit

from . import theme
from .app_settings import AppSettings, spellcheck_changes
from .spellcheck import dictionary, worker
from .text_context_menu import standard_text_menu


_SPELL_MARKER = int(QTextFormat.Property.UserProperty) + 181


class EditorSpellcheck(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.settings = AppSettings()
        self.ignored = set()
        self.errors = []
        self.revision = -1
        self.closed = False
        self.future = None
        self.cancelled = Event()
        self.callback = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.check)
        self.poll = QTimer(self)
        self.poll.setInterval(40)
        self.poll.timeout.connect(self.collect)
        editor.textChanged.connect(self.schedule)
        editor.destroyed.connect(lambda: self.cancelled.set())
        editor.installEventFilter(self)
        spellcheck_changes.words_changed.connect(self.dictionary_changed)

    def dictionary_changed(self, removed):
        self.ignored.difference_update(removed)
        if not self.closed:
            self.schedule()

    def cancel_job(self):
        self.cancelled.set()
        if self.future is not None:
            self.future.cancel()
        self.future = self.callback = None
        self.poll.stop()

    def stop(self):
        self.closed = True
        self.timer.stop()
        self.cancel_job()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Hide:
            self.timer.stop()
            self.cancel_job()
        elif event.type() == QEvent.Type.Show and not self.closed:
            self.schedule()
        return super().eventFilter(watched, event)

    def set_underlines(self, selections):
        # Preserve any other editor overlays. Syntax highlighting belongs to
        # RussianHighlighter; ExtraSelections never modify the document/undo.
        others = [item for item in self.editor.extraSelections()
                  if not item.format.property(_SPELL_MARKER)]
        self.editor.setExtraSelections(others + selections)

    def schedule(self):
        self.cancel_job()
        self.errors = []
        self.revision = -1
        self.set_underlines([])
        if not self.closed:
            self.timer.start()

    def submit(self, function, *args, callback):
        self.cancel_job()
        self.cancelled = Event()
        self.callback = callback
        self.future = worker.submit(function, *args, self.cancelled)
        self.poll.start()

    def collect(self):
        if self.future is None or not self.future.done():
            return
        future, callback = self.future, self.callback
        self.future = self.callback = None
        self.poll.stop()
        if self.closed or not self.editor.isVisible() or self.cancelled.is_set():
            return
        try:
            result = future.result()
        except Exception as error:
            logging.warning("Russian spellcheck unavailable: %s", error)
            window = self.editor.window()
            if hasattr(window, "statusBar"):
                window.statusBar().showMessage(QCoreApplication.translate("editor_spellcheck", "Проверка орфографии недоступна: {0}").format(error), 10000)
            return
        callback(result)

    def check(self):
        if self.closed or not self.editor.isVisible() or self.editor.closing:
            return
        text = self.editor.toPlainText()
        revision = self.editor.document().revision()
        accepted = self.settings.spellcheck_words | self.ignored
        self.submit(dictionary.check, text, accepted,
                    callback=lambda errors: self.show_errors(text, revision, errors))

    def show_errors(self, text, revision, errors):
        if self.editor.document().revision() != revision:
            return
        selections = []
        self.errors = []
        previous = qt_position = 0
        for start, end, word in errors:
            qt_position += len(text[previous:start].encode("utf-16-le")) // 2
            qt_end = qt_position + len(word.encode("utf-16-le")) // 2
            self.errors.append((qt_position, qt_end, word))
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(self.editor.document())
            selection.cursor.setPosition(qt_position)
            selection.cursor.setPosition(qt_end, QTextCursor.MoveMode.KeepAnchor)
            selection.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
            selection.format.setUnderlineColor(theme.color("spellcheck.underline"))
            selection.format.setProperty(_SPELL_MARKER, True)
            selections.append(selection)
            previous, qt_position = end, qt_end
        self.revision = revision
        self.set_underlines(selections)

    def error_at(self, event):
        if self.revision != self.editor.document().revision():
            return None
        if event.reason() == QContextMenuEvent.Reason.Keyboard:
            position = self.editor.textCursor().position()
        else:
            cursor = self.editor.cursorForPosition(event.pos())
            position = cursor.position()
            if event.pos().x() < self.editor.cursorRect(cursor).x():
                position = max(0, position - 1)
        return next((item for item in self.errors if item[0] <= position < item[1]), None)

    def replace(self, error, revision, replacement):
        if self.closed or revision != self.editor.document().revision():
            return
        start, end, word = error
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        if cursor.selectedText() != word:
            return
        cursor.beginEditBlock()
        cursor.insertText(replacement)
        cursor.endEditBlock()
        self.editor.setTextCursor(cursor)

    def accept_word(self, word, *, persist):
        if persist:
            try:
                self.settings.add_spellcheck_word(word)
            except OSError as error:
                QMessageBox.warning(self.editor, QCoreApplication.translate("editor_spellcheck", "Не удалось сохранить слово"), str(error))
                return
        else:
            self.ignored.add(word.casefold())
            self.schedule()

    def context_menu(self, event):
        menu = standard_text_menu(self.editor)
        error = self.error_at(event)
        if error is not None:
            revision = self.revision
            anchor = menu.actions()[0]
            heading = menu.addAction(error[2])
            menu.insertAction(anchor, heading)
            heading.setEnabled(False)
            loading = menu.addAction(QCoreApplication.translate("editor_spellcheck", "Поиск исправлений…"))
            loading.setEnabled(False)
            menu.insertAction(anchor, loading)
            separator = menu.insertSeparator(anchor)
            add = menu.addAction(QCoreApplication.translate("editor_spellcheck", "Добавить в словарь"))
            menu.insertAction(anchor, add)
            add.triggered.connect(lambda: self.accept_word(error[2], persist=True))
            ignore = menu.addAction(QCoreApplication.translate("editor_spellcheck", "Игнорировать"))
            menu.insertAction(anchor, ignore)
            ignore.triggered.connect(lambda: self.accept_word(error[2], persist=False))
            menu.insertSeparator(anchor)

            def suggestions_ready(values):
                if not menu.isVisible() or revision != self.editor.document().revision():
                    return
                if not values:
                    loading.setText(QCoreApplication.translate("editor_spellcheck", "Нет вариантов исправления"))
                    return
                menu.removeAction(loading)
                for value in values:
                    action = menu.addAction(value)
                    menu.insertAction(separator, action)
                    action.triggered.connect(
                        lambda checked=False, value=value: self.replace(error, revision, value))

            self.submit(dictionary.suggestions, error[2], callback=suggestions_ready)
            menu.aboutToHide.connect(self.cancel_job)
        try:
            menu.exec(event.globalPos())
        finally:
            if error is not None:
                self.cancel_job()
            menu.deleteLater()
