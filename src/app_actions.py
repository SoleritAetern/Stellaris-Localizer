"""Shared public links and local help actions; independent of the calling UI."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from src import app_info
from src.guide_dialog import GuideDialog


def open_github():
    return QDesktopServices.openUrl(QUrl(app_info.GITHUB_URL))


def open_discord():
    if app_info.DISCORD_URL:
        return QDesktopServices.openUrl(QUrl(app_info.DISCORD_URL))
    return False


def open_boosty():
    return QDesktopServices.openUrl(QUrl(app_info.BOOSTY_URL))


def show_guide(parent=None):
    dialog = GuideDialog(parent)
    try:
        dialog.exec()
    finally:
        dialog.deleteLater()
