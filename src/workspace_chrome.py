"""Small workspace controls; no dependency on localization layout geometry."""

from PySide6.QtCore import QCoreApplication

from PySide6.QtCore import QEvent, QObject, Qt, Slot
from PySide6.QtGui import QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QTabBar, QToolButton, QGraphicsOpacityEffect
from shiboken6 import isValid

from . import theme


class QuietTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.hovered = -1
        self.currentChanged.connect(self.refresh_close_buttons)
        self.tabMoved.connect(self.refresh_close_buttons)

    def refresh_close_buttons(self, *args):
        for index in range(self.count()):
            for side in (QTabBar.ButtonPosition.LeftSide, QTabBar.ButtonPosition.RightSide):
                button = self.tabButton(index, side)
                if button is not None:
                    if (self.tabData(index) or {}).get("pinned"):
                        self.setTabButton(index, side, None)
                        button.deleteLater()
                        continue
                    # Reserve the close button's space so hover never resizes tabs.
                    effect = button.graphicsEffect()
                    if effect is None:
                        effect = QGraphicsOpacityEffect(button)
                        button.setGraphicsEffect(effect)
                    visible = index in (self.currentIndex(), self.hovered)
                    effect.setOpacity(1.0 if visible else 0.0)
                    button.setEnabled(visible)

    def setTabData(self, index, data):
        super().setTabData(index, data)
        self.refresh_close_buttons()

    def tabInserted(self, index):
        super().tabInserted(index)
        self.refresh_close_buttons()

    def mouseMoveEvent(self, event):
        self.hovered = self.tabAt(event.position().toPoint())
        self.refresh_close_buttons()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hovered = -1
        self.refresh_close_buttons()
        super().leaveEvent(event)


class SplitButton(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("splitPanelButton")
        self.setToolTip(QCoreApplication.translate("workspace_chrome", "Открыть нижнюю панель"))
        self.setAccessibleName(self.toolTip())
        self.setCheckable(True)
        self.setFixedSize(28, 26)
        pixmap = QPixmap(36, 36)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(theme.color('icon.default'), 1.4))
        painter.drawRoundedRect(2, 3, 14, 12, 1, 1)
        painter.drawLine(2, 10, 16, 10)
        painter.end()
        self.setIcon(QIcon(pixmap))

    def watch(self, panel):
        self.panel = panel
        panel.installEventFilter(self)
        self.setChecked(not panel.isHidden())

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self.setChecked(not watched.isHidden())
        return super().eventFilter(watched, event)


class WorkspaceStatus(QFrame):
    def __init__(self, window):
        viewport = window.list_view.viewport()
        super().__init__(viewport)
        self.window = window
        self.viewport = viewport
        self._active = True
        self._connections = []
        self.setObjectName("workspaceStatus")
        self.setFixedHeight(22)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 0, 7, 0)
        layout.setSpacing(3)
        self.attention = QPushButton()
        self.attention.setObjectName("attentionCount")
        self.attention.clicked.connect(self.select_attention)
        layout.addWidget(self.attention)
        layout.addWidget(QLabel("·"))
        self.saved = QLabel()
        layout.addWidget(self.saved)
        self.flags = [self.needs_attention(row) for row in window.model.rows]
        self.count = sum(self.flags)
        self._connections = [
            window.model.dataChanged.connect(self.rows_changed),
            window.undo_stack.cleanChanged.connect(self.update_saved),
            viewport.destroyed.connect(self.dispose),
            window.destroyed.connect(self.dispose),
        ]
        self.update_saved(window.undo_stack.isClean())
        self.update_count()
        window.list_view.set_bottom_content_inset(self.height() + 10)
        # Subscribe only after the complete overlay and its state exist.
        self.viewport.installEventFilter(self)
        self.show()
        self.reposition()

    @Slot()
    def dispose(self):
        """Stop external callbacks before parent/viewport children are deleted."""
        if not self._active:
            return
        self._active = False
        connections, self._connections = self._connections, []
        for connection in connections:
            QObject.disconnect(connection)
        viewport, self.viewport = self.viewport, None
        if viewport is not None and isValid(viewport) and isValid(self):
            viewport.removeEventFilter(self)
        self.window = None

    def _alive(self):
        if not self._active:
            return False
        # Shiboken may invalidate the viewport wrapper before destroyed is
        # delivered, while QUndoStack's destructor still emits cleanChanged.
        if any(obj is None or not isValid(obj) for obj in (
                self, self.viewport, self.window, self.attention, self.saved)):
            self.dispose()
            return False
        return True

    def reposition(self):
        if not self._alive():
            return
        layout = self.layout()
        if layout is None or not isValid(layout):
            self.dispose()
            return
        width = min(layout.sizeHint().width(), max(1, self.viewport.width() - 12))
        self.setFixedWidth(width)
        self.move(max(0, self.viewport.width() - width - 6),
                  max(0, self.viewport.height() - self.height() - 5))
        self.raise_()

    def eventFilter(self, watched, event):
        if not self._alive():
            return False
        if (watched is self.viewport
                and event.type() in (QEvent.Type.Resize, QEvent.Type.Show)):
            self.reposition()
        return super().eventFilter(watched, event)

    @staticmethod
    def needs_attention(row):
        return row.get("section") == "CURRENT" and row.get("translation_status") not in ("OK", "NONE")

    def rows_changed(self, first, last, roles=None):
        if not self._alive():
            return
        for number in range(first.row(), last.row() + 1):
            value = self.needs_attention(self.window.model.rows[number])
            self.count += int(value) - int(self.flags[number])
            self.flags[number] = value
        self.update_count()

    def update_count(self):
        if not self._alive():
            return
        self.attention.setText(QCoreApplication.translate("workspace_chrome", "{0} требуют внимания").format(self.count))
        self.reposition()

    @Slot(bool)
    def update_saved(self, clean):
        if not self._alive():
            return
        self.saved.setText(QCoreApplication.translate("workspace_chrome", "Сохранено") if clean else QCoreApplication.translate("workspace_chrome", "● Не сохранено"))
        self.reposition()

    def select_attention(self):
        if not self._alive():
            return
        for button in self.window.button_group.buttons():
            if button.property("filter_mode") == "ATTENTION":
                button.click()
                break
