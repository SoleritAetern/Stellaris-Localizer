
from PySide6.QtCore import QCoreApplication
import re
from pathlib import Path

from src.project import Project
from src.app_settings import AppSettings
from src.mod_selector import HoverHelpLabel, ModSelector
from src.project_creation import ProjectCreationJob, ProjectCreationProgress
from src.previous_english_status import PreviousEnglishStatus
from src.mod_catalog import ModCatalog
from src.mod_discovery import LOCAL, WORKSHOP
from src.compound_control import CompoundControl
from src.recent_projects import RecentProjects, path_identity
from src.translation_ownership import TranslationOwnershipCheck, project_reference
from src import theme

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Slot

from PySide6.QtWidgets import (
    QDialog,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class FolderRow(QWidget):
    def __init__(self, placeholder="", *, browse_directory=None, compound=False):
        super().__init__()
        self.browse_directory = browse_directory

        self.line_edit = QLineEdit()

        self.line_edit.setPlaceholderText(
            placeholder
        )

        self.button = QPushButton(
            QCoreApplication.translate("project_dialog", "Обзор") if compound
            else QCoreApplication.translate("project_dialog", "Обзор...")
        )

        self.button.clicked.connect(
            self.choose_folder
        )

        layout = QHBoxLayout(self)

        layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        if compound:
            self.control = CompoundControl(self.line_edit, self.button)
            layout.addWidget(self.control)
        else:
            layout.addWidget(self.line_edit)
            layout.addWidget(self.button)

    def choose_folder(self):
        current_path = (
            self.line_edit
            .text()
            .strip()
        )

        folder = QFileDialog.getExistingDirectory(
            self,
            QCoreApplication.translate("project_dialog", "Выберите папку"),
            current_path or str(self.browse_directory or ""),
        )

        if folder:
            self.line_edit.setText(
                folder
            )

    def path(self):
        text = (
            self.line_edit
            .text()
            .strip()
        )

        if not text:
            return None

        return Path(text)

    def set_path(self, path):
        if path:
            self.line_edit.setText(
                str(path)
            )


def project_folder_name(name):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', name.strip()).rstrip(' .')[:96].rstrip(' .')
    if not value or value in ('.', '..'):
        value = 'Проект'
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])', value.split('.')[0], re.I):
        value = '_' + value
    return value


class ImmediateHelpButton(QToolButton):
    """Small tooltip with no platform show/hide delays."""

    def __init__(self, explanation):
        super().__init__()
        self.setText('?')
        self.setAutoRaise(True)
        self.setAccessibleName(explanation)
        self.preview = QLabel(explanation, self, Qt.WindowType.ToolTip)
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setWordWrap(True)
        self.preview.setMargin(10)
        self.preview.setStyleSheet(
            f"background: {theme.css('surface.popover')}; color: {theme.css('text.primary')}; border: 1px solid {theme.css('border.subtle')};"
        )
        self.preview.hide()

    def enterEvent(self, event):
        super().enterEvent(event)
        bounds = self.screen().availableGeometry()
        self.preview.setFixedWidth(min(380, bounds.width()))
        self.preview.adjustSize()
        point = self.mapToGlobal(QPoint(0, self.height() + 4))
        if point.y() + self.preview.height() > bounds.bottom():
            point.setY(self.mapToGlobal(QPoint(0, 0)).y() - self.preview.height() - 4)
        point.setX(max(bounds.left(), min(point.x(), bounds.right() - self.preview.width() + 1)))
        point.setY(max(bounds.top(), point.y()))
        self.preview.move(point)
        self.preview.show()

    def event(self, event):
        if event.type() in (QEvent.Type.Leave, QEvent.Type.Hide, QEvent.Type.MouseButtonPress):
            if hasattr(self, 'preview'):
                self.preview.hide()
        return super().event(event)


def help_label(text, explanation):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    layout.addWidget(QLabel(text))
    button = ImmediateHelpButton(explanation)
    layout.addWidget(button)
    layout.addStretch()
    return widget


