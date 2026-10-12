"""Translation-only scrollbar presentation; all scrolling stays with QScrollBar."""

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QScrollBar, QStyle, QStyleOptionSlider

from . import theme


class TranslationScrollBar(QScrollBar):
    def __init__(self, position_text, parent=None):
        super().__init__(Qt.Orientation.Vertical, parent)
        self.setObjectName("translationScrollBar")
        self.position_text = position_text
        self._hovered = False
        self._thumb_pressed = False
        self._dragging = False
        self.tip = QLabel(self, Qt.WindowType.ToolTip | Qt.WindowType.WindowTransparentForInput)
        self.tip.setObjectName("scrollPositionTip")
        self.tip.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.tip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.tip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tip.hide()
        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.timeout.connect(self._update_tip)
        self.valueChanged.connect(self._schedule_tip)
        self.rangeChanged.connect(self._schedule_tip)
        self.sliderReleased.connect(self._end_drag)

    def thumb_rect(self):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        return self.style().subControlRect(
            QStyle.ComplexControl.CC_ScrollBar, option,
            QStyle.SubControl.SC_ScrollBarSlider, self)

    def paintEvent(self, event):
        if self.maximum() <= self.minimum():
            return
        # Keep the native hit rectangle and gutter fixed. Only the painted
        # thumb expands, so hover cannot rewrap rows or invalidate layouts.
        rect = QRectF(self.thumb_rect())
        active = self._hovered or self.isSliderDown()
        if not active:
            rect.adjust(2, 0, -2, 0)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("scrollbar.hover" if active else "scrollbar.thumb"))
        painter.drawRoundedRect(rect, rect.width() / 2, rect.width() / 2)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._thumb_pressed = (event.button() == Qt.MouseButton.LeftButton
                               and self.maximum() > self.minimum()
                               and self.thumb_rect().contains(event.position().toPoint()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if (self._thumb_pressed and self.isSliderDown()
                and event.buttons() & Qt.MouseButton.LeftButton):
            self._dragging = True
            self._schedule_tip()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._end_drag()

    def hideEvent(self, event):
        self._end_drag()
        super().hideEvent(event)

    def _end_drag(self):
        self._thumb_pressed = self._dragging = False
        self._tip_timer.stop()
        self.tip.hide()
        self.update()

    def _schedule_tip(self, *args):
        if self._dragging:
            # Read the final position after the view has updated local heights.
            self._tip_timer.start(0)

    def _update_tip(self):
        if not self._dragging or not self.isSliderDown() or not self.isVisible():
            self.tip.hide()
            return
        self.tip.setText(self.position_text())
        self.tip.adjustSize()
        center = self.mapToGlobal(self.thumb_rect().center())
        x = self.mapToGlobal(QPoint(0, 0)).x() - self.tip.width() - 8
        y = center.y() - self.tip.height() // 2
        available = self.screen().availableGeometry()
        x = max(available.left(), min(x, available.right() - self.tip.width() + 1))
        y = max(available.top(), min(y, available.bottom() - self.tip.height() + 1))
        self.tip.move(x, y)
        self.tip.show()
