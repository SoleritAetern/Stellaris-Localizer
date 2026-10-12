"""Explicit, non-destructive takeover of a validated orphan Deploy manifest.

No localization file in the target is written or deleted by this module.
Project metadata is durable before the atomic manifest replacement.
"""

import hashlib
import json
import tempfile
from copy import copy
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QCoreApplication, QIODevice, QLockFile, QSaveFile

from .deployment import MANIFEST_NAME, file_hash, load_manifest, target_file
from .exporter import build_ru_index_from_rows, build_russian_localization
from .recent_projects import path_identity


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def foreign_manifest(project):
    target = project.validate_deploy_path()
    manifest = target_file(target, MANIFEST_NAME)
    try:
        payload = manifest.read_bytes()
    except FileNotFoundError:
        return None
    document = json.loads(payload.decode("utf-8"))
    owner = document["project_id"]
    UUID(owner)
    files, kinds = load_manifest(target, owner, with_kinds=True)
    if owner == project.project_id:
        return None
    if any(not Path(name).is_relative_to("localisation") for name in files):
        raise ValueError(QCoreApplication.translate("deployment", "Manifest корня мода содержит файлы вне localisation"))
    if manifest.read_bytes() != payload:
        raise ValueError(QCoreApplication.translate("deploy_recovery", "Manifest изменился. Повторите проверку."))
    legacy = project.resolve_path("deploy_ru")
    if legacy is not None and (legacy / MANIFEST_NAME).exists():
        raise ValueError(QCoreApplication.translate("deploy_recovery", "Найден дополнительный старый manifest Deploy. Автоматическая передача управления недоступна."))
    return {"target": target, "owner_id": owner, "manifest": payload, "files": files, "kinds": kinds}


def generated_names(project):
    # Use the real exporter, not historical inventory or a second filename rule.
    with tempfile.TemporaryDirectory(prefix="sl-takeover-preview-") as temporary:
        root = Path(temporary)
        build_russian_localization(project.snapshot_path("new_en"), build_ru_index_from_rows(project.entries),
                                   root / "localisation", documents=project.documents,
                                   rows=project.entries, source_prefix=project.current_source_prefix)
        return {path.relative_to(root).as_posix().casefold(): path.relative_to(root).as_posix()
                for path in root.rglob("*_l_russian.yml")}


def prepare_takeover(project, lookup_owner):
    """Read-only eligibility and preview. lookup_owner must look up the UUID."""
    plan = foreign_manifest(project)
    if plan is None:
        return None
    ownership = lookup_owner(plan["target"])
    if ownership["state"] == "owned":
        return dict(plan, owners=ownership["owners"])
    if ownership["state"] != "missing" or ownership["owner_id"] != plan["owner_id"]:
        raise ValueError(QCoreApplication.translate("deploy_recovery", "Не удалось подтвердить отсутствие проекта-владельца. Передача управления отменена.\n{0}").format(ownership.get("detail", "")))
    generated = generated_names(project)
    present, missing, changed, custom = {}, [], [], {}
    for name, old_hash in plan["files"].items():
        path = target_file(plan["target"], name)
        try:
            digest = file_hash(path)
        except FileNotFoundError:
            missing.append(name)
            continue
        kind = plan["kinds"].get(name, "GENERATED")
        if kind == "CUSTOM" and name.casefold() in generated:
            raise ValueError(QCoreApplication.translate("deploy_recovery", "CUSTOM совпадает с файлом, который генерирует текущий проект: {0}. Передача управления остановлена, файлы не изменены.").format(name))
        present[name] = digest
        if digest != old_hash:
            changed.append(name)
        if kind == "CUSTOM":
            custom[name] = {"path": path, "sha256": digest}
    # Preserve unrelated existing CUSTOM metadata, but never silently convert
    # an existing CUSTOM to GENERATED, even when its target copy is missing.
    managed = {name.casefold() for name in plan["files"]}
    for name in project.custom_files:
        if name.casefold() not in managed and name.casefold() in generated:
            raise ValueError(QCoreApplication.translate("deploy_recovery", "CUSTOM совпадает с файлом, который генерирует текущий проект: {0}. Передача управления остановлена, файлы не изменены.").format(name))
    plan.update(project_id=project.project_id, project_path=project.file_path.resolve(),
                project_hash=file_hash(project.file_path), generated=generated,
                present=present, missing=missing, changed=changed, custom=custom)
    return plan