class ProjectDialog(QDialog):
    """Create a project from mod roots, with a secondary storage location."""

    def __init__(self, parent=None, create_only=False, *, settings=None, recents=None, active_project_path=None):
        super().__init__(parent)
        self.project = None
        self._creation_job = None
        self._creation_progress = None
        self.existing_project_request = None
        self.recents = recents if recents is not None else RecentProjects()
        self.active_project_path = active_project_path
        self._ownership_key = None
        self._ownership = {"state": "free"}
        self._create_after_check = False
        self.ownership_check = TranslationOwnershipCheck(self)
        self.ownership_check.finished.connect(self._ownership_finished)
        self.finished.connect(self.ownership_check.stop)
        self.settings = settings if settings is not None else AppSettings()
        self.parent_directory = self.settings.projects_directory
        self._name_edited = False
        self.setWindowTitle(QCoreApplication.translate("project_dialog", "Новый проект — Stellaris Localizer"))
        self.setObjectName('newProjectDialog')
        self.project_name = QLineEdit()
        self.project_name.setPlaceholderText(QCoreApplication.translate("project_dialog", "Название проекта"))
        self.project_name.textEdited.connect(self._name_was_edited)
        self.location_label = QLabel()
        self.location_label.setTextFormat(Qt.TextFormat.PlainText)
        self.location_label.setWordWrap(True)
        self.location_label.setProperty('role', 'muted')
        self.change_location = QPushButton(QCoreApplication.translate("project_dialog", "Изменить расположение…"))
        self.change_location.setFlat(True)
        self.change_location.clicked.connect(self.choose_parent_directory)
        self.mod_catalog = ModCatalog(self)
        self.finished.connect(self.mod_catalog.stop)
        self.source_mod_root = ModSelector(self.settings.mods_directory, settings=self.settings,
                                          catalog=self.mod_catalog, language="english", compound=True,
                                          source_kind=WORKSHOP)
        self.old_en = FolderRow(QCoreApplication.translate("project_dialog", "Необязательно: папка английской локализации для первого сравнения"), compound=True)
        self.old_en_status = PreviousEnglishStatus(self.old_en.line_edit, self)
        self.finished.connect(self.old_en_status.stop)
        self.translation_folder = ModSelector(self.settings.mods_directory, settings=self.settings,
                                             catalog=self.mod_catalog, compound=True, source_kind=LOCAL)
        self.no_translation = QCheckBox(QCoreApplication.translate("project_dialog", "Перевод ещё не создан"))
        self.no_translation.setToolTip(
            QCoreApplication.translate("project_dialog", "Используйте этот вариант, если отдельный русский мод ещё не создан.\nПеревод будет храниться внутри проекта.\nПапку мода можно выбрать позже при загрузке перевода."))
        self.no_translation.toggled.connect(self._translation_mode_changed)
        self.translation_folder.selectionChanged.connect(self._suggest_name)
        self.translation_folder.selectionChanged.connect(self._check_translation)
        self.source_mod_root.selectionChanged.connect(self._suggest_name)
        self.project_name.textChanged.connect(self.update_location)
        form = QVBoxLayout()
        form.setSpacing(10)

        def block(title, explanation, *widgets):
            group = QVBoxLayout()
            group.setSpacing(4)
            group.addWidget(HoverHelpLabel(title, explanation), 0, Qt.AlignmentFlag.AlignLeft)
            for widget in widgets:
                group.addWidget(widget)
            form.addLayout(group)

        self.ownership_panel = QWidget()
        ownership_layout = QVBoxLayout(self.ownership_panel)
        ownership_layout.setContentsMargins(0, 0, 0, 0)
        self.ownership_message = QLabel()
        self.ownership_message.setTextFormat(Qt.TextFormat.PlainText)
        self.ownership_message.setWordWrap(True)
        ownership_layout.addWidget(self.ownership_message)
        ownership_actions = QHBoxLayout()
        self.open_owner_button = QPushButton(QCoreApplication.translate("project_dialog", "Открыть проект"))
        self.open_owner_button.setProperty('role', 'primary')
        self.locate_owner_button = QPushButton(QCoreApplication.translate("project_dialog", "Найти .slproj…"))
        self.locate_owner_button.setProperty('role', 'primary')
        self.open_owner_button.clicked.connect(self._open_owner)
        self.locate_owner_button.clicked.connect(self._locate_owner)
        for button in (self.open_owner_button, self.locate_owner_button):
            ownership_actions.addWidget(button)
        ownership_actions.addStretch()
        ownership_layout.addLayout(ownership_actions)
        self.ownership_panel.hide()
        block(QCoreApplication.translate("project_dialog", "Перевод"),
              QCoreApplication.translate("project_dialog", "Выберите мод перевода из списка или найдите его папку вручную.\nЕсли перевод ещё не создан, отметьте «Перевод ещё не создан»."),
              self.translation_folder, self.ownership_panel, self.no_translation)
        block(QCoreApplication.translate("project_dialog", "Исходный мод"),
              QCoreApplication.translate("project_dialog", "Выберите исходный мод из списка или найдите его папку вручную."),
              self.source_mod_root)
        block(QCoreApplication.translate("project_dialog", "Предыдущая английская локализация    необязательно"),
              QCoreApplication.translate("project_dialog", "Укажите папку предыдущей версии мода или её папку localisation.\nЭто позволит сразу увидеть изменения относительно прошлой версии.\nДальше история источника будет сохраняться автоматически."),
              self.old_en, self.old_en_status)
        name_group = QVBoxLayout()
        name_group.setSpacing(4)
        name_group.addWidget(HoverHelpLabel(
            QCoreApplication.translate("project_dialog", "Название проекта"),
            QCoreApplication.translate("project_dialog", "Название подставляется при выборе мода. Его можно изменить.")),
            0, Qt.AlignmentFlag.AlignLeft)
        name_group.addWidget(self.project_name)
        name_group.addWidget(self.location_label)
        name_group.addWidget(self.change_location, 0, Qt.AlignmentFlag.AlignLeft)
        form.addLayout(name_group)
        self.open_existing_button = QPushButton(QCoreApplication.translate("project_dialog", "Открыть .slproj…"))
        self.open_existing_button.setVisible(not create_only)
        self.open_existing_button.clicked.connect(self.open_existing)
        self.create_button = QPushButton(QCoreApplication.translate("project_dialog", "Создать проект"))
        self.create_button.setProperty('role', 'primary')
        self.create_button.clicked.connect(self.accept_project)
        self.cancel_button = QPushButton(QCoreApplication.translate("project_dialog", "Отмена"))
        self.cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.open_existing_button)
        buttons.addStretch()
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.create_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addLayout(form)
        help_hint = QLabel(QCoreApplication.translate("project_dialog", "Для справки наведите курсор на подсвеченные названия полей."))
        help_hint.setWordWrap(True)
        help_hint.setProperty('role', 'muted')
        layout.addWidget(help_hint)
        layout.addLayout(buttons)
        self.mod_catalog.start(self.settings.mods_directory, self.settings.workshop_directory)
        self.update_location()
        self.ensurePolished()
        theme.align_form_controls(
            (self.translation_folder.control, self.source_mod_root.control,
             self.old_en.control, self.project_name),
        )
        height = layout.totalHeightForWidth(760) if layout.hasHeightForWidth() else layout.sizeHint().height()
        self.resize(760, height)

    def _name_was_edited(self, text):
        self._name_edited = True

    def _suggest_name(self):
        if self._name_edited:
            return
        if self.no_translation.isChecked():
            name = self.source_mod_root.selected_name()
        else:
            name = self.translation_folder.selected_name()
        self.project_name.setText(name)

    def _translation_mode_changed(self, checked):
        self.translation_folder.setEnabled(not checked)
        self._suggest_name()
        self._check_translation()

    def _check_translation(self, *, force=False, create=False):
        target = None if self.no_translation.isChecked() else self.translation_folder.path()
        roots = (str(self.settings.projects_directory), str(self.parent_directory))
        recent_paths = tuple(entry["path"] for entry in self.recents.entries)
        if self.active_project_path is not None:
            recent_paths = (str(self.active_project_path), *recent_paths)
        key = (str(target), roots, recent_paths)
        if not force and key == self._ownership_key:
            return
        self._ownership_key = key
        self._create_after_check = create
        self.ownership_check.cancel()
        if target is None:
            self._ownership = {"state": "free"}
        else:
            self._ownership = {"state": "checking"}
            self.ownership_check.start(target, recent_paths, roots)
        self._render_ownership()

    def _ownership_finished(self, result):
        self._ownership = result
        create, self._create_after_check = self._create_after_check, False
        self._render_ownership()
        if create and result["state"] == "free":
            self._create_project()

    def _render_ownership(self):
        state = self._ownership["state"]
        owners = self._ownership.get("owners", [])
        if state == "checking":
            message = QCoreApplication.translate("project_dialog", "Проверка связи перевода с проектами…")
        elif state == "owned":
            message = (QCoreApplication.translate("project_dialog", "Этот перевод уже используется проектом «{0}».").format(owners[0]["name"])
                       if len(owners) == 1 else QCoreApplication.translate("project_dialog", "Этот перевод уже используется несколькими проектами."))
        elif state == "missing":
            message = QCoreApplication.translate("project_dialog", "Этот перевод уже связан с другим проектом Stellaris Localizer, но проект не найден.")
        elif state == "error":
            message = QCoreApplication.translate("project_dialog", "Не удалось проверить связь перевода с проектами. Создание проекта заблокировано.\n{0}").format(self._ownership["detail"])
        else:
            message = ""
        self.ownership_message.setText(message)
        self.ownership_message.setToolTip("\n".join(str(owner["path"]) for owner in owners))
        role = "text.secondary" if state == "checking" else "status.warning_text"
        self.ownership_message.setStyleSheet(f"color: {theme.css(role)};")
        self.open_owner_button.setVisible(state == "owned")
        self.locate_owner_button.setVisible(state == "missing")
        self.ownership_panel.setVisible(state != "free")
        self.update_location()

    def _open_owner(self):
        owners = self._ownership.get("owners", [])
        if not owners:
            return
        owner = owners[0]
        if len(owners) > 1:
            menu = QMenu(self)
            choices = {menu.addAction(f'{item["name"]} — {item["path"]}'): item for item in owners}
            chosen = menu.exec(self.open_owner_button.mapToGlobal(self.open_owner_button.rect().bottomLeft()))
            menu.deleteLater()
            if chosen not in choices:
                return
            owner = choices[chosen]
        self._request_existing(owner["path"], owner["project_id"],
                               check_target=self._ownership.get("owner_id") is None)

    def _locate_owner(self):
        filename, _ = QFileDialog.getOpenFileName(self, QCoreApplication.translate("project_dialog", "Найти .slproj…"), '', 'Stellaris Localizer (*.slproj)')
        if not filename:
            return
        expected = self._ownership.get("owner_id")
        # Without a manifest, only verified projects actually bound to this
        # translation qualify. A similarly named project is not sufficient.
        self._request_existing(filename, expected, check_target=expected is None)

    def _request_existing(self, filename, expected_id, *, check_target=False):
        from uuid import UUID
        try:
            reference = project_reference(filename)
            if ((expected_id is not None and UUID(reference["project_id"]) != UUID(expected_id))
                    or (check_target and (reference["target"] is None or
                        path_identity(reference["target"]) != path_identity(self.translation_folder.path())))):
                QMessageBox.warning(self, QCoreApplication.translate("project_dialog", "Выбран другой проект"),
                                    QCoreApplication.translate("project_dialog", "Выбранный .slproj не соответствует проекту, связанному с этим переводом."))
                return
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            QMessageBox.critical(self, QCoreApplication.translate("project_dialog", "Не удалось открыть проект"), str(error))
            return
        self.existing_project_request = (reference["path"], reference["project_id"])
        self.accept()

    def proposed_folder(self):
        name = project_folder_name(self.project_name.text())
        parent = self.parent_directory.expanduser().resolve()
        candidate = parent / name
        number = 2
        while candidate.exists() or candidate.is_symlink():
            candidate = parent / f'{name} ({number})'
            number += 1
        return candidate

    def update_location(self):
        try:
            folder = self.proposed_folder()
        except (OSError, ValueError) as error:
            self.location_label.setText(QCoreApplication.translate("project_dialog", "Расположение недоступно: {0}").format(error))
            self.create_button.setEnabled(False)
            return
        self.location_label.setText(QCoreApplication.translate("project_dialog", "Проект будет создан: {0}").format(folder))
        self.create_button.setEnabled(bool(self.project_name.text().strip()) and self._creation_job is None
                                      and self._ownership["state"] == "free")

    def choose_parent_directory(self):
        folder = QFileDialog.getExistingDirectory(self, QCoreApplication.translate("project_dialog", "Папка для проектов"), str(self.parent_directory))
        if folder:
            self.parent_directory = Path(folder)
            self.update_location()
            self._check_translation(force=True)

    def accept_project(self):
        if self._creation_job is not None:
            return
        if not self.no_translation.isChecked() and self.translation_folder.path() is not None:
            self._check_translation(force=True, create=True)
            return
        self._create_project()

    def _create_project(self):
        source_mod_root = self.source_mod_root.path()
        translation_root = None if self.no_translation.isChecked() else self.translation_folder.path()
        if source_mod_root is None or not self.project_name.text().strip():
            QMessageBox.warning(self, QCoreApplication.translate("project_dialog", "Не заполнены поля"), QCoreApplication.translate("project_dialog", "Укажите название проекта и корневую папку исходного мода."))
            return
        if not self.no_translation.isChecked() and (translation_root is None or not translation_root.is_dir()):
            QMessageBox.warning(self, QCoreApplication.translate("project_dialog", "Не выбран русский перевод"),
                                QCoreApplication.translate("project_dialog", "Выберите существующую папку русского мода или отметьте «Перевод ещё не создан»."))
            return
        try:
            folder = self.proposed_folder()
            self.location_label.setText(QCoreApplication.translate("project_dialog", "Проект будет создан: {0}").format(folder))
            app = QCoreApplication.instance()
            job = ProjectCreationJob(
                folder, self.project_name.text(), source_mod_root=source_mod_root,
                old_en=self.old_en.path(), translation_root=translation_root, parent=app,
            )
        except Exception as error:
            QMessageBox.critical(self, QCoreApplication.translate("project_dialog", "Не удалось создать проект"), str(error))
            return
        self._creation_job = job
        self._creation_progress = ProjectCreationProgress(self)
        self.create_button.setEnabled(False)
        job.phaseChanged.connect(self._creation_progress.set_phase, Qt.ConnectionType.QueuedConnection)
        job.finished.connect(self._creation_finished, Qt.ConnectionType.QueuedConnection)
        # App ownership keeps the thread alive even if a parent is forcibly
        # deleted. Qt disconnects destroyed UI receivers; finished still cleans
        # up the job. Normal user dismissal is blocked until completion.
        job.finished.connect(job.deleteLater)
        app.aboutToQuit.connect(job.wait_for_shutdown)
        self._creation_progress.show()
        QTimer.singleShot(0, job, job.start)

    @Slot()
    def _creation_finished(self):
        job = self._creation_job
        job.wait()
        self._creation_job = None
        self._creation_progress.accept()
        self._creation_progress.deleteLater()
        self._creation_progress = None
        self.update_location()
        if job.error is not None:
            QMessageBox.critical(self, QCoreApplication.translate("project_dialog", "Не удалось создать проект"), job.error)
            return
        self.project = job.project
        try:
            self.settings.remember_source(job.source_mod_root)
        except OSError as error:
            QMessageBox.warning(self, QCoreApplication.translate("project_dialog", "Проект создан, настройки не сохранены"), str(error))
        self.accept()

    def reject(self):
        if self._creation_job is None:
            super().reject()

    def closeEvent(self, event):
        if self._creation_job is not None:
            event.ignore()
        else:
            super().closeEvent(event)

    def open_existing(self):
        filename, _ = QFileDialog.getOpenFileName(self, QCoreApplication.translate("project_dialog", "Открыть проект"), '', 'Stellaris Localizer (*.slproj)')
        if not filename:
            return
        try:
            self.project = Project.load(filename)
        except Exception as error:
            QMessageBox.critical(self, QCoreApplication.translate("project_dialog", "Не удалось открыть проект"), str(error))
            return
        self.accept()
