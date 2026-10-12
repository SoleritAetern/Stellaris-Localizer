
from PySide6.QtCore import QCoreApplication
from pathlib import Path
import stat


class PreviousLocalisationNotFound(ValueError):
    pass


def previous_localisation(selected):
    """Resolve only the selected localisation directory or its direct child.

    Never search for localisation recursively inside an arbitrary user path.
    """
    selected = Path(selected)
    folder = selected if selected.name.casefold() == "localisation" else selected / "localisation"
    try:
        if stat.S_ISDIR(folder.stat().st_mode):
            return folder.resolve()
    except (FileNotFoundError, NotADirectoryError):
        pass
    raise PreviousLocalisationNotFound(
        QCoreApplication.translate("paths", "Папка localisation не найдена"))


def english_to_russian_path(relative_path):
    relative_path = Path(relative_path)

    file_name = relative_path.name

    if file_name.endswith("_l_english.yml"):
        russian_name = file_name[:-len("_l_english.yml")] + "_l_russian.yml"

    else:
        russian_name = file_name

    return Path(*("russian" if part == "english" else part for part in relative_path.parent.parts)) / russian_name


def legacy_localisation_root(path):
    """Infer a mod root lexically, even if an old external folder is offline."""
    path = Path(path)
    for folder in (path, *path.parents):
        if folder.name.casefold() == "localisation":
            return folder.parent, path.relative_to(folder).as_posix()
    return None, ""


def mod_localisation(root):
    root = Path(root).resolve()
    localisation = root / "localisation"
    if not root.is_dir() or not localisation.is_dir():
        raise ValueError(QCoreApplication.translate("paths", "Не найдена папка localisation внутри оригинального мода: {0}").format(root))
    if not localisation.resolve().is_relative_to(root):
        raise ValueError(QCoreApplication.translate("paths", "Папка localisation не должна ссылаться за пределы оригинального мода."))
    return localisation
