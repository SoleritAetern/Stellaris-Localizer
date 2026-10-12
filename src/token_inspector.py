"""Exact references and one interactive hover inspector per workspace window."""

from PySide6.QtCore import QCoreApplication

from pathlib import Path
from dataclasses import dataclass
from weakref import ref

from PySide6.QtCore import QAbstractProxyModel, QEvent, QModelIndex, QObject, QPersistentModelIndex, QPointF, QRect, QSize, Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QCursor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QTextLine
from PySide6.QtWidgets import QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton, QTextBrowser, QToolButton, QToolTip, QVBoxLayout, QWidget
from shiboken6 import isValid

from . import theme
from .exact_references import ExactReferences
from .compact_lookup import LookupIndicator, source_label
from .text_selection import localization_tokens
from .icons import paint_check


def python_position(text, position):
    return len(text.encode("utf-16-le")[:position * 2].decode("utf-16-le", errors="ignore"))


class CompactReferenceText(QTextBrowser):
    """Plain compact text with Qt's exact wrapped-text cursor hit testing."""
    def __init__(self):
        super().__init__()
        self.value = ""
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        self.setMouseTracking(True)

    def setText(self, text):
        self.value = text
        self.setPlainText(text)
        self.verticalScrollBar().setValue(0)

    def text(self):
        return self.value

    def wheelEvent(self, event):
        event.ignore()


@dataclass
class TokenHit:
    view: object
    index: QPersistentModelIndex
    field: str
    text: str
    token: object
    anchor: QRect
    editor: object = None

    def identity(self):
        return (id(self.view), self.index, self.field, self.text, self.token)


