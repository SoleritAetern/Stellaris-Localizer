"""Find occurrences in the displayed entries without modifying proxy filters."""

from PySide6.QtCore import QCoreApplication

from collections import defaultdict
from copy import deepcopy

from shiboken6 import isValid

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, QPropertyAnimation, QEasingCurve, QItemSelectionModel
from PySide6.QtGui import QAction, QKeySequence, QTextCharFormat, QTextLayout
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QToolButton, QMessageBox, QWidget, QSizePolicy

from . import theme
from .shortcuts import SHORTCUTS
from .global_search import match_spans
from .global_replace import GlobalReplaceCommand, ReplaceScope, TRANSLATION_FIELDS
from .translation_baseline import refresh_baseline


class LocalFind(QFrame):
    def __init__(self, window):
        viewport = window.list_view.viewport()
        super().__init__(viewport)
        self._filters_ready = False
        self.window, self.view, self.proxy = window, window.list_view, window.proxy_model
        self.viewport = viewport
        self.input = QLineEdit()
        self.replacement = QLineEdit()
        self.view.local_find = self
        self.setObjectName("localFind")
        self.replace_mode = False
        self._replacement_undo_active = False
        self.selection_scope = None
        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 5, 6, 5)
        outer.setSpacing(4)
        self.disclosure = QToolButton()
        self.disclosure.setFixedWidth(20)
        self.disclosure.setArrowType(Qt.ArrowType.RightArrow)
        self.disclosure.setToolTip(QCoreApplication.translate("local_find", "Показать / скрыть замену RU"))
        self.disclosure.clicked.connect(lambda: self.set_replace_mode(not self.replace_mode))
        outer.addWidget(self.disclosure, 0, Qt.AlignmentFlag.AlignVCenter)
        body = QVBoxLayout()
        body.setSpacing(3)
        outer.addLayout(body, 1)
        self.find_row = QWidget()
        layout = QHBoxLayout(self.find_row)
        layout.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.find_row)
        layout.setSpacing(4)
        self.input.setPlaceholderText(QCoreApplication.translate("local_find", "Найти в RU"))
        self.input.setClearButtonEnabled(True)
        self.input.setMinimumWidth(60)
        self.counter = QLabel("0 / 0")
        layout.addWidget(self.input, 1)
        controls = QWidget()
        buttons = QHBoxLayout(controls)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(3)
        buttons.addWidget(self.counter)
        self.case_sensitive = QToolButton()
        self.case_sensitive.setText("Aa")
        self.case_sensitive.setCheckable(True)
        self.case_sensitive.setToolTip(QCoreApplication.translate("local_find", "Учитывать регистр"))
        buttons.addWidget(self.case_sensitive)
        self.in_selection = QToolButton()
        self.in_selection.setText("[≡]")
        self.in_selection.setCheckable(True)
        self.in_selection.setToolTip(QCoreApplication.translate("local_find", "Искать только в выделении"))
        self.in_selection.setAccessibleName(QCoreApplication.translate("local_find", "Искать только в выделении"))
        buttons.addWidget(self.in_selection)
        layout.addWidget(controls)
        for label, tip, callback in (("↑", QCoreApplication.translate("local_find", "Предыдущее · Shift+F3"), lambda: self.step(-1)),
                                     ("↓", QCoreApplication.translate("local_find", "Следующее · F3"), lambda: self.step(1)),
                                     ("×", QCoreApplication.translate("local_find", "Закрыть · Esc"), self.close_find)):
            button = QToolButton()
            button.setText(label)
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        self.replace_row = QWidget()
        replacement_layout = QHBoxLayout(self.replace_row)
        replacement_layout.setContentsMargins(0, 0, 0, 0)
        replacement_layout.setSpacing(4)
        self.replacement.setPlaceholderText(QCoreApplication.translate("local_find", "Заменить на"))
        self.replacement.setMinimumWidth(60)
        replacement_layout.addWidget(self.replacement, 1)
        replace_controls = QWidget()
        replace_buttons = QHBoxLayout(replace_controls)
        replace_buttons.setContentsMargins(0, 0, 0, 0)
        replace_buttons.setSpacing(3)
        replace_buttons.addStretch()
        for label, tip, callback in ((QCoreApplication.translate("local_find", "Заменить"), QCoreApplication.translate("local_find", "Заменить текущее и перейти к следующему"), self.replace_current),
                                     (QCoreApplication.translate("local_find", "Все"), QCoreApplication.translate("local_find", "Заменить всё в отображаемых записях…"), self.replace_all)):
            button = QToolButton()
            button.setText(label)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            replace_buttons.addWidget(button)
        controls.setFixedWidth(max(controls.sizeHint().width(), replace_controls.sizeHint().width()) + 24)
        replace_controls.setFixedWidth(controls.width())
        replacement_layout.addWidget(replace_controls)
        body.addWidget(self.replace_row)
        self.replace_row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.replace_row.setMaximumHeight(0)
        self.replace_row.hide()
        self.animation = QPropertyAnimation(self.replace_row, b"maximumHeight", self)
        self.animation.setDuration(140)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.valueChanged.connect(lambda value: self.reposition())
        self.animation.finished.connect(self.finish_animation)
        self.setStyleSheet(f"""
            QFrame#localFind QLineEdit {{
                background: {theme.css('surface.window')};
                border: 1px solid {theme.css('border.find')};
                border-radius: {theme.RADIUS}px;
            }}
            QFrame#localFind QLineEdit:hover {{ border-color: {theme.css('border.hover')}; }}
            QFrame#localFind QLineEdit:focus {{ border-color: {theme.css('border.find_focus')}; }}
            QFrame#localFind QToolButton:checked {{ background: {theme.css('surface.selected')}; border-radius: 3px; }}
        """)
        self.matches, self.by_entry, self.sources = [], {}, {}
        self.current = -1
        self.dirty = True
        self.query_changed = False
        self._initial_anchor = None
        self.match_rows = {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.rebuild)
        self.input.textChanged.connect(self.query_updated)
        self.replacement.textChanged.connect(self.field_edited)
        self.case_sensitive.toggled.connect(self.query_updated)
        self.in_selection.toggled.connect(self.selection_toggled)
        self.view.selectionModel().selectionChanged.connect(self.update_selection_available)
        self.update_selection_available()
        self.proxy.modelReset.connect(self.scope_updated)
        self.proxy.layoutChanged.connect(self.scope_updated)
        self.proxy.rowsInserted.connect(self.scope_updated)
        self.proxy.rowsRemoved.connect(self.scope_updated)
        window.model.dataChanged.connect(self.scope_updated)
        self.actions_by_shortcut = {}
        for shortcut, callback in ((SHORTCUTS["find"], self.toggle_find), (SHORTCUTS["replace"], self.toggle_replace), ("F3", lambda: self.step(1)),
                                    ("Shift+F3", lambda: self.step(-1))):
            action = QAction(self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            window.addAction(action)
            self.actions_by_shortcut[shortcut] = action
        self.escape = QAction(self)
        self.escape.setShortcut(QKeySequence("Esc"))
        self.escape.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape.triggered.connect(self.close_find)
        self.escape.setEnabled(False)
        self.view.addAction(self.escape)
        self.hide()
        # Reparenting/layout events can be synchronous. Subscribe only after
        # every widget, action and state used by the handlers is ready.
        self._filters_ready = True
        self.viewport.installEventFilter(self)
        self.view.installEventFilter(self)
        self.input.installEventFilter(self)
        self.replacement.installEventFilter(self)
        for button in self.findChildren(QToolButton):
            button.installEventFilter(self)

    def reposition(self):
        available = max(1, self.viewport.width() - 16)
        self.setFixedWidth(min(600, available))
        extra = 0 if self.replace_row.isHidden() else min(self.replace_row.maximumHeight(), self.replace_row.sizeHint().height()) + 3
        self.setFixedHeight(self.find_row.sizeHint().height() + extra + 10)
        self.move(max(0, self.viewport.width() - self.width() - 8), 8)
        self.raise_()

    def set_replace_mode(self, enabled):
        enabled = bool(enabled)
        if enabled == self.replace_mode:
            return
        self.replace_mode = enabled
        self.disclosure.setArrowType(Qt.ArrowType.DownArrow if enabled else Qt.ArrowType.RightArrow)
        self.animation.stop()
        if self.isHidden():
            # Open directly in the requested mode, without briefly showing the
            # previous session's Replace row. Animate only a visible panel.
            self.replace_row.setMaximumHeight(self.replace_row.sizeHint().height() if enabled else 0)
            self.replace_row.setVisible(enabled)
            self.reposition()
            return
        height = self.replace_row.height() if not self.replace_row.isHidden() else 0
        self.replace_row.show()
        self.animation.setStartValue(height)
        self.animation.setEndValue(self.replace_row.sizeHint().height() if enabled else 0)
        self.animation.start()
        self.reposition()

    def finish_animation(self):
        if not self.replace_mode:
            self.replace_row.hide()
        self.reposition()

    def toggle_find(self):
        query = self.window.selected_translation_text()
        if (self.window.tab_bar.tabData(self.window.tab_bar.currentIndex()) or {}).get("kind") == "glossary":
            self.window.glossary.page().find.toggle(query=query)
            return
        self.set_replace_mode(False)
        self.open(query=query)

    def toggle_replace(self):
        query = self.window.selected_translation_text()
        self.set_replace_mode(True)
        self.open(query=query)

    def open(self, *, query=None):
        if (self.window.tab_bar.tabData(self.window.tab_bar.currentIndex()) or {}).get("kind") == "glossary":
            self.window.glossary.page().find.toggle(query=query)
            return
        choose_initial = self.isHidden() or (query is not None and query != self.input.text())
        if choose_initial:
            # Read caret geometry before finish_editing destroys/hides it.
            self._initial_anchor = self.capture_anchor()
            self.dirty = True
        self.view.finish_editing()
        self.update_selection_available()
        self.show()
        self.reposition()
        self.escape.setEnabled(True)
        if query is not None and query != self.input.text():
            self.input.setText(query)
        # A newly seeded query follows the same debounce as typing. Merely
        # focusing an open panel must not restart a pending search.
        if self.dirty and not self.timer.isActive():
            self.rebuild()
        field = self.replacement if self.replace_mode else self.input
        field.setFocus(Qt.FocusReason.ShortcutFocusReason)
        field.selectAll()
        self.view.viewport().update()

    def close_find(self):
        self.timer.stop()
        self._initial_anchor = None
        self._replacement_undo_active = False
        self.animation.stop()
        self.replace_row.setMaximumHeight(self.replace_row.sizeHint().height() if self.replace_mode else 0)
        self.finish_animation()
        self.hide()
        self.escape.setEnabled(False)
        self.view.viewport().update()
        self.view.setFocus()

    def eventFilter(self, watched, event):
        if not getattr(self, "_filters_ready", False) or not isValid(self):
            return False
        viewport = getattr(self, "viewport", None)
        input_widget = getattr(self, "input", None)
        replacement_widget = getattr(self, "replacement", None)
        # Qt may still deliver teardown events after a child C++ object dies.
        if any(widget is None or not isValid(widget) for widget in (
                viewport, input_widget, replacement_widget, getattr(self, "view", None),
                getattr(self, "timer", None), getattr(self, "escape", None))):
            return False
        if watched is viewport and event.type() == QEvent.Type.Resize:
            self.reposition()
        if (not self.isHidden() and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
                and event.key() == Qt.Key.Key_Escape):
            # Panel buttons also need to claim Esc before enclosing workspace
            # actions; all close paths share the same cleanup and focus return.
            if event.type() == QEvent.Type.KeyPress:
                self.close_find()
            event.accept()
            return True
        if watched is input_widget or watched is replacement_widget:
            if self._replacement_undo_active and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
                undo = event.matches(QKeySequence.StandardKey.Undo)
                redo = event.matches(QKeySequence.StandardKey.Redo)
                if undo or redo:
                    if event.type() == QEvent.Type.KeyPress:
                        stack = self.window.model.undo_stack
                        (stack.undo if undo else stack.redo)()
                    event.accept()
                    return True
            keys = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            if event.type() == QEvent.Type.ShortcutOverride and event.key() in keys:
                event.accept()
                return True
            if event.type() == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if watched is replacement_widget and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        self.replace_current()
                    else:
                        self.step(-1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
                    return True
        return super().eventFilter(watched, event)

    def selected_entry_ids(self):
        identities = set()
        for index in self.view.selectionModel().selectedIndexes():
            source = self.proxy.mapToSource(index)
            if source.isValid():
                identity = self.window.model.rows[source.row()].get("entry_id")
                if identity is not None:
                    identities.add(identity)
        return frozenset(identities)

    def update_selection_available(self, *args):
        self.in_selection.setEnabled(self.selection_scope is not None or bool(self.selected_entry_ids()))

    def selection_toggled(self, enabled):
        self.selection_scope = self.selected_entry_ids() if enabled else None
        self.query_updated()
        self.update_selection_available()

    def query_updated(self, *args):
        self.field_edited()
        if self._initial_anchor is None:
            self._initial_anchor = self.capture_anchor()
        self.query_changed = True
        self.scope_updated()

    def field_edited(self, *args):
        # After typing, Ctrl+Z belongs to the text field again. Immediately after
        # Replace it must undo the translation even while focus stays here.
        self._replacement_undo_active = False

    def scope_updated(self, *args):
        self.dirty = True
        if not self.isHidden():
            self.timer.start(60)

    def capture_anchor(self):
        """Proxy row, optional RU character position, estimated document y."""
        view = self.view
        delegate = view.itemDelegate()
        for editor, index, field in (
                (delegate.editor, delegate.edit_index, "ru_text"),
                (view.en_selection, getattr(view.en_selection, "index", None), "new_text")):
            if editor is not None and editor.isVisible() and index is not None and index.isValid():
                cursor = editor.textCursor()
                y = editor.viewport().mapTo(view.viewport(), editor.cursorRect(cursor).center()).y()
                position = (len(editor.toPlainText().encode("utf-16-le")[:cursor.position() * 2]
                                .decode("utf-16-le")) if field == "ru_text" else None)
                return index.row(), position, view.verticalOffset() + y
        heights = view._heights
        center = view.verticalOffset() + view.viewport().height() / 2
        index = view.currentIndex()
        if not index.isValid():
            selected = view.selectionModel().selectedRows()
            index = min(selected, key=lambda item: abs(
                heights.prefix(item.row()) + heights.heights[item.row()] / 2 - center), default=index)
        if index.isValid():
            number = index.row()
            return number, None, heights.prefix(number) + heights.heights[number] / 2
        return heights.row_at(center) if len(heights) else -1, None, center

    def nearest_match(self, anchor):
        if not self.matches:
            return -1
        anchor_row, position, y = anchor
        heights = self.view._heights

        def row_distance(identity):
            number = self.match_rows[identity]
            top = heights.prefix(number)
            bottom = top + heights.heights[number]
            return (0 if number == anchor_row else 1,
                    max(top - y, y - bottom, 0), abs(number - anchor_row))

        identity = min(self.by_entry, key=row_distance)
        candidates = self.by_entry[identity]
        number = self.match_rows[identity]
        if number == anchor_row and position is not None:
            return min(candidates, key=lambda item: (
                max(item[1][2] - position, position - item[1][3], 0),
                abs(item[1][2] - position)))[0]
        # Only this candidate row needs exact QTextLayout geometry. No layout
        # or widget creation for the remaining search results.
        index = self.proxy.index(number, 0)
        layout = self.view.itemDelegate().row_layout(self.view._option(index), index)
        row = self.window.model.rows[self.sources[identity]]
        top = heights.prefix(number)
        return min(candidates, key=lambda item: abs(top + self.match_y(layout, row, item[1]) - y))[0]

    def match_y(self, layout, row, hit):
        _, field, start, end = hit
        if field == "key":
            return layout.key.center().y()
        text_layout = layout.source_layout if field == "new_text" else layout.russian_layout
        rect = layout.source if field == "new_text" else layout.russian
        spans = self.display_spans(row, field, start, end, layout.large_change)
        if spans:
            text = text_layout.text()
            first = text_layout.lineForTextPosition(len(text[:spans[0][0]].encode("utf-16-le")) // 2)
            last = text_layout.lineForTextPosition(len(text[:spans[-1][1]].encode("utf-16-le")) // 2 - 1)
            if first.isValid() and last.isValid():
                return rect.y() + (first.y() + last.y() + last.height()) / 2
        return rect.center().y()

    def rebuild(self, *, navigate_query=True):
        self.timer.stop()
        previous = self.matches[self.current] if 0 <= self.current < len(self.matches) else None
        query_changed = self.query_changed
        anchor = self._initial_anchor
        self._initial_anchor = None
        self.query_changed = self.dirty = False
        self.matches, self.by_entry, self.sources = [], defaultdict(list), {}
        self.match_rows = {}
        query = self.input.text()  # Spaces are searchable characters too.
        if query:
            # Python access to source rows; no full UserRole dict conversions.
            numbers = ((i, item[0]) for i, item in enumerate(self.proxy.items) if item[0] is not None) if hasattr(self.proxy, "items") else (
                (i, self.proxy.mapToSource(self.proxy.index(i, 0)).row()) for i in range(self.proxy.rowCount()))
            for proxy_number, number in numbers:
                row = self.window.model.rows[number]
                identity = row.get("entry_id")
                if identity is None or (self.selection_scope is not None and identity not in self.selection_scope):
                    continue
                self.sources[identity] = number
                self.match_rows[identity] = proxy_number
                for start, end in match_spans(row.get("ru_text") or "", query,
                                              case_sensitive=self.case_sensitive.isChecked()):
                    hit = (identity, "ru_text", start, end)
                    self.by_entry[identity].append((len(self.matches), hit))
                    self.matches.append(hit)
        self.current = (self.nearest_match(anchor or self.capture_anchor()) if anchor is not None or query_changed
                        else self.matches.index(previous) if previous in self.matches else -1)
        self.update_counter()
        self.view.viewport().update()
        if navigate_query and (anchor is not None or query_changed) and self.current >= 0 and not self.isHidden():
            self.navigate()

    def replacement_text(self):
        return self.replacement.text()

    def apply_changes(self, changes, *, batch):
        """Use the existing snapshot command, with the local scope already resolved."""
        snapshots = []
        model = self.window.model
        for number, identity, original, value in changes:
            row = model.rows[number]
            if row.get("entry_id") != identity or row.get("ru_text") != original:
                raise ValueError(QCoreApplication.translate("local_find", "Текст изменился. Повторите поиск перед заменой."))
            if value == original:
                continue
            before = {key: deepcopy(row[key]) for key in TRANSLATION_FIELDS if key in row}
            updated = dict(row, ru_text=value, translation_status="EDITED")
            if any(key in row for key in ("translation_state", "translation_origin", "needs_review")):
                updated.update(translation_state="edited", translation_origin="manual")
            if "accepted_source_text" in row:
                refresh_baseline(updated)
            if updated["ru_text"] != value:
                raise ValueError(QCoreApplication.translate("local_find", "Запись {0} должна сохранять whitespace-значение EN. Замена отменена.").format(row.get('key', identity)))
            after = {key: deepcopy(updated[key]) for key in TRANSLATION_FIELDS if key in updated}
            snapshots.append((number, identity, before, after))
        if snapshots:
            command = GlobalReplaceCommand(model, snapshots, ReplaceScope.ALL if batch else ReplaceScope.OCCURRENCE)
            command.setText(QCoreApplication.translate("local_find", "Заменить всё в локальном RU") if batch else QCoreApplication.translate("local_find", "Заменить совпадение RU"))
            model.undo_stack.push(command)
            self._replacement_undo_active = True

    def replace_current(self):
        if not self.replace_mode:
            return
        self.view.finish_editing()
        if self.dirty:
            self.rebuild(navigate_query=False)
        if not self.matches:
            return
        if self.current < 0:
            self.current = 0
        identity, field, start, end = self.matches[self.current]
        number = self.sources[identity]
        original = self.window.model.rows[number]["ru_text"] or ""
        replacement = self.replacement_text()
        value = original[:start] + replacement + original[end:]
        following = {hit[0] for hit in self.matches[self.current + 1:] if hit[0] != identity}
        try:
            self.apply_changes([(number, identity, original, value)], batch=False)
        except ValueError as error:
            QMessageBox.warning(self, QCoreApplication.translate("local_find", "Замена не выполнена"), str(error))
            return
        self.rebuild(navigate_query=False)
        if self.matches:
            # Skip freshly inserted matches, including replacement == query.
            target = next((i for i, hit in enumerate(self.matches)
                           if hit[0] == identity and hit[2] >= start + len(replacement)), None)
            if target is None:
                target = next((i for i, hit in enumerate(self.matches) if hit[0] in following), 0)
            self.current = target
            self.navigate()

    def replace_all(self):
        if not self.replace_mode:
            return
        self.view.finish_editing()
        self.rebuild(navigate_query=False)
        query = self.input.text()
        if not query or not self.matches:
            return
        replacement = self.replacement_text()
        changes = []
        for identity in self.by_entry:
            number = self.sources[identity]
            original = self.window.model.rows[number]["ru_text"] or ""
            chunks, previous = [], 0
            for _, (_, _, start, end) in self.by_entry[identity]:
                chunks.extend((original[previous:start], replacement))
                previous = end
            chunks.append(original[previous:])
            value = "".join(chunks)
            if value != original:
                changes.append((number, identity, original, value))
        if not changes:
            return
        settings = (query, replacement, self.case_sensitive.isChecked(), self.selection_scope)
        scope = QCoreApplication.translate("local_find", "Только зафиксированное выделение текущей вкладки.") if self.selection_scope is not None else QCoreApplication.translate("local_find", "Только текущий отображаемый набор.")
        answer = QMessageBox.question(self, QCoreApplication.translate("local_find", "Заменить всё в рабочем RU?"),
            QCoreApplication.translate("local_find", "{0}\nСовпадений: {1} · Записей: {2}").format(scope, len(self.matches), len(changes)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.dirty or settings != (self.input.text(), self.replacement_text(), self.case_sensitive.isChecked(), self.selection_scope):
            self.rebuild(navigate_query=False)
            return
        try:
            self.apply_changes(changes, batch=True)
        except ValueError as error:
            QMessageBox.warning(self, QCoreApplication.translate("local_find", "Замена не выполнена"), str(error))
        self.rebuild(navigate_query=False)

    def update_counter(self):
        self.counter.setText(f"{self.current + 1} / {len(self.matches)}")

    def step(self, direction):
        if (self.window.tab_bar.tabData(self.window.tab_bar.currentIndex()) or {}).get("kind") == "glossary":
            self.window.glossary.page().find.step(direction)
            return
        if self.isHidden():
            self.open()
            return
        self.view.finish_editing()
        if self.dirty:
            self.rebuild(navigate_query=False)
        if self.matches:
            self.current = ((self.current + direction) % len(self.matches) if self.current >= 0
                            else (0 if direction > 0 else len(self.matches) - 1))
            self.navigate()

    def navigate(self):
        identity, field, start, end = self.matches[self.current]
        number = self.sources.get(identity)
        if number is None:
            return
        index = self.proxy.mapFromSource(self.window.model.index(number, 0))
        if not index.isValid():
            self.scope_updated()
            return
        # Match navigation must not consume the user's multi-card selection.
        self.view.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        row = self.window.model.rows[number]
        # Native scrollTo measures only the target and its viewport. A second
        # correction accounts for newly measured neighbour heights, including
        # matches deep inside a single very tall localization entry.
        for _ in range(2):
            option = self.view._option(index)
            layout = self.view.itemDelegate().row_layout(option, index)
            center = option.rect.y() + self.match_y(layout, row, self.matches[self.current])
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.value() + round(center - self.view.viewport().height() / 2))
        self.update_counter()
        self.view.viewport().update()

        # Navigation may scroll/reposition viewport children. The search panel
        # remains anchored; never reopen it or reset its mode/query here.
        if not self.isHidden():
            self.reposition()

    @staticmethod
    def display_spans(row, field, start, end, clean):
        if field != "new_text" or clean:
            return [(start, end)]
        current, displayed, spans = 0, 0, []
        for segment in row["source_diff"]:
            length = len(segment["text"])
            if segment["type"] != "removed":
                left, right = max(start, current), min(end, current + length)
                if left < right:
                    spans.append((displayed + left - current, displayed + right - current))
                current += length
            displayed += length
        return spans

    def is_current_row(self, row):
        return (not self.isHidden() and 0 <= self.current < len(self.matches)
                and self.matches[self.current][0] == row.get("entry_id"))

    def selections(self, row, field, layout):
        if self.isHidden():
            return []
        text_layout = layout.source_layout if field == "new_text" else layout.russian_layout
        text = text_layout.text()
        result = []
        for number, hit in self.by_entry.get(row.get("entry_id"), ()):
            if hit[1] != field:
                continue
            for start, end in self.display_spans(row, field, hit[2], hit[3], layout.large_change):
                selection = QTextLayout.FormatRange()
                selection.start = len(text[:start].encode("utf-16-le")) // 2
                selection.length = len(text[start:end].encode("utf-16-le")) // 2
                selection.format = QTextCharFormat()
                selection.format.setBackground(theme.color('search.active_match.bg' if number == self.current else 'search.match.bg'))
                result.append(selection)
        return result

    def paint_key(self, painter, rect, shown, metrics, row):
        if self.isHidden():
            return
        prefix = 2 if row.get("kind") == "DISABLED_ENTRY" else 0
        painter.save()
        painter.setClipRect(rect)
        for number, hit in self.by_entry.get(row.get("entry_id"), ()):
            if hit[1] == "key":
                start, end = hit[2] + prefix, hit[3] + prefix
                if start >= len(shown) or "…" in shown[:end]:
                    continue
                x = metrics.horizontalAdvance(shown[:start])
                width = metrics.horizontalAdvance(shown[:end]) - x
                painter.fillRect(QRectF(rect.x() + x, rect.y(), width, rect.height()),
                                 theme.color('search.active_match.bg' if number == self.current else 'search.match.bg'))
        painter.restore()
