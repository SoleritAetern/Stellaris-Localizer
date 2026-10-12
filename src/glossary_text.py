"""Free-text glossary documents. Structured glossary JSON is never touched."""

from PySide6.QtCore import QCoreApplication

from pathlib import Path
import re

from PySide6.QtCore import QEvent, QIODevice, QSaveFile, QStandardPaths, Qt, QTimer, QSignalBlocker, QPointF, QRect, QSize, Signal
from PySide6.QtGui import QFont, QKeySequence, QTextCursor, QTextDocument, QTextCharFormat, QPainter, QTextFormat, QTextOption
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextDocumentLayout, QPlainTextEdit, QStackedWidget, QTextEdit, QToolButton, QVBoxLayout, QWidget, QFileDialog,
)

from . import theme
from .text_overlays import SelectionHighlighter
from .search_selection import SearchSelectionCapture


class NotebookDocumentLayout(QPlainTextDocumentLayout):
    """Extra visual leading, including wrapped lines, without document edits."""

    LINE_SPACING = 1.28

    def blockBoundingRect(self, block):
        rect = super().blockBoundingRect(block)
        if not block.isValid() or not block.isVisible():
            return rect
        layout = block.layout()
        if not layout.lineCount():
            return rect
        # Keep Qt's shaping, wrapping and UTF-16 cursor positions. Recompute
        # from intrinsic line heights so repeated geometry queries never
        # accumulate spacing. The plain-text layout ignores block lineHeight.
        y = 0.0
        for number in range(layout.lineCount()):
            line = layout.lineAt(number)
            line.setPosition(QPointF(line.x(), y))
            y += line.height() * self.LINE_SPACING
        if not block.next().isValid():
            y += self.document().documentMargin()
        rect.setHeight(y)
        return rect


