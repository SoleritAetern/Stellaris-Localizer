"""Last unsaved user state, separate from project persistence and source history."""

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from PySide6.QtCore import (QCoreApplication, QLockFile, QObject,
                           QStandardPaths, QThread, QTimer)
from PySide6.QtWidgets import QMessageBox

from .deployment import file_hash
from .recent_projects import path_identity
from .translation_baseline import refresh_baseline


FIELDS = ("ru_text", "translation_state", "translation_origin", "accepted_source_text",
          "accepted_revision_id", "accepted_source_comment", "ru_comment",
          "ru_comment_trailing", "ignored_diagnostics", "restored_needs_confirmation")
DEFAULTS = {"ru_comment_trailing": "", "ignored_diagnostics": (),
            "restored_needs_confirmation": False}


def recovery_directory():
    root = os.environ.get("LOCALAPPDATA") or QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericDataLocation)
    return Path(root) / "StellarisLocalizer" / "recovery"


def recovery_path(directory, project_path):
    # One slot per path; UUID is independently verified inside the document.
    identity = hashlib.sha256(path_identity(project_path).encode("utf-8")).hexdigest()
    return Path(directory) / (identity + ".json")


def user_state(row):
    return tuple(tuple(row.get(name, ())) if name == "ignored_diagnostics"
                 else row.get(name, DEFAULTS.get(name)) for name in FIELDS)


def apply_state(row, values):
    row.update(zip(FIELDS, values))
    row["ignored_diagnostics"] = list(row["ignored_diagnostics"])
    refresh_baseline(row)


def read_document(path):
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("Recovery file is too large")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_recovery(document, project):
    """Validate the entire overlay before touching any live row."""
    if (not isinstance(document, dict) or type(document.get("recovery_version")) is not int
            or document["recovery_version"] != 1):
        raise ValueError("Unsupported recovery format")
    UUID(document["project_id"])
    UUID(document["session_id"])
    if (document["project_id"] != project.project_id
            or path_identity(document["project_path"]) != path_identity(project.file_path)
            or not Path(document["project_path"]).is_absolute()
            or not isinstance(document["project_name"], str)
            or type(document["generation"]) is not int or document["generation"] < 0
            or datetime.fromisoformat(document["written_at"]).tzinfo is None):
        raise ValueError("Invalid recovery identity")
    # Both the loaded object and actual file must belong to the same baseline.
    if (document.get("base_sha256") != project._saved_fingerprint
            or file_hash(project.file_path) != document["base_sha256"]):
        raise BaselineMismatch(QCoreApplication.translate("recovery", "Сохранённая версия проекта изменилась. Автоматическое восстановление невозможно."))
    rows = {row["entry_id"]: row for row in (*project.entries, *project.removed_entries)}
    revisions = {item["revision_id"] for item in project.source_revisions}
    entries = document["entries"]
    if not isinstance(entries, dict) or not entries:
        raise ValueError("Empty or invalid recovery entries")
    result = {}
    for identity, values in entries.items():
        UUID(identity)
        if identity not in rows or not isinstance(values, dict) or set(values) != set(FIELDS):
            raise ValueError("Unknown entry or user-state field")
        for name in ("ru_text", "accepted_source_text", "accepted_source_comment", "ru_comment"):
            value = values[name]
            if value is not None and (not isinstance(value, str) or (
                    name != "accepted_source_text" and ("\r" in value or "\n" in value))):
                raise ValueError("Invalid recovery text")
        trailing = values["ru_comment_trailing"]
        if trailing is not None and (not isinstance(trailing, str) or any(char in trailing for char in "\r\n")):
            raise ValueError("Invalid comment trailing")
        if (values["translation_state"] not in ("untranslated", "translated", "edited")
                or values["translation_origin"] not in ("none", "en_fallback", "initial_ru", "manual")
                or type(values["restored_needs_confirmation"]) is not bool):
            raise ValueError("Invalid translation state")
        if ((values["ru_text"] is None and values["translation_state"] != "untranslated")
                or (values["translation_origin"] == "en_fallback"
                    and values["translation_state"] != "untranslated")):
            raise ValueError("Inconsistent translation state")
        revision = values["accepted_revision_id"]
        if revision is not None and (type(revision) is not int or revision not in revisions
                                     or values["accepted_source_text"] is None):
            raise ValueError("Unknown accepted revision")
        ignored = values["ignored_diagnostics"]
        if not isinstance(ignored, list) or any(not isinstance(item, str) for item in ignored):
            raise ValueError("Invalid ignored diagnostics")
        result[identity] = user_state(values)
    return result


