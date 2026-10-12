"""Read-only ownership checks for the New Project translation selector."""

import json
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from threading import Event
from uuid import UUID

from PySide6.QtCore import QObject, QTimer, Signal

from .deployment import MANIFEST_NAME, load_manifest, target_file
from .paths import legacy_localisation_root
from .project import Project
from .recent_projects import path_identity


def project_reference(file):
    """Validate the actual document, without loading snapshots or indexing rows."""
    file = Path(file).resolve()
    document = json.loads(file.read_text(encoding="utf-8-sig"))
    Project._validate(document)
    paths = document["paths"]
    target = paths.get("deploy_mod_root")
    if target:
        target = Path(target)
        target = (target if target.is_absolute() else file.parent / target).resolve()
    elif "deploy_mod_root" not in paths and paths.get("deploy_ru"):
        legacy = Path(paths["deploy_ru"])
        legacy = legacy if legacy.is_absolute() else file.parent / legacy
        target, _ = legacy_localisation_root(legacy.resolve())
    return {"path": file, "project_id": document["project_id"],
            "name": document["name"], "target": target}


def inspect_ownership(target, recent_paths, project_roots, cancelled, *, manifest_owner_only=False):
    """Inspect known files and one directory level, never crawl project contents."""
    try:
        target = Path(target).resolve()
        identity = path_identity(target)
        candidates = list(recent_paths)
        for root in dict.fromkeys(project_roots):
            if cancelled():
                return None
            root = Path(root)
            try:
                children = list(root.iterdir())
            except FileNotFoundError:
                continue
            for child in children:
                if child.is_file() and child.suffix.lower() == ".slproj":
                    candidates.append(child)
                elif child.is_dir() and not child.is_symlink() and not child.is_junction():
                    candidates.extend(file for file in child.iterdir()
                                      if file.suffix.lower() == ".slproj" and file.is_file())
        seen = set()
        references = []
        owners = []
        errors = []
        for file in candidates:
            if cancelled():
                return None
            file = Path(file)
            key = path_identity(file)
            if key in seen:
                continue
            seen.add(key)
            try:
                reference = project_reference(file)
            except FileNotFoundError:
                continue  # Stale recent links are not proof of ownership.
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                errors.append(f"{file}: {error}")
                continue
            references.append(reference)
            if reference["target"] is not None and path_identity(reference["target"]) == identity:
                owners.append(reference)
        if cancelled():
            return None
        if owners and not manifest_owner_only:
            # The current project binding takes precedence over an obsolete
            # manifest. Opening it does not transfer ownership or permit Deploy.
            return {"state": "owned", "owners": owners, "owner_id": None}
        manifest = target_file(target, MANIFEST_NAME)
        try:
            manifest.stat()
            has_manifest = True
        except FileNotFoundError:
            has_manifest = False
        if has_manifest:
            document = json.loads(manifest.read_text(encoding="utf-8"))
            owner_id = document["project_id"]
            UUID(owner_id)
            # Reuse Deploy's validation only when no current binding was found.
            load_manifest(target, owner_id)
            owners = [reference for reference in references
                      if UUID(reference["project_id"]) == UUID(owner_id)]
            if cancelled():
                return None
            if owners:
                return {"state": "owned", "owners": owners, "owner_id": owner_id}
            if errors and manifest_owner_only:
                return {"state": "error", "detail": "\n".join(errors)}
            return {"state": "missing", "owner_id": owner_id}
        if errors:
            return {"state": "error", "detail": "\n".join(errors)}
        return {"state": "free"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
        return {"state": "error", "detail": str(error)}


def _shutdown(executor, lifetime, *args):
    lifetime.set()
    executor.shutdown(wait=False, cancel_futures=True)


class TranslationOwnershipCheck(QObject):
    finished = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="translation-owner")
        self._lifetime = Event()
        self._cancel = Event()
        self._future = None
        self._poll = QTimer(self)
        self._poll.setInterval(40)
        self._poll.timeout.connect(self._collect)
        self.destroyed.connect(partial(_shutdown, self._executor, self._lifetime))

    def cancel(self):
        self._cancel.set()
        self._poll.stop()
        if self._future is not None:
            self._future.cancel()
        self._future = None

    def start(self, target, recent_paths, project_roots):
        self.cancel()
        if self._lifetime.is_set():
            return
        self._cancel = Event()
        cancel, lifetime = self._cancel, self._lifetime
        self._future = self._executor.submit(
            inspect_ownership, target, tuple(recent_paths), tuple(project_roots),
            lambda: cancel.is_set() or lifetime.is_set())
        self._poll.start()

    def _collect(self):
        if self._future is None or not self._future.done():
            return
        self._poll.stop()
        result = self._future.result()
        self._future = None
        if result is not None:
            self.finished.emit(result)

    def stop(self, *args):
        self.cancel()
        _shutdown(self._executor, self._lifetime)
