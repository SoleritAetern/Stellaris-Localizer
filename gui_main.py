import argparse
import sys

from PySide6.QtWidgets import QApplication

from src.app_controller import AppController
from src.app_settings import AppSettings
from src.gui import apply_dark_theme
from src.i18n import initialize_ui_language
from src.resources import set_application_icon


def main():
    parser = argparse.ArgumentParser(description="Stellaris Localizer")
    parser.add_argument("project", nargs="?", help="Путь к project.slproj")
    arguments = parser.parse_args()
    app = QApplication(sys.argv)
    app.setOrganizationName("StellarisLocalizer")
    app.setApplicationName("StellarisLocalizer")
    initialize_ui_language(app, AppSettings().ui_language)
    set_application_icon(app)
    apply_dark_theme(app)

    controller = AppController(parent=app)
    controller.launch(arguments.project)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