@dataclass(frozen=True)
class Snapshot:
    project_id: str
    project_path: str
    project_name: str
    base_sha256: str
    session_id: str
    generation: int
    written_at: str
    entries: tuple


class BaselineMismatch(ValueError):
    pass


class Publication:
    """Only this small gate is shared with a writer, never Project or widgets."""
    def __init__(self):
        self.lock = threading.Lock()
        self.epoch = 0

    def invalidate(self, path, *, remove):
        with self.lock:
            self.epoch += 1
            if remove:
                path.unlink(missing_ok=True)


class RecoveryWrite(QThread):
    def __init__(self, path, snapshot, gate, parent):
        super().__init__(parent)
        self.path, self.snapshot, self.gate = path, snapshot, gate
        self.epoch = gate.epoch
        self.error = None
        self.published = False
        self.elapsed = 0.0
        self.commit_seconds = 0.0

    def run(self):
        started = time.perf_counter()
        temporary = self.path.with_name(self.path.name + "." + uuid4().hex + ".tmp")
        try:
            # Python's disk IO/fsync release the GIL. In the pinned PySide build,
            # QSaveFile.commit held it for ~250 ms on a 15 MB recovery snapshot.
            with temporary.open("xb") as output:
                def write(text):
                    output.write(text.encode("utf-8"))

                data = self.snapshot
                header = {name: getattr(data, name) for name in (
                    "project_id", "project_path", "project_name", "base_sha256",
                    "session_id", "generation", "written_at")}
                header["recovery_version"] = 1
                write(json.dumps(header, ensure_ascii=False)[:-1] + ',"entries":{')
                for number, (identity, values) in enumerate(data.entries):
                    if self.epoch != self.gate.epoch:
                        return
                    write(("," if number else "") + json.dumps(identity) + ":"
                          + json.dumps(dict(zip(FIELDS, values)), ensure_ascii=False, allow_nan=False))
                    if number % 64 == 63:
                        time.sleep(.001)
                write("}}\n")
                output.flush()
                os.fsync(output.fileno())
            with self.gate.lock:
                if self.epoch != self.gate.epoch:
                    return
                commit_started = time.perf_counter()
                os.replace(temporary, self.path)
                self.commit_seconds = time.perf_counter() - commit_started
                self.published = True
        except Exception as error:
            self.error = str(error)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Never read a .tmp as recovery; a later locked session cleans it.
            self.elapsed = time.perf_counter() - started