class LineNumberArea(QWidget):
    """One painted gutter for an entire document, including wrapped blocks."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_line_numbers(event)


class NotebookEdit(QPlainTextEdit):
    def __init__(self, *, syntax_ranges=None, document=None):
        super().__init__()
        self.search_selection_capture = SearchSelectionCapture(self)
        self._other_selections = []
        self._gutter_width = 0
        self.gutter = LineNumberArea(self)
        if document is not None:
            self.setDocument(document)
        else:
            self._notebook_layout = NotebookDocumentLayout(self.document())
            self.document().setDocumentLayout(self._notebook_layout)
            self.document().setDocumentMargin(8)
        font = QFont()
        font.setFamilies(["Cascadia Mono", "Consolas"])
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        font.setPointSizeF(11.0)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.blockCountChanged.connect(self.update_gutter_width)
        self.updateRequest.connect(self.update_gutter)
        self.cursorPositionChanged.connect(self.highlight_current_line)
        heading_format = QTextCharFormat()
        heading_format.setForeground(theme.color("editor.heading"))
        heading_format.setFontWeight(QFont.Weight.Bold)
        # A shared document already has its owner's highlighter and undo history.
        self.highlighter = None if document is not None else SelectionHighlighter(
            self.document(), syntax_ranges if syntax_ranges is not None else lambda text: (),
            heading_format=heading_format)
        self.update_gutter_width()
        self.highlight_current_line()

    def gutter_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 24 + self.fontMetrics().horizontalAdvance("9") * digits

    def position_gutter(self):
        viewport = self.viewport().geometry()
        self.gutter.setGeometry(QRect(viewport.left() - self._gutter_width, viewport.top(),
                                      self._gutter_width, viewport.height()))

    def update_gutter_width(self, *args):
        width = self.gutter_width()
        if width != self._gutter_width:
            self._gutter_width = width
            self.setViewportMargins(width, 0, 0, 0)
        self.position_gutter()
        self.gutter.update()

    def update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self._gutter_width, rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "gutter", None) is not None:
            self.position_gutter()

    def paint_line_numbers(self, event):
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), theme.color('editor.gutter.bg'))
        painter.setFont(self.font())
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        current = self.textCursor().blockNumber()
        while block.isValid() and top <= event.rect().bottom():
            height = self.blockBoundingRect(block).height()
            if block.isVisible() and top + height >= event.rect().top():
                painter.setPen(theme.color('editor.gutter.active' if number == current else 'editor.gutter.fg'))
                painter.drawText(QRect(0, round(top), self._gutter_width - 12, self.fontMetrics().height()),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, str(number + 1))
            top += height
            block = block.next()
            number += 1
        painter.end()

    def setExtraSelections(self, selections):
        # Find owns its own highlight; retain it above the current-line background.
        self._other_selections = list(selections)
        self.highlight_current_line()

    def highlight_current_line(self):
        line = QTextEdit.ExtraSelection()
        line.format.setBackground(theme.color('editor.current_line.bg'))
        line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        line.cursor = self.textCursor()
        line.cursor.clearSelection()
        super().setExtraSelections([line, *self._other_selections])
        self.gutter.update()

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            if any(event.matches(key) for key in (
                QKeySequence.StandardKey.Copy, QKeySequence.StandardKey.Cut,
                QKeySequence.StandardKey.Paste, QKeySequence.StandardKey.Undo,
                QKeySequence.StandardKey.Redo, QKeySequence.StandardKey.SelectAll,
            )):
                event.accept()
                return True
        result = super().event(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and getattr(self, "gutter", None) is not None:
            self.update_gutter_width()
        return result


class TextFind(QFrame):
    def __init__(self, page):
        super().__init__(page.current_editor().viewport())
        self.page = page
        self.setObjectName("localFind")
        self.matches = []
        self.current = -1
        self.revision = -1
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 3, 5, 3)
        layout.setSpacing(4)
        self.input = QLineEdit()
        self.input.setPlaceholderText(QCoreApplication.translate("glossary_text", "Найти в глоссарии"))
        self.counter = QLabel("0 / 0")
        layout.addWidget(self.input, 1)
        layout.addWidget(self.counter)
        for text, tip, callback in (("↑", QCoreApplication.translate("glossary_text", "Предыдущее · Shift+F3"), lambda: self.step(-1)),
                                    ("↓", QCoreApplication.translate("glossary_text", "Следующее · F3"), lambda: self.step(1)),
                                    ("×", QCoreApplication.translate("glossary_text", "Закрыть · Esc"), self.close_find)):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            layout.addWidget(button)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.rebuild)
        self.input.textChanged.connect(lambda: self.timer.start())
        self.input.installEventFilter(self)
        for editor in page.editors.values():
            editor.viewport().installEventFilter(self)
            editor.installEventFilter(self)
            editor.textChanged.connect(self.text_changed)
            editor.updateRequest.connect(self.viewport_updated)
        self.hide()

    def viewport_updated(self, *args):
        # QPlainTextEdit scrolls its viewport children along with the text.
        # Find is fixed UI, so restore its anchor after that native scroll.
        if not self.isHidden():
            self.reposition()

    def text_changed(self):
        if not self.isHidden():
            self.timer.start()

    def reposition(self):
        viewport = self.page.current_editor().viewport()
        self.setFixedWidth(min(440, max(1, viewport.width() - 16)))
        self.setFixedHeight(self.layout().sizeHint().height())
        self.move(max(0, viewport.width() - self.width() - 8), 8)
        self.raise_()

    def attach(self):
        visible = not self.isHidden()
        self.setParent(self.page.current_editor().viewport())
        if visible:
            self.show()
            self.reposition()
            self.rebuild()

    def toggle(self, *, query=None):
        was_hidden = self.isHidden()
        self.show()
        self.reposition()
        if query is not None and query != self.input.text():
            self.input.setText(query)
        elif was_hidden:
            self.rebuild()
        self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.input.selectAll()

    def close_find(self):
        self.timer.stop()
        self.hide()
        self.page.current_editor().setExtraSelections([])
        self.page.current_editor().setFocus()

    def rebuild(self):
        self.timer.stop()
        editor = self.page.current_editor()
        document = editor.document()
        self.revision = document.revision()
        self.matches = []
        if self.input.text():
            cursor = document.find(self.input.text(), 0, QTextDocument.FindFlag(0))
            while not cursor.isNull():
                self.matches.append((cursor.selectionStart(), cursor.selectionEnd()))
                cursor = document.find(self.input.text(), cursor.selectionEnd(), QTextDocument.FindFlag(0))
        position = editor.textCursor().selectionStart()
        self.current = next((i for i, (start, end) in enumerate(self.matches) if start >= position), 0) if self.matches else -1
        self.counter.setText(f"{self.current + 1} / {len(self.matches)}")
        if self.matches:
            self.navigate()
        else:
            editor.setExtraSelections([])

    def step(self, direction):
        if self.isHidden():
            self.toggle()
            return
        if self.timer.isActive() or self.revision != self.page.current_editor().document().revision():
            self.rebuild()
            return
        if self.matches:
            self.current = (self.current + direction) % len(self.matches)
            self.navigate()

    def navigate(self):
        editor = self.page.current_editor()
        start, end = self.matches[self.current]
        cursor = QTextCursor(editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = QTextCharFormat()
        selection.format.setBackground(theme.color('search.active.bg'))
        editor.setExtraSelections([selection])
        self.counter.setText(f"{self.current + 1} / {len(self.matches)}")

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize and watched is self.parentWidget():
            self.reposition()
        if not self.isHidden() and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            key = event.key()
            handled = key == Qt.Key.Key_Escape or (watched is self.input and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter))
            if handled:
                if event.type() == QEvent.Type.ShortcutOverride:
                    event.accept()
                elif key == Qt.Key.Key_Escape:
                    self.close_find()
                else:
                    self.step(-1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
                return True
        return super().eventFilter(watched, event)


class TextGlossaryPage(QWidget):
    presentation_changed = Signal()

    def __init__(self, project_root, parent=None, *, syntax_ranges=None):
        super().__init__(parent)
        global_root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.paths = {"project": Path(project_root) / "glossary.txt", "global": global_root / "glossary.txt"}
        self.expected = {}
        self.errors = {}
        self.editors = {}
        self.load_state = {scope: "uninitialized" for scope in self.paths}
        self.dirty = {scope: False for scope in self.paths}
        self._observed_text = {scope: "" for scope in self.paths}
        self._loading = set()
        self._closing = False
        self._close_accepted = False
        self.scope = "project"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        toolbar = QHBoxLayout()
        self.buttons = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.stack = QStackedWidget()
        for scope, label in (("project", "Project"), ("global", "Global")):
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setStyleSheet(f"QToolButton {{ border: none; padding: 4px 10px; }} QToolButton:checked {{ background: {theme.css('surface.selected')}; }}")
            button.clicked.connect(lambda checked=False, scope=scope: self.switch_scope(scope))
            self.group.addButton(button)
            toolbar.addWidget(button)
            self.buttons[scope] = button
            editor = NotebookEdit(syntax_ranges=syntax_ranges)
            editor.setStyleSheet(f"QPlainTextEdit {{ background: {theme.css('surface.window')}; color: {theme.css('editor.text')}; border: none; padding: 0; }}")
            editor.setTabStopDistance(editor.fontMetrics().horizontalAdvance(" ") * 4)
            self.stack.addWidget(editor)
            self.editors[scope] = editor
            self.read_clean(scope)
        self.buttons[self.scope].setChecked(True)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        layout.addWidget(self.stack, 1)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setProperty("role", "muted")
        layout.addWidget(self.notice)
        self.autosave_timers = {}
        # Subscribe only after both initial reads. Layout/highlighter changes are
        # not user text edits, even when Qt marks the document as modified.
        for scope, editor in self.editors.items():
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(700)
            timer.timeout.connect(lambda scope=scope: self.save_entry(scopes=(scope,), show_error=False))
            self.autosave_timers[scope] = timer
            editor.document().contentsChange.connect(
                lambda position, removed, added, scope=scope: self.text_edited(scope, removed, added))
        self.find = TextFind(self)
        self.show_errors()

    def current_editor(self):
        return self.editors[self.scope]

    def text_edited(self, scope, removed, added):
        if scope in self._loading or not (removed or added):
            return
        # Qt can report removed/added ranges for formatting as well. Only a
        # change in plain text is a draft edit, never margins or highlighting.
        text = self.editors[scope].toPlainText()
        if text == self._observed_text[scope]:
            return
        self._observed_text[scope] = text
        self.dirty[scope] = True
        if not self._closing and self.load_state[scope] in ("loaded", "new"):
            self.autosave_timers[scope].start()

    @staticmethod
    def read_payload(path):
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def read_clean(self, scope):
        editor = self.editors[scope]
        if self.dirty[scope]:
            return
        path = self.paths[scope]
        try:
            payload = self.read_payload(path)
            text = payload.decode("utf-8-sig") if payload is not None else ""
            if scope not in self.expected or self.expected[scope] != payload:
                self._loading.add(scope)
                try:
                    with QSignalBlocker(editor):
                        editor.setPlainText(text)
                        editor.document().setModified(False)
                finally:
                    self._loading.discard(scope)
            self.expected[scope] = payload
            self._observed_text[scope] = editor.toPlainText()
            self.load_state[scope] = "new" if payload is None else "loaded"
            self.dirty[scope] = False
            self.errors.pop(scope, None)
            editor.setReadOnly(False)
        except (OSError, UnicodeError) as error:
            self.load_state[scope] = "failed"
            self.expected.pop(scope, None)
            self.errors[scope] = QCoreApplication.translate("glossary_text", "{0}: не удалось прочитать {1}\n{2}").format(scope, path, error)
            editor.setReadOnly(True)
        self.presentation_changed.emit()

    def show_errors(self):
        self.notice.setText("\n".join(self.errors.values()))
        self.notice.setVisible(bool(self.errors))
        self.presentation_changed.emit()

    def save_entry(self, *, show_error=True, scopes=None):
        if self._close_accepted:
            return True  # The user already resolved Glossary for this close attempt.
        successful = True
        for scope in (self.editors if scopes is None else scopes):
            self.autosave_timers[scope].stop()
            editor = self.editors[scope]
            if not self.dirty[scope]:
                continue
            path = self.paths[scope]
            try:
                if self.load_state[scope] not in ("loaded", "new") or scope not in self.expected:
                    raise OSError(QCoreApplication.translate("glossary_text", "Исходный документ не был прочитан; перезапись запрещена."))
                disk = self.read_payload(path)
                if disk != self.expected[scope]:
                    raise OSError(QCoreApplication.translate("glossary_text", "Файл изменён другой программой. Черновик сохранён в редакторе; внешние изменения не перезаписаны."))
                payload = editor.toPlainText().encode("utf-8")
                if payload != disk:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    output = QSaveFile(str(path))
                    output.setDirectWriteFallback(False)
                    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                        raise OSError(output.errorString())
                    if output.write(payload) != len(payload):
                        output.cancelWriting()
                        raise OSError(output.errorString())
                    if not output.commit():
                        raise OSError(output.errorString())
                self.expected[scope] = payload
                self.load_state[scope] = "loaded"
                self.dirty[scope] = False
                editor.document().setModified(False)
                self.errors.pop(scope, None)
            except (OSError, ValueError) as error:
                successful = False
                self.errors[scope] = QCoreApplication.translate("glossary_text", "{0}: не удалось сохранить {1}\n{2}").format(scope, path, error)
        self.show_errors()
        if not successful and show_error:
            QMessageBox.warning(self, QCoreApplication.translate("glossary_text", "Глоссарий не сохранён"), self.notice.text())
        return successful

    def resolve_draft(self):
        return self.save_entry()

    def prepare_close(self):
        if self._close_accepted:
            return True
        if self._closing:
            return False
        self._closing = True
        for timer in self.autosave_timers.values():
            timer.stop()
        self.find.timer.stop()
        if self.save_entry(show_error=False):
            self._close_accepted = True
            return True
        box = QMessageBox(self)
        box.setWindowTitle(QCoreApplication.translate("glossary_text", "Глоссарий не сохранён"))
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(QCoreApplication.translate("glossary_text", "Не удалось сохранить изменения Glossary. Несохранённый текст остаётся в редакторе."))
        box.setInformativeText(QCoreApplication.translate("glossary_text", "Можно сохранить несохранённый текст в отдельные копии или закрыть окно без его сохранения. При закрытии без сохранения этот текст будет потерян."))
        box.setDetailedText(self.notice.text())
        backup = box.addButton(QCoreApplication.translate("glossary_text", "Сохранить копии…"), QMessageBox.ButtonRole.ActionRole)
        discard = box.addButton(QCoreApplication.translate("glossary_text", "Закрыть без сохранения"), QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QCoreApplication.translate("glossary_text", "Отмена"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(backup)
        box.setEscapeButton(cancel)
        box.exec()
        chosen = box.clickedButton()
        accepted = chosen is discard or (chosen is backup and self.save_recovery_copies())
        box.deleteLater()
        if accepted:
            self._close_accepted = True
            return True
        self.cancel_close()
        return False

    def save_recovery_copies(self):
        used = set()
        for scope, editor in self.editors.items():
            if not self.dirty[scope]:
                continue
            filename, _ = QFileDialog.getSaveFileName(
                self, QCoreApplication.translate("glossary_text", "Сохранить копию Glossary · {0}").format(scope),
                str(self.paths[scope].parent / f"glossary-{scope}-recovery.txt"), QCoreApplication.translate("glossary_text", "Текст (*.txt)"))
            if not filename:
                return False
            try:
                path = Path(filename).resolve()
                if path in used or any(path == original.resolve() for original in self.paths.values()):
                    raise OSError(QCoreApplication.translate("glossary_text", "Для копии выберите отдельный файл, отличный от исходных Glossary и других копий."))
                output = QSaveFile(str(path))
                output.setDirectWriteFallback(False)
                if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                    raise OSError(output.errorString())
                payload = editor.toPlainText().encode("utf-8")
                if output.write(payload) != len(payload):
                    output.cancelWriting()
                    raise OSError(output.errorString())
                if not output.commit():
                    raise OSError(output.errorString())
                used.add(path)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, QCoreApplication.translate("glossary_text", "Копия не сохранена"), str(error))
                return False
        return True

    def cancel_close(self):
        self._closing = self._close_accepted = False
        for scope, timer in self.autosave_timers.items():
            if self.dirty[scope] and self.load_state[scope] in ("loaded", "new"):
                timer.start()

    def switch_scope(self, scope, *, focus=True):
        # A failed document retains its draft; it must not lock out the other one.
        self.save_entry(scopes=(self.scope,), show_error=False)
        self.current_editor().setExtraSelections([])
        self.scope = scope
        self.read_clean(scope)
        self.stack.setCurrentWidget(self.current_editor())
        self.buttons[scope].setChecked(True)
        self.find.attach()
        if focus:
            self.current_editor().setFocus()
        self.show_errors()
        return True

    def showEvent(self, event):
        super().showEvent(event)
        for scope in self.editors:
            self.read_clean(scope)
        self.show_errors()
        self.current_editor().setFocus()

    def search_text(self, query, *, limit=None, case_sensitive=False):
        if len(query) >= 2 and query[0] == query[-1] == '"':
            query = query[1:-1]
        if not query:
            return []
        results = []
        for scope, editor in self.editors.items():
            self.read_clean(scope)
            text = editor.toPlainText()
            for number, match in enumerate(re.finditer(re.escape(query), text, 0 if case_sensitive else re.IGNORECASE)):
                if limit is None and number >= 150:
                    break
                start, end = match.span()
                left = max(text.rfind("\n", 0, start) + 1, start - 55)
                right = text.find("\n", end)
                right = min(len(text) if right < 0 else right, end + 75)
                results.append({"source": "glossary", "scope": scope, "start": start, "end": end,
                                "query": query, "case_sensitive": case_sensitive,
                                "match": match.group(), "snippet": text[left:right].replace("\n", " ")})
                if limit is not None and len(results) >= limit:
                    self.show_errors()
                    return results
        self.show_errors()
        return results

    def open_match(self, result):
        if not self.switch_scope(result["scope"]):
            return
        self.find.close_find()
        editor = self.current_editor()
        text = editor.toPlainText()
        start, end = result["start"], result["end"]
        if text[start:end] != result["match"]:
            matches = list(re.finditer(re.escape(result["query"]), text,
                                      0 if result.get("case_sensitive", False) else re.IGNORECASE))
            if not matches:
                self.notice.setText(QCoreApplication.translate("glossary_text", "Совпадение больше не найдено. Повторите поиск."))
                self.notice.show()
                return
            start, end = min(matches, key=lambda match: abs(match.start() - start)).span()
        cursor = QTextCursor(editor.document())
        cursor.setPosition(len(text[:start].encode("utf-16-le")) // 2)
        cursor.setPosition(len(text[:end].encode("utf-16-le")) // 2, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        editor.centerCursor()
        editor.setFocus()


class TextGlossaryMirror(QWidget):
    """Lower-panel views of the notebook's documents; no IO or autosave here."""

    def __init__(self, notebook, parent=None):
        super().__init__(parent)
        self.notebook = notebook
        self.editors = {}
        self.buttons = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        toolbar = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.stack = QStackedWidget()
        for scope, source in notebook.editors.items():
            button = QToolButton()
            button.setText(notebook.buttons[scope].text())
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setStyleSheet(notebook.buttons[scope].styleSheet())
            button.clicked.connect(lambda checked=False, scope=scope: self.switch_scope(scope))
            self.group.addButton(button)
            toolbar.addWidget(button)
            self.buttons[scope] = button
            editor = NotebookEdit(document=source.document())
            editor.setFont(source.font())
            editor.setStyleSheet(source.styleSheet())
            editor.setTabStopDistance(source.tabStopDistance())
            self.editors[scope] = editor
            self.stack.addWidget(editor)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        layout.addWidget(self.stack, 1)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setProperty("role", "muted")
        layout.addWidget(self.notice)
        notebook.presentation_changed.connect(self.sync_presentation)
        self.sync_presentation()

    def sync_presentation(self):
        scope = self.notebook.scope
        self.stack.setCurrentWidget(self.editors[scope])
        self.buttons[scope].setChecked(True)
        for key, editor in self.editors.items():
            editor.setReadOnly(self.notebook.editors[key].isReadOnly())
        self.notice.setText("\n".join(self.notebook.errors.values()))
        self.notice.setVisible(bool(self.notebook.errors))

    def switch_scope(self, scope):
        self.notebook.switch_scope(scope, focus=False)
        self.editors[scope].setFocus()