class TokenInspector(QFrame):
    def __init__(self, window):
        super().__init__(window)
        self._disposed = False
        self._connections = []
        self._app_ref = None
        self._window_ref = ref(window)
        self.references = ExactReferences(window)
        self.views = []
        self.hit = None
        self.pending = None
        self.editing = False
        self.expanded = False
        self.expanding = False
        self._expanded_origin = None
        self.target = None
        self.compact_ru = None
        self.lookup_request = None
        self.loading = False
        self.can_edit_display = False
        self.copies = {}
        self.feedback_buttons = set()
        self.definitions = []
        self.nested_popups = []
        self.nested_pending = None
        self.chain_key = None
        self.editor_text = {}
        inspector_ref = ref(self)

        def owner_destroyed(*args):
            inspector = inspector_ref()
            if inspector is not None:
                inspector.dispose()

        self._connect(window, window.destroyed, owner_destroyed)
        self.setObjectName("tokenInspector")
        self.setStyleSheet(f"""
            QFrame#tokenInspector {{ background: {theme.css('surface.popover')}; border: 1px solid {theme.css('border.subtle')}; border-radius: 6px; }}
            QFrame#tokenInspector QTextBrowser {{ background: transparent; border: none; }}
            QFrame#tokenInspector QFrame#inspectorBlock {{ background: {theme.css('surface.popover')}; border: 1px solid {theme.css('border.subtle')}; border-radius: 5px; }}
            QFrame#tokenInspector QToolButton {{ background: transparent; border: none; border-radius: 3px; padding: 0; }}
            QFrame#tokenInspector QToolButton:hover {{ background: {theme.css('surface.hover')}; }}
            QFrame#tokenInspector QToolButton::menu-indicator {{ image: none; width: 0px; }}
        """)
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        self.compact = QFrame()
        compact_layout = QVBoxLayout(self.compact)
        compact_layout.setContentsMargins(0, 0, 0, 0)
        compact_layout.setSpacing(3)
        self.compact_text = CompactReferenceText()
        compact_layout.addWidget(self.compact_text, 1)
        self.compact_footer = QFrame()
        self.compact_footer.setFixedHeight(24)
        footer = QHBoxLayout(self.compact_footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(3)
        self.compact_source = source_label()
        footer.addWidget(self.compact_source)
        footer.addStretch(1)
        compact_layout.addWidget(self.compact_footer)
        self.copy_ru_button = QToolButton()
        self.copy_ru_button.setIcon(self.small_icon("copy"))
        self.copy_ru_button.setToolTip(QCoreApplication.translate("token_inspector", "Копировать RU"))
        self.copy_ru_button.setAccessibleName(QCoreApplication.translate("token_inspector", "Копировать RU"))
        self._connect(self.copy_ru_button, self.copy_ru_button.clicked, self.copy_compact_ru)
        self.expand_button = QToolButton()
        self.expand_button.setIcon(self.small_icon("expand"))
        self.expand_button.setToolTip(QCoreApplication.translate("token_inspector", "Показать подробности"))
        self.expand_button.setAccessibleName(QCoreApplication.translate("token_inspector", "Показать подробности"))
        self._connect(self.expand_button, self.expand_button.clicked, self.expand)
        for button in (self.copy_ru_button, self.expand_button):
            button.setFixedSize(24, 24)
            button.setIconSize(QSize(16, 16))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            footer.addWidget(button)
        layout.addWidget(self.compact)
        self.details = QFrame()
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(7)
        header = QHBoxLayout()
        header.setSpacing(4)
        self.key_label = QLabel()
        self.key_label.setTextFormat(Qt.TextFormat.PlainText)
        self.key_label.setMinimumWidth(0)
        header.addWidget(self.key_label, 1)
        self.key_copy = self.copy_button("key", QCoreApplication.translate("token_inspector", "Скопировать ключ"))
        header.addWidget(self.key_copy)
        self.file_label = QLabel()
        self.file_label.setTextFormat(Qt.TextFormat.PlainText)
        self.file_label.setMinimumWidth(0)
        self.file_label.setStyleSheet(f"color: {theme.css('text.secondary')};")
        header.addWidget(self.file_label, 1)
        details_layout.addLayout(header)
        self.definition_choice = QComboBox()
        self.definition_choice.setToolTip(QCoreApplication.translate("token_inspector", "Найдено несколько точных определений"))
        self._connect(self.definition_choice, self.definition_choice.currentIndexChanged, self.show_definition)
        details_layout.addWidget(self.definition_choice)
        self.detail_note = QLabel()
        self.detail_note.setWordWrap(True)
        self.detail_note.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_note.setStyleSheet(f"color: {theme.css('text.secondary')};")
        details_layout.addWidget(self.detail_note)
        self.text_blocks = {}
        for name, title in (("en", "EN"), ("ru", "RU"), ("display", QCoreApplication.translate("token_inspector", "Дополнительный текст"))):
            frame = QFrame()
            frame.setObjectName("inspectorBlock")
            block_layout = QVBoxLayout(frame)
            block_layout.setContentsMargins(7, 4, 7, 5)
            block_layout.setSpacing(2)
            block_header = QHBoxLayout()
            label = QLabel(title)
            label.setStyleSheet(f"color: {theme.css('text.secondary')}; border: none;")
            block_header.addWidget(label)
            block_header.addStretch(1)
            button = self.copy_button(name, QCoreApplication.translate("token_inspector", "Скопировать {0}").format(title))
            block_header.addWidget(button)
            block_layout.addLayout(block_header)
            text = QTextBrowser()
            text.setReadOnly(True)
            text.setOpenLinks(False)
            text.setOpenExternalLinks(False)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            text.document().setDocumentMargin(0)
            block_layout.addWidget(text)
            self.text_blocks[name] = (frame, text, button)
            details_layout.addWidget(frame)
        self.details.hide()
        layout.addWidget(self.details)
        self.detail_actions = QFrame()
        actions_layout = QHBoxLayout(self.detail_actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(5)
        actions_layout.addStretch(1)
        self.go_button = QToolButton()
        self.go_button.setIcon(self.small_icon("navigate"))
        self.go_button.setToolTip(QCoreApplication.translate("token_inspector", "Перейти к строке"))
        self.go_button.setAccessibleName(self.go_button.toolTip())
        self.go_button.setFixedSize(26, 26)
        self._connect(self.go_button, self.go_button.clicked, self.go_to_entry)
        actions_layout.addWidget(self.go_button)
        self.panel_button = QToolButton()
        self.panel_button.setIcon(window.open_right_button.icon())
        self.panel_button.setToolTip(QCoreApplication.translate("token_inspector", "Открыть в нижней панели"))
        self.panel_button.setAccessibleName(self.panel_button.toolTip())
        self.panel_button.setFixedSize(26, 26)
        self._connect(self.panel_button, self.panel_button.clicked, self.open_in_panel)
        actions_layout.addWidget(self.panel_button)
        self.detail_actions.hide()
        layout.addWidget(self.detail_actions)
        self.edit_button = QPushButton(QCoreApplication.translate("token_inspector", "Задать отображаемый текст…"))
        self._connect(self.edit_button, self.edit_button.clicked, self.begin_display_edit)
        layout.addWidget(self.edit_button)
        self.edit_row = QFrame()
        edit_layout = QHBoxLayout(self.edit_row)
        edit_layout.setContentsMargins(0, 0, 0, 0)
        self.display_input = QLineEdit()
        self.display_input.setPlaceholderText(QCoreApplication.translate("token_inspector", "Отображаемый текст"))
        edit_layout.addWidget(self.display_input)
        save = QPushButton(QCoreApplication.translate("token_inspector", "ОК"))
        self._connect(save, save.clicked, self.save_display)
        self._connect(self.display_input, self.display_input.returnPressed, self.save_display)
        edit_layout.addWidget(save)
        layout.addWidget(self.edit_row)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.feedback_timer = QTimer(self)
        self.feedback_timer.setSingleShot(True)
        self.feedback_timer.setInterval(650)
        self._connect(self.feedback_timer, self.feedback_timer.timeout, self.reset_copy_feedback)
        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.setInterval(0)
        self._connect(self.delay, self.delay.timeout, self.open_pending)
        self.nested_delay = QTimer(self)
        self.nested_delay.setSingleShot(True)
        self.nested_delay.setInterval(self.delay.interval())
        self._connect(self.nested_delay, self.nested_delay.timeout, self.open_nested_pending)
        self.leave = QTimer(self)
        self.leave.setSingleShot(True)
        self.leave.setInterval(200)
        self._connect(self.leave, self.leave.timeout, self.close_if_outside)
        self.pointer = QTimer(self)
        self.pointer.setInterval(120)
        self._connect(self.pointer, self.pointer.timeout, self.check_pointer)
        self.expand_animation = QPropertyAnimation(self, b"geometry", self)
        self.expand_animation.setDuration(150)
        self.expand_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._connect(self.expand_animation, self.expand_animation.finished, self.expansion_finished)
        self._connect(window.model, window.model.dataChanged, self.working_text_changed)
        self.loading_indicator = LookupIndicator(self)
        self.hide()
        # All handler state exists before subscribing to application events.
        app = QApplication.instance()
        self._app_ref = ref(app)
        app.installEventFilter(self)

    @property
    def window(self):
        window_ref = getattr(self, "_window_ref", None)
        return window_ref() if window_ref is not None else None

    def _connect(self, sender, signal, callback):
        # The connection handle neither owns the sender nor depends on its
        # Python wrapper surviving (e.g. the locally created OK button).
        self._connections.append(signal.connect(callback))

    def _alive(self):
        if getattr(self, "_disposed", True) or not isValid(self):
            return False
        window = self.window
        if window is None or not isValid(window):
            self.dispose()
            return False
        return True

    def dispose(self):
        if getattr(self, "_disposed", True):
            return
        # Mark first: hide/disconnect may synchronously deliver more Qt events.
        self._disposed = True
        if self.lookup_request is not None:
            self.lookup_request.cancel()
        self.loading_indicator.stop()
        app = self._app_ref() if self._app_ref is not None else None
        if app is not None and isValid(app) and isValid(self):
            app.removeEventFilter(self)
        self._app_ref = None
        for name in ("delay", "nested_delay", "leave", "pointer", "expand_animation", "feedback_timer"):
            timer = getattr(self, name, None)
            if timer is not None and isValid(timer):
                timer.stop()
        connections, self._connections = self._connections, []
        for connection in connections:
            if connection:
                QObject.disconnect(connection)
        window = self.window
        for popup in self.nested_popups:
            if isValid(popup):
                popup.hide()
                popup.deleteLater()
        self.nested_popups.clear()
        self.nested_pending = None
        if isValid(self) and window is not None and isValid(window):
            self.hide()
        self.hit = self.pending = None
        self.views.clear()
        self.editor_text.clear()
        self.copies.clear()
        self.definitions.clear()
        self.feedback_buttons.clear()
        self.compact_ru = None
        self.target = None
        self.expanding = False
        self.editing = False
        self.references.dispose()
        self._window_ref = None

    @staticmethod
    def small_icon(kind):
        pixmap = QPixmap(32, 32)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(theme.color('icon.default'), 1.2,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if kind == "copy":
            painter.drawRoundedRect(5, 5, 8, 9, 1, 1)
            painter.drawLine(3, 10, 2, 10)
            painter.drawLine(2, 10, 2, 2)
            painter.drawLine(2, 2, 10, 2)
        elif kind == "check":
            paint_check(painter, QRect(0, 0, 16, 16), theme.css('status.ready'))
        elif kind == "navigate":
            painter.drawLine(3, 8, 13, 8)
            painter.drawLine(9, 4, 13, 8)
            painter.drawLine(13, 8, 9, 12)
        else:
            painter.drawLine(4, 6, 8, 10)
            painter.drawLine(8, 10, 12, 6)
        painter.end()
        return QIcon(pixmap)

    def copy_compact_ru(self):
        if not self._alive():
            return
        if self.compact_ru is not None:
            QApplication.clipboard().setText(self.compact_ru)
            self.close_preview()

    def expand(self):
        if not self._alive() or self.loading:
            return
        self.delay.stop()
        self.pending = None
        self.leave.stop()
        self.hide_nested_from(0)
        start = self.geometry()
        self._expanded_origin = start.topLeft()
        self.expanding = True
        self.expanded = True
        self.compact.hide()
        self.details.show()
        self.detail_actions.show()
        self.edit_button.setVisible(self.can_edit_display)
        self.position_panel()
        end = self.geometry()
        self.setGeometry(start)
        self.expand_animation.setStartValue(start)
        self.expand_animation.setEndValue(end)
        self.expand_animation.start()
        self.raise_()

    def expansion_finished(self):
        if not self._alive():
            return
        self.expanding = False
        self.check_pointer()

    def pointer_inside(self):
        point = QCursor.pos()
        return (self.rect().contains(self.mapFromGlobal(point))
                or any(popup.isVisible() and popup.rect().contains(popup.mapFromGlobal(point))
                       for popup in self.nested_popups)
                or (self.definition_choice.view().isVisible()
                    and self.definition_choice.view().rect().contains(self.definition_choice.view().mapFromGlobal(point))))

    def hide_nested_from(self, depth):
        self.nested_delay.stop()
        self.nested_pending = None
        for popup in self.nested_popups[depth:]:
            if popup.lookup_request is not None:
                popup.lookup_request.cancel()
            popup.loading_indicator.stop()
            popup.hide()
            popup.key = popup.value = popup.parent_hit = None

    def nested_hit_at(self, point):
        """Return parent depth + exact token span; never inspect a Level 3 body."""
        under_pointer = QApplication.widgetAt(point)
        candidates = [(i + 1, popup.body, popup.body.text())
                      for i, popup in reversed(list(enumerate(self.nested_popups))) if popup.isVisible()]
        if self.expanded:
            candidates.extend((0, browser, self.copies.get(name))
                              for name, (frame, browser, button) in self.text_blocks.items() if frame.isVisible())
        else:
            candidates.append((0, self.compact_text, self.compact_text.text()))
        for depth, browser, text in candidates:
            viewport = browser.viewport()
            if under_pointer is not viewport or not viewport.rect().contains(viewport.mapFromGlobal(point)):
                continue
            if depth >= 2 or text is None:
                return None
            local = viewport.mapFromGlobal(point)
            cursor = browser.cursorForPosition(local)
            if local.x() < browser.cursorRect(cursor).x():
                cursor.movePosition(cursor.MoveOperation.PreviousCharacter)
            position = cursor.position()
            left = browser.cursorRect(cursor)
            cursor.movePosition(cursor.MoveOperation.NextCharacter)
            right = browser.cursorRect(cursor)
            # cursorForPosition alone also returns a cursor in empty right margin.
            if (not left.top() <= local.y() <= left.bottom() or local.x() < left.x()
                    or (left.y() == right.y() and local.x() > right.x() + 1)):
                return None
            offset = python_position(text, position)
            for token in localization_tokens(text):
                if token.start <= offset < token.end and token.key is not None:
                    return depth, browser, token.start, token.end, token.key
            return None
        return None

    def update_nested_hover(self):
        if not self._alive() or self.editing or self.expanding:
            return
        hit = None if QApplication.mouseButtons() else self.nested_hit_at(QCursor.pos())
        if hit is None:
            self.nested_delay.stop()
            self.nested_pending = None
            return
        depth, browser, start, end, key = hit
        if (depth < len(self.nested_popups) and self.nested_popups[depth].isVisible()
                and self.nested_popups[depth].parent_hit == hit):
            self.nested_delay.stop()
            self.nested_pending = None
            return
        keys = [self.chain_key] + [popup.key for popup in self.nested_popups if popup.isVisible()]
        if key in keys:
            self.nested_delay.stop()
            self.nested_pending = None
            return
        if hit != self.nested_pending:
            self.nested_pending = hit
            self.nested_delay.start()

    def open_nested_pending(self):
        if not self._alive() or not self.isVisible() or self.nested_pending is None:
            return
        hit = self.nested_hit_at(QCursor.pos())
        if hit != self.nested_pending:
            self.nested_pending = None
            return
        depth, browser, start, end, key = hit
        keys = [self.chain_key] + [popup.key for popup in self.nested_popups if popup.isVisible()]
        if depth >= 2 or key in keys:
            return
        self.hide_nested_from(depth)
        if depth == len(self.nested_popups):
            popup = QFrame(self.window)
            popup.setObjectName("tokenInspector")
            popup.setStyleSheet(self.styleSheet())
            layout = QVBoxLayout(popup)
            layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            layout.setContentsMargins(8, 6, 8, 6)
            layout.setSpacing(3)
            popup.body = CompactReferenceText()
            layout.addWidget(popup.body, 1)
            popup.footer = QFrame()
            popup.footer.setFixedHeight(24)
            footer = QHBoxLayout(popup.footer)
            footer.setContentsMargins(0, 0, 0, 0)
            footer.setSpacing(3)
            popup.source_label = source_label()
            footer.addWidget(popup.source_label)
            footer.addStretch(1)
            layout.addWidget(popup.footer)
            popup.copy_button = QToolButton()
            popup.copy_button.setIcon(self.small_icon("copy"))
            popup.copy_button.setIconSize(QSize(16, 16))
            popup.copy_button.setFixedSize(24, 24)
            popup.copy_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            popup.copy_button.setToolTip(QCoreApplication.translate("token_inspector", "Копировать RU"))
            popup.copy_button.setAccessibleName(popup.copy_button.toolTip())
            self._connect(popup.copy_button, popup.copy_button.clicked, lambda: self.copy_nested(depth))
            footer.addWidget(popup.copy_button)
            popup.lookup_request = None
            popup.loading_indicator = LookupIndicator(popup)
            self.nested_popups.append(popup)
        popup = self.nested_popups[depth]
        popup.key, popup.value, popup.parent_hit = key, None, hit
        popup.body.setText(QCoreApplication.translate("token_inspector", "Поиск…"))
        popup.source_label.clear()
        popup.copy_button.hide()
        self.position_nested()
        popup.show()
        popup.raise_()
        popup.loading_indicator.start()
        popup.lookup_request = self.references.request(
            key, lambda scope, definitions: self.finish_nested_lookup(popup, hit, scope, definitions))
        self.leave.stop()

    def finish_nested_lookup(self, popup, hit, scope, definitions):
        if (not self._alive() or not self.isVisible() or not isValid(popup)
                or not popup.isVisible() or popup.parent_hit != hit):
            return
        popup.lookup_request = None
        popup.loading_indicator.stop()
        value = self.definition_ru(definitions[0][3]) if len(definitions) == 1 else None
        text = (value if value is not None else QCoreApplication.translate("token_inspector", "Перевод отсутствует")) if len(definitions) == 1 else (
            QCoreApplication.translate("token_inspector", "Несколько определений") if definitions else QCoreApplication.translate("token_inspector", "Ключ не найден"))
        popup.value = value
        popup.body.setText(text[:1000])
        popup.source_label.setText((QCoreApplication.translate("token_inspector", "Мод") if scope == "Project" else "Vanilla") if definitions else "")
        popup.copy_button.setVisible(value is not None)
        self.position_nested()

    def copy_nested(self, depth):
        if not self._alive() or depth >= len(self.nested_popups):
            return
        popup = self.nested_popups[depth]
        if popup.value is not None:
            QApplication.clipboard().setText(popup.value)
            self.hide_nested_from(depth)

    def position_nested(self):
        bounds = self.window.rect().adjusted(8, 8, -8, -8)
        parent = self
        for popup in self.nested_popups:
            if popup.key is None:
                break
            metrics = popup.body.fontMetrics()
            body_width = max((metrics.horizontalAdvance(line) for line in popup.body.text().splitlines()), default=0)
            width = min(380, bounds.width(), max(140, body_width + 18))
            height = metrics.boundingRect(QRect(0, 0, max(1, width - 18), 10000),
                                          Qt.TextFlag.TextWordWrap, popup.body.text()).height()
            popup.body.setFixedHeight(min(metrics.lineSpacing() * 4, max(metrics.lineSpacing(), height)))
            popup.resize(width, min(bounds.height(), popup.body.height() + 39))
            x = parent.geometry().right() + 1
            if x + width > bounds.right() + 1:
                x = parent.x() - width
            popup.move(max(bounds.left(), min(x, bounds.right() - width + 1)),
                       max(bounds.top(), min(parent.y(), bounds.bottom() - popup.height() + 1)))
            parent = popup

    def copy_button(self, name, tooltip):
        button = QToolButton()
        button.setIcon(self.small_icon("copy"))
        button.setFixedSize(24, 24)
        button.setIconSize(QSize(16, 16))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._connect(button, button.clicked, lambda: self.copy_value(name, button))
        return button

    def reset_copy_feedback(self):
        if not self._alive():
            return
        self.feedback_timer.stop()
        for button in self.feedback_buttons:
            button.setIcon(self.small_icon("copy"))
        self.feedback_buttons.clear()

    def target_row(self):
        if not self._alive() or self.target is None:
            return None
        scope, row = self.target
        if scope == "Project":
            number = self.window.entry_rows.get(row.get("entry_id"))
            if number is not None and self.window.model.rows[number] is row:
                return scope, number
        else:
            number = next((i for i, candidate in enumerate(self.window.vanilla_rows) if candidate is row), None)
            if number is not None:
                return scope, number
        return None

    def go_to_entry(self):
        target = self.target_row()
        if target is None or target[0] != "Project":
            return
        window = self.window
        identity = window.model.rows[target[1]]["entry_id"]
        self.close_preview()
        window.open_workspace_entry(identity)

    def open_in_panel(self):
        target = self.target_row()
        if target is None:
            return
        window = self.window
        self.close_preview()
        if target[0] == "Project":
            window.open_secondary_preview(target[1])
        else:
            window.open_secondary_vanilla_preview(target[1])

    def register(self, view):
        if not self._alive() or view is None or not isValid(view):
            return
        if view not in self.views:
            self.views.append(view)
            bar = view.verticalScrollBar()
            self._connect(bar, bar.valueChanged, self.scrolled)

    def close_preview(self):
        if not self._alive():
            return
        if self.lookup_request is not None:
            self.lookup_request.cancel()
            self.lookup_request = None
        self.loading = False
        self.loading_indicator.stop()
        self.expand_animation.stop()
        self.expanding = False
        self.definition_choice.hidePopup()
        self.reset_copy_feedback()
        self.hide_nested_from(0)
        self.hide()
        self.delay.stop()
        self.leave.stop()
        self.pointer.stop()
        self.hit = self.pending = None
        self.target = None
        self.chain_key = None
        self.editing = False

    def scrolled(self, *args):
        if not self._alive():
            return
        # A click in the popup can commit RU on FocusOut and adjust the view's
        # scrollbar. Do not swallow the impending Copy/Expand click in that case.
        pointer_inside = self.isVisible() and (self.expanding or self.pointer_inside())
        if not self.editing and not pointer_inside:
            self.close_preview()

    def working_text_changed(self, *args):
        if not self._alive():
            return
        # Values stay live in the index. Dismiss a visible snapshot of them,
        # without rebuilding the project index after a translation edit.
        # Clicking into this panel may commit the delegate editor on FocusOut;
        # keep the panel alive long enough to receive that same click.
        pointer_inside = self.isVisible() and (self.expanding or self.pointer_inside())
        if not self.editing and not pointer_inside:
            self.close_preview()

    def source_index(self, hit):
        if not self._alive() or hit is None or not isValid(hit.view):
            return QModelIndex()
        index = QModelIndex(hit.index)
        while index.isValid():
            model = index.model()
            if model is None or not isValid(model):
                return QModelIndex()
            if not isinstance(model, QAbstractProxyModel):
                break
            index = model.mapToSource(index)
        return index

    def hit_at(self, global_point):
        if not self._alive():
            return None
        under_pointer = QApplication.widgetAt(global_point)
        for view in self.views:
            if not isValid(view) or not view.isVisible():
                continue
            viewport = view.viewport()
            if viewport is None or not isValid(viewport):
                continue
            point = viewport.mapFromGlobal(global_point)
            if not viewport.rect().contains(point):
                continue
            delegate = view.itemDelegate()
            if delegate is None or not isValid(delegate):
                continue
            for editor, field, index in (
                (getattr(delegate, "editor", None), "ru_text", getattr(delegate, "edit_index", QPersistentModelIndex())),
                (getattr(view, "en_selection", None), "new_text", getattr(getattr(view, "en_selection", None), "index", QPersistentModelIndex())),
            ):
                if editor is None or not isValid(editor) or not editor.isVisible() or not index.isValid():
                    continue
                editor_viewport = editor.viewport()
                document = editor.document()
                if (editor_viewport is None or not isValid(editor_viewport)
                        or document is None or not isValid(document)):
                    continue
                local = editor_viewport.mapFromGlobal(global_point)
                if under_pointer is not editor_viewport or not editor_viewport.rect().contains(local):
                    continue
                revision = document.revision()
                cached = self.editor_text.get(id(editor))
                if cached is None or cached[0] != revision or cached[1] is not editor:
                    self.editor_text = {id(editor): (revision, editor, editor.toPlainText())}
                text = self.editor_text[id(editor)][2]
                cursor = editor.cursorForPosition(local)
                position = cursor.position()
                if local.x() < editor.cursorRect(cursor).x():
                    position = max(0, position - 1)
                # Reject blank space beyond the last glyph, not just nearest cursor.
                next_cursor = editor.textCursor()
                next_cursor.setPosition(position)
                left = editor.cursorRect(next_cursor)
                next_cursor.movePosition(next_cursor.MoveOperation.NextCharacter)
                right = editor.cursorRect(next_cursor)
                if not left.top() <= local.y() <= left.bottom() or local.x() < left.x() or (right.y() == left.y() and local.x() > right.x() + 1):
                    return None
                offset = python_position(text, position)
                return self.make_hit(view, index, field, text, offset, global_point, editor)
            if under_pointer is not viewport:
                continue
            index = view.indexAt(point)
            if not index.isValid():
                return None
            row = index.data(Qt.ItemDataRole.UserRole)
            if not row or row.get("kind", "ENTRY") not in ("ENTRY", "DISABLED_ENTRY"):
                return None
            option = view._option(index)
            layout = delegate.row_layout(option, index)
            for field, rect, text_layout in (("new_text", layout.source, layout.source_layout),
                                              ("ru_text", layout.russian, layout.russian_layout)):
                local = point - option.rect.topLeft() - rect.topLeft()
                for i in range(text_layout.lineCount()):
                    line = text_layout.lineAt(i)
                    if not line.naturalTextRect().contains(QPointF(local)):
                        continue
                    offset = python_position(text_layout.text(), line.xToCursor(local.x(), QTextLine.CursorPosition.CursorOnCharacter))
                    if field == "new_text" and not layout.large_change:
                        displayed, current = 0, 0
                        for segment in row["source_diff"]:
                            length = len(segment["text"])
                            if displayed <= offset < displayed + length:
                                if segment["type"] == "removed":
                                    return None
                                offset = current + offset - displayed
                                break
                            displayed += length
                            if segment["type"] != "removed":
                                current += length
                    return self.make_hit(view, index, field, row.get(field) or "", offset, global_point)
        return None

    @staticmethod
    def make_hit(view, index, field, text, offset, point, editor=None):
        for token in localization_tokens(text):
            if token.start <= offset < token.end and token.kind in ("variable", "bracket"):
                return TokenHit(view, QPersistentModelIndex(index), field, text, token,
                                QRect(point.x(), point.y(), 1, 18), editor)
        return None

    def eventFilter(self, watched, event):
        if not self._alive():
            return False
        window = self.window  # _alive checked both Python reference and C++ owner.
        if not window.isVisible() or watched is None or not isValid(watched):
            return False
        if (self.expanded and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
                and event.matches(QKeySequence.StandardKey.Copy)):
            for frame, browser, button in self.text_blocks.values():
                if watched is browser or watched is browser.viewport():
                    # Keep the workspace's Copy-entry action from replacing a
                    # selected fragment with the whole localization row.
                    if event.type() == QEvent.Type.KeyPress:
                        browser.copy()
                    event.accept()
                    return True
        if event.type() == QEvent.Type.ToolTip and self.isVisible() and isinstance(watched, QWidget):
            if any(isValid(view) and (watched is view or view.isAncestorOf(watched)) for view in self.views):
                QToolTip.hideText()
                event.accept()
                return True
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape and self.isVisible():
            self.close_preview()
            return False
        if self.expanding:
            return False
        if (event.type() == QEvent.Type.Enter and self.isVisible() and not self.editing
                and isinstance(watched, QWidget) and (watched is self or self.isAncestorOf(watched))):
            # Cancel a gap-crossing timeout on entering the card/buttons, even
            # if Qt has not delivered a MouseMove inside the popup yet.
            self.leave.stop()
            self.delay.stop()
            self.pending = None
        if event.type() == QEvent.Type.MouseMove and not self.editing:
            if self.isVisible() and self.pointer_inside():
                self.leave.stop()
                self.delay.stop()
                self.pending = None
                self.update_nested_hover()
                return False
            self.nested_delay.stop()
            self.nested_pending = None
            if event.buttons():
                return False
            hit = self.hit_at(QCursor.pos())
            if hit is not None:
                if self.hit is not None and hit.identity() == self.hit.identity():
                    self.leave.stop()
                    self.delay.stop()
                    self.pending = None
                elif self.pending is None or hit.identity() != self.pending.identity():
                    # Do not destroy the current hover when crossing another
                    # token on the short route to the popup's buttons.
                    self.leave.stop()
                    self.pending = hit
                    self.delay.start()
            elif self.isVisible():
                if not self.leave.isActive():
                    self.leave.start()
            else:
                self.delay.stop()
                self.pending = None
        if event.type() == QEvent.Type.Resize and watched is self.window and self.isVisible():
            self.position_panel()
        return False

    def open_pending(self):
        if not self._alive():
            return
        hit = self.hit_at(QCursor.pos())
        if hit is None or self.pending is None or hit.identity() != self.pending.identity():
            return
        if self.lookup_request is not None:
            self.lookup_request.cancel()
            self.lookup_request = None
        self.hit, self.pending = hit, None
        self.hide_nested_from(0)
        self.edit_row.hide()
        self.message.clear()
        self.message.hide()
        self.editing = False
        self.expanded = False
        self._expanded_origin = None
        self.target = None
        self.copies = {}
        self.reset_copy_feedback()
        self.detail_actions.hide()
        token = hit.token
        key = token.key
        self.chain_key = key
        self.compact_ru = None
        self.compact_source.clear()
        self.compact_text.setText(QCoreApplication.translate("token_inspector", "Поиск…"))
        self.copy_ru_button.hide()
        self.expand_button.setEnabled(False)
        self.details.hide()
        self.edit_button.hide()
        self.compact.show()
        self.loading = key is not None
        self.position_panel()
        QToolTip.hideText()
        self.show()
        self.raise_()
        self.pointer.start()
        if key is None:
            self.finish_lookup(hit, "Project", [])
        else:
            self.loading_indicator.start()
            self.lookup_request = self.references.request(
                key, lambda scope, definitions: self.finish_lookup(hit, scope, definitions))

    def finish_lookup(self, hit, scope, definitions):
        if not self._alive() or not self.isVisible() or self.hit is not hit:
            return
        self.lookup_request = None
        self.loading = False
        self.loading_indicator.stop()
        self.expand_button.setEnabled(True)
        token, key = hit.token, hit.token.key
        self.header_key = key if key is not None else token.raw
        # Preserve the previous expression-copy payload (without brackets).
        self.copies["key"] = key if key is not None else token.expression
        self.key_copy.setToolTip(QCoreApplication.translate("token_inspector", "Скопировать ключ") if key is not None else QCoreApplication.translate("token_inspector", "Скопировать выражение"))
        self.key_copy.setAccessibleName(self.key_copy.toolTip())
        self.definitions = definitions
        self.definition_scope = scope
        self.definition_choice.blockSignals(True)
        self.definition_choice.clear()
        for en, file, line, row in definitions:
            display_file = self.display_file(scope, file)
            self.definition_choice.addItem(f"{display_file} · {line or '—'}")
        self.definition_choice.blockSignals(False)
        self.definition_choice.setVisible(len(definitions) > 1)
        self.compact_ru = None
        if key is None:
            compact_text = QCoreApplication.translate("token_inspector", "Динамическое значение")
            note = QCoreApplication.translate("token_inspector", "Динамическое значение\nОпределяется игрой во время выполнения.")
        else:
            if not definitions:
                compact_text = QCoreApplication.translate("token_inspector", "Ключ не найден")
                note = QCoreApplication.translate("token_inspector", "Ключ не найден")
            elif len(definitions) > 1:
                compact_text = QCoreApplication.translate("token_inspector", "Несколько определений")
                note = QCoreApplication.translate("token_inspector", "Найдено несколько точных определений")
            else:
                row = definitions[0][3]
                self.target = (scope, row) if row is not None else None
                self.compact_ru = self.definition_ru(row)
                compact_text = self.compact_ru if self.compact_ru is not None else QCoreApplication.translate("token_inspector", "Перевод отсутствует")
                note = ""
        self.detail_note.setText(note)
        self.detail_note.setVisible(bool(note))
        self.set_text_block("display", token.display, visible=token.kind == "bracket" and token.display is not None)
        self.show_definition(0)
        self.details.hide()
        # Display is bounded, clipboard retains the complete exact RU value.
        self.compact_text.setText(compact_text[:1000])
        self.compact_source.setText((QCoreApplication.translate("token_inspector", "Мод") if scope == "Project" else "Vanilla") if definitions else "")
        self.copy_ru_button.setVisible(self.compact_ru is not None)
        self.go_button.setEnabled(self.target is not None and scope == "Project" and bool(self.target[1].get("entry_id")))
        self.panel_button.setEnabled(self.target is not None)
        self.compact.show()
        source = self.source_index(hit)
        self.can_edit_display = (token.kind == "bracket" and hit.field == "ru_text"
                                 and source.isValid() and source.model() is self.window.model)
        self.edit_button.hide()
        self.position_panel()
        QToolTip.hideText()
        self.show()
        self.raise_()
        self.pointer.start()

    @staticmethod
    def definition_ru(row):
        if row is None or row.get("translation_origin") == "en_fallback" or row.get("translation_state") == "untranslated":
            return None
        return row.get("ru_text")

    def display_file(self, scope, file):
        if file is None:
            return None
        return self.window.translation_target_path(file) if scope == "Project" else Path(file)

    def set_text_block(self, name, text, *, visible=True):
        frame, browser, button = self.text_blocks[name]
        frame.setVisible(visible)
        browser.setPlainText(text if text is not None else QCoreApplication.translate("token_inspector", "Перевод отсутствует"))
        browser.verticalScrollBar().setValue(0)
        button.setEnabled(text is not None)
        self.copies[name] = text

    def show_definition(self, index):
        if not self._alive():
            return
        self.reset_copy_feedback()
        self.hide_nested_from(0)
        valid = 0 <= index < len(self.definitions)
        self.header_file = ""
        self.file_label.setToolTip("")
        if valid:
            en, file, line, row = self.definitions[index]
            display_file = self.display_file(self.definition_scope, file)
            self.header_file = f"{display_file.name if display_file is not None else '—'} · {line or '—'}"
            self.file_label.setToolTip(f"{display_file or '—'} · {line or '—'}")
            self.set_text_block("en", en)
            self.set_text_block("ru", self.definition_ru(row))
        else:
            self.set_text_block("en", None, visible=False)
            self.set_text_block("ru", None, visible=False)
        if self.expanded:
            self.position_panel()

    def copy_value(self, identity, button):
        if not self._alive():
            return
        value = self.copies.get(identity)
        if value is not None:
            QApplication.clipboard().setText(value)
            button.setIcon(self.small_icon("check"))
            self.feedback_buttons.add(button)
            self.feedback_timer.start()

    def position_panel(self):
        if not self._alive() or self.hit is None or not isValid(self.hit.view):
            return
        bounds = self.window.rect().adjusted(8, 8, -8, -8)
        if self.expanded:
            self.ensurePolished()
            width = min(520, bounds.width())
            metrics = self.key_label.fontMetrics()
            header_width = max(1, width - 52)
            file_width = min(header_width // 2, metrics.horizontalAdvance(self.header_file)) if self.header_file else 0
            self.file_label.setVisible(bool(self.header_file))
            self.file_label.setFixedWidth(file_width)
            self.file_label.setText(self.file_label.fontMetrics().elidedText(self.header_file, Qt.TextElideMode.ElideMiddle, file_width))
            self.key_label.setText(metrics.elidedText(self.header_key, Qt.TextElideMode.ElideRight, header_width - file_width))
            self.key_label.setToolTip(self.header_key)
            visible_blocks = [(name, browser) for name, (frame, browser, button) in self.text_blocks.items() if not frame.isHidden()]
            # Each read-only text area scrolls internally; no nested card scroller.
            budget = max(40, min(440, bounds.height()) - 135 - (40 if self.can_edit_display else 0))
            text_limit = max(24, budget // max(1, len(visible_blocks)) - 35)
            for name, browser in visible_blocks:
                browser.ensurePolished()
                text_width = max(1, width - 52)
                browser.document().setTextWidth(text_width)
                # A hidden QTextBrowser may not have reflowed its document yet.
                # Measure bounded plain text at the target width before animation.
                text = self.copies.get(name)
                if text is None:
                    text = QCoreApplication.translate("token_inspector", "Перевод отсутствует")
                metrics = browser.fontMetrics()
                text_height = metrics.boundingRect(QRect(0, 0, text_width, 10000),
                                                   Qt.TextFlag.TextWordWrap, text[:1000]).height()
                browser.setFixedHeight(min(text_limit, max(metrics.lineSpacing() + 4, text_height + 5)))
            # Child height changes post LayoutRequest events. Before measuring
            # an animation target, invalidate every enclosing layout explicitly:
            # otherwise details still reports QTextBrowser's initial 192px hint
            # (or the previous text's height), stretching/clipping the blocks.
            for name, browser in visible_blocks:
                block = self.text_blocks[name][0]
                block.ensurePolished()
                block.layout().invalidate()
                block.layout().activate()
            self.details.layout().invalidate()
            self.details.layout().activate()
            self.layout().invalidate()
            self.layout().activate()
            height = min(460, bounds.height(), self.sizeHint().height())
        else:
            metrics = self.compact_text.fontMetrics()
            body_width = max((metrics.horizontalAdvance(line) for line in self.compact_text.text().splitlines()), default=0)
            width = min(380, bounds.width(), max(140, body_width + 18))
            text_width = max(1, width - 18)
            text_height = metrics.boundingRect(QRect(0, 0, text_width, 10000),
                                              Qt.TextFlag.TextWordWrap, self.compact_text.text()).height()
            self.compact_text.setFixedHeight(min(metrics.lineSpacing() * 4, max(metrics.lineSpacing(), text_height)))
            height = self.compact_text.height() + 39
        self.resize(width, height)
        if self.expanded and self._expanded_origin is not None:
            x, y = self._expanded_origin.x(), self._expanded_origin.y()
        else:
            point = self.window.mapFromGlobal(self.hit.anchor.bottomLeft())
            x, y = point.x(), point.y() + 3
            if y + self.height() > bounds.bottom():
                y = self.window.mapFromGlobal(self.hit.anchor.topLeft()).y() - self.height() - 5
        self.move(max(bounds.left(), min(x, bounds.right() - self.width() + 1)),
                  max(bounds.top(), min(y, bounds.bottom() - self.height() + 1)))
        self.position_nested()

    def check_pointer(self):
        if not self._alive():
            return
        if self.editing or self.expanding:
            return
        if self.pointer_inside():
            self.leave.stop()
            self.delay.stop()
            self.pending = None
            self.update_nested_hover()
            return
        self.nested_delay.stop()
        self.nested_pending = None
        hit = self.hit_at(QCursor.pos())
        if hit is not None and any(existing is not None and hit.identity() == existing.identity()
                                   for existing in (self.hit, self.pending)):
            self.leave.stop()
        elif not self.leave.isActive():
            self.leave.start()

    def close_if_outside(self):
        if not self._alive():
            return
        if not self.editing and not self.expanding and not self.pointer_inside():
            hit = self.hit_at(QCursor.pos())
            if hit is None or not any(existing is not None and hit.identity() == existing.identity()
                                      for existing in (self.hit, self.pending)):
                self.close_preview()

    def begin_display_edit(self):
        if not self._alive() or self.hit is None or not isValid(self.hit.view):
            return
        self.editing = True
        self.leave.stop()
        # Commit an existing RU draft before applying a separate model command.
        self.hit.view.finish_editing()
        if not self._alive() or self.hit is None:
            return
        self.display_input.setText(self.hit.token.display or "")
        self.edit_row.show()
        self.message.show()
        self.position_panel()
        self.display_input.setFocus()
        self.display_input.selectAll()

    def save_display(self):
        if not self._alive() or self.hit is None:
            return
        hit = self.hit
        source = self.source_index(hit)
        if not source.isValid() or source.model() is not self.window.model:
            self.message.setText(QCoreApplication.translate("token_inspector", "Запись больше недоступна. Откройте токен заново."))
            return
        text = self.window.model.rows[source.row()].get("ru_text") or ""
        if text != hit.text:
            self.message.setText(QCoreApplication.translate("token_inspector", "RU изменился. Закройте панель и откройте токен заново."))
            return
        replacement = hit.token.with_display(self.display_input.text())
        tokens = localization_tokens(replacement)
        if not tokens or tokens[0].kind != "bracket" or tokens[0].start != 0 or tokens[0].end != len(replacement):
            self.message.setText(QCoreApplication.translate("token_inspector", "Текст нарушает границы конструкции []. Проверьте скобки и кавычки."))
            self.position_panel()
            return
        self.window.model.setData(source, text[:hit.token.start] + replacement + text[hit.token.end:], Qt.ItemDataRole.EditRole)
        self.close_preview()
