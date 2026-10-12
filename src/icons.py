"""Small shared vector marks; no per-row widgets or pixmaps."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen


def paint_check(painter, rect, color):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.center().x() - 9, rect.center().y() - 9)
    pen = QPen(QColor(color), 2.3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(QPointF(3, 9))
    path.lineTo(7, 13)
    path.lineTo(15, 4)
    painter.drawPath(path)
    painter.restore()