def _write_bytes(path, data):
    output = QSaveFile(str(path))
    output.setDirectWriteFallback(False)
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(output.errorString())
    if output.write(data) != len(data):
        output.cancelWriting()
        raise OSError(output.errorString())
    if not output.commit():
        raise OSError(output.errorString())


def _check_target(plan):
    if target_file(plan["target"], MANIFEST_NAME).read_bytes() != plan["manifest"]:
        raise ValueError(QCoreApplication.translate("deploy_recovery", "Manifest изменился. Повторите проверку."))
    for name in plan["files"]:
        path = target_file(plan["target"], name)
        try:
            current = file_hash(path)
        except FileNotFoundError:
            current = None
        if current != plan["present"].get(name):
            raise ValueError(QCoreApplication.translate("deploy_recovery", "Файл изменился после preview: {0}. Повторите проверку.").format(name))


def accept_takeover(project, plan, lookup_owner):
    """Call only after explicit confirmation. Revalidate before any publication."""
    if "owners" in plan:
        raise ValueError(QCoreApplication.translate("deploy_recovery", "Проект-владелец найден. Передача управления недоступна."))
    if (project.project_id != plan["project_id"] or project.file_path.resolve() != plan["project_path"]
            or path_identity(project.validate_deploy_path()) != path_identity(plan["target"])):
        raise ValueError(QCoreApplication.translate("deploy_recovery", "Проект или папка перевода изменились. Повторите проверку."))
    # Includes fresh owner discovery, exporter output paths and target hashes.
    fresh = prepare_takeover(project, lookup_owner)
    if fresh != plan:
        raise ValueError(QCoreApplication.translate("deploy_recovery", "Данные изменились после preview. Повторите проверку."))
    path = project.file_path.resolve()
    lock = QLockFile(str(path) + ".lock")
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise ValueError(QCoreApplication.translate("deploy_recovery", "Проект сохраняется другим экземпляром приложения. Повторите позже."))
    project_written = False
    try:
        old_project = path.read_bytes()
        if (_digest(old_project) != plan["project_hash"] or project._saved_path != path
                or project._saved_fingerprint != plan["project_hash"]):
            raise ValueError(QCoreApplication.translate("deploy_recovery", "Проект на диске изменился. Передача управления отменена."))
        _check_target(plan)
        # Rollback bytes stay in memory. Only existing CUSTOM files get durable
        # copies, through the same verified store used by normal Deploy.
        candidate = copy(project)
        managed = {name.casefold() for name in plan["files"]}
        candidate.custom_files = {name: record for name, record in project.custom_files.items()
                                  if name.casefold() not in managed}
        records, _ = candidate.store_deploy_custom(plan["custom"])
        document = json.loads(old_project.decode("utf-8"))
        document["custom_files"] = records
        project._validate(document)
        payload = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        next_manifest = {"schema_version": 1, "project_id": project.project_id,
                         "files": plan["present"],
                         "kinds": {name: "CUSTOM" if name in plan["custom"] else "GENERATED"
                                   for name in plan["present"]}}
        manifest_payload = (json.dumps(next_manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        ownership = lookup_owner(plan["target"])
        if ownership["state"] != "missing" or ownership.get("owner_id") != plan["owner_id"]:
            raise ValueError(QCoreApplication.translate("deploy_recovery", "Данные изменились после preview. Повторите проверку."))
        _check_target(plan)
        # Narrow metadata write: no history rotation, source cleanup or schema change.
        _write_bytes(path, payload)
        project_written = True
        _check_target(plan)
        _write_bytes(target_file(plan["target"], MANIFEST_NAME), manifest_payload)
        # QSaveFile commit is the last fallible publication. On its failure the
        # old manifest is intact, so reverting the project cannot orphan an owner.
    except Exception as error:
        if project_written:
            try:
                _write_bytes(path, old_project)
            except Exception as rollback_error:
                # Target owner is still old; keep memory in sync with saved metadata.
                project.custom_files = records
                project._saved_fingerprint = _digest(payload)
                raise OSError(QCoreApplication.translate("deploy_recovery", "Не удалось откатить metadata проекта. Старый manifest и файлы перевода не изменены; подготовленные CUSTOM сохранены в проекте.")) from rollback_error
        raise
    finally:
        lock.unlock()
    project.custom_files = records
    project._saved_fingerprint = _digest(payload)
    return {"preserved": len(plan["custom"]), "missing": len(plan["missing"])}
