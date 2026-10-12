from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QLayout, QVBoxLayout

from src import app_actions, app_info
from src.icon_buttons import HoverIconButton as _AboutIconButton
from src.resources import themed_svg_icon


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.guide_requested = False
        self.setWindowTitle(self.tr("О программе"))
        # Initial focus belongs to the dialog, but it is not an extra Tab stop.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(6)
        icon = QLabel()
        icon.setPixmap(QApplication.windowIcon().pixmap(40, 40))
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        title = QLabel("Stellaris Localizer")
        font = title.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 2)
        title.setFont(font)
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(QLabel(app_info.display_version()), 0, Qt.AlignmentFlag.AlignHCenter)
        description = QLabel(self.tr("Инструмент для перевода локализации Stellaris."))
        description.setProperty("role", "muted")
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setFixedWidth(max(260, title.sizeHint().width()))
        layout.addWidget(description, 0, Qt.AlignmentFlag.AlignHCenter)
        authors = QLabel(app_info.APP_AUTHORS)
        authors.setProperty("role", "muted")
        links = QVBoxLayout()
        links.setSpacing(4)
        links.addWidget(authors, 0, Qt.AlignmentFlag.AlignHCenter)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addStretch()
        self.github_button = _AboutIconButton("GitHub")
        self.guide_button = _AboutIconButton(self.tr("Инструкция"))
        self.discord_button = _AboutIconButton("Discord")
        self.boosty_button = _AboutIconButton(self.tr("Поддержать на Boosty"))
        for button in (self.github_button, self.guide_button, self.discord_button, self.boosty_button):
            button.setProperty("role", "icon")
            button.setFixedSize(32, 32)
            button.setIconSize(QSize(18, 18))
            actions.addWidget(button)
        actions.addStretch()
        for button, filename in ((self.github_button, "github.svg"),
                                 (self.guide_button, "guide_book.svg"),
                                 (self.discord_button, "discord.svg"),
                                 (self.boosty_button, "boosty.svg")):
            button.setIcon(themed_svg_icon(filename))
        self.github_button.clicked.connect(app_actions.open_github)
        self.discord_button.setEnabled(bool(app_info.DISCORD_URL))
        self.discord_button.clicked.connect(app_actions.open_discord)
        self.boosty_button.clicked.connect(app_actions.open_boosty)
        self.guide_button.clicked.connect(self.open_guide)
        links.addLayout(actions)
        layout.addLayout(links)

    def showEvent(self, event):
        super().showEvent(event)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def event(self, event):
        if event.type() in (QEvent.Type.Hide, QEvent.Type.WindowDeactivate, QEvent.Type.Move):
            for button in self.findChildren(_AboutIconButton):
                button.caption.hide()
        return super().event(event)

    def open_guide(self):
        # The caller opens the independent dialog after this exec() has ended.
        self.guide_requested = True
        self.accept()


def show_about(parent=None):
    dialog = AboutDialog(parent)
    dialog.exec()
    guide_requested = dialog.guide_requested
    dialog.deleteLater()
    if guide_requested:
        app_actions.show_guide(parent)
