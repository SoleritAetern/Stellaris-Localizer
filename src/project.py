"""Versioned project documents. Exported YML is never used to restore a project."""

from PySide6.QtCore import QCoreApplication, QT_TRANSLATE_NOOP

import json
import hashlib
import os
import shutil
import tempfile
from copy import deepcopy
from datetime import datetime
from math import isfinite
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import UUID, uuid4

from PySide6.QtCore import QIODevice, QLockFile, QSaveFile

from src.diff import compare_indexes
from src.diagnostics import Diagnostic, working_diagnostics
from src.localization_syntax import AutoFix, is_whitespace_text
from src.localization_document import COMMENT_FIELDS, LocalizationDocument, read_documents, hydrate_comments, inline_comment
from src.exporter import build_ru_index_from_rows, build_russian_localization, export_to_directory
from src.row_diagnostics import ignored_ids
from src.parser import build_key_index, parse_localization_folder
from src.view_model import build_localization_rows
from src.translation_baseline import BASELINE_FIELDS, initialize_baseline, refresh_baseline
from src.source_revisions import reconcile_entries, revision_record
from src.deployment import deploy_localization, MANIFEST_NAME, file_hash, target_file
from src.source_normalization import NORMALIZATION_VERSION, upgrade_normalization, DISABLED_SHADOW_VERSION, upgrade_disabled_shadows
from src.paths import english_to_russian_path, legacy_localisation_root, mod_localisation, previous_localisation
from src.history import HISTORY_FIELDS, separate_removed, rotate_history, cleanup_snapshots


SCHEMA_VERSION = 2
DIAGNOSTICS_VERSION = 7
PATH_NAMES = ("new_en", "old_en", "initial_ru", "output_ru", "stellaris", "deploy_ru")
OPTIONAL_PATH_NAMES = ("source_mod_root", "deploy_mod_root", "legacy_output_ru")
SNAPSHOT_NAMES = ("new_en", "old_en", "initial_ru")
PATH_FIELDS = ("old_file", "new_file", "ru_file", "target_ru_file")
ROW_FIELDS = (
    "entry_id", "position", "section", "key", "source_status", "old_text", "new_text",
    *PATH_FIELDS, "old_line", "new_line", "ru_line", "old_key", "rename_score",
)
SOURCE_STATUSES = {
    "NEW", "REMOVED", "UNCHANGED", "CHANGED", "MOVED", "MOVED_CHANGED",
    "RENAMED", "RENAMED_CHANGED", "NO_HISTORY", "NO_SOURCE",
}


class ProjectError(ValueError):
    """Invalid project data, unsafe paths, or a failed project operation."""


class ProjectConflictError(ProjectError):
    """The on-disk project no longer matches this instance's saved state."""


class TranslationValidationError(ProjectError):
    """One report for invalid translation fields, with entry identities retained."""

    def __init__(self, issues):
        self.issues = issues
        details = [QCoreApplication.translate("project", "Некорректное состояние перевода. Ошибок: {0}.").format(len(issues))]
        for entry, field_name, value, reason in issues[:5]:
            file = entry.get("new_file") or entry.get("old_file") or entry.get("ru_file") or "—"
            shown = repr(value)
            if len(shown) > 240:
                shown = shown[:237] + "..."
            details.append(
                QCoreApplication.translate("project", "Ключ: {0}\nФайл: {1}\nПоле: {2}\nЗначение: {3} ({4})\n{5}").format(entry.get('key', '—'), file, field_name, shown, type(value).__name__, QCoreApplication.translate("project", reason))
            )
        if len(issues) > 5:
            details.append(QCoreApplication.translate("project", "Показаны первые 5 ошибок из {0}.").format(len(issues)))
        super().__init__("\n\n".join(details))


def _uuid(value):
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProjectError(QCoreApplication.translate("project", "Некорректный UUID проекта или записи.")) from exc


def _relative_file(value):
    if value is None:
        return
    if not isinstance(value, str) or not value:
        raise ProjectError(QCoreApplication.translate("project", "Некорректный относительный путь записи."))
    path = Path(value)
    if path.is_absolute() or path.drive or ".." in path.parts:
        raise ProjectError(QCoreApplication.translate("project", "Путь записи выходит за пределы снимка."))


def _atomic_write(path, document):
    payload = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    fingerprint = hashlib.sha256(payload).hexdigest()
    output = QSaveFile(str(path))
    output.setDirectWriteFallback(False)
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ProjectError(QCoreApplication.translate("project", "Не удалось сохранить проект: {0}").format(output.errorString()))
    if output.write(payload) != len(payload):
        output.cancelWriting()
        raise ProjectError(QCoreApplication.translate("project", "Не удалось записать проект: {0}").format(output.errorString()))
    if not output.commit():
        raise ProjectError(QCoreApplication.translate("project", "Не удалось завершить сохранение: {0}").format(output.errorString()))
    # Hash our committed bytes, not a later read that could belong to a writer
    # which does not participate in the save lock.
    return fingerprint


