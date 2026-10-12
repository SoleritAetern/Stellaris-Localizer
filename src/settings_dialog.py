"""Small global Settings form; Cancel leaves preferences untouched."""

from PySide6.QtCore import QCoreApplication

import os
import stat
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QKeySequence

from src.project_dialog import FolderRow
from src.mod_discovery import discover_mods
from src.initial_mod_paths import detect_initial_roots
from src.shortcuts import SHORTCUTS, SHORTCUT_LABELS
from src.i18n import current_ui_language
from src import theme


class PathHelpLabel(QLabel):
    """Immediate help on the label itself, without a separate help control."""

    def __init__(self, text, explanation):
        super().__init__(text)
        self.setAccessibleDescription(explanation)
        self.setProperty("role", "help")
        self.preview = QLabel(explanation, self, Qt.WindowType.ToolTip)
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setWordWrap(True)
        self.preview.setMargin(8)
        self.preview.setStyleSheet(
            f"background: {theme.css('surface.popover')}; color: {theme.css('text.primary')}; "
            f"border: 1px solid {theme.css('border.subtle')};"
        )
        self.preview.hide()

    def enterEvent(self, event):
        super().enterEvent(event)
        bounds = self.screen().availableGeometry()
        self.preview.setFixedWidth(min(410, bounds.width()))
        self.preview.adjustSize()
        point = self.mapToGlobal(QPoint(0, self.height() + 3))
        if point.y() + self.preview.height() > bounds.bottom():
            point.setY(self.mapToGlobal(QPoint(0, 0)).y() - self.preview.height() - 3)
        point.setX(max(bounds.left(), min(point.x(), bounds.right() - self.preview.width() + 1)))
        point.setY(max(bounds.top(), point.y()))
        self.preview.move(point)
        self.preview.show()

    def event(self, event):
        if event.type() in (QEvent.Type.Leave, QEvent.Type.Hide, QEvent.Type.MouseButtonPress):
            preview = getattr(self, "preview", None)
            if preview is not None:
                preview.hide()
        return super().event(event)


def _writable_directory(path):
    if os.name != "nt":
        return os.access(path, os.W_OK | os.X_OK)
    # os.access(W_OK) on Windows does not check the directory ACL. Open the
    # existing directory requesting add-file/add-subdirectory rights instead.
    # OPEN_EXISTING + BACKUP_SEMANTICS creates neither a directory nor a probe file.
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x0002 | 0x0004, 0x1 | 0x2 | 0x4, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        return False
    close(handle)
    return True


def _validation_path(text):
    text = text.strip()
    if not text or any(ord(char) < 32 for char in text):
        raise ValueError("Empty or invalid path")
    path = Path(text).expanduser()
    if os.name == "nt":
        parts = path.parts[1:] if path.anchor else path.parts
        if any(any(char in '<>:"|?*' for char in part)
               or part.endswith((" ", ".")) and part not in (".", "..")
               or Path(part).is_reserved() for part in parts):
            raise ValueError("Invalid Windows path")
    return path.resolve()


