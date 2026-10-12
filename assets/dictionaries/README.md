# Offline Russian spellcheck

`ru_RU/ru_RU.aff` and `ru_RU/ru_RU.dic` are unmodified Hunspell data from
LibreOffice. The pinned upstream commit is recorded in `ru_RU/SOURCE.txt`;
copyright, redistribution terms and disclaimer are in `ru_RU/README_ru_RU.txt`.

Runtime engine: Spylls 0.1.7 (pure Python, no runtime dependencies).
Its MPL-2.0 license and source notice are included in `assets/licenses/`.

Install application dependencies before building:

    .venv\Scripts\python.exe -m pip install -r requirements.txt
    .venv\Scripts\python.exe -m PyInstaller "Stellaris Localizer.spec"

The existing spec copies the entire `assets` directory, including the dictionary
and licenses. Spylls is a normal Python import discoverable by PyInstaller; no
native Hunspell DLL or separately installed system dictionary is required.
`src.resources.asset_path()` resolves these files in source and frozen builds.
There are no dictionary downloads or network requests at application runtime.

Only a live RU editor is checked after a 400 ms debounce. Suggestions (at most
five) are computed on demand when its native text context menu is opened over
an underlined word. Dictionary loading, lookup and suggestions run serially on
one worker; closing/changing the editor cancels queued work and discards stale
results. Running work cooperatively stops between dictionary operations.

User words are stored under `spellcheck/ru_user_words` in the existing
`QSettings("StellarisLocalizer", "StellarisLocalizer")` store, independently of
projects. Ignore applies only to the current editor session. Recognized syntax
tokens are excluded using `construction_spans`; color codes are skipped, but
ordinary Russian text between color codes is still checked. Underlines are
extra selections and never enter saved text or the undo history.