@dataclass
class Project:
    file_path: Path
    project_id: str
    name: str
    paths: dict
    snapshots: dict
    entries: list
    source_diagnostics: list[Diagnostic] = field(default_factory=list)
    source_auto_fixes: list[AutoFix] = field(default_factory=list)
    documents: list[LocalizationDocument] = field(default_factory=list)
    source_revisions: list[dict] = field(default_factory=list)
    current_revision_id: int = 1
    thumbnail: dict = field(default_factory=lambda: {"status": "missing", "path": None, "sha256": None})
    custom_files: dict = field(default_factory=dict)
    removed_entries: list = field(default_factory=list)
    history_cleanup_errors: list = field(default_factory=list, repr=False)
    _pending_thumbnail: bytes | None = field(default=None, repr=False)
    # In-memory save baseline only; neither field belongs to the project schema.
    _saved_path: Path | None = field(default=None, repr=False, compare=False)
    _saved_fingerprint: str | None = field(default=None, repr=False, compare=False)
    # Prepared imports are not published until save. Keep exact bytes for retry,
    # even if the external source disappears. Not part of the project schema.
    _pending_source_snapshots: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def diagnostics(self):
        return [*self.source_diagnostics, *working_diagnostics(self.entries)]

    def refresh_source_diagnostics(self):
        """Read snapshots for diagnostics only; never rebuild working entries."""
        collected = []
        fixes = []
        for name in SNAPSHOT_NAMES:
            folder = self.snapshot_path(name)
            if folder is None:
                continue
            source = "ru" if name == "initial_ru" else name
            language = "russian" if name == "initial_ru" else "english"
            found = []
            fixed = []
            try:
                parse_localization_folder(folder, language, diagnostics=found, source=source, auto_fixes=fixed)
            except (OSError, UnicodeError) as error:
                found.append(Diagnostic("DIAGNOSTICS_UNAVAILABLE", "warning", "", 1, None,
                                        QT_TRANSLATE_NOOP("StoredMessage", "Не удалось прочитать диагностику снимка: {0}").format(error), source))
            collected.extend(replace(item, file=(Path(self.snapshots[name]) / item.file).as_posix()) for item in found)
            fixes.extend(replace(item, file=(Path(self.snapshots[name]) / item.file).as_posix()) for item in fixed)
        self.source_diagnostics = collected
        self.source_auto_fixes = fixes

    @property
    def root(self):
        return self.file_path.parent

    @property
    def current_source_prefix(self):
        return self.source_revisions[-1].get("localisation_prefix", "") if self.source_revisions else ""

    def resolve_path(self, name):
        value = self.paths.get(name)
        if value is None:
            return None
        path = Path(value)
        return (path if path.is_absolute() else self.root / path).resolve()

    def snapshot_path(self, name):
        value = self.snapshots[name]
        if value is None:
            return None
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ProjectError(QCoreApplication.translate("project", "Снимок источников находится вне папки проекта."))
        return path

    def to_document(self):
        entries = []
        for row in [*self.entries, *self.removed_entries]:
            entry = {field: row.get(field) for field in ROW_FIELDS}
            entry.update({name: row[name] for name in HISTORY_FIELDS if name in row})
            entry["kind"] = row.get("kind", "ENTRY")
            entry.update({field: row.get(field) for field in BASELINE_FIELDS})
            entry.update({name: row[name] for name in COMMENT_FIELDS if name in row})
            ignored = ignored_ids(row.get("ignored_diagnostics", []))
            if ignored:
                entry["ignored_diagnostics"] = ignored
            for field in PATH_FIELDS:
                value = entry[field]
                entry[field] = Path(value).as_posix() if value is not None else None
            entry["translation"] = {
                "text": row["ru_text"], "state": row["translation_state"],
                "origin": row["translation_origin"], "needs_review": row["needs_review"],
            }
            entries.append(entry)
        return {
            "schema_version": SCHEMA_VERSION, "project_id": self.project_id,
            "name": self.name, "paths": {name: value for name, value in self.paths.items() if name != "stellaris"},
            "snapshots": dict(self.snapshots), "entries": [e for e in entries if e["section"] == "CURRENT"],
            "removed_entries": [e for e in entries if e["section"] == "REMOVED"],
            "source_revisions": deepcopy(self.source_revisions), "current_revision_id": self.current_revision_id,
            # Optional derived metadata; old schema-1 documents remain readable.
            "diagnostics": [item.to_dict() for item in self.source_diagnostics],
            "diagnostics_version": DIAGNOSTICS_VERSION,
            "auto_fixes": [item.to_dict() for item in self.source_auto_fixes],
            "documents": [document.to_dict() for document in self.documents],
            "normalization_version": NORMALIZATION_VERSION,
            "disabled_shadow_version": DISABLED_SHADOW_VERSION,
            "thumbnail": dict(self.thumbnail),
            "custom_files": deepcopy(self.custom_files),
        }

    def save(self):
        path = self.file_path.resolve()
        # Lock only the check/write/cleanup transaction, not the editing session.
        # Otherwise two writers could both pass the fingerprint check.
        lock = QLockFile(str(path) + ".lock")
        lock.setStaleLockTime(0)  # A slow save must not expire by age.
        if not lock.tryLock(0):
            if lock.error() == QLockFile.LockError.LockFailedError:
                raise ProjectError(QCoreApplication.translate("project", "Другой экземпляр приложения сохраняет этот проект. Повторите сохранение позже. Ваши изменения остаются в текущем окне."))
            raise ProjectError(QCoreApplication.translate("project", "Не удалось защитить сохранение проекта. Проверьте доступ к папке проекта. Ваши изменения остаются в текущем окне."))
        try:
            try:
                actual = file_hash(path)
            except FileNotFoundError:
                actual = None
            # A new destination is allowed only when it does not already exist.
            expected = self._saved_fingerprint if path == self._saved_path else None
            if actual != expected:
                raise ProjectConflictError(QCoreApplication.translate("project", "Проект на диске изменён или удалён после открытия либо последнего сохранения. Сохранение отменено, чтобы не перезаписать чужие изменения.\n\nВаши несохранённые изменения остаются в текущем окне. Перед повторным открытием проекта скопируйте нужные переводы; повторное сохранение из этого окна не устранит конфликт."))
            self._save_locked(path)
        finally:
            lock.unlock()

    def _save_locked(self, path):
        rotate_history(self, prune_removed=True)
        document = self.to_document()
        self._validate(document)
        asset = self.root / "assets" / "thumbnail.png"
        previous = None
        published = False
        published_snapshots = []
        try:
            for relative, files in self._pending_source_snapshots.items():
                target = target_file(self.root.resolve(), relative)
                revision_root = target.parent
                if revision_root.parent != (self.root / "sources" / "revisions").resolve():
                    raise ValueError("Invalid pending source revision path")
                revision_root.parent.mkdir(parents=True, exist_ok=True)
                # Never claim ownership of a pre-existing revision directory.
                revision_root.mkdir()
                published_snapshots.append(revision_root)
                target.mkdir()
                for name, content in files.items():
                    output = target_file(target, name)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(content)
            if self._pending_thumbnail is not None:
                if not asset.resolve().is_relative_to(self.root.resolve()):
                    raise ProjectError(QCoreApplication.translate("project", "Thumbnail находится вне папки проекта."))
                previous = asset.read_bytes() if asset.exists() else None
                if previous != self._pending_thumbnail:
                    self._write_asset(asset, self._pending_thumbnail)
                    published = True
            fingerprint = _atomic_write(path, document)
        except Exception as error:
            try:
                if published:
                    if previous is None:
                        asset.unlink()
                    else:
                        self._write_asset(asset, previous)
            finally:
                for revision_root in reversed(published_snapshots):
                    try:
                        shutil.rmtree(revision_root)
                    except OSError as cleanup_error:
                        error.add_note(f"Snapshot rollback failed: {cleanup_error}")
            raise
        # _atomic_write returned only after QSaveFile.commit succeeded. From
        # here on these snapshots belong to the saved manifest, even if later
        # maintenance fails. Do not include cleanup in the rollback scope.
        self._saved_path, self._saved_fingerprint = path, fingerprint
        self._pending_thumbnail = None
        self._pending_source_snapshots.clear()
        cleanup_snapshots(self)

    @staticmethod
    def _write_asset(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        output = QSaveFile(str(path))
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise ProjectError(output.errorString())
        if output.write(data) != len(data):
            output.cancelWriting()
            raise ProjectError(QCoreApplication.translate("project", "Не удалось записать thumbnail проекта."))
        if not output.commit():
            raise ProjectError(output.errorString())

    def prepare_thumbnail(self):
        root = self.resolve_path("source_mod_root")
        path = root / "thumbnail.png" if root else None
        self._pending_thumbnail = path.read_bytes() if path is not None and path.is_file() else None
        self.thumbnail = ({"status": "available", "path": "assets/thumbnail.png",
                           "sha256": hashlib.sha256(self._pending_thumbnail).hexdigest()}
                          if self._pending_thumbnail is not None else
                          {"status": "missing", "path": None, "sha256": None})

    @classmethod
    def load(cls, file_path):
        file_path = Path(file_path).resolve()
        try:
            payload = file_path.read_bytes()
            document = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ProjectError(QCoreApplication.translate("project", "Не удалось открыть проект: {0}").format(exc)) from exc
        cls._validate(document)
        legacy = document["schema_version"] == 1
        rows = []
        for entry in [*document["entries"], *document.get("removed_entries", [])]:
            row = {field: entry[field] for field in ROW_FIELDS}
            row["kind"] = entry.get("kind", "ENTRY")
            row.update({name: entry[name] for name in HISTORY_FIELDS if name in entry})
            row.update({name: entry[name] for name in COMMENT_FIELDS if name in entry})
            ignored = ignored_ids(entry.get("ignored_diagnostics", []))
            if ignored:
                row["ignored_diagnostics"] = ignored
            for field in PATH_FIELDS:
                if row[field] is not None:
                    row[field] = Path(row[field])
            translation = entry["translation"]
            row.update(
                ru_text=translation["text"], translation_state=translation["state"],
                translation_origin=translation["origin"], needs_review=translation["needs_review"],
            )
            if legacy:
                initialize_baseline(row, legacy=True)
            else:
                row.update({field: entry.get(field) for field in BASELINE_FIELDS})
                refresh_baseline(row, document["current_revision_id"])
            rows.append(row)
        project = cls(file_path, document["project_id"], document["name"],
                      dict(document["paths"], deploy_ru=document["paths"].get("deploy_ru"),
                           stellaris=document["paths"].get("stellaris")), document["snapshots"], rows)
        # Capture the exact bytes read, before any in-memory legacy migrations.
        project._saved_path = file_path
        project._saved_fingerprint = hashlib.sha256(payload).hexdigest()
        project.source_revisions = ([revision_record(1, project.snapshots["new_en"])] if legacy
                                    else document["source_revisions"])
        if legacy:
            project.source_revisions[0]["source_path"] = str(project.resolve_path("new_en"))
        project.current_revision_id = 1 if legacy else document["current_revision_id"]
        project.thumbnail = document.get("thumbnail", project.thumbnail)
        project.custom_files = deepcopy(document.get("custom_files", {}))
        # Preserve legacy snapshot-relative entry paths. A revision prefix makes
        # their export paths relative to localisation without rewriting snapshots.
        if "source_mod_root" not in project.paths:
            mod_root, _ = legacy_localisation_root(project.resolve_path("new_en"))
            project.paths["source_mod_root"] = str(mod_root) if mod_root else None
        for revision in project.source_revisions:
            if "localisation_prefix" not in revision:
                _, prefix = legacy_localisation_root(revision.get("source_path") or project.resolve_path("new_en"))
                revision["localisation_prefix"] = prefix if prefix != "." else ""
        if "deploy_mod_root" not in project.paths:
            old_target = project.resolve_path("deploy_ru")
            mod_root, _ = legacy_localisation_root(old_target) if old_target else (None, "")
            project.paths["deploy_mod_root"] = str(mod_root) if mod_root else None
        old_output = project.resolve_path("output_ru")
        if old_output != project.root / "output_ru":
            project.paths.setdefault("legacy_output_ru", project.paths["output_ru"])
            project.paths["output_ru"] = "output_ru"
        for revision in project.source_revisions:
            path = (project.root / revision["snapshot"]).resolve()
            if not path.is_relative_to(project.root.resolve()) or (revision.get("snapshot_available", True) and not path.is_dir()):
                raise ProjectError(QCoreApplication.translate("project", "Не найден снимок revision {0}: {1}").format(revision['revision_id'], path))
        for name in SNAPSHOT_NAMES:
            path = project.snapshot_path(name)
            if path is not None and not path.is_dir():
                raise ProjectError(QCoreApplication.translate("project", "Не найден снимок источника: {0}").format(path))
        # External import paths and OUTPUT are deliberately not read here.
        if document.get("documents"):
            try:
                project.documents = [LocalizationDocument.from_dict(value) for value in document["documents"]]
            except (ValueError, TypeError, KeyError) as error:
                raise ProjectError(QCoreApplication.translate("project", "Некорректная структура документов: {0}").format(error)) from error
        else:
            project.documents = read_documents(project.snapshot_path("new_en"), project.entries)
        hydrate_comments(project.entries, project.documents, project.snapshot_path("initial_ru"))
        if document.get("normalization_version") != NORMALIZATION_VERSION:
            upgrade_normalization(project)
        if document.get("disabled_shadow_version") != DISABLED_SHADOW_VERSION:
            upgrade_disabled_shadows(project)
        # Older projects lack the author's comment baseline. Recover it from
        # the accepted immutable revision, never from the translator's comment.
        missing_comments = {entry["entry_id"] for entry in [*document["entries"], *document.get("removed_entries", [])]
                            if "accepted_source_comment" not in entry}
        comment_indexes = {}
        for row in project.entries:
            if row["entry_id"] not in missing_comments:
                continue
            revision_id = row.get("accepted_revision_id")
            if revision_id not in comment_indexes:
                revision = next((item for item in project.source_revisions if item["revision_id"] == revision_id), None)
                root = (project.root / revision["snapshot"] if revision.get("snapshot_available", True) else None
                        ) if revision else project.snapshot_path("old_en")
                comment_indexes[revision_id] = (build_key_index(parse_localization_folder(root, "english")[1])
                                                if root is not None else {})
            candidates = comment_indexes[revision_id].get(row["key"], [])
            if not candidates and row.get("old_key"):
                candidates = comment_indexes[revision_id].get(row["old_key"], [])
            candidate = next((item for item in candidates if item["text"] == row.get("accepted_source_text")), None)
            row["accepted_source_comment"] = (inline_comment(candidate.get("trailing", "")) if candidate else None)
            refresh_baseline(row, project.current_revision_id)
        if "diagnostics" not in document or document.get("diagnostics_version") != DIAGNOSTICS_VERSION:
            project.refresh_source_diagnostics()
        else:
            if isinstance(document["diagnostics"], list):
                project.source_diagnostics = [item for value in document["diagnostics"]
                                              if (item := Diagnostic.from_dict(value)) is not None and item.scope == "source"]
            if isinstance(document.get("auto_fixes"), list):
                project.source_auto_fixes = [item for value in document["auto_fixes"]
                                             if (item := AutoFix.from_dict(value)) is not None]
        for row in project.entries:
            if row.get("new_file") is not None:
                row["target_ru_file"] = english_to_russian_path(Path(project.current_source_prefix) / row["new_file"])
        separate_removed(project)
        # Normalize legacy pins after baseline/comment migrations have consumed
        # their snapshots. Loading changes no files; save commits before cleanup.
        rotate_history(project, prune_removed=True)
        return project

    @staticmethod
    def _validate(document):
        if not isinstance(document, dict):
            raise ProjectError(QCoreApplication.translate("project", "Проект должен содержать JSON-объект."))
        if type(document.get("schema_version")) is not int or document["schema_version"] not in (1, SCHEMA_VERSION):
            raise ProjectError(QCoreApplication.translate("project", "Неподдерживаемая версия формата проекта."))
        _uuid(document.get("project_id"))
        custom_files = document.get("custom_files", {})
        if not isinstance(custom_files, dict):
            raise ProjectError(QCoreApplication.translate("project", "Некорректный список CUSTOM"))
        identities = set()
        for name, record in custom_files.items():
            _relative_file(name)
            if (not isinstance(name, str) or not isinstance(record, dict) or set(record) != {"path", "sha256"}
                    or not Path(name).is_relative_to("localisation") or not name.endswith(".yml")
                    or Path(name).as_posix() != name or name.casefold() in identities):
                raise ProjectError(QCoreApplication.translate("project", "Некорректная запись CUSTOM"))
            digest = record["sha256"]
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)
                    or record["path"] != f"assets/custom/{digest}/{name}"):
                raise ProjectError(QCoreApplication.translate("project", "Некорректный путь/хеш CUSTOM"))
            identities.add(name.casefold())
        if not isinstance(document.get("name"), str) or not document["name"].strip():
            raise ProjectError(QCoreApplication.translate("project", "Не задано название проекта."))
        paths = document.get("paths")
        snapshots = document.get("snapshots")
        if (not isinstance(paths, dict) or not (set(PATH_NAMES) - {"deploy_ru", "stellaris"}).issubset(paths)
                or not set(paths).issubset(set(PATH_NAMES) | set(OPTIONAL_PATH_NAMES))):
            raise ProjectError(QCoreApplication.translate("project", "Некорректный список путей проекта."))
        for name, value in paths.items():
            if value is None and name not in ("new_en", "output_ru"):
                continue
            if not isinstance(value, str) or not value.strip() or "\0" in value:
                raise ProjectError(QCoreApplication.translate("project", "Некорректный путь: {0}").format(name))
        if not isinstance(snapshots, dict) or set(snapshots) != set(SNAPSHOT_NAMES):
            raise ProjectError(QCoreApplication.translate("project", "Некорректный список снимков проекта."))
        for name, value in snapshots.items():
            if name == "new_en" and document["schema_version"] == 2:
                _relative_file(value)
                if not value or not Path(value).is_relative_to("sources"):
                    raise ProjectError(QCoreApplication.translate("project", "Некорректный путь current snapshot."))
                continue
            if value != f"sources/{name}" and not (value is None and name != "new_en"):
                raise ProjectError(QCoreApplication.translate("project", "Некорректный путь снимка: {0}").format(name))
        revision_ids = set()
        if document["schema_version"] == 2:
            revisions = document.get("source_revisions")
            if not isinstance(revisions, list) or not revisions:
                raise ProjectError(QCoreApplication.translate("project", "Отсутствует история источника."))
            snapshot_paths = set()
            for expected, revision in enumerate(revisions, 1):
                if (not isinstance(revision, dict) or type(revision.get("revision_id")) is not int
                        or revision["revision_id"] != expected):
                    raise ProjectError(QCoreApplication.translate("project", "Некорректная последовательность source revisions."))
                path = revision.get("snapshot")
                if type(revision.get("snapshot_available", True)) is not bool:
                    raise ProjectError(QCoreApplication.translate("project", "Некорректное состояние revision."))
                source_files = revision.get("source_files", [])
                if not isinstance(source_files, list):
                    raise ProjectError(QCoreApplication.translate("project", "Некорректный список файлов revision."))
                for source_file in source_files:
                    _relative_file(source_file)
                    if not isinstance(source_file, str) or not source_file:
                        raise ProjectError(QCoreApplication.translate("project", "Некорректный путь файла revision."))
                _relative_file(path)
                if not path or not Path(path).is_relative_to("sources") or path in snapshot_paths:
                    raise ProjectError(QCoreApplication.translate("project", "Некорректный снимок source revision."))
                try:
                    date = datetime.fromisoformat(revision["created_at"])
                    if date.tzinfo is None:
                        raise ValueError("missing timezone")
                except (KeyError, ValueError, TypeError):
                    raise ProjectError(QCoreApplication.translate("project", "Некорректное время импорта source revision.")) from None
                if revision.get("label") is not None and not isinstance(revision["label"], str):
                    raise ProjectError(QCoreApplication.translate("project", "Некорректная метка source revision."))
                import_path = revision.get("source_path")
                prefix = revision.get("localisation_prefix", "")
                if not isinstance(prefix, str):
                    raise ProjectError(QCoreApplication.translate("project", "Некорректный relative path источника."))
                if prefix:
                    _relative_file(prefix)
                if import_path is not None and (not isinstance(import_path, str) or not import_path
                                               or "\0" in import_path or not Path(import_path).is_absolute()):
                    raise ProjectError(QCoreApplication.translate("project", "Некорректный исходный путь source revision."))
                snapshot_paths.add(path)
                revision_ids.add(expected)
            if (type(document.get("current_revision_id")) is not int
                    or document["current_revision_id"] != len(revisions)
                    or snapshots["new_en"] != revisions[-1]["snapshot"]
                    or not revisions[-1].get("snapshot_available", True)):
                raise ProjectError(QCoreApplication.translate("project", "Current source не соответствует истории revisions."))
        thumbnail = document.get("thumbnail")
        if thumbnail is not None:
            if (not isinstance(thumbnail, dict) or thumbnail.get("status") not in ("missing", "available")
                    or (thumbnail["status"] == "missing" and (thumbnail.get("path") is not None or thumbnail.get("sha256") is not None))
                    or (thumbnail["status"] == "available" and
                        (thumbnail.get("path") != "assets/thumbnail.png" or not isinstance(thumbnail.get("sha256"), str)
                         or len(thumbnail["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in thumbnail["sha256"])))):
                raise ProjectError(QCoreApplication.translate("project", "Некорректные данные thumbnail."))
        entries = document.get("entries")
        if not isinstance(entries, list):
            raise ProjectError(QCoreApplication.translate("project", "Некорректный список записей проекта."))
        removed = document.get("removed_entries", [])
        if not isinstance(removed, list) or any(not isinstance(e, dict) or e.get("section") != "REMOVED" for e in removed):
            raise ProjectError(QCoreApplication.translate("project", "Некорректный архив удалённых записей."))
        ids = set()
        translation_issues = []
        for entry in [*entries, *removed]:
            if not isinstance(entry, dict) or not set(ROW_FIELDS).issubset(entry):
                raise ProjectError(QCoreApplication.translate("project", "Неполная запись проекта."))
            _uuid(entry["entry_id"])
            for flag in ("restored_from_archive", "restored_needs_confirmation"):
                if type(entry.get(flag, False)) is not bool:
                    raise ProjectError(QCoreApplication.translate("project", "Некорректное состояние истории записи."))
            removed_revision = entry.get("removed_revision_id")
            if removed_revision is not None and (type(removed_revision) is not int or removed_revision not in revision_ids):
                raise ProjectError(QCoreApplication.translate("project", "Некорректная revision удаления."))
            if entry.get("last_source_prefix"):
                _relative_file(entry["last_source_prefix"])
            if document["schema_version"] == 2:
                if any(name not in entry for name in ("accepted_source_text", "accepted_revision_id")):
                    raise ProjectError(QCoreApplication.translate("project", "В записи отсутствует accepted baseline."))
                accepted, revision = entry["accepted_source_text"], entry["accepted_revision_id"]
                if accepted is not None and not isinstance(accepted, str):
                    raise ProjectError(QCoreApplication.translate("project", "Некорректный accepted source text."))
                if revision is not None and (type(revision) is not int or revision not in revision_ids or accepted is None):
                    raise ProjectError(QCoreApplication.translate("project", "Некорректная accepted revision."))
            if entry.get("kind", "ENTRY") not in ("ENTRY", "DISABLED_ENTRY"):
                raise ProjectError(QCoreApplication.translate("project", "Некорректный тип записи проекта."))
            for name in (*COMMENT_FIELDS, "accepted_source_comment"):
                value = entry.get(name)
                if value is not None and (not isinstance(value, str) or "\n" in value or "\r" in value):
                    raise ProjectError(QCoreApplication.translate("project", "Некорректный inline-комментарий: {0}").format(name))
            if entry["entry_id"] in ids:
                raise ProjectError(QCoreApplication.translate("project", "Повторяющийся идентификатор записи."))
            ids.add(entry["entry_id"])
            if not isinstance(entry["key"], str) or not entry["key"]:
                raise ProjectError(QCoreApplication.translate("project", "Некорректный ключ локализации."))
            if (entry["section"] not in ("CURRENT", "REMOVED")
                    or not isinstance(entry["source_status"], str)
                    or entry["source_status"] not in SOURCE_STATUSES):
                raise ProjectError(QCoreApplication.translate("project", "Некорректное состояние источника."))
            for field in ("old_text", "new_text", "old_key"):
                if entry[field] is not None and not isinstance(entry[field], str):
                    raise ProjectError(QCoreApplication.translate("project", "Некорректное поле: {0}").format(field))
            if entry["section"] == "CURRENT" and entry["new_text"] is None:
                raise ProjectError(QCoreApplication.translate("project", "У актуальной записи отсутствует NEW EN."))
            for field in ("position", "old_line", "new_line", "ru_line"):
                if entry[field] is not None and (type(entry[field]) is not int or entry[field] < 1):
                    raise ProjectError(QCoreApplication.translate("project", "Некорректный номер: {0}").format(field))
            for field in PATH_FIELDS:
                _relative_file(entry[field])
            score = entry["rename_score"]
            if score is not None and (type(score) not in (int, float) or not isfinite(score) or not 0 <= score <= 1):
                raise ProjectError(QCoreApplication.translate("project", "Некорректная оценка переименования."))
            translation = entry.get("translation")
            if not isinstance(translation, dict) or set(translation) != {"text", "state", "origin", "needs_review"}:
                translation_issues.append((entry, "translation", translation, QT_TRANSLATE_NOOP("project", "Ожидаются поля text, state, origin, needs_review.")))
                continue
            text = translation["text"]
            if text is not None and (not isinstance(text, str) or "\n" in text or "\r" in text):
                translation_issues.append((entry, "translation.text", text, QT_TRANSLATE_NOOP("project", "Перевод должен храниться одной строкой с литеральными \\n.")))
            if translation["state"] not in ("untranslated", "translated", "edited"):
                translation_issues.append((entry, "translation.state", translation["state"], QT_TRANSLATE_NOOP("project", "Неизвестное состояние перевода.")))
            if translation["origin"] not in ("en_fallback", "initial_ru", "manual", "none"):
                translation_issues.append((entry, "translation.origin", translation["origin"], QT_TRANSLATE_NOOP("project", "Неизвестное происхождение перевода.")))
            if type(translation["needs_review"]) is not bool:
                translation_issues.append((entry, "translation.needs_review", translation["needs_review"], QT_TRANSLATE_NOOP("project", "Ожидается bool: True или False.")))
            if text is None and translation["state"] != "untranslated":
                translation_issues.append((entry, "translation.state", translation["state"], QT_TRANSLATE_NOOP("project", "Отсутствующий перевод не может быть переведённым.")))
            if translation["origin"] == "en_fallback" and translation["state"] != "untranslated":
                translation_issues.append((entry, "translation.state", translation["state"], QT_TRANSLATE_NOOP("project", "Английская заглушка должна оставаться непереведённой.")))
        if translation_issues:
            raise TranslationValidationError(translation_issues)

    @classmethod
    def create(cls, root, name, new_en=None, old_en=None, initial_ru=None,
               output_ru=None, stellaris=None, *, source_mod_root=None, deploy_mod_root=None,
               progress=None):
        # Optional phase codes, independent of the caller's UI/threading model.
        def report(phase):
            if progress is not None:
                progress(phase)

        report("preparing_files")
        root = Path(root).resolve()
        if source_mod_root is not None:
            source_mod_root = Path(source_mod_root).resolve()
            new_en = mod_localisation(source_mod_root)
            if root.is_relative_to(source_mod_root) or source_mod_root.is_relative_to(root):
                raise ProjectError(QCoreApplication.translate("project", "Папка проекта не должна пересекаться с оригинальным модом."))
        elif new_en is not None:
            source_mod_root, _ = legacy_localisation_root(Path(new_en).resolve())
        if not name.strip():
            raise ProjectError(QCoreApplication.translate("project", "Введите название проекта."))
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise ProjectError(QCoreApplication.translate("project", "Для нового проекта выберите новую или пустую папку."))
        if old_en is not None:
            old_en = previous_localisation(old_en)
        originals = {"new_en": new_en, "old_en": old_en, "initial_ru": initial_ru}
        source_files = {}
        for key, value in originals.items():
            if value is None:
                if key == "new_en":
                    raise ProjectError(QCoreApplication.translate("project", "Необходимо выбрать NEW EN."))
                continue
            source = Path(value).resolve()
            if not source.is_dir():
                raise ProjectError(QCoreApplication.translate("project", "Не найдена папка источника: {0}").format(source))
            if root.is_relative_to(source) or source.is_relative_to(root):
                raise ProjectError(QCoreApplication.translate("project", "Папка проекта не должна пересекаться с исходными каталогами."))
            originals[key] = source
            language = "russian" if key == "initial_ru" else "english"
            files = sorted(source.rglob(f"*_l_{language}.yml"))
            if key == "new_en" and not files:
                raise ProjectError(QCoreApplication.translate("project", "В NEW EN не найдены файлы *_l_english.yml."))
            source_files[key] = files
        paths = {key: str(value) if value is not None else None for key, value in originals.items()}
        paths["stellaris"] = str(Path(stellaris).resolve()) if stellaris else None
        paths["deploy_ru"] = None
        paths["source_mod_root"] = str(source_mod_root) if source_mod_root else None
        paths["deploy_mod_root"] = str(Path(deploy_mod_root).resolve()) if deploy_mod_root else None
        paths["output_ru"] = "output_ru"
        if output_ru is not None and Path(output_ru).resolve() != root / "output_ru":
            paths["legacy_output_ru"] = str(Path(output_ru).resolve())
        snapshots = {key: f"sources/{key}" if key in source_files else None for key in SNAPSHOT_NAMES}
        project = cls(root / "project.slproj", str(uuid4()), name.strip(), paths, snapshots, [])
        project.source_revisions = [revision_record(1, snapshots["new_en"])]
        project.source_revisions[0]["source_path"] = str(originals["new_en"])
        _, prefix = legacy_localisation_root(originals["new_en"])
        project.source_revisions[0]["localisation_prefix"] = prefix if prefix != "." else ""
        project.prepare_thumbnail()
        project.validate_output_path()
        if deploy_mod_root is not None:
            project.validate_deploy_path()
        root.mkdir(parents=True, exist_ok=True)
        # Publish a complete manifest last; never overwrite an existing project.
        with tempfile.TemporaryDirectory(prefix=".creating-", dir=root) as temporary:
            staging = Path(temporary)
            indexes = {}
            for key, files in source_files.items():
                report({"new_en": "reading_source", "old_en": "reading_previous",
                        "initial_ru": "reading_translation"}[key])
                target = staging / snapshots[key]
                target.mkdir(parents=True)
                for file in files:
                    destination = target / file.relative_to(originals[key])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(file, destination)
                language = "russian" if key == "initial_ru" else "english"
                diagnostics = []
                fixes = []
                _, entries = parse_localization_folder(
                    target, language, diagnostics=diagnostics, source="ru" if key == "initial_ru" else key,
                    auto_fixes=fixes,
                )
                project.source_diagnostics.extend(
                    replace(item, file=(Path(snapshots[key]) / item.file).as_posix()) for item in diagnostics
                )
                project.source_auto_fixes.extend(
                    replace(item, file=(Path(snapshots[key]) / item.file).as_posix()) for item in fixes
                )
                indexes[key] = build_key_index(entries)
                if key == "new_en":
                    new_entries = entries
            old_index = indexes.get("old_en", {})
            ru_index = indexes.get("initial_ru", {})
            report("comparing" if old_en is not None else "preparing_project")
            results = compare_indexes(old_index, indexes["new_en"], ru_index, has_old_source=old_en is not None)
            report("preparing_project")
            rows = build_localization_rows(new_entries, old_index, indexes["new_en"], ru_index, results)
            by_key = {result["key"]: result for result in results}
            for row in rows:
                result = by_key[row["key"]]
                imported = result["ru"] is not None
                row.update(
                    entry_id=str(uuid4()), old_key=result["old_key"], rename_score=result["rename_score"],
                    translation_state="translated" if imported else "untranslated",
                    translation_origin="initial_ru" if imported else ("en_fallback" if row["new_text"] is not None else "none"),
                    needs_review=result["translation_status"] == "NEEDS_REVIEW",
                )
                initialize_baseline(row)
                if row.get("new_file") is not None:
                    row["target_ru_file"] = english_to_russian_path(Path(project.current_source_prefix) / row["new_file"])
            project.entries = rows
            project.documents = read_documents(staging / snapshots["new_en"], rows)
            staged_project = cls(staging / "project.slproj", project.project_id, project.name, paths, snapshots, rows,
                                 source_diagnostics=project.source_diagnostics, source_auto_fixes=project.source_auto_fixes,
                                 documents=project.documents)
            staged_project.source_revisions = deepcopy(project.source_revisions)
            staged_project.thumbnail = dict(project.thumbnail)
            staged_project._pending_thumbnail = project._pending_thumbnail
            report("saving")
            staged_project.save()
            if (root / "sources").exists() or project.file_path.exists():
                raise ProjectError(QCoreApplication.translate("project", "Папка проекта была изменена во время создания."))
            os.replace(staging / "sources", root / "sources")
            assets_published = False
            try:
                if (staging / "assets").exists():
                    if (root / "assets").exists():
                        raise ProjectError(QCoreApplication.translate("project", "Папка assets была создана другим процессом."))
                    os.rename(staging / "assets", root / "assets")
                    assets_published = True
                os.replace(staging / "project.slproj", project.file_path)
            except Exception:
                # Roll back only our own newly published snapshots, so creation
                # can be retried in the same empty folder after an I/O failure.
                os.replace(root / "sources", staging / "sources")
                if assets_published:
                    os.replace(root / "assets", staging / "assets")
                raise
        project._saved_path = project.file_path.resolve()
        project._saved_fingerprint = staged_project._saved_fingerprint
        project._pending_thumbnail = None
        separate_removed(project)
        return project

    def source_update_files(self, new_en=None):
        """Resolve and enumerate the source without changing the project."""
        mod_root = self.resolve_path("source_mod_root")
        source = (Path(new_en).resolve() if new_en is not None else
                  mod_localisation(mod_root) if mod_root is not None else self.resolve_path("new_en"))
        if not source.is_dir():
            raise ProjectError(QCoreApplication.translate("project", "Не найдена папка новой английской локализации."))
        if source.is_relative_to(self.root) or self.root.is_relative_to(source):
            raise ProjectError(QCoreApplication.translate("project", "Источник обновления не должен пересекаться с папкой проекта."))
        files = sorted(source.rglob("*_l_english.yml"))
        if not files:
            raise ProjectError(QCoreApplication.translate("project", "В источнике не найдены файлы *_l_english.yml."))
        return source, files

    def prepare_source_update(self, new_en=None, label=None, *, analysis=None, rename_decisions=None):
        """Build a candidate; save publishes its captured source bytes and manifest."""
        """Build a candidate on an immutable snapshot; publish its manifest with save()."""
        rename_matches = None
        if analysis is None:
            source, files = self.source_update_files(new_en)
            copy_root = source
        else:
            if new_en is not None:
                raise ProjectError(QCoreApplication.translate("project", "Нельзя менять источник после Preview. Повторите анализ."))
            analysis.validate(self)
            rename_matches = analysis.resolved_matches(rename_decisions or {})
            source, copy_root = analysis.source, analysis.staged
            files = [copy_root / path for path in analysis.hashes]
        revision_id = self.current_revision_id + 1
        relative = f"sources/revisions/{revision_id}-{uuid4().hex}/new_en"
        candidate = deepcopy(self)
        candidate.current_revision_id = revision_id
        candidate.source_revisions.append(revision_record(revision_id, relative, label))
        # Retain known external source paths when CURRENT moves to a later import.
        candidate.source_revisions[-2].setdefault("source_path", str(self.resolve_path("new_en")))
        candidate.source_revisions[-1]["source_path"] = str(source)
        _, prefix = legacy_localisation_root(source)
        candidate.source_revisions[-1]["localisation_prefix"] = prefix if prefix != "." else ""
        if new_en is not None:
            inferred, _ = legacy_localisation_root(source)
            candidate.paths["source_mod_root"] = str(inferred) if inferred else None
        candidate.snapshots["new_en"] = relative
        candidate.paths["new_en"] = str(source)
        candidate.prepare_thumbnail()
        candidate.validate_output_path()
        with tempfile.TemporaryDirectory(prefix=".updating-", dir=self.root) as temporary:
            staged = Path(temporary) / "new_en"
            staged.mkdir()
            for file in files:
                target = staged / file.relative_to(copy_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
            if analysis is not None:
                analysis.validate_bytes(staged)
            diagnostics, fixes = [], []
            _, entries = parse_localization_folder(staged, "english", diagnostics=diagnostics,
                                                    source="new_en", auto_fixes=fixes)
            candidate.entries = reconcile_entries([*self.entries, *self.removed_entries], entries, revision_id,
                                                   rename_matches=rename_matches,
                                                   archive_matches=analysis.resolved_archive_matches(rename_decisions or {})
                                                   if analysis is not None else None)
            candidate.removed_entries = []
            for row in candidate.entries:
                if row["section"] == "REMOVED" and row.get("removed_revision_id") == revision_id:
                    row["last_source_prefix"] = self.current_source_prefix
            separate_removed(candidate)
            for row in candidate.entries:
                if row.get("new_file") is not None:
                    row["target_ru_file"] = english_to_russian_path(Path(candidate.current_source_prefix) / row["new_file"])
            candidate.documents = read_documents(staged, candidate.entries)
            candidate.source_revisions[-1]["source_files"] = [
                (Path(candidate.current_source_prefix) / doc.file).as_posix() for doc in candidate.documents]
            rotate_history(candidate, prune_removed=True)
            candidate.source_diagnostics = [d for d in self.source_diagnostics if d.source != "new_en"]
            candidate.source_diagnostics.extend(replace(d, file=(Path(relative) / d.file).as_posix()) for d in diagnostics)
            candidate.source_auto_fixes = [d for d in self.source_auto_fixes if d.source != "new_en"]
            candidate.source_auto_fixes.extend(replace(d, file=(Path(relative) / d.file).as_posix()) for d in fixes)
            self._validate(candidate.to_document())
            candidate._pending_source_snapshots[relative] = {
                path.relative_to(staged).as_posix(): path.read_bytes()
                for path in staged.rglob("*") if path.is_file()
            }
        return candidate

    def validate_output_path(self, output=None):
        output = Path(output).resolve() if output is not None else self.resolve_path("output_ru")
        self.validate_delivery_path(output, "OUTPUT")
        deploy = self.resolve_path("deploy_mod_root") or self.resolve_path("deploy_ru")
        if deploy is not None and (output.is_relative_to(deploy) or deploy.is_relative_to(output)):
            raise ProjectError(QCoreApplication.translate("project", "OUTPUT и Deploy должны находиться в отдельных папках."))
        if output.is_dir() and any(output.rglob(MANIFEST_NAME)):
            raise ProjectError(QCoreApplication.translate("project", "OUTPUT содержит manifest Deploy. Выберите отдельную папку экспорта."))
        if any((parent / MANIFEST_NAME).exists() for parent in (output, *output.parents)):
            raise ProjectError(QCoreApplication.translate("project", "Папка управляется синхронизацией мода. Выберите отдельную папку для ручного экспорта."))
        return output

    def validate_delivery_path(self, target, operation):
        target = Path(target).resolve()
        protected_files = (self.file_path.resolve(), self.root / "ui-state.json")
        if any(file == target or file.is_relative_to(target) for file in protected_files):
            raise ProjectError(QCoreApplication.translate("project", "{0}: выбрана папка проекта или её родитель. Выберите отдельную папку локализации.").format(operation))
        protected_dirs = [self.root.resolve() / "sources", self.root.resolve() / "assets"]
        protected_dirs.extend(self.resolve_path(name) for name in ("new_en", "old_en", "initial_ru", "stellaris")
                              if self.paths[name] and not (name == "initial_ru" and operation == "Deploy"
                                  and self.resolve_path(name).is_relative_to((target / "localisation").resolve())))
        if self.paths.get("source_mod_root"):
            protected_dirs.append(self.resolve_path("source_mod_root"))
        protected_dirs.extend(Path(revision["source_path"]) for revision in self.source_revisions if revision.get("source_path"))
        protected_dirs.extend(mod_root for revision in self.source_revisions if revision.get("source_path")
                              if (mod_root := legacy_localisation_root(revision["source_path"])[0]) is not None)
        for folder in protected_dirs:
            folder = folder.resolve()
            if target.is_relative_to(folder) or folder.is_relative_to(target):
                raise ProjectError(QCoreApplication.translate("project", "{0} не должен пересекаться с исходниками или snapshots/revisions.").format(operation))
        if target.exists() and not target.is_dir():
            raise ProjectError(QCoreApplication.translate("project", "{0}: целевой путь должен быть папкой.").format(operation))
        if target.is_dir() and any(target.rglob("*.slproj")):
            raise ProjectError(QCoreApplication.translate("project", "{0}: целевая папка содержит проект .slproj.").format(operation))
        if operation != "Deploy" and ((target / "descriptor.mod").exists() or (target / "localisation").is_dir()):
            raise ProjectError(QCoreApplication.translate("project", "{0}: выбран корень мода. Выберите папку localisation/russian.").format(operation))
        return target

    def validate_deploy_path(self, target=None):
        target = target if target is not None else self.resolve_path("deploy_mod_root")
        if target is None:
            raise ProjectError(QCoreApplication.translate("project", "Выберите корневую папку переводного мода внутри Stellaris mods."))
        target = self.validate_delivery_path(target, "Deploy")
        if (target.is_relative_to(self.root.resolve()) or target == Path(target.anchor) or target == Path.home().resolve()
                or target.name.casefold() in ("mod", "mods", "localisation", "russian", "english")):
            raise ProjectError(QCoreApplication.translate("project", "Deploy: выберите отдельный корень переводного мода, а не общий каталог или папку проекта/локализации."))
        output = self.resolve_path("output_ru")
        if target.is_relative_to(output) or output.is_relative_to(target):
            raise ProjectError(QCoreApplication.translate("project", "Deploy и OUTPUT должны находиться в отдельных папках."))
        return target

    def set_deploy_path(self, target):
        target = self.validate_deploy_path(target)
        self.paths["deploy_mod_root"] = str(target)

    def set_source_mod_root(self, root):
        root = Path(root).resolve()
        localisation = mod_localisation(root)
        if root.is_relative_to(self.root.resolve()) or self.root.resolve().is_relative_to(root):
            raise ProjectError(QCoreApplication.translate("project", "Оригинальный мод не должен пересекаться с папкой проекта."))
        deploy = self.resolve_path("deploy_mod_root") or self.resolve_path("deploy_ru")
        if deploy is not None and (root.is_relative_to(deploy) or deploy.is_relative_to(root)):
            raise ProjectError(QCoreApplication.translate("project", "Оригинальный мод не должен пересекаться с Deploy."))
        if not any(localisation.rglob("*_l_english.yml")):
            raise ProjectError(QCoreApplication.translate("project", "В localisation оригинального мода нет *_l_english.yml."))
        self.paths["source_mod_root"] = str(root)

    def deploy_file_inventory(self):
        """Historical GENERATED paths and exact initial RU bytes, never guessed keys."""
        generated = set()
        sources = []
        for revision in self.source_revisions:
            if "source_files" in revision:
                generated.update((Path("localisation") / english_to_russian_path(path)).as_posix()
                                 for path in revision["source_files"])
            elif revision.get("snapshot_available", True):
                sources.append((self.root / revision["snapshot"], revision.get("localisation_prefix", "")))
        old = self.snapshot_path("old_en")
        if old is not None:
            _, prefix = legacy_localisation_root(self.resolve_path("old_en"))
            sources.append((old, "" if prefix == "." else prefix))
        for root, prefix in sources:
            for path in root.rglob("*_l_english.yml"):
                relative = Path(prefix) / path.relative_to(root)
                generated.add((Path("localisation") / english_to_russian_path(relative)).as_posix())
        initial_sources = {}
        imported = self.snapshot_path("initial_ru")
        if imported is not None:
            _, prefix = legacy_localisation_root(self.resolve_path("initial_ru"))
            prefix = "" if prefix == "." else prefix
            for path in imported.rglob("*_l_russian.yml"):
                name = (Path("localisation") / prefix / path.relative_to(imported)).as_posix()
                initial_sources[name] = path
        return generated, initial_sources

    def custom_deploy_sources(self):
        result = {}
        for name, record in self.custom_files.items():
            path = target_file(self.root, record["path"])
            if not path.is_file() or file_hash(path) != record["sha256"]:
                raise ProjectError(QCoreApplication.translate("project", "Копия CUSTOM отсутствует или повреждена: {0}").format(name))
            result[name] = path
        return result

    def preserve_deploy_custom(self, selected):
        """Save immutable copies first, then atomically publish their references."""
        records, stored = self.store_deploy_custom(selected)
        previous = self.custom_files
        self.custom_files = records
        try:
            self.save()
        except Exception:
            self.custom_files = previous
            raise
        return stored

    def store_deploy_custom(self, selected):
        """Prepare immutable CUSTOM assets; caller commits their metadata."""
        records = deepcopy(self.custom_files)
        stored = {}
        for name, item in selected.items():
            target_file(self.root, name)
            data = Path(item["path"]).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != item["sha256"]:
                raise ProjectError(QCoreApplication.translate("project", "Файл изменился после preview: {0}. Повторите Deploy.").format(name))
            relative = f"assets/custom/{digest}/{name}"
            asset = target_file(self.root, relative)
            asset.parent.mkdir(parents=True, exist_ok=True)
            if asset.exists():
                if not asset.is_file() or file_hash(asset) != digest:
                    raise ProjectError(QCoreApplication.translate("project", "Повреждена сохранённая копия CUSTOM: {0}").format(name))
            else:
                output = QSaveFile(str(asset))
                output.setDirectWriteFallback(False)
                if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                    raise ProjectError(output.errorString())
                if output.write(data) != len(data):
                    output.cancelWriting()
                    raise ProjectError(QCoreApplication.translate("project", "Не удалось сохранить CUSTOM: {0}").format(name))
                if not output.commit():
                    raise ProjectError(output.errorString())
            records[name] = {"path": relative, "sha256": digest}
            stored[name] = asset
        return records, stored

    def deploy(self, *, review=None):
        target = self.validate_deploy_path()
        source = self.snapshot_path("new_en")
        if not source.is_dir():
            raise ProjectError(QCoreApplication.translate("project", "Не найден снимок current source."))
        generated, initial_sources = self.deploy_file_inventory()
        initial_files = {name: file_hash(path) for name, path in initial_sources.items()}
        return deploy_localization(source, self.entries, self.documents, target, self.project_id,
                                   source_prefix=self.current_source_prefix, legacy_target=self.resolve_path("deploy_ru"),
                                   initial_files=initial_files, generated_history=generated,
                                   initial_sources=initial_sources, custom_files=self.custom_deploy_sources(),
                                   review=review, preserve_custom=self.preserve_deploy_custom)

    def export(self, destination=None, *, confirm_overwrite=None):
        output = self.validate_output_path(destination)
        source = self.snapshot_path("new_en")
        if not source.is_dir():
            raise ProjectError(QCoreApplication.translate("project", "Не найден снимок NEW EN."))
        builder = export_to_directory if destination is not None else build_russian_localization
        options = {"confirm_overwrite": confirm_overwrite} if destination is not None else {}
        result = builder(source, build_ru_index_from_rows(self.entries), output,
                         documents=self.documents, rows=self.entries,
                         source_prefix=self.current_source_prefix, **options)
        if result is None:
            return None
        files, _ = result
        return {
            "files": len(files), "output_path": output,
            "missing": sum(row["section"] == "CURRENT" and row["translation_state"] == "untranslated"
                           and not is_whitespace_text(row["new_text"]) for row in self.entries),
        }
