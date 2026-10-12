
from PySide6.QtCore import QCoreApplication
from pathlib import Path
from math import cos, sin, pi

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal, QUrl
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPainterPath, QDesktopServices, QIcon, QPixmap, QPen
from PySide6.QtWidgets import (
    QWidget, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QSizePolicy, QMenu, QStyledItemDelegate, QStyle, QMessageBox, QToolButton, QButtonGroup, QListView,
)

from src.recent_projects import is_available
from src import theme
from src.project_thumbnails import ProjectThumbnails, paint_cover
from src.app_settings import AppSettings
from src.app_info import display_version
from src.i18n import current_ui_language
from src import app_actions, app_info
from src.icon_buttons import HoverIconButton
from src.resources import themed_svg_icon


PROJECT_DETAILS_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def toolbar_icon(kind):
    pixmap = QPixmap(40, 40)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(theme.color('icon.toolbar'), 1.3))
    if kind == "grid":
        for x in (3, 11):
            for y in (3, 11):
                painter.drawRoundedRect(QRectF(x, y, 6, 6), 1, 1)
    elif kind == "list":
        for y in (4, 9, 14):
            painter.drawRoundedRect(QRectF(3, y, 3, 3), .5, .5)
            painter.drawLine(9, y + 1, 17, y + 1)
    elif kind == "globe":
        painter.drawEllipse(QRectF(2, 2, 16, 16))
        painter.drawEllipse(QRectF(7, 2, 6, 16))
        painter.drawLine(2, 10, 18, 10)
    else:
        path = QPainterPath()
        for i in range(32):
            angle = i * pi / 16
            radius = 8 if i % 4 in (1, 2) else 6.3
            x, y = 10 + radius * cos(angle), 10 + radius * sin(angle)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawEllipse(QRectF(7, 7, 6, 6))
    painter.end()
    return QIcon(pixmap)


class RecentProjectDelegate(QStyledItemDelegate):
    def __init__(self, parent):
        super().__init__(parent)
        self.thumbnails = ProjectThumbnails()

    @staticmethod
    def fonts(option):
        title = QFont(option.font)
        title.setBold(True)
        path = QFont(option.font)
        path.setPointSizeF(max(8.0, option.font.pointSizeF() - 1))
        return title, path

    def sizeHint(self, option, index):
        title, path = self.fonts(option)
        text_height = 16 + QFontMetrics(title).height() + 3 + QFontMetrics(path).height()
        details = index.data(PROJECT_DETAILS_ROLE)
        if details and not details[2]:
            text_height += 24
        if self.parent().grid_mode:
            width = self.parent().card_width
            return QSize(width, round(width * 9 / 16) + text_height)
        return QSize(200, max(68, text_height))

    def locate_rect(self, rect, available):
        if available:
            return QRect()
        left = rect.left() + (10 if self.parent().grid_mode else 110)
        return QRect(left, rect.bottom() - 24, min(224, rect.right() - left - 10), 20)

    def paint(self, painter, option, index):
        details = index.data(PROJECT_DETAILS_ROLE)
        if details is None:
            return
        name, path, available = details
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        background = theme.color('card.selected' if selected else 'card.hover' if hovered else 'card.bg')
        border = theme.color('card.selected_border' if selected else 'card.border')
        title_color = theme.color('text.primary' if available else 'card.unavailable_title')
        path_color = theme.color('text.secondary' if available else 'card.unavailable_path')

        painter.save()
        try:
            painter.setClipRect(option.rect)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            card = option.rect.adjusted(0, 1, -1, -1)
            painter.setPen(border)
            painter.setBrush(background)
            painter.drawRoundedRect(QRectF(card).adjusted(0.5, 0.5, -0.5, -0.5), theme.RADIUS, theme.RADIUS)
            grid = self.parent().grid_mode
            image_rect = (QRect(card.left() + 1, card.top() + 1, card.width() - 2, round(card.width() * 9 / 16))
                          if grid else QRect(card.left() + 8, card.top() + 8, 90, card.height() - 16))
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(card).adjusted(1, 1, -1, -1), theme.RADIUS, theme.RADIUS)
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
            if not available:
                painter.setOpacity(0.45)
            paint_cover(painter, image_rect, self.thumbnails.image(index.data(Qt.ItemDataRole.UserRole), path))
            painter.restore()
            title_font, path_font = self.fonts(option)
            title_metrics = QFontMetrics(title_font)
            path_metrics = QFontMetrics(path_font)
            text_left = card.left() + 10 if grid else image_rect.right() + 12
            text_top = image_rect.bottom() + 7 if grid else card.top() + 8
            text_width = max(0, card.right() - text_left - 12)
            title_rect = QRect(text_left, text_top, text_width, title_metrics.height())
            path_rect = QRect(title_rect.left(), title_rect.bottom() + 4, text_width, path_metrics.height())
            title = name
            painter.setFont(title_font)
            painter.setPen(title_color)
            painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             title_metrics.elidedText(title, Qt.TextElideMode.ElideRight, text_width))
            painter.setFont(path_font)
            painter.setPen(path_color)
            painter.drawText(path_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             path_metrics.elidedText(path, Qt.TextElideMode.ElideMiddle, text_width))
            if not available:
                action = self.locate_rect(option.rect, available)
                painter.setPen(theme.color('text.secondary'))
                painter.drawText(action, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, QCoreApplication.translate("start_window", "Не найден · Найти проект…"))
        finally:
            painter.restore()