def path_status(kind, text):
    """Read-only advisory validation; never creates folders or probe files."""
    if not text.strip() and kind in ("mods", "workshop", "game"):
        return QCoreApplication.translate("settings_dialog", "Путь не указан"), "neutral"
    failure = {"projects": QCoreApplication.translate("settings_dialog", "! Папка недоступна"),
               "mods": QCoreApplication.translate("settings_dialog", "! Папка ваших модов не найдена или недоступна"),
               "workshop": QCoreApplication.translate("settings_dialog", "! Папка Workshop не найдена или недоступна"),
               "game": QCoreApplication.translate("settings_dialog", "! Это не корневая папка Stellaris")}[kind]
    try:
        path = _validation_path(text)
        if kind == "projects":
            ancestor = path
            while True:
                try:
                    info = ancestor.stat()
                    break
                except FileNotFoundError:
                    if ancestor.parent == ancestor:
                        return failure, "warning"
                    ancestor = ancestor.parent
            if not stat.S_ISDIR(info.st_mode) or not _writable_directory(ancestor):
                return failure, "warning"
            if ancestor == path:
                return QCoreApplication.translate("settings_dialog", "✓ Папка доступна"), "success"
            return QCoreApplication.translate("settings_dialog", "Папка будет создана при необходимости"), "neutral"
        if kind in ("mods", "workshop"):
            if not path.is_dir():
                return failure, "warning"
            count = len(discover_mods(path, workshop=kind == "workshop"))
            if not count:
                return QCoreApplication.translate("settings_dialog", "! Папка существует, но подходящие моды не найдены"), "warning"
            message = (QCoreApplication.translate("settings_dialog", "✓ Папка модов найдена · модов: {0}") if kind == "mods"
                       else QCoreApplication.translate("settings_dialog", "✓ Папка Workshop найдена · модов: {0}"))
            return message.format(count), "success"
        if path.name.casefold() == "localisation" and path.is_dir():
            return QCoreApplication.translate("settings_dialog", "! Выберите корневую папку Stellaris уровнем выше"), "warning"
        if (path / "stellaris.exe").is_file() and (path / "localisation").is_dir():
            return QCoreApplication.translate("settings_dialog", "✓ Stellaris найдена · localisation найдена"), "success"
    except (OSError, ValueError, RuntimeError):
        # Invalid/inaccessible paths (including symlink loops) are advisory.
        pass
    return failure, "warning"


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None, *, first_run=False):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(QCoreApplication.translate("settings_dialog", "Первоначальная настройка") if first_run else QCoreApplication.translate("settings_dialog", "Настройки"))
        self.setObjectName("settingsDialog")
        self.resize(700, 320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        layout.addWidget(self.tabs, 1)
        paths_page = QWidget()
        paths_layout = QVBoxLayout(paths_page)
        paths_layout.setContentsMargins(0, 8, 0, 0)
        paths_layout.setSpacing(10)
        self.tabs.addTab(paths_page, QCoreApplication.translate("settings_dialog", "Пути"))
        if first_run:
            introduction = QLabel(QCoreApplication.translate("settings_dialog", "Укажите основные папки. Их можно изменить позже в настройках."))
            introduction.setWordWrap(True)
            introduction.setProperty("role", "muted")
            paths_layout.addWidget(introduction)
        self.projects = FolderRow(compound=True)
        self.projects.set_path(settings.projects_directory)
        self.mods = FolderRow(compound=True)
        self.mods.set_path(settings.mods_directory)
        self.workshop = FolderRow(compound=True)
        self.workshop.set_path(settings.workshop_directory)
        self.game = FolderRow(QCoreApplication.translate("settings_dialog", "Корневая папка установленной Stellaris"), compound=True)
        self.game.set_path(settings.game_directory)
        self.status_labels = {}
        self.auto_find_buttons = {}
        self.status_rows = {}
        fields = (
            ("projects", self.projects, QCoreApplication.translate("settings_dialog", "Папка проектов по умолчанию"),
             QCoreApplication.translate("settings_dialog", "Здесь Stellaris Localizer будет создавать новые проекты.\nИзменение папки не перемещает существующие проекты.")),
            ("mods", self.mods, QCoreApplication.translate("settings_dialog", "Папка ваших модов"),
             QCoreApplication.translate("settings_dialog", "Папка с вашими локальными модами и переводами Stellaris.\nОбычно: Документы\\Paradox Interactive\\Stellaris\\mod")),
            ("workshop", self.workshop, QCoreApplication.translate("settings_dialog", "Папка модов Steam Workshop"),
             QCoreApplication.translate("settings_dialog", "Папка с модами Stellaris, установленными через Steam Workshop.\nОбычно: steamapps\\workshop\\content\\281990")),
            ("game", self.game, QCoreApplication.translate("settings_dialog", "Папка установленной игры"),
             QCoreApplication.translate("settings_dialog", "Корневая папка установленной Stellaris.\nОбычно: steamapps\\common\\Stellaris")),
        )
        for kind, row, title, explanation in fields:
            group = QVBoxLayout()
            group.setSpacing(3)
            label = PathHelpLabel(title, explanation)
            label.setBuddy(row.line_edit)
            group.addWidget(label, 0, Qt.AlignmentFlag.AlignLeft)
            group.addWidget(row)
            status = QLabel()
            status.setTextFormat(Qt.TextFormat.PlainText)
            status.setWordWrap(True)
            font = status.font()
            font.setPointSizeF(max(8.0, font.pointSizeF() - 1))
            status.setFont(font)
            self.status_labels[kind] = status
            if kind in ("mods", "workshop"):
                status_row = QHBoxLayout()
                status_row.setContentsMargins(0, 0, 0, 0)
                status_row.setSpacing(3)
                status_row.addWidget(status)
                find = QToolButton()
                find.setText(QCoreApplication.translate("settings_dialog", "Найти автоматически"))
                find.setFont(font)
                find.setAutoRaise(True)
                find.setCursor(Qt.CursorShape.PointingHandCursor)
                find.setStyleSheet(
                    f"QToolButton {{ color: {theme.css('action.accent')}; background: transparent; "
                    "border: 1px solid transparent; padding: 0px 2px; }"
                    f"QToolButton:hover {{ color: {theme.css('text.primary')}; background: {theme.css('surface.hover')}; }}"
                    f"QToolButton:pressed {{ background: {theme.css('surface.pressed')}; }}"
                    f"QToolButton:focus {{ border-bottom-color: {theme.css('border.focus')}; }}"
                )
                find.clicked.connect(lambda checked=False, kind=kind: self._find_mod_root(kind))
                self.auto_find_buttons[kind] = find
                status_row.addWidget(find)
                status_row.addStretch()
                self.status_rows[kind] = status_row
                group.addLayout(status_row)
            else:
                group.addWidget(status)
            paths_layout.addLayout(group)
            row.line_edit.textChanged.connect(lambda text, kind=kind: self._validate(kind, text))
            self._validate(kind, row.line_edit.text())
        paths_layout.addStretch()
        help_hint = QLabel(QCoreApplication.translate("settings_dialog", "Для справки наведите курсор на подсвеченные названия полей."))
        help_hint.setWordWrap(True)
        help_hint.setProperty("role", "muted")
        paths_layout.addWidget(help_hint)
        shortcuts_page = QWidget()
        shortcuts_layout = QVBoxLayout(shortcuts_page)
        shortcuts_layout.setContentsMargins(0, 8, 0, 0)
        self.tabs.addTab(shortcuts_page, QCoreApplication.translate("settings_dialog", "Горячие клавиши"))
        shortcuts = QGridLayout()
        shortcuts.setVerticalSpacing(2)
        shortcuts.setHorizontalSpacing(16)
        shortcuts.setColumnStretch(0, 1)
        for row, (key, title) in enumerate(SHORTCUT_LABELS):
            label = QLabel(QCoreApplication.translate("shortcuts", title))
            label.setProperty("role", "muted")
            value = QLabel(QKeySequence(SHORTCUTS[key]).toString(QKeySequence.SequenceFormat.NativeText))
            value.setProperty("role", "muted")
            shortcuts.addWidget(label, row, 0)
            shortcuts.addWidget(value, row, 1, Qt.AlignmentFlag.AlignRight)
        shortcuts_layout.addLayout(shortcuts)
        shortcuts_layout.addStretch()
        interface_page = QWidget()
        interface_layout = QVBoxLayout(interface_page)
        interface_layout.setContentsMargins(0, 8, 0, 0)
        self.tabs.addTab(interface_page, self.tr("Интерфейс"))
        language_row = QGridLayout()
        language_label = QLabel(self.tr("Язык интерфейса"))
        self.language_combo = QComboBox()
        self.language_combo.addItem("Русский", "ru")
        self.language_combo.addItem("English", "en")
        self.language_combo.setCurrentIndex(self.language_combo.findData(settings.ui_language))
        language_label.setBuddy(self.language_combo)
        self.language_combo.setAccessibleName(language_label.text())
        language_row.addWidget(language_label, 0, 0)
        language_row.addWidget(self.language_combo, 0, 1)
        language_row.setColumnStretch(2, 1)
        interface_layout.addLayout(language_row)
        self.language_restart_hint = QLabel(self.tr("Изменение вступит в силу после перезапуска."))
        self.language_restart_hint.setProperty("role", "muted")
        self.language_restart_hint.setWordWrap(True)
        interface_layout.addWidget(self.language_restart_hint)
        interface_layout.addStretch()
        self.language_combo.currentIndexChanged.connect(self._update_language_hint)
        self._update_language_hint()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(QCoreApplication.translate("settings_dialog", "Продолжить") if first_run else QCoreApplication.translate("settings_dialog", "Сохранить"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(QCoreApplication.translate("settings_dialog", "Отмена"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)
        self.ensurePolished()
        theme.align_form_controls(tuple(row.control for row in (self.projects, self.mods, self.workshop, self.game)))

    def _update_language_hint(self):
        self.language_restart_hint.setVisible(self.language_combo.currentData() != current_ui_language())

    def _validate(self, kind, text):
        message, state = path_status(kind, text)
        if kind in self.auto_find_buttons:
            empty = not text.strip()
            self.auto_find_buttons[kind].setVisible(empty)
            self.status_rows[kind].setStretch(0, 0 if empty else 1)
            self.status_rows[kind].setStretch(2, 1 if empty else 0)
            # Keep the short prompt/failure and its action on one compact line.
            self.status_labels[kind].setWordWrap(not empty)
            if empty:
                message = QCoreApplication.translate("settings_dialog", "Путь не указан") + " ·"
        status = self.status_labels[kind]
        color = {"success": theme.css('status.ready'), "neutral": theme.css('text.secondary'),
                 "warning": theme.css("status.warning_text")}[state]
        status.setStyleSheet(f"color: {color};")
        status.setText(message)

    def _find_mod_root(self, kind):
        row = self.mods if kind == "mods" else self.workshop
        if row.line_edit.text().strip():
            return
        try:
            local, workshop = detect_initial_roots(
                self.settings.documents(), self.mods.path(), self.game.path(),
                find_local=kind == "mods", find_workshop=kind == "workshop",
            )
            path = local if kind == "mods" else workshop
        except (OSError, ValueError, RuntimeError):
            path = None
        if path is not None:
            # Only the draft field changes; textChanged performs normal validation.
            row.set_path(path)
        else:
            self.status_labels[kind].setText(
                QCoreApplication.translate("settings_dialog", "Не удалось найти папку автоматически.") + " ·"
            )

    def accept(self):
        try:
            projects, mods, game = self.projects.path(), self.mods.path(), self.game.path()
            workshop = self.workshop.path()
            if projects is None:
                raise ValueError(QCoreApplication.translate("settings_dialog", "Укажите папку проектов по умолчанию."))
            paths = [path.expanduser().resolve() if path is not None else None for path in (projects, mods, game)]
            for path in paths[:1]:
                if path is not None and path.exists() and not path.is_dir():
                    raise ValueError(QCoreApplication.translate("settings_dialog", "Путь должен указывать на папку: {0}").format(path))
            self.settings.save(*paths, self.settings.view_mode,
                               workshop=workshop.expanduser().resolve() if workshop is not None else None,
                               ui_language=self.language_combo.currentData())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, QCoreApplication.translate("settings_dialog", "Не удалось сохранить настройки"), str(error))
            return
        super().accept()
