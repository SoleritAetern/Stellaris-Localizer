"""One reusable EN selection control and an on-demand diff panel per view."""

from PySide6.QtCore import QCoreApplication

from PySide6.QtCore import QEvent, QPointF, QPersistentModelIndex, Qt
from PySide6.QtGui import QFont, QKeySequence, QMouseEvent, QPalette, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextOption
from PySide6.QtWidgets import QApplication, QFrame, QPlainTextEdit, QTextBrowser, QVBoxLayout

from src.text_selection import TokenSelectionMixin
from src.whitespace_markers import BoundaryWhitespaceMixin
from src.text_context_menu import standard_text_menu
from src.search_selection import SearchSelectionCapture
from src import theme


def forward_mouse(event, source, target):
    point = target.mapFromGlobal(source.mapToGlobal(event.position().toPoint()))
    forwarded = QMouseEvent(event.type(), QPointF(point), event.globalPosition(),
                            event.button(), event.buttons(), event.modifiers())
    QApplication.sendEvent(target, forwarded)


class SelectionHighlighter(QSyntaxHighlighter):
    def __init__(self, document, syntax_ranges, *, heading_format=None):
        super().__init__(document)
        self.syntax_ranges = syntax_ranges
        self.heading_format = heading_format

    def highlightBlock(self, text):
        if self.heading_format is not None and text.startswith("# "):
            self.setFormat(0, len(text.encode("utf-16-le")) // 2, self.heading_format)
            return
        for item in self.syntax_ranges(text):
            start = len(text[:item.start].encode("utf-16-le")) // 2
            length = len(text[item.start:item.start + item.length].encode("utf-16-le")) // 2
            self.setFormat(start, length, item.format)


class EnglishSelectionOverlay(BoundaryWhitespaceMixin, TokenSelectionMixin, QPlainTextEdit):
    def __init__(self, view):
        super().__init__(view.viewport())
        self.view = view
        self.search_selection_capture = SearchSelectionCapture(self)
        self.index = QPersistentModelIndex()
        self.dragging = False
        self.viewport().setMouseTracking(True)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setUndoRedoEnabled(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # AlwaysOff only hides the bars: Qt still permits selection auto-scroll
        # (including a spare visual line at the bottom of an exact-fit document).
        # This overlay uses the painted EN rectangle, not a scrollable viewport.
        # Pin the ranges synchronously whenever Qt recalculates them, so both
        # drag-selection and cursor ensure-visible keep the document at (0, 0).
        self.verticalScrollBar().rangeChanged.connect(self._pin_scroll_ranges)
        self.horizontalScrollBar().rangeChanged.connect(self._pin_scroll_ranges)
        self._pin_scroll_ranges()
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(0)
        self.highlighter = SelectionHighlighter(self.document(), view.itemDelegate().syntax_ranges)
        view.occurrences.attach(self)
        if getattr(view, "local_find", None) is not None:
            view.local_find.attach_editor(self)
        view.viewport().installEventFilter(self)
        view.verticalScrollBar().valueChanged.connect(self.dismiss_for_scroll)
        self.hide()

    def _pin_scroll_ranges(self, *_):
        for bar in (self.verticalScrollBar(), self.horizontalScrollBar()):
            if bar.minimum() != 0 or bar.maximum() != 0:
                bar.setRange(0, 0)

    def open(self, index, event):
        delegate = self.view.itemDelegate()
        option = self.view._option(index)
        layout = delegate.row_layout(option, index)
        row = index.data(Qt.ItemDataRole.UserRole)
        self.index = QPersistentModelIndex(index)
        self.setFont(delegate.body_font(option))
        palette = QPalette(option.palette)
        if self.view.selectionModel().isSelected(index):
            palette.setColor(QPalette.ColorRole.Base, palette.highlight().color())
            palette.setColor(QPalette.ColorRole.Text, palette.highlightedText().color())
        self.setPalette(palette)
        self.setStyleSheet(f"QPlainTextEdit {{ background:{palette.base().color().name()}; "
                          f"color:{palette.text().color().name()}; border:0; padding:0; margin:0; "
                          f"selection-background-color:{theme.css('selection.en.bg')}; }}")
        self.setGeometry(layout.source.translated(option.rect.topLeft()))
        self.setPlainText(row["new_text"] if row.get("new_text") is not None else "")
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if not self.index.isValid():
            self.dismiss()
            return
        forward_mouse(event, self.view.viewport(), self.viewport())
        self.dragging = event.type() == QEvent.Type.MouseButtonPress
        if self.dragging:
            self.viewport().grabMouse()

    def dismiss_for_scroll(self, *args):
        self.view.occurrences.retain_for_scroll(self)
        self.dismiss()

    def dismiss(self, *args):
        if self.dragging:
            self.viewport().releaseMouse()
        self.dragging = False
        self.index = QPersistentModelIndex()
        self.hide()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.dragging:
            self.viewport().releaseMouse()
            self.dragging = False

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.dismiss()

    def wheelEvent(self, event):
        # Wheel scrolling belongs to the localization list, not to an invisible
        # scrollbar inside the temporary read-only field.
        self.dismiss_for_scroll()
        QApplication.sendEvent(self.view.viewport(), event)

    def contextMenuEvent(self, event):
        menu = standard_text_menu(self)
        try:
            menu.exec(event.globalPos())
        finally:
            menu.deleteLater()

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and (
                event.key() == Qt.Key.Key_Escape or event.matches(QKeySequence.StandardKey.Copy)
                or event.matches(QKeySequence.StandardKey.SelectAll)):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
            self.view.setFocus()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel:
            self.dismiss_for_scroll()
        elif event.type() in (QEvent.Type.Resize, QEvent.Type.Hide):
            self.dismiss()
        return super().eventFilter(watched, event)


class DetailedDiffPopover(QFrame):
    def __init__(self, view):
        super().__init__(view.window())
        self.view = view
        self.index = QPersistentModelIndex()
        self.setObjectName("detailedDiffPopover")
        self.setStyleSheet(f"QFrame#detailedDiffPopover {{ background:{theme.css('surface.popover')}; "
                          f"border:1px solid {theme.css('border.subtle')}; border-radius:8px; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.text = QTextBrowser()
        self.text.setOpenLinks(False)
        self.text.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.text)
        self.hide()
        QApplication.instance().installEventFilter(self)
        view.verticalScrollBar().valueChanged.connect(self.hide)

    def _append_version(self, cursor, title, segments, excluded, body_font):
        heading = QTextCharFormat()
        heading.setFontWeight(QFont.Weight.Bold)
        cursor.insertBlock()
        cursor.insertText(title, heading)
        cursor.insertBlock()
        body = QTextCharFormat()
        body.setFont(body_font)
        cursor.setCharFormat(body)
        start = cursor.position()
        parts = [part for part in segments if part["type"] != excluded]
        text = "".join(part["text"] for part in parts)
        cursor.insertText(text, body)

        # QTextCursor uses UTF-16 offsets; diff and syntax ranges use Python
        # character offsets. Build the mapping once for this side of the diff.
        offsets = [0]
        for char in text:
            offsets.append(offsets[-1] + (2 if ord(char) > 0xffff else 1))

        def apply_format(begin, end, char_format):
            selection = QTextCursor(cursor.document())
            selection.setPosition(start + offsets[begin])
            selection.setPosition(start + offsets[end], QTextCursor.MoveMode.KeepAnchor)
            selection.mergeCharFormat(char_format)

        for item in self.view.itemDelegate().syntax_ranges(text):
            apply_format(item.start, item.start + item.length, item.format)
        position = 0
        for part in parts:
            end = position + len(part["text"])
            if part["type"] in ("removed", "added"):
                changed = QTextCharFormat()
                removed = part["type"] == "removed"
                changed.setForeground(theme.color("diff.detail.removed" if removed else "diff.detail.added"))
                changed.setFontStrikeOut(removed)
                apply_format(position, end, changed)
            position = end
        cursor.insertBlock()
        cursor.setCharFormat(QTextCharFormat())

    def toggle(self, index):
        if self.isVisible() and self.index == index:
            self.hide()
        else:
            self.open(index)

    def hideEvent(self, event):
        self.index = QPersistentModelIndex()
        super().hideEvent(event)

    def open(self, index):
        self.index = QPersistentModelIndex(index)
        row = index.data(Qt.ItemDataRole.UserRole)
        option = self.view._option(index)
        self.text.clear()
        cursor = QTextCursor(self.text.document())
        heading = QTextCharFormat()
        heading.setFontWeight(QFont.Weight.Bold)
        cursor.insertText(row["key"], heading)
        body_font = self.view.itemDelegate().body_font(option)
        self._append_version(cursor, QCoreApplication.translate("text_overlays", "Предыдущий EN"), row["source_diff"], "added", body_font)
        self._append_version(cursor, QCoreApplication.translate("text_overlays", "Текущий EN"), row["source_diff"], "removed", body_font)
        self.text.moveCursor(QTextCursor.MoveOperation.Start)
        rect = self.view.itemDelegate().row_layout(option, index).actions["diff"].translated(option.rect.topLeft())
        parent = self.parentWidget()
        anchor = parent.mapFromGlobal(self.view.viewport().mapToGlobal(rect.bottomRight()))
        width, height = min(500, parent.width() - 16), min(380, parent.height() - 16)
        x = anchor.x() if anchor.x() + width <= parent.width() - 8 else anchor.x() - width
        y = anchor.y() + 4 if anchor.y() + height + 4 <= parent.height() - 8 else anchor.y() - rect.height() - height
        self.setGeometry(max(8, min(x, parent.width() - width - 8)),
                         max(8, min(y, parent.height() - height - 8)), width, height)
        self.show()
        self.raise_()
        self.text.setFocus()

    def eventFilter(self, watched, event):
        if self.isVisible():
            if event.type() == QEvent.Type.MouseButtonPress:
                if watched is self.view.viewport() and event.button() == Qt.MouseButton.LeftButton:
                    _, action = self.view._action_at(event.position().toPoint())
                    if action == "diff":
                        # The view owns this press/release gesture. Keep the
                        # current target until its row action toggles/switches it.
                        return False
                if not self.rect().contains(self.mapFromGlobal(event.globalPosition().toPoint())):
                    self.hide()
            elif event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.hide()
                return True
            elif watched in (self.parentWidget(), self.view.viewport()) and event.type() in (QEvent.Type.Resize, QEvent.Type.Hide):
                self.hide()
        return super().eventFilter(watched, event)