class RecentProjectList(QListWidget):
    openRequested = Signal(object)
    locateRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.grid_mode = True
        self.card_width = 244
        self._locate_pressed = None
        self.setItemDelegate(RecentProjectDelegate(self))
        self.setUniformItemSizes(False)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QListWidget { border: none; }")
        self.itemDoubleClicked.connect(self.openRequested.emit)

    def set_display_mode(self, mode):
        self.grid_mode = mode == "grid"
        self.setViewMode(QListView.ViewMode.IconMode if self.grid_mode else QListView.ViewMode.ListMode)
        self.setMovement(QListView.Movement.Static)
        self.setDragDropMode(self.DragDropMode.NoDragDrop)
        self.setFlow(QListView.Flow.LeftToRight if self.grid_mode else QListView.Flow.TopToBottom)
        self.setWrapping(self.grid_mode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSpacing(6 if self.grid_mode else 5)
        self._update_grid()
        self.doItemsLayout()
        if self.currentItem() is not None:
            self.scrollToItem(self.currentItem())

    def _update_grid(self):
        if self.grid_mode:
            available = max(100, self.viewport().width())
            self.card_width = max(100, min(244, available - 2 * self.spacing()))
            font = self.font()
            path_font = QFont(font)
            path_font.setPointSizeF(max(8.0, font.pointSizeF() - 1))
            text_height = 19 + QFontMetrics(font).height() + QFontMetrics(path_font).height()
            if any(not self.item(i).data(PROJECT_DETAILS_ROLE)[2] for i in range(self.count())):
                text_height += 24
            self.setGridSize(QSize(self.card_width + 2 * self.spacing(),
                                   round(self.card_width * 9 / 16) + text_height + 2 * self.spacing()))
        else:
            self.setGridSize(QSize())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_grid()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            item = self.currentItem()
            if item is not None and item.isSelected() and not event.isAutoRepeat():
                self.openRequested.emit(item)
            event.accept()
            return
        super().keyPressEvent(event)

    def _locate_at(self, point):
        item = self.itemAt(point)
        if item is not None:
            details = item.data(PROJECT_DETAILS_ROLE)
            if details and self.itemDelegate().locate_rect(self.visualItemRect(item), details[2]).contains(point):
                return item
        return None

    def mousePressEvent(self, event):
        item = self._locate_at(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and item is not None:
            self.setCurrentItem(item)
            self._locate_pressed = item.data(Qt.ItemDataRole.UserRole)
            event.accept()
            return
        self._locate_pressed = None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        project_id, self._locate_pressed = self._locate_pressed, None
        if event.button() == Qt.MouseButton.LeftButton and project_id is not None:
            item = self._locate_at(event.position().toPoint())
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == project_id:
                self.locateRequested.emit(project_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._locate_at(event.position().toPoint()) is not None:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class StartWindow(QWidget):
    newRequested = Signal()
    openRequested = Signal()
    recentRequested = Signal(str)
    locateRequested = Signal(str)
    removeRequested = Signal(str)
    deleteRequested = Signal(str)
    settingsRequested = Signal()

    def __init__(self, recents, *, settings=None):
        super().__init__()
        self.recents = recents
        self.settings = settings if settings is not None else AppSettings()
        self.setObjectName("startWindow")
        self.setStyleSheet(f"""
            QWidget#startWindow QToolButton {{
                border: 1px solid transparent; border-radius: 4px;
                background: transparent; padding: 3px;
            }}
            QWidget#startWindow QToolButton:hover {{ background: {theme.css('surface.hover')}; }}
            QWidget#startWindow QToolButton:checked {{
                background: {theme.css('surface.selected')}; border-color: {theme.css('border.subtle')};
            }}
            QPushButton#startNew, QPushButton#startOpen {{
                padding: 4px 10px; border: 1px solid {theme.css('border.subtle')};
                border-radius: 4px; background: {theme.css('surface.control')};
            }}
            QPushButton#startNew:hover, QPushButton#startOpen:hover {{ background: {theme.css('surface.hover')}; }}
        """)
        self.setWindowTitle(QCoreApplication.translate("start_window", "Stellaris Localizer — проекты"))
        self.resize(920, 640)
        self.setMinimumSize(520, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        title = QLabel("Stellaris Localizer")
        font = title.font()
        font.setPointSize(16)
        title.setFont(font)
        layout.addWidget(title)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.new_button = QPushButton(QCoreApplication.translate("start_window", "+ Новый проект"))
        self.new_button.setObjectName("startNew")
        self.open_button = QPushButton(QCoreApplication.translate("start_window", "Открыть проект"))
        self.open_button.setObjectName("startOpen")
        self.new_button.clicked.connect(self.newRequested.emit)
        self.open_button.clicked.connect(self.openRequested.emit)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch()
        settings_button = QToolButton()
        settings_button.setIcon(toolbar_icon("settings"))
        settings_button.setIconSize(QSize(20, 20))
        settings_button.setFixedSize(28, 28)
        settings_button.setToolTip(QCoreApplication.translate("start_window", "Настройки"))
        settings_button.setAccessibleName(QCoreApplication.translate("start_window", "Настройки"))
        settings_button.clicked.connect(self.settingsRequested.emit)
        buttons.addWidget(settings_button)
        layout.addLayout(buttons)
        heading = QLabel(QCoreApplication.translate("start_window", "Недавние проекты"))
        heading.setProperty("role", "section")
        heading_row = QHBoxLayout()
        heading_row.setSpacing(3)
        heading_row.addWidget(heading)
        heading_row.addStretch()
        self.mode_buttons = {}
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for mode, label in (("grid", QCoreApplication.translate("start_window", "Карточки")), ("list", QCoreApplication.translate("start_window", "Список"))):
            button = QToolButton()
            button.setIcon(toolbar_icon(mode))
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(28, 28)
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.clicked.connect(lambda checked=False, mode=mode: self.set_display_mode(mode))
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            heading_row.addWidget(button)
        layout.addLayout(heading_row)
        self.list = RecentProjectList()
        self.list.setSpacing(5)
        self.set_display_mode(self.settings.view_mode, persist=False)
        self.list.openRequested.connect(self._open_item)
        self.list.locateRequested.connect(self.locateRequested.emit)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.list, 1)
        self.message = QLabel()
        self.message.setProperty("role", "muted")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        version = QLabel(display_version())
        version.setProperty("role", "muted")
        version.setStyleSheet(f"color: {theme.css('text.disabled')}; font-size: 11px;")
        self.footer = QWidget()
        footer = QGridLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setHorizontalSpacing(12)
        left_zone = QWidget()
        left = QHBoxLayout(left_zone)
        left.setContentsMargins(0, 0, 0, 0)
        self.language_button = QToolButton()
        self.language_button.setIcon(toolbar_icon("globe"))
        self.language_button.setIconSize(QSize(18, 18))
        self.language_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.language_button.setFixedHeight(28)
        self.language_button.setToolTip(self.tr("Язык интерфейса"))
        self.language_button.setAccessibleName(self.tr("Язык интерфейса"))
        self.language_button.setStyleSheet(f"color: {theme.css('text.secondary')};")
        self.language_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        language_menu = QMenu(self.language_button)
        self.language_actions = {}
        for code, name in (("ru", "Русский"), ("en", "English")):
            action = language_menu.addAction(name)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, code=code: self._set_ui_language(code))
            self.language_actions[code] = action
        language_menu.aboutToShow.connect(self.refresh_language)
        self.language_button.setMenu(language_menu)
        left.addWidget(self.language_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.language_restart_hint = QLabel(self.tr("Изменение вступит в силу после перезапуска."))
        self.language_restart_hint.setProperty("role", "muted")
        self.language_restart_hint.setWordWrap(True)
        self.language_restart_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        left.addWidget(self.language_restart_hint, 1)
        self.help_links = QWidget()
        self.help_links.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        links = QHBoxLayout(self.help_links)
        links.setContentsMargins(0, 0, 0, 0)
        links.setSpacing(6)
        self.github_button = HoverIconButton("GitHub", placement="above")
        self.guide_button = HoverIconButton(self.tr("Инструкция"), placement="above")
        self.discord_button = HoverIconButton("Discord", placement="above")
        self.boosty_button = HoverIconButton(self.tr("Поддержать на Boosty"), placement="above")
        for button, filename in ((self.github_button, "github.svg"),
                                 (self.guide_button, "guide_book.svg"),
                                 (self.discord_button, "discord.svg"),
                                 (self.boosty_button, "boosty.svg")):
            button.setProperty("role", "icon")
            button.setFixedSize(30, 30)
            button.setIconSize(QSize(17, 17))
            button.setIcon(themed_svg_icon(filename, 17))
            links.addWidget(button)
        self.github_button.clicked.connect(app_actions.open_github)
        self.discord_button.clicked.connect(app_actions.open_discord)
        self.boosty_button.clicked.connect(app_actions.open_boosty)
        self.discord_button.setEnabled(bool(app_info.DISCORD_URL))
        self.guide_button.clicked.connect(lambda: app_actions.show_guide(self))
        footer.addWidget(left_zone, 0, 0)
        footer.addWidget(self.help_links, 0, 1, Qt.AlignmentFlag.AlignCenter)
        footer.addWidget(version, 0, 2, Qt.AlignmentFlag.AlignRight)
        # Equal outer columns keep help centered independently of side content.
        side_width = max(self.language_button.sizeHint().width(), version.sizeHint().width())
        for column in (0, 2):
            footer.setColumnMinimumWidth(column, side_width)
            footer.setColumnStretch(column, 1)
        layout.addWidget(self.footer)
        self.refresh_language()
        self.refresh()

    def refresh_language(self):
        language = self.settings.ui_language
        self.language_button.setText(language.upper())
        for code, action in self.language_actions.items():
            action.setChecked(code == language)
        self.language_restart_hint.setVisible(language != current_ui_language())

    def _set_ui_language(self, language):
        if language != self.settings.ui_language:
            try:
                self.settings.set_ui_language(language)
            except OSError as error:
                QMessageBox.warning(self, self.tr("Не удалось сохранить настройки"), str(error))
        self.refresh_language()

    def set_display_mode(self, mode, *, persist=True):
        mode = mode if mode in ("grid", "list") else "grid"
        self.mode_buttons[mode].setChecked(True)
        self.list.set_display_mode(mode)
        if persist:
            try:
                self.settings.set_view_mode(mode)
            except OSError as error:
                QMessageBox.warning(self, QCoreApplication.translate("start_window", "Не удалось сохранить настройки"), str(error))

    def refresh(self):
        self.list.itemDelegate().thumbnails.refresh()
        selected = self.list.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected is not None and selected.isSelected() else None
        self.list.clear()
        for entry in self.recents.entries:
            available = is_available(entry)
            suffix = "" if available else QCoreApplication.translate("start_window", " — недоступен")
            item = QListWidgetItem(f'{entry["name"]}{suffix}\n{entry["path"]}')
            item.setData(Qt.ItemDataRole.UserRole, entry["project_id"])
            item.setData(PROJECT_DETAILS_ROLE, (entry["name"], entry["path"], available))
            item.setToolTip(entry["path"])
            self.list.addItem(item)
            if entry["project_id"] == selected_id:
                self.list.setCurrentItem(item)
        self.message.setText(self.recents.error or ("" if self.recents.entries else QCoreApplication.translate("start_window", "Недавних проектов пока нет.")))
        self.message.setVisible(bool(self.message.text()))
        self.list._update_grid()

    def showEvent(self, event):
        self.refresh_language()
        self.refresh()
        super().showEvent(event)

    def _open_item(self, item):
        self.recentRequested.emit(item.data(Qt.ItemDataRole.UserRole))

    def _context_menu(self, position):
        item = self.list.itemAt(position)
        if item is None:
            return
        self.list.setCurrentItem(item)
        project_id = item.data(Qt.ItemDataRole.UserRole)
        menu = self.project_menu(project_id)
        menu.exec(self.list.viewport().mapToGlobal(position))
        menu.deleteLater()

    def project_menu(self, project_id):
        menu = QMenu(self)
        menu.addAction(QCoreApplication.translate("start_window", "Открыть")).triggered.connect(lambda: self.recentRequested.emit(project_id))
        entry = self.recents.get(project_id)
        reveal = menu.addAction(QCoreApplication.translate("start_window", "Показать в проводнике"))
        reveal.setEnabled(entry is not None and Path(entry["path"]).parent.is_dir())
        if entry is not None:
            reveal.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(entry["path"]).parent))))
        if entry is not None and not is_available(entry):
            locate = menu.addAction(QCoreApplication.translate("start_window", "Найти перемещённый проект…"))
            locate.triggered.connect(lambda: self.locateRequested.emit(project_id))
        menu.addSeparator()
        menu.addAction(QCoreApplication.translate("start_window", "Убрать из недавних")).triggered.connect(lambda: self.removeRequested.emit(project_id))
        menu.addAction(QCoreApplication.translate("start_window", "Удалить проект…")).triggered.connect(lambda: self.deleteRequested.emit(project_id))
        return menu

    def confirm_deletion(self, name, root):
        box = QMessageBox(self)
        box.setWindowTitle(QCoreApplication.translate("start_window", "Удаление проекта"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(QCoreApplication.translate("start_window", "Удалить проект «{0}»?").format(name))
        box.setInformativeText(
            QCoreApplication.translate("start_window", "Будет полностью удалена папка:\n{0}\n\nВключая project.slproj, snapshots/revisions, output_ru, ui-state и остальные файлы внутри папки проекта.\n\nЭто действие нельзя отменить.").format(root))
        cancel = box.addButton(QCoreApplication.translate("start_window", "Отмена"), QMessageBox.ButtonRole.RejectRole)
        delete = box.addButton(QCoreApplication.translate("start_window", "Удалить проект"), QMessageBox.ButtonRole.DestructiveRole)
        delete.setProperty("role", "danger")
        delete.style().unpolish(delete)
        delete.style().polish(delete)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        accepted = box.clickedButton() == delete
        box.deleteLater()
        return accepted
