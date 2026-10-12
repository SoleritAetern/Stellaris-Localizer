"""Small shared presentation pieces for root and nested exact-lookup popups."""

from time import monotonic

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QWidget

from . import theme


def source_label():
    label = QLabel()
    label.setStyleSheet(f"color: {theme.css('text.secondary')};")
    font = label.font()
    if font.pointSizeF() > 0:
        font.setPointSizeF(font.pointSizeF() * 0.9)
    label.setFont(font)
    return label


class LookupIndicator(QWidget):
    def __init__(self, popup):
        super().__init__(popup)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.setInterval(150)
        self.delay.timeout.connect(self._animate)
        self.frames = QTimer(self)
        self.frames.setInterval(30)
        self.frames.timeout.connect(self.update)
        self.started = 0
        popup.installEventFilter(self)
        self.hide()

    def start(self):
        self.stop()
        self.started = monotonic()
        self.delay.start()

    def stop(self):
        self.delay.stop()
        self.frames.stop()
        self.hide()

    def _place(self):
        popup = self.parentWidget()
        self.setGeometry(2, popup.height() - 3, max(0, popup.width() - 4), 2)

    def _animate(self):
        if self.parentWidget().isVisible():
            self._place()
            self.show()
            self.raise_()
            self.frames.start()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self._place()
        elif event.type() == QEvent.Type.Hide:
            self.stop()
        return False

    def paintEvent(self, event):
        segment = max(18, self.width() // 4)
        phase = ((monotonic() - self.started) % 0.9) / 0.9
        x = round(phase * (self.width() + segment) - segment)
        painter = QPainter(self)
        painter.fillRect(x, 0, segment, self.height(), theme.color('action.accent'))
