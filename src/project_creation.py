"""One data-only creation job and its non-cancellable modal status window."""

from PySide6.QtCore import QCoreApplication, QThread, Qt, Signal, Slot, QT_TRANSLATE_NOOP
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout

from . import theme
from .mod_selector import russian_localisation
from .project import Project


_PHASES = {
    "preparing_files": QT_TRANSLATE_NOOP("project_creation", "Подготовка файлов…"),
    "reading_source": QT_TRANSLATE_NOOP("project_creation", "Чтение исходной локализации…"),
    "reading_previous": QT_TRANSLATE_NOOP("project_creation", "Чтение предыдущей версии…"),
    "reading_translation": QT_TRANSLATE_NOOP("project_creation", "Чтение перевода…"),
    "comparing": QT_TRANSLATE_NOOP("project_creation", "Сравнение с предыдущей версией…"),
    "preparing_project": QT_TRANSLATE_NOOP("project_creation", "Подготовка проекта…"),
    "saving": QT_TRANSLATE_NOOP("project_creation", "Сохранение проекта…"),
}


class ProjectCreationJob(QThread):
    phaseChanged = Signal(str)

    def __init__(self, root, name, *, source_mod_root, old_en, translation_root, parent):
        super().__init__(parent)
        # Only immutable form values cross into run(), never widgets/settings.
        self.root, self.name = root, name
        self.source_mod_root = source_mod_root
        self.old_en, self.translation_root = old_en, translation_root
        self.project = None
        self.error = None

    def run(self):
        try:
            self.phaseChanged.emit("preparing_files")
            translation = self.translation_root.resolve() if self.translation_root is not None else None
            initial_ru = russian_localisation(translation) if translation is not None else None
            self.project = Project.create(
                self.root, self.name, source_mod_root=self.source_mod_root,
                old_en=self.old_en, initial_ru=initial_ru, deploy_mod_root=translation,
                progress=self.phaseChanged.emit,
            )
        except Exception as error:
            self.error = str(error)

    @Slot()
    def wait_for_shutdown(self):
        # Only application shutdown waits. A running atomic creation transaction
        # must finish before QApplication destroys its child QThread objects.
        self.wait()


class ProjectCreationProgress(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(QCoreApplication.translate("project_creation", "Создание проекта"))
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setMinimumWidth(360)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.indicator = QProgressBar()
        self.indicator.setRange(0, 0)
        self.indicator.setTextVisible(False)
        self.indicator.setFixedHeight(4)
        self.indicator.setStyleSheet(
            f"QProgressBar {{ border: none; border-radius: 2px; background: {theme.css('surface.control')}; }}"
            f"QProgressBar::chunk {{ background: {theme.css('action.accent')}; width: 48px; border-radius: 2px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)
        layout.addWidget(self.label)
        layout.addWidget(self.indicator)
        self.set_phase("preparing_files")

    @Slot(str)
    def set_phase(self, phase):
        self.label.setText(QCoreApplication.translate("project_creation", _PHASES[phase]))

    def reject(self):
        # Escape is not cancellation; there is no partial-transaction Cancel UX.
        pass

    def closeEvent(self, event):
        event.ignore()
