"""Application assets in a source checkout or a PyInstaller bundle."""

from pathlib import Path
import sys

from PySide6.QtGui import QIcon


def asset_path(*parts):
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root.joinpath("assets", *parts)


def themed_svg_icon(filename, size=18):
    """Render an explicitly monochrome action asset, fitting its painted bounds.

    Source files and unrelated decorative/brand images are never modified.
    """
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QBitmap, QImage, QPainter, QPixmap, QRegion
    from PySide6.QtSvg import QSvgRenderer
    from src import theme

    renderer = QSvgRenderer(str(asset_path("icons", filename)))
    if not renderer.isValid():
        return QIcon()
    view = renderer.viewBoxF()
    scale = 256 / max(view.width(), view.height())
    image = QImage(max(1, round(view.width() * scale)), max(1, round(view.height() * scale)),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    bounds = QRegion(QBitmap.fromImage(image.createAlphaMask())).boundingRect()
    if bounds.isEmpty():
        return QIcon()
    image = image.copy(bounds)
    icon = QIcon()
    for ratio in (1, 2, 3):
        pixels = size * ratio
        fitted = image.scaled(QSize(pixels, pixels), Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        for mode, role in ((QIcon.Mode.Normal, "icon.default"),
                           (QIcon.Mode.Active, "icon.hover"),
                           (QIcon.Mode.Disabled, "icon.disabled")):
            pixmap = QPixmap(pixels, pixels)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.drawImage((pixels - fitted.width()) // 2, (pixels - fitted.height()) // 2, fitted)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), theme.color(role))
            painter.end()
            pixmap.setDevicePixelRatio(ratio)
            icon.addPixmap(pixmap, mode)
    return icon


def set_application_icon(app):
    # Windows uses this identity to group our windows independently of python.exe.
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("StellarisLocalizer.App")
    path = asset_path("icons", "app.ico")
    if not path.is_file():
        path = asset_path("icons", "app.png")
    app.setWindowIcon(QIcon(str(path)))
