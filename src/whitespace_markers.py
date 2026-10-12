"""Paint boundary/repeated-space hints without changing document text or formats."""

from functools import lru_cache
import re

from PySide6.QtCore import QPointF
from PySide6.QtGui import QFont, QFontMetricsF, QPainter

from . import theme


@lru_cache(maxsize=1024)
def _marker_ranges(text, leading, trailing):
    """Disjoint UTF-16 ranges: boundary spaces or runs of 2+ U+0020 spaces."""
    ranges = []
    previous = qt_end = 0
    for match in re.finditer(r" +", text):
        start, end = match.span()
        if end - start >= 2 or (leading and start == 0) or (trailing and end == len(text)):
            qt_start = qt_end + len(text[previous:start].encode("utf-16-le")) // 2
            qt_end = qt_start + end - start
            ranges.append((qt_start, qt_end))
            previous = end
    return tuple(ranges)


def _cursor_x(line, position):
    value = line.cursorToX(position)
    # PySide versions expose either the x coordinate or (x, adjusted cursor).
    return value[0] if isinstance(value, tuple) else value


def draw_boundary_spaces(painter, layout, origin, *, text=None, leading=True, trailing=True):
    text = layout.text() if text is None else text
    if not text or " " not in text:
        return
    ranges = _marker_ranges(text, leading, trailing)
    if not ranges:
        return
    font = QFont(layout.font())
    if font.pointSizeF() > 0:
        font.setPointSizeF(font.pointSizeF() * 0.8)
    elif font.pixelSize() > 0:
        font.setPixelSize(max(1, round(font.pixelSize() * 0.8)))
    bounds = QFontMetricsF(font).boundingRect("·")
    painter.save()
    try:
        painter.translate(origin)
        painter.setFont(font)
        painter.setPen(theme.color("editor.whitespace_marker"))
        clip = painter.clipBoundingRect() if painter.hasClipping() else None
        for number in range(layout.lineCount()):
            line = layout.lineAt(number)
            if clip is not None and not line.rect().intersects(clip):
                continue
            start, end = line.textStart(), line.textStart() + line.textLength()
            for begin, finish in ranges:
                for position in range(max(start, begin), min(end, finish)):
                    left = _cursor_x(line, position)
                    right = _cursor_x(line, position + 1)
                    x = (left + right) / 2 - bounds.center().x()
                    y = line.y() + line.height() / 2 - bounds.center().y()
                    painter.drawText(QPointF(x, y), "·")
    finally:
        painter.restore()


class BoundaryWhitespaceMixin:
    """Same hints over the native QPlainTextEdit paint, selection and cursor."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        try:
            painter.setClipRegion(event.region())
            block = self.firstVisibleBlock()
            offset = self.contentOffset()
            while block.isValid():
                rect = self.blockBoundingGeometry(block).translated(offset)
                if rect.top() > event.rect().bottom():
                    break
                if block.isVisible() and rect.bottom() >= event.rect().top():
                    draw_boundary_spaces(
                        painter, block.layout(), rect.topLeft(), text=block.text(),
                        leading=not block.previous().isValid(), trailing=not block.next().isValid())
                block = block.next()
        finally:
            painter.end()
