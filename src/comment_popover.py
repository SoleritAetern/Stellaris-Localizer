"""One reusable comment overlay per view; no per-row widgets or layout changes."""

from PySide6.QtCore import QCoreApplication

import re

from PySide6.QtCore import QAbstractProxyModel, QEvent, QModelIndex, QPersistentModelIndex, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QFontMetrics, QKeySequence, QTextOption

from . import theme
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QLayout, QPlainTextEdit, QPushButton, QVBoxLayout


def single_line(text):
    return re.sub(r'[\r\n\u2028\u2029]+', ' ', text)


class CommentTextEdit(QPlainTextEdit):
    accepted = Signal()
    cancelled = Signal()

    def insertFromMimeData(self, source):
        self.insertPlainText(single_line(source.text()))

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape)
                    or any(event.matches(key) for key in (
                        QKeySequence.StandardKey.Copy, QKeySequence.StandardKey.Cut,
                        QKeySequence.StandardKey.Paste, QKeySequence.StandardKey.SelectAll,
                        QKeySequence.StandardKey.Undo, QKeySequence.StandardKey.Redo))):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accepted.emit()  # Shift+Enter also saves; never inserts a newline.
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)


class CommentPopover(QFrame):
    def __init__(self, view, working_model):
        # A child overlay, not a top-level window: no taskbar entry or modality.
        super().__init__(view.window())
        self.view, self.working_model = view, working_model
        self.display_index = QPersistentModelIndex()
        self.source_index = QPersistentModelIndex()
        self.pending_index = QPersistentModelIndex()
        self.action = "comment"
        self.pending_action = "comment"
        self.anchor_rect = QRect()
        self.pinned = self.editable = False
        self.original = None
        self._finishing = False
        self._loading = False
        self.setObjectName("commentPopover")
        self.setStyleSheet(f"""
            QFrame#commentPopover {{
                background: {theme.css('surface.comment_popover')};
                border: 1px solid {theme.css('border.subtle')};
                border-radius: 8px;
            }}
            QFrame#commentPopover QLabel {{ background: transparent; border: none; }}
            QFrame#commentPopover QPlainTextEdit {{ border-radius: 4px; padding: 4px; }}
        """)
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        self.author_label = QLabel(QCoreApplication.translate("comment_popover", "Комментарий автора"), self)
        self.author = CommentTextEdit(self)
        self.author.setReadOnly(True)
        self.translation_label = QLabel(QCoreApplication.translate("comment_popover", "Комментарий перевода"), self)
        self.translation = CommentTextEdit(self)
        self.translation.setPlaceholderText(QCoreApplication.translate("comment_popover", "Добавить комментарий…"))
        for editor in (self.author, self.translation):
            editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            editor.setTabChangesFocus(True)
            editor.cancelled.connect(self.cancel)
            editor.accepted.connect(self.accept)
        for widget in (self.author_label, self.author, self.translation_label, self.translation):
            layout.addWidget(widget)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.ok = QPushButton(QCoreApplication.translate("comment_popover", "ОК"), self)
        self.ok.setFixedWidth(64)
        self.ok.clicked.connect(self.accept)
        buttons.addWidget(self.ok)
        layout.addLayout(buttons)
        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.setInterval(350)
        self.hover_timer.timeout.connect(self._show_preview)
        self.exit_timer = QTimer(self)
        self.exit_timer.setSingleShot(True)
        self.exit_timer.setInterval(180)
        self.exit_timer.timeout.connect(self._leave_preview)
        self.pointer_timer = QTimer(self)
        self.pointer_timer.setInterval(100)
        self.pointer_timer.timeout.connect(self._check_pointer)
        self.geometry_timer = QTimer(self)
        self.geometry_timer.setSingleShot(True)
        self.geometry_timer.timeout.connect(self.reposition)
        self.translation.textChanged.connect(self.schedule_geometry)
        self.view.verticalScrollBar().valueChanged.connect(self.schedule_geometry)
        self.view.verticalScrollBar().rangeChanged.connect(self.schedule_geometry)
        working_model.dataChanged.connect(self._model_changed)
        QApplication.instance().installEventFilter(self)
        self.hide()

    def hover(self, index, action):
        if self.pinned:
            return
        self.hover_timer.stop()
        if action != "comment" and not action.startswith("inline_comment_"):
            self.pending_index = QPersistentModelIndex()
            return
        row = index.data(Qt.ItemDataRole.UserRole) if index.isValid() else None
        if not row or (row.get("new_comment") is None and row.get("ru_comment") is None):
            return
        if self.isVisible() and self.display_index == index and self.action == action:
            self.exit_timer.stop()
        else:
            if self.isVisible():
                self.finish()
            self.pending_index, self.pending_action = QPersistentModelIndex(index), action
            self.hover_timer.start()

    def _show_preview(self):
        index, action = self.view._action_at(self.view.viewport().mapFromGlobal(QCursor.pos()))
        if self.pending_index.isValid() and self.pending_index == index and self.pending_action == action:
            self.open(QModelIndex(self.pending_index), self.pending_action, edit=False)

    def open(self, index, action="comment", *, edit=True):
        if not index.isValid():
            return
        source = index
        while isinstance(source.model(), QAbstractProxyModel):
            source = source.model().mapToSource(source)
        source = QPersistentModelIndex(source)
        display = QPersistentModelIndex(index)
        if self.isVisible() and self.display_index == index and self.pinned:
            self.translation.setFocus()
            return
        self.finish()
        if edit:
            self.view.finish_editing()
        if not source.isValid() or not display.isValid():
            return
        row = source.data(Qt.ItemDataRole.UserRole)
        self.display_index, self.source_index, self.action = display, source, action
        self.pinned = edit
        self.editable = edit and source.model() is self.working_model
        self.original = row.get("ru_comment")
        self._loading = True
        self._set_author(row)
        self.translation.setPlainText(self.original or "")
        self.translation.setReadOnly(not self.editable)
        self.ok.setVisible(self.pinned)
        self._loading = False
        if not self.reposition(opening=True):
            return
        self.show()
        self.raise_()
        self.pointer_timer.start()
        if self.editable:
            self.translation.setFocus(Qt.FocusReason.OtherFocusReason)

    def _set_author(self, row):
        changed = "accepted_source_comment" in row and row.get("accepted_source_comment") != row.get("new_comment")
        self.author_label.setText(QCoreApplication.translate("comment_popover", "Комментарий автора") + (" *" if changed else ""))
        self.author_label.setVisible(row.get("new_comment") is not None or changed)
        self.author.setVisible(row.get("new_comment") is not None)
        self.author.setPlainText(row.get("new_comment") or "")
        self.author_label.setToolTip(QCoreApplication.translate("comment_popover", "Комментарий автора изменён после подтверждения") if changed else "")

    def _anchor(self):
        index = QModelIndex(self.display_index)
        if not index.isValid() or index.model() is not self.view.model() or not self.view.isVisible():
            return QRect()
        visible = self.view.viewport().rect()
        rect = self.view.visualRect(index)
        if not rect.intersects(visible):
            return QRect()
        # Exactly one visible row; this does not invalidate its layout or height.
        option = self.view._option(index)
        delegate = self.view.itemDelegate()
        if self.action.startswith("inline_comment_"):
            label = self.action.removeprefix("inline_comment_")
            candidates = [area for area, _, name in delegate.comment_rects(option, index) if name == label]
            anchor = candidates[0] if candidates else QRect()
        else:
            anchor = delegate.row_layout(option, index).actions["comment"].translated(rect.topLeft())
        if anchor.isEmpty():
            return QRect()
        # A tall partially visible entry may have its header above the viewport.
        anchor.moveTop(max(visible.top(), min(anchor.top(), visible.bottom() - anchor.height())))
        return QRect(self.view.viewport().mapToGlobal(anchor.topLeft()), anchor.size())

    def schedule_geometry(self, *args):
        if self.isVisible() and not self._loading and not self.geometry_timer.isActive():
            self.geometry_timer.start(0)

    def reposition(self, *, opening=False):
        if not opening and not self.isVisible():
            return False
        anchor = self._anchor()
        if anchor.isEmpty():
            self.finish()
            return False
        self.anchor_rect = anchor
        bounds = self.parentWidget().rect().adjusted(8, 8, -8, -8)
        width = min(400, max(1, bounds.width()))
        for editor, maximum in ((self.author, 120), (self.translation, 160)):
            metrics = QFontMetrics(editor.font())
            size = metrics.boundingRect(QRect(0, 0, max(1, width - 50), 10000),
                                        Qt.TextFlag.TextWordWrap, editor.toPlainText() or " ")
            editor.setFixedHeight(min(maximum, max(54, size.height() + 22)))
        self.layout().activate()
        height = min(380, self.sizeHint().height(), max(1, bounds.height()))
        self.resize(width, height)
        local = self.parentWidget().mapFromGlobal(anchor.bottomRight())
        left_top = self.parentWidget().mapFromGlobal(anchor.topLeft())
        x, y = local.x() + 6, local.y() + 6
        if x + width > bounds.right():
            x = left_top.x() - width - 6
        if y + height > bounds.bottom():
            y = left_top.y() - height - 6
        self.move(max(bounds.left(), min(x, bounds.right() - width + 1)),
                  max(bounds.top(), min(y, bounds.bottom() - height + 1)))
        return True

    def _pointer_inside(self):
        point = QCursor.pos()
        return self.rect().contains(self.mapFromGlobal(point)) or self.anchor_rect.contains(point)

    def _check_pointer(self):
        if not self.display_index.isValid() or not self.view.isVisible():
            self.finish()
        elif not self.pinned:
            if self._pointer_inside():
                self.exit_timer.stop()
            elif not self.exit_timer.isActive():
                self.exit_timer.start()

    def _leave_preview(self):
        if not self.pinned and not self._pointer_inside():
            self.finish()

    def _model_changed(self, top, bottom, roles):
        if (self.isVisible() and self.source_index.isValid() and not self._finishing
                and self.source_index.model() is self.working_model
                and top.row() <= self.source_index.row() <= bottom.row()):
            row = self.source_index.data(Qt.ItemDataRole.UserRole)
            self._set_author(row)
            if not self.editable:
                self.original = row.get("ru_comment")
                self.translation.setPlainText(self.original or "")
            self.schedule_geometry()

    def finish(self):
        self._dismiss(save=True)

    def accept(self):
        self.finish()
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def cancel(self):
        self._dismiss(save=False)
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _dismiss(self, *, save):
        if self._finishing:
            return
        self._finishing = True
        try:
            for timer in (self.hover_timer, self.exit_timer, self.pointer_timer, self.geometry_timer):
                timer.stop()
            if save and self.editable and self.source_index.isValid():
                value = single_line(self.translation.toPlainText())
                if value != (self.original or ""):
                    self.working_model.set_comment(QModelIndex(self.source_index), value if value.strip() else None)
            self.editable = self.pinned = False
            self.hide()
            self.display_index = QPersistentModelIndex()
            self.source_index = QPersistentModelIndex()
            self.anchor_rect = QRect()
        finally:
            self._finishing = False

    def eventFilter(self, watched, event):
        if not self.isVisible():
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and not self.rect().contains(self.mapFromGlobal(event.globalPosition().toPoint())):
            self.finish()
        elif kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.cancel()
            return True
        elif watched in (self.view, self.view.viewport(), self.parentWidget()):
            if kind in (QEvent.Type.Resize, QEvent.Type.Move):
                self.schedule_geometry()
            elif kind in (QEvent.Type.Hide, QEvent.Type.Close, QEvent.Type.WindowDeactivate):
                self.finish()
        return False