class RecoverySession(QObject):
    def __init__(self, manager, project):
        super().__init__(manager)
        self.manager, self.project = manager, project
        self.path = recovery_path(manager.directory, project.file_path)
        self.session_id = str(uuid4())
        self.gate = Publication()
        self.lock = QLockFile(str(self.path) + ".lock")
        self.lock.setStaleLockTime(0)
        self.enabled = self.lock.tryLock(0)
        self.warning = ""
        self.window = None
        self.rows = {row["entry_id"]: row for row in (*project.entries, *project.removed_entries)}
        self.baseline = {identity: user_state(row) for identity, row in self.rows.items()}
        self.base_hash = project._saved_fingerprint
        self.changed = {}
        self.restored = False
        self.closed = False
        self.generation = 0
        self.last_entries = ()
        self.job = None
        self.connections = []
        self.snapshot_seconds = self.write_seconds = 0.0
        self.commit_seconds = 0.0
        self.written_generation = 0
        self.timer = QTimer(self)
        self.timer.setInterval(25_000)
        self.timer.timeout.connect(self.checkpoint)

    def offer(self, parent):
        if not self.enabled:
            self.warning = QCoreApplication.translate("recovery", "Аварийное восстановление недоступно: проект уже открыт в другом экземпляре приложения.")
            return
        # Only this path's abandoned temporary writes, while holding its lock.
        for temporary in self.path.parent.glob(self.path.name + ".*.tmp"):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # An invalidated writer may still be closing its temporary file.
                # It cannot publish without the gate; cleanup can wait.
                pass
        if not self.path.exists():
            return
        try:
            document = read_document(self.path)
            states = validate_recovery(document, self.project)
        except Exception as error:
            self.invalid_recovery(parent, error)
            return
        box = QMessageBox(parent)
        box.setWindowTitle(QCoreApplication.translate("recovery", "Аварийное восстановление"))
        box.setText(QCoreApplication.translate("recovery", "Обнаружены несохранённые изменения проекта «{0}».\nВосстановить их?")
                    .format(self.project.name))
        restore = box.addButton(QCoreApplication.translate("recovery", "Восстановить"), QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(QCoreApplication.translate("recovery", "Не восстанавливать"), QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(restore)
        box.setEscapeButton(discard)
        box.exec()
        if box.clickedButton() is restore:
            try:
                # The confirmation can stay open while an external writer saves.
                states = validate_recovery(document, self.project)
            except Exception as error:
                self.invalid_recovery(parent, error)
                return
            for identity, values in states.items():
                apply_state(self.rows[identity], values)
            self.changed = {identity: user_state(self.rows[identity]) for identity in states}
            self.restored = True
        else:
            self.gate.invalidate(self.path, remove=True)

    def invalid_recovery(self, parent, error):
        # Do not overwrite/delete an incompatible or damaged recovery later.
        self.enabled = False
        reason = (str(error) if isinstance(error, (BaselineMismatch, OSError)) else
                  QCoreApplication.translate("recovery", "Аварийная копия повреждена или не соответствует записям проекта."))
        QMessageBox.warning(parent, QCoreApplication.translate("recovery", "Аварийное восстановление"),
                            QCoreApplication.translate("recovery", "Не удалось восстановить проект. Аварийная копия сохранена:\n{0}\n\n{1}")
                            .format(self.path, reason))

    def bind(self, window):
        self.window = window
        window.recovery = self
        self.connections.append(window.model.dataChanged.connect(self.model_changed))
        self.connections.append(window.undo_stack.cleanChanged.connect(self.clean_changed))
        if self.restored:
            window.undo_stack.resetClean()
            self.last_entries = tuple(self.changed.items())
        if self.enabled:
            self.timer.start()
        if self.warning:
            window.statusBar().showMessage(self.warning, 15000)

    def model_changed(self, top, bottom, roles=()):
        if not self.enabled or self.closed:
            return
        for row in self.window.model.rows[top.row():bottom.row() + 1]:
            identity = row.get("entry_id")
            if identity not in self.baseline:
                continue
            values = user_state(row)
            if values == self.baseline[identity]:
                self.changed.pop(identity, None)
            else:
                self.changed[identity] = values

    def clean_changed(self, clean):
        if clean and self.enabled and not self.closed:
            # Run after the current editor/undo transaction has finished.
            QTimer.singleShot(0, self, self.checkpoint)

    def capture(self):
        started = time.perf_counter()
        states = dict(self.changed)
        for identity, draft in self.window.recovery_drafts().items():
            row = self.rows.get(identity)
            if row is None:
                continue
            values = dict(zip(FIELDS, states.get(identity, user_state(row))))
            values.update(draft)
            frozen = user_state(values)
            if frozen == self.baseline[identity]:
                states.pop(identity, None)
            else:
                states[identity] = frozen
        entries = tuple(states.items())
        self.snapshot_seconds = time.perf_counter() - started
        return entries

    def checkpoint(self):
        if not self.enabled or self.closed or self.window is None:
            return
        if self.project._saved_fingerprint != self.base_hash:
            # CUSTOM/takeover can commit metadata after the normal Save hook.
            # They do not alter user entries; retain any edits made since then.
            self.base_hash = self.project._saved_fingerprint
            self.remove()
            self.last_entries = None
        entries = self.capture()
        if self.job is not None:
            return  # Completion captures the latest generation, no snapshot queue.
        if entries == self.last_entries:
            return
        self.generation += 1
        if not entries:
            self.remove()
            self.last_entries = ()
            return
        snapshot = Snapshot(self.project.project_id, path_identity(self.project.file_path),
                            self.project.name, self.base_hash, self.session_id, self.generation,
                            datetime.now(timezone.utc).isoformat(), entries)
        self.last_entries = entries
        self.job = RecoveryWrite(self.path, snapshot, self.gate, self.manager)
        self.job.finished.connect(self.write_finished)
        self.job.start()

    def write_finished(self):
        job, self.job = self.job, None
        self.write_seconds = job.elapsed
        self.commit_seconds = job.commit_seconds
        if job.published:
            self.written_generation = job.snapshot.generation
        if job.error:
            self.last_entries = None  # Retry at the next periodic tick.
            if not self.closed and self.window is not None:
                self.window.statusBar().showMessage(
                    QCoreApplication.translate("recovery", "Не удалось записать аварийную копию: {0}").format(job.error), 15000)
        job.deleteLater()
        if self.closed:
            self.deleteLater()
        elif not job.error:
            self.checkpoint()

    def remove(self):
        try:
            self.gate.invalidate(self.path, remove=True)
        except OSError as error:
            if self.window is not None:
                self.window.statusBar().showMessage(
                    QCoreApplication.translate("recovery", "Не удалось удалить аварийную копию: {0}").format(error), 15000)

    def saved(self):
        if self.closed:
            return
        # Save has committed all active drafts. No Project serialization here.
        for identity in self.changed:
            self.baseline[identity] = user_state(self.rows[identity])
        self.changed.clear()
        self.base_hash = self.project._saved_fingerprint
        self.restored = False
        self.last_entries = ()
        if self.enabled:
            self.remove()

    def finish(self, *, discard=False):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        for connection in self.connections:
            QObject.disconnect(connection)
        self.connections.clear()
        if self.enabled:
            if discard:
                self.remove()
            else:
                self.gate.invalidate(self.path, remove=False)
        self.lock.unlock()
        if self.window is not None:
            self.window.recovery = None
        self.window = self.project = None
        if self.job is None:
            self.deleteLater()


class RecoveryManager(QObject):
    def __init__(self, parent=None, directory=None):
        super().__init__(parent)
        self.directory = Path(directory) if directory is not None else recovery_directory()
        QCoreApplication.instance().aboutToQuit.connect(self.shutdown)

    def prepare(self, project, parent):
        session = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            session = RecoverySession(self, project)
            session.offer(parent)
            return session
        except OSError as error:
            if session is not None:
                session.finish()
            QMessageBox.warning(parent, QCoreApplication.translate("recovery", "Аварийное восстановление"),
                                QCoreApplication.translate("recovery", "Аварийное восстановление недоступно: {0}").format(error))
            return None

    def startup_paths(self, parent):
        if not self.directory.is_dir():
            return []
        paths = []
        for file in sorted(self.directory.glob("*.json")):
            lock = QLockFile(str(file) + ".lock")
            lock.setStaleLockTime(0)
            if not lock.tryLock(0):
                continue
            try:
                data = read_document(file)
                path = Path(data["project_path"])
                if recovery_path(self.directory, path) != file or not path.is_absolute() or not path.is_file():
                    raise BaselineMismatch(QCoreApplication.translate("recovery", "Файл проекта отсутствует или путь аварийной копии некорректен."))
                paths.append(path)
            except Exception as error:
                reason = (str(error) if isinstance(error, (BaselineMismatch, OSError)) else
                          QCoreApplication.translate("recovery", "Аварийная копия повреждена или не соответствует записям проекта."))
                QMessageBox.warning(parent, QCoreApplication.translate("recovery", "Аварийное восстановление"),
                                    QCoreApplication.translate("recovery", "Не удалось восстановить проект. Аварийная копия сохранена:\n{0}\n\n{1}")
                                    .format(file, reason))
            finally:
                lock.unlock()
        return paths

    def shutdown(self):
        # QObject/QThread children outlive every window; never destroy a running job.
        for session in self.findChildren(RecoverySession):
            session.finish()
        for job in self.findChildren(RecoveryWrite):
            job.wait()
