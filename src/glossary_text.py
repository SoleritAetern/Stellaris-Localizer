"""Free-text glossary documents. Structured glossary JSON is never touched."""

from PySide6.QtCore import QCoreApplication

from pathlib import Path
import re

from PySide6.QtCore import QEvent, QIODevice, QSaveFile, QStandardPaths, Qt, QTimer, QSignalBlocker, QPointF, QRect, QSize, Signal
from PySide6.QtGui import QFont, QKeySequence, QTextCursor, QTextCharFormat, QPainter, QTextFormat, QTextOption
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextDocumentLayout, QPlainTextEdit, QStackedWidget, QTextEdit, QToolButton, QVBoxLayout, QWidget, QFileDialog,
)

from . import theme
from .text_overlays import SelectionHighlighter
from .search_selection import SearchSelectionCapture
from .local_find_text import GlossaryFindTarget


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
        self._anchor = None
        self._selection = None
        self._dragging = False
        self._drag_y = 0
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(50)
        self._scroll_timer.timeout.connect(self._scroll_drag)

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_line_numbers(event)

    def _block_at(self, y):
        # Use the same logical blocks and wrapped heights as the painted gutter.
        y = max(0, min(y, self.height() - 1))
        block = self.editor.firstVisibleBlock()
        top = self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top()
        while block.isValid():
            height = self.editor.blockBoundingRect(block).height()
            if block.isVisible() and (y < top + height or not block.next().isValid()):
                return block
            top += height
            following = block.next()
            if not following.isValid():
                return block
            block = following
        return block

    def _select_block(self, block):
        if self._anchor is None or not block.isValid():
            return
        anchor = self._anchor.block()
        if not anchor.isValid():
            return

        def end_of_line(item):
            following = item.next()
            return following.position() if following.isValid() else self.editor.document().characterCount() - 1

        cursor = QTextCursor(self.editor.document())
        if block.position() < anchor.position():
            cursor.setPosition(end_of_line(anchor))
            cursor.setPosition(block.position(), QTextCursor.MoveMode.KeepAnchor)
        else:
            cursor.setPosition(anchor.position())
            cursor.setPosition(end_of_line(block), QTextCursor.MoveMode.KeepAnchor)
        # Selecting a wrapped line includes its offscreen end. Keep the pointer's
        # visual row stable; only the edge-drag timer should scroll the viewport.
        bar = self.editor.verticalScrollBar()
        scroll = bar.value()
        self.editor.setTextCursor(cursor)
        bar.setValue(scroll)
        self._selection = (cursor.anchor(), cursor.position())

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        block = self._block_at(event.position().y())
        if not block.isValid():
            return
        cursor = self.editor.textCursor()
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            if self._anchor is None or self._selection != (cursor.anchor(), cursor.position()):
                self._anchor = QTextCursor(self.editor.document())
                self._anchor.setPosition(cursor.anchor())
        else:
            self._anchor = QTextCursor(block)
        self.editor.setFocus(Qt.FocusReason.MouseFocusReason)
        self._dragging = True
        self._drag_y = event.position().y()
        self._select_block(block)
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            super().mouseMoveEvent(event)
            return
        self._drag_y = event.position().y()
        self._select_block(self._block_at(self._drag_y))
        if self._drag_y < 0 or self._drag_y >= self.height():
            if not self._scroll_timer.isActive():
                self._scroll_timer.start()
        else:
            self._scroll_timer.stop()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._select_block(self._block_at(event.position().y()))
            self._dragging = False
            self._scroll_timer.stop()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def _scroll_drag(self):
        if not self._dragging or not QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            self._dragging = False
            self._scroll_timer.stop()
            return
        distance = self._drag_y if self._drag_y < 0 else self._drag_y - self.height() + 1
        step = min(5, 1 + int(abs(distance) / 20))
        bar = self.editor.verticalScrollBar()
        bar.setValue(bar.value() + (-step if distance < 0 else step))
        self._select_block(self._block_at(self._drag_y))

    def hideEvent(self, event):
        self._dragging = False
        self._scroll_timer.stop()
        super().hideEvent(event)


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

    def center_range(self, start, end):
        """Scroll to a range using temporary cursors, never the editor's caret."""
        first, last = QTextCursor(self.document()), QTextCursor(self.document())
        first.setPosition(start)
        last.setPosition(max(start, end - 1))
        bar = self.verticalScrollBar()
        block = first.block()
        self.blockBoundingRect(block)
        layout = block.layout()
        number = layout.lineForTextPosition(first.positionInBlock()).lineNumber()
        if last.block() == block:
            number = (number + layout.lineForTextPosition(last.positionInBlock()).lineNumber()) // 2
        remaining = self.viewport().height() / 2 - layout.lineAt(number).height() / 2
        # Shape only the target and the preceding half-viewport. Distant wrapped
        # blocks may still have estimated line counts; pixel-delta scrolling
        # alone would walk through them one block at a time.
        while remaining > 0:
            previous, previous_number = block, number - 1
            if previous_number < 0:
                previous = block.previous()
                if not previous.isValid():
                    break
                self.blockBoundingRect(previous)
                previous_number = previous.layout().lineCount() - 1
            height = previous.layout().lineAt(previous_number).height() * NotebookDocumentLayout.LINE_SPACING
            if remaining < height / 2:
                break
            remaining -= height
            block, number = previous, previous_number
        bar.setValue(block.firstLineNumber() + number)
        # QPlainTextEdit's scrollbar uses visual lines rather than pixels.
        # Recheck after scrolling so wrapped blocks can finish their layout.
        for _ in range(8):
            block = first.block()
            self.blockBoundingRect(block)
            line = block.layout().lineForTextPosition(first.positionInBlock())
            line_height = max(1, line.height() * NotebookDocumentLayout.LINE_SPACING)
            y = (self.cursorRect(first).center().y() + self.cursorRect(last).center().y()) / 2
            steps = round((y - self.viewport().height() / 2) / line_height)
            old = bar.value()
            bar.setValue(old + steps)
            if bar.value() == old:
                break

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


class TextGlossaryPage(QWidget):
    presentation_changed = Signal()

    def __init__(self, project_root, parent=None, *, syntax_ranges=None, find_controller=None):
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
        self.find = find_controller
        self.find_target = GlossaryFindTarget(self, self.find) if self.find is not None else None
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
        if self.find_target is not None:
            self.find_target.attach()
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
        if self.find is not None:
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
