"""Application-local references to projects, never translation data."""

from PySide6.QtCore import QCoreApplication

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QIODevice, QSaveFile, QStandardPaths


class RecentProjectsError(ValueError):
    pass


def path_identity(path):
    return os.path.normcase(str(Path(path).resolve()))


def is_available(entry):
    try:
        return Path(entry["path"]).is_file()
    except OSError:
        return False


class RecentProjects:
    def __init__(self, file_path=None):
        if file_path is None:
            directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
            if not directory:
                raise RecentProjectsError(QCoreApplication.translate("recent_projects", "Не найден каталог данных приложения."))
            file_path = Path(directory) / "recent-projects.json"
        self.file_path = Path(file_path)
        self.entries = []
        self.error = None
        try:
            self.entries = self._read()
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            # Do not overwrite an unreadable list with an empty one.
            self.error = QCoreApplication.translate("recent_projects", "Не удалось прочитать список недавних проектов: {0}").format(error)

    def _read(self):
        if not self.file_path.exists():
            return []
        document = json.loads(self.file_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise RecentProjectsError(QCoreApplication.translate("recent_projects", "Неподдерживаемый формат списка."))
        entries = document["projects"]
        if not isinstance(entries, list):
            raise RecentProjectsError(QCoreApplication.translate("recent_projects", "Некорректный список проектов."))
        result = []
        ids, paths = set(), set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"project_id", "name", "path", "last_opened"}:
                raise RecentProjectsError(QCoreApplication.translate("recent_projects", "Некорректная ссылка на проект."))
            if not all(isinstance(value, str) and value for value in entry.values()):
                raise RecentProjectsError(QCoreApplication.translate("recent_projects", "Неполная ссылка на проект."))
            UUID(entry["project_id"])
            if not Path(entry["path"]).is_absolute() or "\0" in entry["path"]:
                raise RecentProjectsError(QCoreApplication.translate("recent_projects", "Некорректный путь проекта."))
            identity = path_identity(entry["path"])
            if entry["project_id"] not in ids and identity not in paths:
                result.append(entry)
                ids.add(entry["project_id"])
                paths.add(identity)
        return result[:20]

    def _save(self, entries):
        if self.error:
            raise RecentProjectsError(self.error)
        payload = (json.dumps({"schema_version": 1, "projects": entries},
                              ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        output = QSaveFile(str(self.file_path))
        output.setDirectWriteFallback(False)
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise RecentProjectsError(output.errorString())
        if output.write(payload) != len(payload):
            output.cancelWriting()
            raise RecentProjectsError(output.errorString())
        if not output.commit():
            raise RecentProjectsError(output.errorString())
        self.entries = entries

    def record(self, project):
        path = project.file_path.resolve()
        if not path.is_file():
            raise RecentProjectsError(QCoreApplication.translate("recent_projects", "Файл проекта недоступен."))
        entry = {
            "project_id": project.project_id, "name": project.name, "path": str(path),
            "last_opened": datetime.now(timezone.utc).isoformat(),
        }
        identity = path_identity(path)
        remaining = [item for item in self.entries if item["project_id"] != project.project_id
                     and path_identity(item["path"]) != identity]
        self._save([entry, *remaining][:20])

    def remove(self, project_id):
        self._save([item for item in self.entries if item["project_id"] != project_id])

    def get(self, project_id):
        return next((item for item in self.entries if item["project_id"] == project_id), None)
