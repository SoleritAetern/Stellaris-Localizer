"""Shared immediate captions for compact push buttons and activity-bar actions."""

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import QLabel, QPushButton, QToolButton


class _HoverCaption(QObject):
    def __init__(self, button, label, placement):
        super().__init__(button)
        self.button = button
        self.placement = placement
        self.window = None
        button.setAccessibleName(label)
        button.setToolTip(label)
        self.label = QLabel(label, button, Qt.WindowType.ToolTip
                            | Qt.WindowType.WindowTransparentForInput
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.label.setProperty("role", "hoverCaption")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.label.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        font = button.font()
        font.setPointSizeF(max(8, font.pointSizeF() - 1))
        self.label.setFont(font)
        self.label.hide()
        button.installEventFilter(self)

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.button:
            if kind == QEvent.Type.Show and self.window is None:
                self.window = self.button.window()
                self.window.installEventFilter(self)
            elif kind == QEvent.Type.ToolTip:
                return True  # Suppress Qt's delayed duplicate popup.
            elif kind == QEvent.Type.Enter:
                self.label.ensurePolished()
                self.label.adjustSize()
                x = (self.button.width() - self.label.width()) // 2
                y = self.button.height() + 3
                if self.placement == "above":
                    y = -self.label.height() - 3
                elif self.placement == "right":
                    x = self.button.width() + 3
                    y = (self.button.height() - self.label.height()) // 2
                self.label.move(self.button.mapToGlobal(QPoint(x, y)))
                self.label.show()
            elif kind in (QEvent.Type.Leave, QEvent.Type.Hide, QEvent.Type.MouseButtonPress):
                self.label.hide()
        elif watched is self.window and kind in (QEvent.Type.Hide, QEvent.Type.WindowDeactivate,
                                                QEvent.Type.Move, QEvent.Type.Resize):
            self.label.hide()
        return super().eventFilter(watched, event)


class HoverIconButton(QPushButton):
    def __init__(self, label, *, placement="below", parent=None):
        super().__init__(parent)
        self._hover_caption = _HoverCaption(self, label, placement)
        self.caption = self._hover_caption.label


class HoverToolButton(QToolButton):
    def __init__(self, label, *, placement="right", parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._hover_caption = _HoverCaption(self, label, placement)
        self.caption = self._hover_caption.label
