"""Explicit project-root deletion, independent of import/export and translation data."""

from PySide6.QtCore import QCoreApplication

import json
import os
from pathlib import Path
import shutil
import stat
from uuid import UUID

from PySide6.QtCore import QStandardPaths

from .project import Project


class ProjectDeletionError(ValueError):
    pass


def _reject(message):
    raise ProjectDeletionError(message)


def _is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _shared_directories():
    paths = {Path.home()}
    for location in (
        QStandardPaths.StandardLocation.HomeLocation,
        QStandardPaths.StandardLocation.DesktopLocation,
        QStandardPaths.StandardLocation.DocumentsLocation,
        QStandardPaths.StandardLocation.DownloadLocation,
        QStandardPaths.StandardLocation.GenericDataLocation,
        QStandardPaths.StandardLocation.AppLocalDataLocation,
        QStandardPaths.StandardLocation.AppDataLocation,
        QStandardPaths.StandardLocation.GenericConfigLocation,
        QStandardPaths.StandardLocation.TempLocation,
    ):
        paths.update(Path(p) for p in QStandardPaths.standardLocations(location) if p)
    paths.update(Path(os.environ[key]) for key in (
        "USERPROFILE", "PUBLIC", "ALLUSERSPROFILE", "APPDATA", "LOCALAPPDATA",
        "ProgramData", "SystemRoot", "ProgramFiles", "ProgramFiles(x86)",
    ) if os.environ.get(key))
    return {p.resolve() for p in paths}


def validate_project_deletion(entry, *, active_project=None):
    """Return the exact validated root; read metadata only, never load/normalize sources."""
    value = entry.get("path")
    if not isinstance(value, str) or not value or "\0" in value:
        _reject(QCoreApplication.translate("project_deletion", "В списке отсутствует корректный путь проекта."))
    manifest = Path(value)
    if not manifest.is_absolute() or manifest.name.lower() != "project.slproj" or ".." in manifest.parts:
        _reject(QCoreApplication.translate("project_deletion", "Для удаления требуется точный путь к project.slproj внутри папки проекта."))
    root = manifest.parent
    # Check lexical paths before resolve(), including every ancestor and the file itself.
    for path in (manifest, root, *root.parents):
        if _is_link(path):
            _reject(QCoreApplication.translate("project_deletion", "Удаление через symlink/junction или reparse point запрещено: {0}").format(path))
    if not root.is_dir() or not manifest.is_file():
        _reject(QCoreApplication.translate("project_deletion", "Папка проекта или непосредственно расположенный в ней project.slproj недоступны."))
    root = root.resolve(strict=True)
    if root == Path(root.anchor):
        _reject(QCoreApplication.translate("project_deletion", "Нельзя удалить корень диска."))
    for shared in _shared_directories():
        if root == shared or root in shared.parents:
            _reject(QCoreApplication.translate("project_deletion", "Нельзя удалить системный или общий каталог: {0}").format(root))
    for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if os.environ.get(name) and root.is_relative_to(Path(os.environ[name]).resolve()):
            _reject(QCoreApplication.translate("project_deletion", "Нельзя удалять проект внутри системного каталога."))
    if root.name.casefold() in {
        "documents", "appdata", "desktop", "downloads", "users", "windows",
        "program files", "programdata", "mod", "mods", "localisation", "localization",
        "russian", "english", "sources", "revisions",
    }:
        _reject(QCoreApplication.translate("project_deletion", "Выбран общий каталог, каталог мода или локализации, а не отдельная папка проекта."))
    document = json.loads(manifest.read_text(encoding="utf-8-sig"))
    Project._validate(document)
    expected_id = entry.get("project_id")
    if expected_id is not None and UUID(expected_id) != UUID(document["project_id"]):
        _reject(QCoreApplication.translate("project_deletion", "UUID project.slproj не совпадает с выбранным проектом из недавних."))
    if active_project is not None and (
        document["project_id"] == active_project.project_id
        or active_project.root.resolve().is_relative_to(root)
        or root.is_relative_to(active_project.root.resolve())
    ):
        _reject(QCoreApplication.translate("project_deletion", "Проект открыт. Закройте рабочее окно перед удалением проекта."))

    protected = [v for k, v in document["paths"].items()
                 if k in ("new_en", "old_en", "initial_ru", "stellaris", "deploy_ru", "source_mod_root", "deploy_mod_root") and v]
    protected.extend(r["source_path"] for r in document.get("source_revisions", []) if r.get("source_path"))
    for value in protected:
        path = Path(value)
        path = (path if path.is_absolute() else root / path).resolve()
        if path == root or path.is_relative_to(root):
            _reject(QCoreApplication.translate("project_deletion", "Удаление затронет исходную папку или Deploy: {0}").format(path))
        # A project manifest placed in the mod root must not authorize deleting the mod.
        if root in path.parents and any(p.name.casefold() in ("mod", "mods", "localisation", "localization") for p in path.parents):
            _reject(QCoreApplication.translate("project_deletion", "Нельзя удалить корень мода."))
    # A moved or unrelated mod folder may not be reflected in the stored paths.
    if any((root / name).exists() for name in ("descriptor.mod", "localisation", "localization")):
        _reject(QCoreApplication.translate("project_deletion", "Папка похожа на корень мода, удаление запрещено."))
    if active_project is not None:
        for name in ("new_en", "old_en", "initial_ru", "stellaris", "deploy_ru", "source_mod_root", "deploy_mod_root"):
            path = active_project.resolve_path(name)
            if path is not None and path.resolve().is_relative_to(root):
                _reject(QCoreApplication.translate("project_deletion", "Папка используется открытым проектом как источник или Deploy."))

    def walk_error(error):
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in (*dirs, *files):
            path = Path(directory) / name
            if _is_link(path):
                _reject(QCoreApplication.translate("project_deletion", "В папке проекта есть ссылка/junction; безопасное удаление не подтверждено: {0}").format(path))
    return root


def delete_project_directory(entry, *, confirmed_root, active_project=None):
    # Recheck UUID, paths and links after the confirmation dialog has closed.
    root = validate_project_deletion(entry, active_project=active_project)
    if root != Path(confirmed_root):
        _reject(QCoreApplication.translate("project_deletion", "Путь проекта изменился после подтверждения. Удаление отменено."))
    shutil.rmtree(root)
