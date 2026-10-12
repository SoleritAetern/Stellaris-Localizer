"""Flat model/delegate view with exact geometry only around the viewport."""

from contextlib import contextmanager
from bisect import bisect_left

from PySide6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QModelIndex, QObject, QPersistentModelIndex, QRect, Qt, Signal
from PySide6.QtGui import QPainter, QRegion
from PySide6.QtWidgets import QAbstractItemView, QFrame, QStyle, QStyleOptionViewItem

from .height_index import HeightIndex
from .selection_occurrences import SelectionOccurrences
from .translation_scrollbar import TranslationScrollBar
from .text_overlays import EnglishSelectionOverlay, DetailedDiffPopover, forward_mouse


ROW_APPEARANCE_ROLE = int(Qt.ItemDataRole.UserRole) + 1
BLANK_ROW_HEIGHT = 18
# Qt applies the system wheel-line count: the usual 3 lines give 48 px/notch.
WHEEL_PIXELS_PER_LINE = 16


class LocalizationListView(QAbstractItemView):
    rowActionRequested = Signal(str, QModelIndex)
    rowActionHovered = Signal(QModelIndex, str)
    OVERSCAN_ROWS = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._heights = HeightIndex()
        self._scroll_entry_rows = []
        self._measured = []
        self._generation = 0
        self._layout_width = 0
        self._laying_out = False
        self._materialization_depth = 0
        self._switching_model = False
        self._model_connections = []
        self._size_hint_connection = None
        self._pressed_action = None
        self._hover_index = QPersistentModelIndex()
        self._hover_action = None
        self.en_selection = None
        self.occurrences = SelectionOccurrences(self)
        self.detailed_diff = None
        self.setMouseTracking(True)
        self._word_wrap = True
        self.setVerticalScrollBar(TranslationScrollBar(self.scroll_position_text, self))
        # A constant gutter avoids width oscillation when estimated total height
        # crosses the viewport height during lazy measurement.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setAutoScroll(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def setWordWrap(self, enabled):
        # Compatibility: the existing delegate owns text wrapping.
        self._word_wrap = bool(enabled)

    def wordWrap(self):
        return self._word_wrap

    def layout_width(self):
        return max(self._layout_width or self.viewport().width(), 100)

    def horizontalOffset(self):
        return 0

    def verticalOffset(self):
        return self.verticalScrollBar().value()

    def scroll_position_text(self):
        total = len(self._scroll_entry_rows)
        if not total:
            return "0 / 0"
        row = self._heights.row_at(self.verticalOffset())
        # Top visible entry, or the next entry if the viewport starts in a
        # COMMENT/BLANK. Trailing structure keeps the last entry's ordinal.
        current = min(total, bisect_left(self._scroll_entry_rows, row) + 1)
        return f"{current} / {total}"

    def isIndexHidden(self, index):
        return False

    def _valid(self, index):
        return (index.isValid() and index.model() is self.model() and index.column() == 0
                and not index.parent().isValid() and 0 <= index.row() < len(self._heights))

    def _anchor(self):
        row = self._heights.row_at(self.verticalOffset())
        if row < 0:
            return None
        return (self._heights.identities[row], row,
                self.verticalOffset() - self._heights.prefix(row))

    def _restore_anchor(self, anchor):
        if anchor is None or not self._heights:
            return
        identity, previous_row, offset = anchor
        row = self._heights.positions.get(identity, min(previous_row, len(self._heights) - 1))
        offset = min(max(0, offset), self._heights.heights[row] - 1)
        self.verticalScrollBar().setValue(self._heights.prefix(row) + offset)

    def _rebuild_heights(self, anchor=None):
        """O(N) numeric setup only on model structure changes, never on resize."""
        if self._switching_model:
            return
        anchor = self._anchor() if anchor is None else anchor
        previous = dict(zip(self._heights.identities, self._heights.heights))
        identities, heights = [], []
        entry_rows = []
        model = self.model()
        if model is not None:
            metadata = model.layout_metadata
            for i in range(model.rowCount()):
                identity, kind = metadata(i)
                if kind in ("ENTRY", "DISABLED_ENTRY"):
                    entry_rows.append(i)
                estimate = {"BLANK": BLANK_ROW_HEIGHT, "COMMENT": 30}.get(kind, 96)
                identities.append(identity)
                heights.append(previous.get(identity, estimate))
        self._heights = HeightIndex(identities, heights)
        self._scroll_entry_rows = entry_rows
        self._measured = [-1] * len(heights)
        self._generation += 1
        self._laying_out = True
        try:
            self.updateGeometries()
            self._restore_anchor(anchor)
        finally:
            self._laying_out = False
        self.doItemsLayout()

    def _structure_changed(self, *args):
        self._rebuild_heights()

    def setModel(self, model):
        self.dismiss_text_overlays()
        self.finish_editing()
        for signal, slot in self._model_connections:
            signal.disconnect(slot)
        self._model_connections.clear()
        delegate = self.itemDelegate()
        if hasattr(delegate, "clear_height_cache"):
            delegate.clear_height_cache()
        self._switching_model = True
        try:
            super().setModel(model)
        finally:
            self._switching_model = False
        if model is not None:
            for signal in (model.layoutChanged, model.rowsInserted, model.rowsRemoved, model.rowsMoved):
                signal.connect(self._structure_changed)
                self._model_connections.append((signal, self._structure_changed))
        self._rebuild_heights()

    def reset(self):
        self.dismiss_text_overlays()
        anchor = self._anchor() if hasattr(self, "_heights") else None
        super().reset()
        if hasattr(self, "_heights") and not self._switching_model:
            self._rebuild_heights(anchor)

    def setItemDelegate(self, delegate):
        connection = self._size_hint_connection
        if delegate is self.itemDelegate() and connection:
            return
        self._size_hint_connection = None
        if connection:
            QObject.disconnect(connection)
        super().setItemDelegate(delegate)
        if delegate is not None:
            # Qt disconnects this automatically when the view or delegate dies.
            self._size_hint_connection = delegate.sizeHintChanged.connect(self._size_hint_changed)
        self._generation += 1
        self.doItemsLayout()

    def finish_editing(self):
        self.dismiss_text_overlays()
        popover = getattr(self, "comment_popover", None)
        if popover is not None:
            popover.finish()
        delegate = self.itemDelegate()
        editor = getattr(delegate, "editor", None)
        if editor is not None:
            delegate.commit_and_close(editor)

    def dismiss_text_overlays(self):
        self.occurrences.clear()
        overlay = getattr(self, "en_selection", None)
        if overlay is not None:
            overlay.dismiss()
        detail = getattr(self, "detailed_diff", None)
        if detail is not None:
            detail.hide()

    def show_detailed_diff(self, index):
        if self.detailed_diff is None:
            self.detailed_diff = DetailedDiffPopover(self)
        self.detailed_diff.toggle(index)

    def _text_area_at(self, index, point):
        delegate = self.itemDelegate()
        if index.isValid() and hasattr(delegate, "text_area_at"):
            return delegate.text_area_at(self._option(index), index, point)
        return None

    def _select_english(self, index, event):
        target = QPersistentModelIndex(index)
        self.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        if target.isValid():
            if self.en_selection is None:
                self.en_selection = EnglishSelectionOverlay(self)
            self.en_selection.open(QModelIndex(target), event)
        event.accept()

    def _edit_russian(self, index, event):
        target = QPersistentModelIndex(index)
        self.dismiss_text_overlays()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if not target.isValid():
            return
        self.selectionModel().setCurrentIndex(QModelIndex(target), QItemSelectionModel.SelectionFlag.ClearAndSelect)
        if target.isValid():
            self.edit(QModelIndex(target), QAbstractItemView.EditTrigger.AllEditTriggers, None)
            delegate = self.itemDelegate()
            editor = getattr(delegate, "editor", None)
            if editor is not None and delegate.edit_index == target:
                self.updateEditorGeometries()
                forward_mouse(event, self.viewport(), editor.viewport())
                editor.begin_pointer_drag()
        event.accept()

    def _action_at(self, point):
        index = self.indexAt(point)
        delegate = self.itemDelegate()
        action = (delegate.action_at(self._option(index), index, point)
                  if index.isValid() and hasattr(delegate, "action_at") else None)
        return index, action

    def hover_action(self, index):
        return self._hover_action if self._hover_index == index else None

    def mousePressEvent(self, event):
        index, action = self._action_at(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and action:
            # Consume before QAbstractItemView can change the selection/current row.
            self._pressed_action = (QPersistentModelIndex(index), action)
            event.accept()
            return
        self._pressed_action = None
        if event.button() == Qt.MouseButton.LeftButton and not event.modifiers():
            area = self._text_area_at(index, event.position().toPoint())
            if area == "EN":
                self._select_english(index, event)
                return
            if area == "RU" and index.flags() & Qt.ItemFlag.ItemIsEditable:
                self._edit_russian(index, event)
                return
        if event.button() == Qt.MouseButton.RightButton and index.isValid():
            if index.flags() & Qt.ItemFlag.ItemIsSelectable and not self.selectionModel().isSelected(index):
                self.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        pressed = self._pressed_action
        if pressed is not None and event.button() == Qt.MouseButton.LeftButton:
            self._pressed_action = None
            index, action = self._action_at(event.position().toPoint())
            if pressed == (QPersistentModelIndex(index), action):
                self.rowActionRequested.emit(action, index)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        index, action = self._action_at(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and action:
            event.accept()
            return
        area = self._text_area_at(index, event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and not event.modifiers() and area == "EN":
            self._select_english(index, event)
            return
        super().mouseDoubleClickEvent(event)
        # Keep the existing delegate editor activation. Forward the initial
        # double click so read-mode RU immediately selects a word/construction
        # rather than leaving the whole translation selected by setEditorData.
        delegate = self.itemDelegate()
        if (area == "RU" and event.button() == Qt.MouseButton.LeftButton
                and getattr(delegate, "editor", None) is not None and delegate.edit_index == index):
            forward_mouse(event, self.viewport(), delegate.editor.viewport())

    def mouseMoveEvent(self, event):
        index, action = self._action_at(event.position().toPoint())
        hovered = QPersistentModelIndex(index) if action else QPersistentModelIndex()
        if (hovered, action) != (self._hover_index, self._hover_action):
            previous = QModelIndex(self._hover_index)
            self._hover_index, self._hover_action = hovered, action
            self.rowActionHovered.emit(index, action or "")
            for changed in (previous, index):
                if changed.isValid():
                    self.viewport().update(self.visualRect(changed))
        if self._pressed_action is None:
            super().mouseMoveEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.Leave and hasattr(self, "_hover_index") and self._hover_index.isValid():
            rect = self.visualRect(QModelIndex(self._hover_index))
            self._hover_index, self._hover_action = QPersistentModelIndex(), None
            self.rowActionHovered.emit(QModelIndex(), "")
            self.viewport().update(rect)
        return super().viewportEvent(event)

    def _option(self, index):
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        option.index = index
        option.rect = self.visualRect(index)
        selection = self.selectionModel()
        if selection is not None and selection.isSelected(index):
            option.state |= QStyle.StateFlag.State_Selected
        else:
            option.state &= ~QStyle.StateFlag.State_Selected
        if index == self.currentIndex() and self.hasFocus():
            option.state |= QStyle.StateFlag.State_HasFocus
        else:
            option.state &= ~QStyle.StateFlag.State_HasFocus
        return option

    def _measure(self, row):
        if self._measured[row] == self._generation:
            return
        index = self.model().index(row, 0)
        height = self.itemDelegate().sizeHint(self._option(index), index).height()
        self._heights.update(row, height)
        self._measured[row] = self._generation

    def _measure_bottom(self):
        remaining = self.viewport().height()
        row = len(self._heights) - 1
        while row >= 0 and remaining > 0:
            self._measure(row)
            remaining -= self._heights.heights[row]
            row -= 1
        for extra in range(row, max(-1, row - self.OVERSCAN_ROWS), -1):
            self._measure(extra)

    @contextmanager
    def defer_materialization(self):
        """Apply tab geometry/filter/position before measuring its visible range."""
        self._materialization_depth += 1
        try:
            yield
        finally:
            self._materialization_depth -= 1
            if not self._materialization_depth:
                self._ensure_window()
                self.viewport().update()

    def _ensure_window(self):
        if (self._materialization_depth or self._laying_out or not self.isVisible() or not self._heights
                or not hasattr(self.itemDelegate(), "row_layout")):
            return
        # A Qt-delayed layout can run from the base resizeEvent before our own
        # resize handler completes. It must use the actual viewport width too.
        width = max(100, self.viewport().width())
        if width != self._layout_width:
            self._layout_width = width
            self._generation += 1
        self._laying_out = True
        try:
            anchor = self._anchor()
            bar = self.verticalScrollBar()
            at_bottom = bar.maximum() > 0 and bar.value() == bar.maximum()
            top = anchor[1]
            delegate = self.itemDelegate()
            edit_index = getattr(delegate, "edit_index", QModelIndex())
            if getattr(delegate, "editor", None) is not None and edit_index.isValid():
                if 0 <= edit_index.row() < len(self._heights):
                    self._measure(edit_index.row())
            if at_bottom:
                self._measure_bottom()
                self.updateGeometries()
                bar.setValue(bar.maximum())
                self.updateEditorGeometries()
                return
            # Measure the anchor and only three preceding rows. Keep its screen
            # position while estimates above it are replaced by exact heights.
            for row in range(max(0, top - self.OVERSCAN_ROWS), top + 1):
                self._measure(row)
            offset = min(anchor[2], self._heights.heights[top] - 1)
            y = -offset
            row = top
            # Decide the next row using the height just measured, not a batch of
            # stale estimates which could expand into thousands of offscreen rows.
            while row < len(self._heights) and y < self.viewport().height():
                self._measure(row)
                y += self._heights.heights[row]
                row += 1
            for extra in range(row, min(len(self._heights), row + self.OVERSCAN_ROWS)):
                self._measure(extra)
            self.updateGeometries()
            self._restore_anchor(anchor)
            desired = self._heights.prefix(top) + min(anchor[2], self._heights.heights[top] - 1)
            if bar.value() < desired:
                # The document got shorter near its end. Fill the newly exposed
                # viewport backwards, without measuring the rest of the document.
                self._measure_bottom()
                self.updateGeometries()
                bar.setValue(bar.maximum())
            self.updateEditorGeometries()
        finally:
            self._laying_out = False

    def doItemsLayout(self):
        # All callers, including Qt's delayed layout requests, stay virtual.
        if not hasattr(self, "_heights"):
            return
        # QAbstractItemView only cancels its pending layout timer and updates
        # scrollbar geometry here; unlike QListView it does not lay out rows.
        super().doItemsLayout()
        self._ensure_window()
        self.viewport().update()

    def finish_resize(self):
        width = max(100, self.viewport().width())
        if width != self._layout_width:
            self._layout_width = width
            # The previous numeric heights remain estimates. No O(N) invalidation
            # and no clearing/destruction of thousands of cached QTextLayouts.
            self._generation += 1
        self.doItemsLayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_heights"):
            self.finish_resize()
            delegate = self.itemDelegate()
            if getattr(delegate, "editor", None) is not None:
                delegate.schedule_editor_refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.finish_resize()

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, "_heights") and event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._generation += 1
            self.doItemsLayout()

    def set_bottom_content_inset(self, pixels):
        self._bottom_content_inset = max(0, int(pixels))
        self.updateGeometries()
        self.viewport().update()

    def updateGeometries(self):
        if not hasattr(self, "_heights"):
            return
        self.horizontalScrollBar().setRange(0, 0)
        bar = self.verticalScrollBar()
        bar.setPageStep(max(1, self.viewport().height()))
        # Retain native fractional-angle accumulation and system wheel settings.
        bar.setSingleStep(WHEEL_PIXELS_PER_LINE)
        inset = getattr(self, "_bottom_content_inset", 0) if self._heights else 0
        bar.setRange(0, max(0, self._heights.total + inset - self.viewport().height()))

    def wheelEvent(self, event):
        delta = event.pixelDelta().y()
        if delta and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            # QScrollBar's angle-based wheel path loses precise device pixels.
            # Use the existing pixel scrollbar and its virtualization callback;
            # no animation, extra refresh, or row-based rounding is needed.
            bar = self.verticalScrollBar()
            previous = bar.value()
            bar.setValue(previous - delta)
            event.setAccepted(bar.value() != previous)
            return
        # Preserve native angle accumulation, modifiers and scroll phases.
        super().wheelEvent(event)

    def scrollContentsBy(self, dx, dy):
        if not hasattr(self, "_heights") or self._laying_out:
            return
        self._ensure_window()
        self.updateEditorGeometries()
        self.viewport().update()

    def visualRect(self, index):
        if not self._valid(index):
            return QRect()
        row = index.row()
        return QRect(0, self._heights.prefix(row) - self.verticalOffset(),
                     self.layout_width(), self._heights.heights[row])

    def indexAt(self, point):
        if not self.viewport().rect().contains(point) or not self._heights:
            return QModelIndex()
        self._ensure_window()
        y = point.y() + self.verticalOffset()
        if y >= self._heights.total:
            return QModelIndex()
        return self.model().index(self._heights.row_at(y), 0)

    def sizeHintForRow(self, row):
        return self._heights.heights[row] if 0 <= row < len(self._heights) else -1

    def sizeHintForColumn(self, column):
        return self.layout_width() if column == 0 else -1

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible):
        if not self._valid(index):
            return
        # An explicit navigation target is about to become visible. Measuring it
        # never requires measuring the rows between it and the old viewport.
        self._laying_out = True
        try:
            self._measure(index.row())
            self.updateGeometries()
            top = self._heights.prefix(index.row())
            height = self._heights.heights[index.row()]
            page = max(1, self.viewport().height() - getattr(self, "_bottom_content_inset", 0))
            value = self.verticalOffset()
            if hint == self.ScrollHint.PositionAtTop:
                value = top
            elif hint == self.ScrollHint.PositionAtBottom:
                value = top + height - page
            elif hint == self.ScrollHint.PositionAtCenter:
                value = top - (page - height) // 2
            elif top < value or height > page:
                value = top
            elif top + height > value + page:
                value = top + height - page
            self.verticalScrollBar().setValue(value)
        finally:
            self._laying_out = False
        self._ensure_window()
        self.viewport().update()

    def relayout_index(self, index, height=None):
        if not self._valid(index):
            return
        row = index.row()
        self._measured[row] = -1
        if height is not None:
            anchor = self._anchor()
            self._laying_out = True
            try:
                self._heights.update(row, height)
                self._measured[row] = self._generation
                self.updateGeometries()
                self._restore_anchor(anchor)
            finally:
                self._laying_out = False
        self.doItemsLayout()

    def _size_hint_changed(self, index):
        self.relayout_index(index)

    def dataChanged(self, top_left, bottom_right, roles=()):
        self.dismiss_text_overlays()
        if ROW_APPEARANCE_ROLE in roles:
            # Metadata-only changes cannot affect wrapping or item heights.
            for row in self._visible_rows():
                if top_left.row() <= row <= bottom_right.row():
                    self.viewport().update(self.visualRect(self.model().index(row, 0)))
            return
        if hasattr(self, "_heights"):
            if self._valid(top_left) and self._valid(bottom_right):
                delegate = self.itemDelegate()
                for row in range(top_left.row(), bottom_right.row() + 1):
                    self._measured[row] = -1
                    if hasattr(delegate, "layout_cache"):
                        delegate.layout_cache.pop(self._heights.identities[row], None)
            else:
                self._generation += 1
        super().dataChanged(top_left, bottom_right, roles)
        self.doItemsLayout()

    def _visible_rows(self):
        if not self._heights:
            return range(0)
        first = self._heights.row_at(self.verticalOffset())
        last = self._heights.row_at(self.verticalOffset() + max(0, self.viewport().height() - 1))
        return range(first, last + 1)

    def paintEvent(self, event):
        self._ensure_window()
        painter = QPainter(self.viewport())
        try:
            painter.setClipRegion(event.region())
            painter.fillRect(event.rect(), self.palette().base())
            for row in self._visible_rows():
                index = self.model().index(row, 0)
                option = self._option(index)
                if event.region().intersects(option.rect):
                    self.itemDelegate().paint(painter, option, index)
        finally:
            painter.end()

    def _selectable(self, row):
        flags = self.model().flags(self.model().index(row, 0))
        return bool(flags & Qt.ItemFlag.ItemIsSelectable and flags & Qt.ItemFlag.ItemIsEnabled)

    def _selection(self, first, last):
        result = QItemSelection()
        start = None
        for row in range(first, last + 1):
            selectable = self._selectable(row)
            if selectable and start is None:
                start = row
            if start is not None and (not selectable or row == last):
                end = row if selectable else row - 1
                result.select(self.model().index(start, 0), self.model().index(end, 0))
                start = None
        return result

    def setSelection(self, rect, flags):
        if not self._heights or self.selectionModel() is None:
            return
        rect = rect.normalized()
        first = self._heights.row_at(rect.top() + self.verticalOffset())
        last = self._heights.row_at(rect.bottom() + self.verticalOffset())
        selection = self._selection(first, last) if (
            rect.right() >= 0 and rect.left() < self.viewport().width()
            and rect.top() + self.verticalOffset() < self._heights.total) else QItemSelection()
        self.selectionModel().select(selection, flags)

    def selectAll(self):
        if self.selectionMode() in (self.SelectionMode.NoSelection, self.SelectionMode.SingleSelection):
            return
        if self._heights:
            self.selectionModel().select(self._selection(0, len(self._heights) - 1),
                                         QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def visualRegionForSelection(self, selection):
        region = QRegion()
        # Selection can span the entire document without requesting any text layout.
        for row in self._visible_rows():
            index = self.model().index(row, 0)
            if selection.contains(index):
                region |= QRegion(self.visualRect(index).intersected(self.viewport().rect()))
        return region

    def moveCursor(self, action, modifiers):
        if not self._heights:
            return QModelIndex()
        current = self.currentIndex().row() if self.currentIndex().isValid() else -1
        direction = -1 if action in (self.CursorAction.MoveUp, self.CursorAction.MovePrevious,
                                      self.CursorAction.MovePageUp, self.CursorAction.MoveEnd) else 1
        if action == self.CursorAction.MoveHome:
            target, direction = 0, 1
        elif action == self.CursorAction.MoveEnd:
            target = len(self._heights) - 1
        elif action in (self.CursorAction.MovePageUp, self.CursorAction.MovePageDown):
            target = self._heights.row_at(self._heights.prefix(max(0, current)) + direction * self.viewport().height())
            if target == current:
                target += direction
        elif action in (self.CursorAction.MoveLeft, self.CursorAction.MoveRight):
            target = max(0, current)
        else:
            target = current + direction if current >= 0 else 0
        target = max(0, min(target, len(self._heights) - 1))
        while 0 <= target < len(self._heights):
            if self._selectable(target):
                return self.model().index(target, 0)
            target += direction
        return self.currentIndex() if self._valid(self.currentIndex()) else QModelIndex()
