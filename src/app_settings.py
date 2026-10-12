"""Application preferences, independent of project manifests."""

from PySide6.QtCore import QCoreApplication

from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Signal


class _SpellcheckChanges(QObject):
    # All AppSettings instances share the same global user dictionary.
    words_changed = Signal(object)  # Removed words; also clear session ignores.


spellcheck_changes = _SpellcheckChanges()


class AppSettings:
    # Reuse the previous Start Window/wizard store and keys. No second store or
    # destructive migration: existing view mode and default parent survive.
    PROJECTS = "projects/parent_directory"
    MODS = "paths/stellaris_mod_directory"
    WORKSHOP = "paths/stellaris_workshop_directory"
    GAME = "paths/stellaris_game_directory"
    VIEW = "start/view_mode"
    UI_LANGUAGE = "ui_language"
    INITIALIZED = "setup/completed"
    SPELLCHECK_WORDS = "spellcheck/ru_user_words"

    def __init__(self):
        self.store = QSettings("StellarisLocalizer", "StellarisLocalizer")

    @staticmethod
    def documents():
        return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation))

    def _path(self, key, default=None):
        value = self.store.value(key, "")
        return Path(value).expanduser() if isinstance(value, str) and value.strip() else default

    @property
    def projects_directory(self):
        return self._path(self.PROJECTS, self.documents() / "Stellaris Localizer" / "Projects")

    @property
    def mods_directory(self):
        # Discovery sources are explicit; missing and cleared roots are disabled.
        return self._path(self.MODS)

    @property
    def workshop_directory(self):
        return self._path(self.WORKSHOP)

    def initialize_mod_sources(self):
        """Try each never-saved root once; empty values also record completion."""
        missing_local = not self.store.contains(self.MODS)
        missing_workshop = not self.store.contains(self.WORKSHOP)
        if not (missing_local or missing_workshop):
            return
        from .initial_mod_paths import detect_initial_roots

        local, workshop = detect_initial_roots(
            self.documents(), self.mods_directory, self.game_directory,
            find_local=missing_local, find_workshop=missing_workshop,
        )
        for key, missing, path in ((self.MODS, missing_local, local),
                                   (self.WORKSHOP, missing_workshop, workshop)):
            if missing:
                self.store.setValue(key, str(path) if path else "")
        self.sync()

    @property
    def setup_completed(self):
        if self.store.contains(self.INITIALIZED):
            return self.store.value(self.INITIALIZED, False, type=bool)
        # Earlier Settings versions saved PROJECTS on every successful Save.
        # A view-mode preference alone does not imply completed path setup.
        return self.store.contains(self.PROJECTS)

    @property
    def game_directory(self):
        return self._path(self.GAME)

    @property
    def view_mode(self):
        value = self.store.value(self.VIEW, "grid")
        return value if value in ("grid", "list") else "grid"

    @property
    def ui_language(self):
        value = self.store.value(self.UI_LANGUAGE, "ru")
        return value if value in ("ru", "en") else "ru"

    def set_ui_language(self, language):
        self.store.setValue(self.UI_LANGUAGE, language if language in ("ru", "en") else "ru")
        self.sync()

    @property
    def last_source_mod_root(self):
        return self._path("paths/source_mod_root")

    def sync(self):
        self.store.sync()
        if self.store.status() != QSettings.Status.NoError:
            raise OSError(QCoreApplication.translate("app_settings", "Не удалось сохранить настройки приложения."))

    @property
    def spellcheck_words(self):
        return {word.casefold() for word in self.store.value(self.SPELLCHECK_WORDS, [], type=list)
                if isinstance(word, str) and word}

    def add_spellcheck_word(self, word):
        self._save_spellcheck_words(self.spellcheck_words | {word.casefold()})

    def remove_spellcheck_words(self, words):
        self._save_spellcheck_words(self.spellcheck_words - {word.casefold() for word in words})

    def _save_spellcheck_words(self, words):
        previous = self.spellcheck_words
        self.store.setValue(self.SPELLCHECK_WORDS, sorted(words))
        try:
            self.sync()
        except OSError:
            self.store.setValue(self.SPELLCHECK_WORDS, sorted(previous))
            raise
        spellcheck_changes.words_changed.emit(previous - words)

    def set_view_mode(self, mode):
        self.store.setValue(self.VIEW, mode if mode in ("grid", "list") else "grid")
        self.sync()

    def remember_source(self, path):
        self.store.setValue("paths/source_mod_root", str(path))
        self.sync()

    def save(self, projects, mods, game, view, *, workshop=None, ui_language=None):
        values = {self.PROJECTS: str(projects), self.MODS: str(mods) if mods else "",
                  self.WORKSHOP: str(workshop) if workshop else "",
                  self.GAME: str(game) if game else "", self.VIEW: view, self.INITIALIZED: True}
        if ui_language is not None:
            values[self.UI_LANGUAGE] = ui_language if ui_language in ("ru", "en") else "ru"
        for key, value in values.items():
            self.store.setValue(key, value)
        self.sync()

    def migrate_legacy_game(self, path):
        # An explicitly cleared global value also wins over all legacy projects.
        if path is None or self.store.contains(self.GAME):
            return
        path = Path(path)
        if path.name.lower() == "localisation":
            path = path.parent
        elif path.parent.name.lower() == "localisation":
            path = path.parent.parent
        self.store.setValue(self.GAME, str(path))
        self.sync()
