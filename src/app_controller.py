"""Own the launch screen and at most one active project window."""

from PySide6.QtCore import QCoreApplication

from pathlib import Path
from copy import copy, deepcopy

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QVBoxLayout, QLabel, QPushButton, QDialogButtonBox

from src.gui import MainWindow
from src.project import Project
from src.project_dialog import ProjectDialog
from src.project_settings_dialog import ProjectSettingsDialog
from src.mod_selector import ModTargetDialog
from src.deploy_dialog import review_deploy, review_takeover
from src.deploy_recovery import foreign_manifest, prepare_takeover, accept_takeover
from src.translation_ownership import inspect_ownership
from src.source_update_preview import analyze_source_update
from src.source_update_dialog import SourceUpdateDialog
from src.recent_projects import RecentProjects, is_available, path_identity
from src.vanilla_reference import VanillaReference
from src.start_window import StartWindow, toolbar_icon
from src.ui_state import ProjectUiState
from src.project_deletion import validate_project_deletion, delete_project_directory
from src.app_settings import AppSettings
from src.settings_dialog import SettingsDialog
from src.user_dictionary_dialog import UserDictionaryDialog
from src.about_dialog import show_about
from src.shortcuts import SHORTCUTS
from src.glossary_ui import GlossaryController


class AppController(QObject):
    def __init__(self, recents=None, parent=None):
        super().__init__(parent)
        self.recents = recents if recents is not None else RecentProjects()
        self.settings = AppSettings()
        self.project = None
        self.window = None
        self.start_window = StartWindow(self.recents, settings=self.settings)
        self.start_window.settingsRequested.connect(self.show_settings)
        self.start_window.newRequested.connect(self.new_project)
        self.start_window.openRequested.connect(self.choose_project)
        self.start_window.recentRequested.connect(self.open_recent)
        self.start_window.locateRequested.connect(self.locate_project)
        self.start_window.removeRequested.connect(self.remove_recent)
        self.start_window.deleteRequested.connect(self.delete_recent_project)

    def launch(self, path=None):
        self.start_window.show()
        try:
            self.settings.initialize_mod_sources()
        except OSError as error:
            QMessageBox.warning(self.start_window,
                                QCoreApplication.translate("settings_dialog", "Не удалось сохранить настройки"), str(error))
        if not self.settings.setup_completed:
            dialog = SettingsDialog(self.settings, self.start_window, first_run=True)
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()
        if path:
            self.open_path(path)

    def show_projects(self):
        self.start_window.show()
        self.start_window.raise_()
        self.start_window.activateWindow()

    def _parent_window(self):
        return self.start_window if self.start_window.isVisible() else self.window

    def choose_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self._parent_window(), QCoreApplication.translate("app_controller", "Открыть проект"), "", "Stellaris Localizer (*.slproj)",
        )
        if path:
            self.open_path(path)

    def new_project(self):
        dialog = ProjectDialog(self._parent_window(), create_only=True, settings=self.settings,
                               recents=self.recents,
                               active_project_path=self.project.file_path if self.project is not None else None)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.existing_project_request is not None:
                path, expected_id = dialog.existing_project_request
                dialog.deleteLater()
                self.open_path(path, expected_id=expected_id)
                return
            project = dialog.project
            # The wizard has already saved the new project, even if the user
            # subsequently cancels switching away from the current one.
            if not self.activate_project(project):
                self._remember(project)
        dialog.deleteLater()

    def show_settings(self):
        dialog = SettingsDialog(self.settings, self._parent_window())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.start_window.set_display_mode(self.settings.view_mode, persist=False)
            self.start_window.refresh_language()
        dialog.deleteLater()

    def show_user_dictionary(self):
        dialog = UserDictionaryDialog(self.settings, self._parent_window())
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def open_path(self, path, expected_id=None):
        if self.project is not None and path_identity(path) == path_identity(self.project.file_path):
            if expected_id is not None and expected_id != self.project.project_id:
                self._wrong_project()
                return False
            self._remember(self.project)
            self.start_window.hide()
            self.window.raise_()
            self.window.activateWindow()
            return True
        try:
            project = Project.load(path)
        except Exception as error:
            QMessageBox.critical(self._parent_window(), QCoreApplication.translate("app_controller", "Не удалось открыть проект"), str(error))
            self.start_window.refresh()
            return False
        if expected_id is not None and project.project_id != expected_id:
            self._wrong_project()
            return False
        return self.activate_project(project)

    def _wrong_project(self):
        QMessageBox.warning(self._parent_window(), QCoreApplication.translate("app_controller", "Выбран другой проект"),
                            QCoreApplication.translate("app_controller", "UUID выбранного файла не совпадает с проектом из списка.\nДля другого проекта используйте «Открыть проект»."))

    def _build_window(self, project):
        vanilla_rows = []
        reference_message = ""
        try:
            self.settings.migrate_legacy_game(project.resolve_path("stellaris"))
        except OSError as error:
            reference_message = QCoreApplication.translate("app_controller", "Не удалось перенести путь справочника в настройки: {0}").format(error)
        game_root = self.settings.game_directory
        stellaris = game_root / "localisation" if game_root is not None else None
        vanilla_reference = VanillaReference(stellaris) if stellaris is not None else None

        def save_project(rows):
            project.entries = rows
            project.save()
            return {"project_path": project.file_path}

        window = MainWindow(
            project.entries, save_callback=save_project, vanilla_rows=vanilla_rows,
            export_callback=self.export_current, project_name=project.name,
            documents=project.documents,
            source_revisions=project.source_revisions,
            source_prefix=project.current_source_prefix,
            deploy_callback=self.deploy_current,
            vanilla_loader=vanilla_reference.load if vanilla_reference is not None else None,
        )
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.explorer_files.bind_project(project)
        window.glossary = GlossaryController(window, project)
        window.open_glossary_tab()
        window.ui_state = ProjectUiState(window, project)
        window.closed.connect(lambda: self._window_closed(window))
        bar = window.menuBar()
        menu = bar.addMenu(QCoreApplication.translate("app_controller", "Проект"))
        menu.addAction(QCoreApplication.translate("app_controller", "Новый проект…")).triggered.connect(self.new_project)
        menu.addAction(QCoreApplication.translate("app_controller", "Открыть проект…")).triggered.connect(self.choose_project)
        menu.addAction(QCoreApplication.translate("app_controller", "К списку проектов…")).triggered.connect(self.show_projects)
        menu.addSeparator()
        window.save_action.setText(QCoreApplication.translate("app_controller", "Сохранить"))
        menu.addAction(window.save_action)
        menu.addSeparator()
        menu.addAction(QCoreApplication.translate("app_controller", "Обновить источники…")).triggered.connect(self.update_source)
        menu.addSeparator()
        menu.addAction(QCoreApplication.translate("app_controller", "Экспортировать локализацию…")).triggered.connect(window.export_changes)
        menu.addAction(QCoreApplication.translate("app_controller", "Загрузить в папку mod…")).triggered.connect(window.deploy_changes)
        menu.addSeparator()
        menu.addAction(QCoreApplication.translate("app_controller", "Настройки проекта…")).triggered.connect(self.show_project_settings)
        menu.addAction(QCoreApplication.translate("app_controller", "Закрыть проект")).triggered.connect(window.close)
        edit = bar.addMenu(QCoreApplication.translate("app_controller", "Правка"))
        for action, title in ((window.undo_action, QCoreApplication.translate("app_controller", "Отменить")), (window.redo_action, QCoreApplication.translate("app_controller", "Повторить")),
                              (window.copy_action, QCoreApplication.translate("app_controller", "Копировать")), (window.paste_action, QCoreApplication.translate("app_controller", "Вставить"))):
            if action is window.copy_action:
                edit.addSeparator()
            action.setText(title)
            edit.addAction(action)
        search = bar.addMenu(QCoreApplication.translate("app_controller", "Поиск"))
        for action, title in ((window.local_find.actions_by_shortcut[SHORTCUTS["find"]], QCoreApplication.translate("app_controller", "Найти в текущей вкладке")),
                              (window.replace_file_action, QCoreApplication.translate("app_controller", "Заменить в текущей вкладке")),
                              (window.global_search_action, QCoreApplication.translate("app_controller", "Поиск по проекту")),
                              (window.replace_project_action, QCoreApplication.translate("app_controller", "Замена по проекту"))):
            action.setText(title)
            if action is window.global_search_action:
                search.addSeparator()
            search.addAction(action)
        view = bar.addMenu(QCoreApplication.translate("app_controller", "Вид"))
        window.toggle_sidebar_action.setText(QCoreApplication.translate("app_controller", "Показать / скрыть боковую панель"))
        view.addAction(window.toggle_sidebar_action)
        view.addAction(QCoreApplication.translate("app_controller", "Открыть / скрыть нижнюю панель")).triggered.connect(window.toggle_secondary_panel)
        application_menu = bar.addMenu(QCoreApplication.translate("app_controller", "Приложение"))
        settings_action = application_menu.addAction(QCoreApplication.translate("app_controller", "Настройки…"))
        settings_action.setToolTip(QCoreApplication.translate("app_controller", "Настройки"))
        settings_action.triggered.connect(self.show_settings)
        window.sidebar.settings_button.setDefaultAction(settings_action)
        window.sidebar.settings_button.setIcon(toolbar_icon("settings"))
        application_menu.addAction(QCoreApplication.translate("app_controller", "Пользовательский словарь…")).triggered.connect(self.show_user_dictionary)
        application_menu.addSeparator()
        application_menu.addAction(QCoreApplication.translate("app_controller", "О программе")).triggered.connect(lambda: show_about(window))
        if reference_message:
            window.statusBar().showMessage(reference_message, 15000)
        return window

    def show_project_settings(self):
        if self.project is None or self.window is None:
            return
        project, window = self.project, self.window

        def save_settings(name, source, target):
            # Validate the final pair of paths without mutating the live project
            # or copying its entries/snapshots. Existing safety checks stay intact.
            candidate = copy(project)
            candidate.paths = dict(project.paths)
            source_changed = source != project.resolve_path("source_mod_root")
            target_changed = target != project.resolve_path("deploy_mod_root")
            if target_changed or target is None:
                candidate.paths["deploy_mod_root"] = str(target) if target else None
                candidate.paths["deploy_ru"] = None  # No legacy fallback after detaching.
            try:
                if source_changed:
                    candidate.set_source_mod_root(source)
                if target_changed and target is not None:
                    if not target.is_dir():
                        raise ValueError(QCoreApplication.translate("app_controller", "Папка русского мода не найдена. Выберите существующую папку."))
                    candidate.set_deploy_path(target)
            except (OSError, ValueError) as error:
                QMessageBox.warning(dialog, QCoreApplication.translate("app_controller", "Не удалось сохранить настройки проекта"), str(error))
                return False
            previous_name, previous_paths = project.name, project.paths
            project.name, project.paths = name, candidate.paths
            saved = False
            try:
                saved = window.save_changes()
            finally:
                if not saved:
                    project.name, project.paths = previous_name, previous_paths
            if not saved:
                return False
            window.project_title = f"{name} — Stellaris Localizer"
            window.update_dirty_state(window.undo_stack.isClean())
            if name != previous_name:
                self._remember(project)
            message = (QCoreApplication.translate("app_controller", "Настройки сохранены. Для загрузки нового источника выберите «Обновить источники…».")
                       if source_changed else QCoreApplication.translate("app_controller", "Настройки проекта сохранены."))
            window.statusBar().showMessage(message, 10000)
            return True

        dialog = ProjectSettingsDialog(project, self.settings, save_settings, window)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def export_current(self):
        if self.project is None:
            return None
        folder = QFileDialog.getExistingDirectory(self.window, QCoreApplication.translate("app_controller", "Экспортировать локализацию — выберите папку"))
        if not folder:
            return None
        def confirm(names):
            preview = "\n".join(names[:8]) + (QCoreApplication.translate("app_controller", "\n…ещё {0}").format(len(names) - 8) if len(names) > 8 else "")
            return QMessageBox.question(self.window, QCoreApplication.translate("app_controller", "Заменить файлы экспорта?"),
                QCoreApplication.translate("app_controller", "В выбранной папке уже есть {0} файлов с такими именами:\n{1}\n\nЗаменить эти файлы? Остальные файлы останутся без изменений.").format(len(names), preview),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        return self.project.export(folder, confirm_overwrite=confirm)

    def configure_deploy_path(self):
        if self.project is None or self.window is None:
            return False
        dialog = ModTargetDialog(self.settings.mods_directory, self.window,
                                 current=self.project.resolve_path("deploy_mod_root"), settings=self.settings)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            path = dialog.selector.path()
        finally:
            dialog.deleteLater()
        previous = self.project.paths.get("deploy_mod_root")
        try:
            self.project.set_deploy_path(path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self.window, QCoreApplication.translate("app_controller", "Небезопасная папка русского мода"), str(error))
            return False
        if not self.window.save_changes():
            self.project.paths["deploy_mod_root"] = previous
            return False
        self.window.statusBar().showMessage(QCoreApplication.translate("app_controller", "Папка русского мода: {0}").format(self.project.resolve_path('deploy_mod_root')), 8000)
        return True

    def configure_source_mod_root(self):
        if self.project is None or self.window is None:
            return
        path = QFileDialog.getExistingDirectory(self.window, QCoreApplication.translate("app_controller", "Корень оригинального мода"),
                                                str(self.project.resolve_path("source_mod_root") or ""))
        if not path:
            return
        previous = self.project.paths.get("source_mod_root")
        try:
            self.project.set_source_mod_root(path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self.window, QCoreApplication.translate("app_controller", "Не удалось изменить папку мода"), str(error))
            return
        if not self.window.save_changes():
            self.project.paths["source_mod_root"] = previous
            return
        self.window.statusBar().showMessage(QCoreApplication.translate("app_controller", "Папка оригинального мода сохранена. Для импорта выберите «Обновить источники»."), 10000)

    def deploy_current(self):
        if self.project is None:
            return None
        if not self.project.paths.get("deploy_mod_root") and not self.configure_deploy_path():
            return None
        project = self.project
        if foreign_manifest(project) is not None:
            def lookup_owner(target):
                # Re-read the recent list on every check, including after preview.
                recent = RecentProjects(self.recents.file_path)
                if recent.error:
                    return {"state": "error", "detail": recent.error}
                paths = [project.file_path, *(entry["path"] for entry in self.recents.entries),
                         *(entry["path"] for entry in recent.entries)]
                return inspect_ownership(target, paths,
                                         (self.settings.projects_directory, project.root.parent),
                                         lambda: False, manifest_owner_only=True)

            plan = prepare_takeover(project, lookup_owner)
            if plan is None:
                return None  # Manifest changed between checks; do not auto-deploy.
            if "owners" in plan:
                owner = plan["owners"][0]
                box = QMessageBox(self.window)
                box.setTextFormat(Qt.TextFormat.PlainText)
                box.setWindowTitle(QCoreApplication.translate("app_controller", "Проект-владелец найден"))
                box.setText(QCoreApplication.translate("app_controller", "Эта папка перевода связана с проектом «{0}». Передача управления недоступна.\n{1}").format(owner["name"], owner["path"]))
                open_button = box.addButton(QCoreApplication.translate("app_controller", "Открыть проект"), QMessageBox.ButtonRole.AcceptRole)
                cancel = box.addButton(QCoreApplication.translate("app_controller", "Отмена"), QMessageBox.ButtonRole.RejectRole)
                box.setDefaultButton(cancel)
                box.exec()
                if box.clickedButton() == open_button:
                    QTimer.singleShot(0, self, lambda: self.open_path(owner["path"], expected_id=owner["project_id"]))
                box.deleteLater()
                return None
            if not review_takeover(plan, self.window):
                return None
            accept_takeover(project, plan, lookup_owner)
        return project.deploy(review=lambda plan: review_deploy(plan, self.window))

    def update_source(self):
        if self.window is None:
            return
        project, window = self.project, self.window
        candidate = None
        try:
            with analyze_source_update(project) as analysis:
                dialog = SourceUpdateDialog(analysis, window)
                try:
                    if dialog.exec() != QDialog.DialogCode.Accepted:
                        return
                    decisions = dict(dialog.decisions)
                finally:
                    dialog.deleteLater()
                if self.project is not project or self.window is not window:
                    return
                analysis.validate(project)
                # Saving/committing working RU and creating a revision are only
                # permitted after the explicit Apply action in Preview.
                if not window.save_changes():
                    return
                updated = project.prepare_source_update(analysis=analysis, rename_decisions=decisions)
                candidate = self._build_window(updated)
                updated.save()
        except Exception as error:
            if candidate is not None:
                candidate.deleteLater()
            QMessageBox.critical(self.window, QCoreApplication.translate("app_controller", "Не удалось обновить источник"), str(error))
            return
        self.activate_project(updated, candidate=candidate)
        if updated.history_cleanup_errors:
            # Keep collected diagnostic text unchanged; localize at display time.
            cleanup_messages = []
            for message in updated.history_cleanup_errors:
                prefix, separator, reason = message.partition(": ")
                cleanup_messages.append(prefix + separator + QCoreApplication.translate("history", reason))
            QMessageBox.warning(self.window, QCoreApplication.translate("app_controller", "Обновление сохранено, очистка не завершена"),
                                "\n".join(cleanup_messages))

    def activate_project(self, project, candidate=None):
        try:
            candidate = candidate if candidate is not None else self._build_window(project)
        except Exception as error:
            QMessageBox.critical(self._parent_window(), QCoreApplication.translate("app_controller", "Не удалось открыть проект"), str(error))
            return False
        old = self.window
        if old is not None:
            if self.project is not None and self.project.project_id == project.project_id:
                # Preserve session history when a source update rebuilds this
                # project's window; never serialize it or share across projects.
                candidate.closed_workspace_tabs.extend(deepcopy(old.closed_workspace_tabs))
            # Keep a visible top-level window while closeEvent runs, so Qt does
            # not quit between closing the old project and showing the new one.
            start_was_visible = self.start_window.isVisible()
            self.start_window.show()
            if not old.close():
                candidate.deleteLater()
                if not start_was_visible:
                    self.start_window.hide()
                old.raise_()
                old.activateWindow()
                return False
        self.project = project
        self.window = candidate
        candidate.ui_state.show_window()
        candidate.ui_state.restore()
        self.start_window.hide()
        self._remember(project)
        return True

    def _window_closed(self, window):
        if self.window is window:
            self.window = None
            self.project = None

    def _remember(self, project):
        try:
            self.recents.record(project)
        except (OSError, ValueError) as error:
            message = QCoreApplication.translate("app_controller", "Проект открыт, но список недавних не сохранён: {0}").format(error)
            self.start_window.message.setText(message)
            if self.window is not None:
                self.window.statusBar().showMessage(message, 15000)
        else:
            self.start_window.refresh()

    def open_recent(self, project_id):
        entry = self.recents.get(project_id)
        if entry is None:
            return
        if is_available(entry):
            self.open_path(entry["path"], expected_id=project_id)
            return
        self.start_window.refresh()
        box = QMessageBox(self._parent_window())
        box.setWindowTitle(QCoreApplication.translate("app_controller", "Проект недоступен"))
        box.setText(QCoreApplication.translate("app_controller", "Не найден или недоступен файл проекта:\n{0}").format(entry['path']))
        locate = box.addButton(QCoreApplication.translate("app_controller", "Найти .slproj…"), QMessageBox.ButtonRole.ActionRole)
        remove = box.addButton(QCoreApplication.translate("app_controller", "Убрать из недавних"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() == locate:
            self.locate_project(project_id)
        elif box.clickedButton() == remove:
            self.remove_recent(project_id)

    def locate_project(self, project_id):
        entry = self.recents.get(project_id)
        if entry is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self._parent_window(), QCoreApplication.translate("app_controller", "Найти проект «{0}»").format(entry['name']), "",
            "Stellaris Localizer (*.slproj)",
        )
        if path:
            # Loading and UUID validation happen before replacing the old link.
            self.open_path(path, expected_id=project_id)

    def remove_recent(self, project_id):
        try:
            self.recents.remove(project_id)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self._parent_window(), QCoreApplication.translate("app_controller", "Не удалось изменить список"), str(error))
        self.start_window.refresh()

    def delete_recent_project(self, project_id):
        if not self.start_window.isVisible():
            return False
        entry = self.recents.get(project_id)
        if entry is None:
            return False
        entry = dict(entry)
        try:
            root = validate_project_deletion(entry, active_project=self.project)
            if not self.start_window.confirm_deletion(entry["name"], root):
                return False
            delete_project_directory(entry, confirmed_root=root, active_project=self.project)
        except (OSError, ValueError, TypeError, KeyError) as error:
            QMessageBox.warning(self.start_window, QCoreApplication.translate("app_controller", "Не удалось удалить проект"), str(error))
            self.start_window.refresh()
            return False
        # Only remove the shortcut after the entire directory was deleted.
        self.remove_recent(project_id)
        return True
