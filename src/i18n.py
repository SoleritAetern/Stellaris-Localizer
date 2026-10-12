"""Startup-only Qt UI translations; independent of Stellaris content languages."""

from PySide6.QtCore import QCoreApplication, QTranslator

from .resources import asset_path


def current_ui_language():
    """Effective startup language, distinct from a saved restart preference."""
    return getattr(QCoreApplication.instance(), "_ui_language", "ru")


def initialize_ui_language(app, language):
    """Install the optional English catalog before creating any windows.

    Return the effective language. Repeated calls leave the startup choice
    intact: changing the preference requires an application restart.
    """
    if hasattr(app, "_ui_language"):
        return app._ui_language

    app._ui_language = "ru"
    app._ui_translator = None
    if language == "en":
        translator = QTranslator(app)
        catalog = asset_path("translations", "stellaris_localizer_en.qm")
        if translator.load(str(catalog)) and app.installTranslator(translator):
            # Keep both the Qt parent and Python reference for the app lifetime.
            app._ui_translator = translator
            app._ui_language = "en"
        else:
            translator.deleteLater()
    return app._ui_language
