"""Project settings draft. External folders are only read, never modified here."""

from PySide6.QtCore import QCoreApplication

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFrame, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout,
)

from src import theme
from src.compound_control import CompoundControl
from src.mod_selector import HoverHelpLabel, ModTargetDialog, mod_name, russian_localisation
from src.paths import mod_localisation


class ProjectSettingsDialog(QDialog):
    def __init__(self, project, settings, save_callback, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.save_callback = save_callback
        self.source_root = project.resolve_path("source_mod_root")
        self.target_root = project.resolve_path("deploy_mod_root")
        self.setWindowTitle(QCoreApplication.translate("project_settings_dialog", "Настройки проекта"))
        self.setObjectName("projectSettingsDialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        def group(title, explanation):
            block = QVBoxLayout()
            block.setSpacing(4)
            block.addWidget(HoverHelpLabel(title, explanation), 0, Qt.AlignmentFlag.AlignLeft)
            layout.addLayout(block)
            return block

        name = group(QCoreApplication.translate("project_settings_dialog", "Название проекта"),
                     QCoreApplication.translate("project_settings_dialog", "Отображаемое название проекта.\nИзменение названия не перемещает и не переименовывает папку проекта."))
        self.name = QLineEdit(project.name)
        name.addWidget(self.name)

        source = group(QCoreApplication.translate("project_settings_dialog", "Исходный мод"),
                       QCoreApplication.translate("project_settings_dialog", "Корневая папка оригинального мода, из которого берётся английская локализация.\nПосле смены пути используйте «Обновить источники», чтобы загрузить новую версию."))
        self.source_path = QLineEdit()
        self.source_path.setReadOnly(True)
        self.source_path.setPlaceholderText(QCoreApplication.translate("project_settings_dialog", "Не выбран"))
        change_source = QPushButton(QCoreApplication.translate("project_settings_dialog", "Изменить"))
        change_source.clicked.connect(self.choose_source)
        self.source_control = CompoundControl(self.source_path, change_source)
        source.addWidget(self.source_control)
        self.source_status = self.status_label()
        source.addWidget(self.source_status)

        target = group(QCoreApplication.translate("project_settings_dialog", "Перевод"),
                       QCoreApplication.translate("project_settings_dialog", "Папка мода, куда команда «Загрузить в папку mod» записывает готовый перевод.\nСмена папки не удаляет существующие файлы."))
        self.target_name = QLineEdit()
        self.target_name.setReadOnly(True)
        self.change_target = QPushButton(QCoreApplication.translate("project_settings_dialog", "Изменить"))
        self.change_target.clicked.connect(self.choose_target)
        self.target_control = CompoundControl(self.target_name, self.change_target)
        target.addWidget(self.target_control)
        self.target_status = self.status_label()
        target.addWidget(self.target_status)

        layout.addStretch(1)
        footer = QFrame()
        footer.setProperty("role", "dialogFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 12, 0, 0)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(QCoreApplication.translate("project_settings_dialog", "Сохранить"))
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("role", "primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(QCoreApplication.translate("project_settings_dialog", "Отмена"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setProperty("role", "secondary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer_layout.addWidget(buttons)
        layout.addWidget(footer)
        self.refresh_source()
        self.refresh_target()
        self.ensurePolished()
        theme.align_form_controls((self.name, self.source_control, self.target_control))
        height = layout.totalHeightForWidth(690) if layout.hasHeightForWidth() else layout.sizeHint().height()
        self.resize(690, height)

    @staticmethod
    def status_label():
        label = QLabel()
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        font = label.font()
        font.setPointSizeF(max(8, font.pointSizeF() - 1))
        label.setFont(font)
        return label

    @staticmethod
    def show_status(label, message, success=False):
        label.setText(message)
        label.setVisible(bool(message))
        label.setStyleSheet(f"color: {theme.css('status.ready') if success else theme.css('status.warning_text')};")

    def refresh_source(self):
        root = self.source_root
        self.source_path.setText(str(root) if root else "")
        self.source_path.setCursorPosition(0)
        self.source_path.setToolTip(str(root) if root else "")
        message = QCoreApplication.translate("project_settings_dialog", "! В выбранном моде не найдена папка localisation")
        success = False
        try:
            if root is not None and root.name.casefold() == "localisation":
                message = QCoreApplication.translate("project_settings_dialog", "! Выберите корневую папку мода уровнем выше")
            elif root is not None:
                localisation = mod_localisation(root)
                count = sum(1 for path in localisation.rglob("*_l_english.yml") if path.is_file())
                message = (QCoreApplication.translate("project_settings_dialog", "✓ localisation найдена · английских файлов: {0}").format(count) if count
                           else QCoreApplication.translate("project_settings_dialog", "! Английские файлы локализации не найдены"))
                success = bool(count)
        except (OSError, ValueError, RuntimeError):
            pass
        self.show_status(self.source_status, message, success)

    def refresh_target(self):
        root = self.target_root
        self.target_name.setText(mod_name(root) if root else QCoreApplication.translate("project_settings_dialog", "Не привязан"))
        self.target_name.setCursorPosition(0)
        self.target_name.setToolTip(str(root) if root else "")
        message, success = "", False
        if root is not None:
            try:
                if not root.is_dir():
                    message = QCoreApplication.translate("project_settings_dialog", "! Папка перевода не найдена")
                else:
                    message = (QCoreApplication.translate("project_settings_dialog", "✓ Русская локализация найдена") if russian_localisation(root)
                               else QCoreApplication.translate("project_settings_dialog", "✓ Мод найден · русская локализация не найдена"))
                    success = True
            except OSError:
                message = QCoreApplication.translate("project_settings_dialog", "! Не удалось прочитать папку перевода")
        self.show_status(self.target_status, message, success)

    def choose_source(self):
        path = QFileDialog.getExistingDirectory(self, QCoreApplication.translate("project_settings_dialog", "Исходный мод — выберите корневую папку"),
                                               str(self.source_root or self.settings.last_source_mod_root or ""))
        if path:
            from pathlib import Path
            self.source_root = Path(path).resolve()
            self.refresh_source()

    def choose_target(self):
        dialog = ModTargetDialog(self.settings.mods_directory, self, current=self.target_root, settings=self.settings)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.target_root = dialog.selector.path()
                self.refresh_target()
        finally:
            dialog.deleteLater()

    def detach_target(self):
        answer = QMessageBox.question(
            self, QCoreApplication.translate("project_settings_dialog", "Отвязать русский мод"), QCoreApplication.translate("project_settings_dialog", "Отвязать русский мод от проекта?\n\nФайлы мода не будут удалены. Путь можно выбрать снова позже."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.target_root = None
            self.refresh_target()

    def accept(self):
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, QCoreApplication.translate("project_settings_dialog", "Название проекта"), QCoreApplication.translate("project_settings_dialog", "Введите название проекта."))
            self.name.setFocus()
            return
        if self.save_callback(name, self.source_root, self.target_root):
            super().accept()
