"""In-memory project artwork; fallback gradients are deterministic and never saved."""

import hashlib
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient


class ProjectThumbnails:
    def __init__(self):
        self.images = OrderedDict()
        self.placeholders = OrderedDict()

    @staticmethod
    def remember(cache, key, image):
        cache[key] = image
        cache.move_to_end(key)
        while len(cache) > 40:
            cache.popitem(last=False)
        return image

    def refresh(self):
        # Refresh on returning to Start Window, not on every paint/hover.
        self.images.clear()

    def image(self, project_id, project_file):
        path = str(Path(project_file).parent / 'assets' / 'thumbnail.png')
        if path not in self.images:
            image = QPixmap(path)
            if not image.isNull():
                image = image.scaled(960, 540, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
            self.remember(self.images, path, image)
        image = self.images[path]
        if not image.isNull():
            return image
        if project_id not in self.placeholders:
            self.remember(self.placeholders, project_id, self.placeholder(project_id))
        return self.placeholders[project_id]

    @staticmethod
    def placeholder(project_id):
        seed = hashlib.sha256(project_id.encode('utf-8')).digest()
        image = QPixmap(640, 360)
        image.fill(QColor('#18252a'))
        painter = QPainter(image)
        try:
            base = QLinearGradient(0, 0, 640, 360)
            base.setColorAt(0, QColor('#17272c'))
            base.setColorAt(0.55, QColor('#202932'))
            base.setColorAt(1, QColor('#24232c'))
            painter.fillRect(image.rect(), base)
            for i, color in enumerate(('#426b61', '#365e79', '#625277', '#796547')):
                x = 640 * (0.1 + seed[i * 3] / 255 * 0.8)
                y = 360 * (0.05 + seed[i * 3 + 1] / 255 * 0.9)
                radius = 210 + seed[i * 3 + 2] / 255 * 190
                glow = QRadialGradient(QPointF(x, y), radius)
                tint = QColor(color)
                tint.setAlpha(95)
                glow.setColorAt(0, tint)
                tint.setAlpha(0)
                glow.setColorAt(1, tint)
                painter.fillRect(image.rect(), glow)
        finally:
            painter.end()
        return image


def paint_cover(painter, destination, image):
    """Center crop in source coordinates, preserving image aspect ratio."""
    if destination.width() <= 0 or destination.height() <= 0 or image.isNull():
        return
    ratio = max(destination.width() / image.width(), destination.height() / image.height())
    width, height = destination.width() / ratio, destination.height() / ratio
    source = QRectF((image.width() - width) / 2, (image.height() - height) / 2, width, height)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawPixmap(QRectF(destination), image, source)
