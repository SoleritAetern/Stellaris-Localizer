"""Offline Markdown help, loaded from the same runtime assets as the app icon."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFrame, QTextBrowser, QVBoxLayout

from src import theme
from src.resources import asset_path


class GuideDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Инструкция"))
        self.resize(860, 680)
        self.setMinimumSize(480, 320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.browser = QTextBrowser()
        self.browser.setProperty("role", "documentation")
        self.browser.setFrameShape(QFrame.Shape.NoFrame)
        self.browser.setAccessibleName(self.tr("Инструкция"))
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self.open_link)
        document = self.browser.document()
        document.setDocumentMargin(24)
        font = self.font()
        font.setPointSizeF(font.pointSizeF() + 1)
        document.setDefaultFont(font)
        document.setDefaultStyleSheet(
            f"a {{ color: {theme.css('action.accent')}; }} "
            f"code, pre {{ background-color: {theme.css('surface.control')}; font-family: Consolas; }} "
            "p, li { margin-top: 6px; margin-bottom: 6px; }"
        )
        path = asset_path("docs", "guide.md")
        document.setBaseUrl(QUrl.fromLocalFile(str(path)))
        try:
            self.browser.setMarkdown(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            self.browser.setPlainText(self.tr("Не удалось открыть локальную инструкцию."))
        layout.addWidget(self.browser, 1)

    def open_link(self, url):
        base = self.browser.document().baseUrl()
        resolved = base.resolved(url)
        if resolved.hasFragment() and resolved.adjusted(QUrl.UrlFormattingOption.RemoveFragment) == base:
            self.browser.scrollToAnchor(url.fragment())
        elif url.scheme().lower() in ("https", "http", "mailto"):
            QDesktopServices.openUrl(url)
