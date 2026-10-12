"""Publish only project-owned localisation files, with rollback on write errors."""

from PySide6.QtCore import QCoreApplication

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from src.exporter import build_russian_localization, build_ru_index_from_rows

MANIFEST_NAME = ".stellaris-localizer-manifest.json"


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def target_file(target, relative):
    if not isinstance(relative, str):
        raise ValueError(QCoreApplication.translate("deployment", "Некорректный путь файла Deploy"))
    path = Path(relative)
    if (not relative or path.is_absolute() or path.drive
            or ".." in path.parts or any(":" in part for part in path.parts)
            or path.as_posix() != relative):
        raise ValueError(QCoreApplication.translate("deployment", "Некорректный путь файла Deploy"))
    result = target / path
    if not result.resolve().is_relative_to(target.resolve()):
        raise ValueError(QCoreApplication.translate("deployment", "Файл Deploy выходит за пределы целевой папки"))
    for component in (result, *result.parents):
        if component == target:
            break
        if component.is_symlink() or component.is_junction():
            raise ValueError(QCoreApplication.translate("deployment", "Deploy не изменяет ссылки: {0}").format(component))
        if component != result and component.exists() and not component.is_dir():
            raise ValueError(QCoreApplication.translate("deployment", "Путь Deploy проходит через файл: {0}").format(component))
    return result


def load_manifest(target, project_id, *, with_kinds=False):
    manifest = target_file(target, MANIFEST_NAME)
    if not manifest.exists():
        return ({}, {}) if with_kinds else {}
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(QCoreApplication.translate("deployment", "Не удалось прочитать manifest Deploy: {0}").format(error)) from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError(QCoreApplication.translate("deployment", "Неизвестный формат manifest Deploy"))
    if document.get("project_id") != project_id:
        raise ValueError(QCoreApplication.translate("deployment", "Целевая папка Deploy управляется другим проектом"))
    files = document.get("files")
    if not isinstance(files, dict):
        raise ValueError(QCoreApplication.translate("deployment", "Некорректный список файлов manifest Deploy"))
    kinds = document.get("kinds", {})
    if (not isinstance(kinds, dict) or not kinds.keys() <= files.keys()
            or any(kind not in ("GENERATED", "CUSTOM") for kind in kinds.values())):
        raise ValueError(QCoreApplication.translate("deployment", "Некорректные типы файлов manifest Deploy"))
    identities = set()
    for relative, digest in files.items():
        path = target_file(target, relative)
        suffix = ".yml" if kinds.get(relative) == "CUSTOM" else "_l_russian.yml"
        if (not relative.endswith(suffix) or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest) or str(path).casefold() in identities):
            raise ValueError(QCoreApplication.translate("deployment", "Некорректная запись manifest Deploy"))
        identities.add(str(path).casefold())
    return (files, kinds) if with_kinds else files


def deploy_localization(source, rows, documents, target, project_id, *, source_prefix="", legacy_target=None,
                        initial_files=None, generated_history=(), custom_files=None, initial_sources=None,
                        review=None, preserve_custom=None):
    if not callable(review):
        raise ValueError(QCoreApplication.translate("deployment", "Deploy требует подтверждения в окне предварительного просмотра."))
    target = Path(target).resolve()
    previous, previous_kinds = load_manifest(target, project_id, with_kinds=True)
    manifest_path = target_file(target, MANIFEST_NAME)
    manifest_digest = file_hash(manifest_path) if manifest_path.exists() else None
    if any(not Path(name).is_relative_to("localisation") for name in previous):
        raise ValueError(QCoreApplication.translate("deployment", "Manifest корня мода содержит файлы вне localisation"))
    legacy_manifest = None
    legacy_digest = None
    if legacy_target is not None:
        legacy_target = Path(legacy_target).resolve()
        if legacy_target.is_relative_to(target / "localisation") and (legacy_target / MANIFEST_NAME).exists():
            prefix = legacy_target.relative_to(target)
            legacy_manifest = (prefix / MANIFEST_NAME).as_posix()
            target_file(target, legacy_manifest)  # Reject links/junctions before migration.
            legacy_digest = file_hash(target / legacy_manifest)
            legacy_files, legacy_kinds = load_manifest(legacy_target, project_id, with_kinds=True)
            for relative, digest in legacy_files.items():
                name = (prefix / relative).as_posix()
                existing = next((key for key in previous if key.casefold() == name.casefold()), None)
                if existing is not None:
                    if (previous[existing] != digest or previous_kinds.get(existing, "GENERATED")
                            != legacy_kinds.get(relative, "GENERATED")):
                        raise ValueError(QCoreApplication.translate("deployment", "Старый и новый manifest Deploy противоречат друг другу"))
                    name = existing
                previous[name] = digest
                previous_kinds[name] = legacy_kinds.get(relative, "GENERATED")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".sl-deploy-", dir=target.parent)).resolve()
    staged, backup = temporary / "result", temporary / "backup"
    keep_backup = False
    try:
        build_russian_localization(source, build_ru_index_from_rows(rows), staged / "localisation",
                                   documents=documents, rows=rows, source_prefix=source_prefix)
        current = {path.relative_to(staged).as_posix(): file_hash(path)
                   for path in sorted(staged.rglob("*_l_russian.yml"))}
        generated = set(current)
        generated_ids = {name.casefold(): name for name in generated}
        kinds = {name: "GENERATED" for name in current}

        def stage_custom(files):
            for name, source_file in files.items():
                if name.casefold() in generated_ids:
                    raise ValueError(QCoreApplication.translate("deployment", "CUSTOM конфликтует с current GENERATED: {0}").format(name))
                destination = target_file(staged, name)
                if not Path(name).is_relative_to("localisation") or not name.endswith(".yml"):
                    raise ValueError(QCoreApplication.translate("deployment", "CUSTOM должен находиться внутри localisation: {0}").format(name))
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_file, destination)
                current[name] = file_hash(destination)
                kinds[name] = "CUSTOM"

        stage_custom(custom_files or {})
        # A CUSTOM manifest entry must never become stale merely because the
        # project lost its copy/metadata (e.g. an older backup was opened).
        current_ids = {name.casefold(): name for name in current}
        for name, kind in previous_kinds.items():
            if kind == "CUSTOM" and kinds.get(current_ids.get(name.casefold())) != "CUSTOM":
                raise ValueError(QCoreApplication.translate("deployment", "В проекте отсутствует CUSTOM из manifest: {0}. Восстановите проектную копию.").format(name))
        # Canonicalize equivalent Windows paths before calculating stale files.
        previous = {current_ids.get(name.casefold(), name): digest for name, digest in previous.items()}
        owned_ids = {name.casefold() for name in previous}
        history = {name.casefold() for name in generated_history} | set(generated_ids)
        unmanaged_stale = set()
        for name, digest in (initial_files or {}).items():
            canonical = current_ids.get(name.casefold(), name)
            if name.casefold() in owned_ids or kinds.get(canonical) == "CUSTOM":
                continue
            path = target_file(target, name)
            # Exact historical path AND an unchanged imported file are both
            # required. Similar basenames, keys, or modified content prove nothing.
            if name.casefold() in history and path.is_file() and file_hash(path) == digest:
                previous[canonical] = digest
                owned_ids.add(name.casefold())
                if canonical not in current:
                    unmanaged_stale.add(canonical)

        # Unmanaged CUSTOM copies can be adopted only when byte-identical.
        for name in current:
            if kinds[name] == "CUSTOM" and name not in previous:
                path = target_file(target, name)
                if path.is_file() and file_hash(path) == current[name]:
                    previous[name] = current[name]

        candidates = {}
        preserved = set(name for name in current if kinds[name] == "CUSTOM")
        # Include initial RU extras even when deploying into a new empty mod.
        # They remain snapshot-only until explicitly marked CUSTOM by the user.
        for name, path in (initial_sources or {}).items():
            if (name.casefold() not in current_ids and name.casefold() not in history
                    and name.casefold() not in owned_ids):
                candidates[name] = {"path": path, "sha256": file_hash(path), "origin": "initial RU"}
                preserved.add(name)
        localisation = target / "localisation"
        stale_candidates = set()
        if localisation.exists():
            target_file(target, "localisation")
            previous_names = {name.casefold(): name for name in previous}
            for path in sorted(localisation.rglob("*.yml")):
                name = path.relative_to(target).as_posix()
                target_file(target, name)
                if name.casefold() in current_ids:
                    continue
                owned = previous_names.get(name.casefold())
                if owned is not None:
                    # Reuse ordinary keep/delete/leave decisions for stale
                    # GENERATED, including those inherited by a takeover.
                    # This must also hold after cancelling preview/restarting.
                    if file_hash(path) != previous[owned]:
                        raise ValueError(QCoreApplication.translate("deployment", "Файл изменён вне программы; Deploy отменён: {0}").format(owned))
                    stale_candidates.add(owned)
                    previous.pop(owned)
                # Prefer the actual target over an older initial snapshot.
                name = owned or name
                for old_name in list(candidates):
                    if old_name.casefold() == name.casefold():
                        candidates.pop(old_name)
                        preserved.discard(old_name)
                candidates[name] = {"path": path, "sha256": file_hash(path), "origin": "Deploy target"}
                if owned is None:
                    preserved.add(name)
        stale = set(previous) - set(current)
        unknown_preview = {name: dict(item) for name, item in candidates.items()
                           if name not in stale and name not in current}
        for item in unknown_preview.values():
            if item["origin"] == "Deploy target":
                stat = Path(item["path"]).stat()
                item["stat"] = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        plan = {"target": target, "updated": sorted(current), "stale": sorted(stale),
                "unmanaged_stale": sorted(unmanaged_stale), "preserved": sorted(preserved),
                "custom": sorted(name for name in current if kinds[name] == "CUSTOM"),
                "candidates": candidates, "unknown_delete_paths": [],
                "stale_candidates": sorted(stale_candidates)}
        decision = review(plan)
        if decision is None:
            return None
        unknown_delete_paths = set()
        if isinstance(decision, dict):
            selected = set(decision.get("custom_paths", []))
            unknown_delete_paths = set(decision.get("unknown_delete_paths", []))
            unchanged = set(decision.get("unchanged_paths", []))
            unknown = set(unknown_preview)
            if (not unknown_delete_paths <= unknown or not unchanged <= unknown
                    or selected & unknown_delete_paths or selected & unchanged or unknown_delete_paths & unchanged
                    or (selected & unknown) | unknown_delete_paths | unchanged != unknown):
                raise ValueError(QCoreApplication.translate("deployment", "Для каждого неопознанного файла необходимо выбрать одно действие."))
        else:
            # Existing callers can still return just the files to preserve;
            # that contract never authorizes deletion of unknown files.
            selected = set(decision)
        plan["unknown_delete_paths"] = sorted(unknown_delete_paths)
        if not selected <= candidates.keys():
            raise ValueError(QCoreApplication.translate("deployment", "Некорректный выбор CUSTOM в preview Deploy"))

        def validate_unknown_deletion(relative):
            item = unknown_preview[relative]
            if (item["origin"] != "Deploy target" or not Path(relative).is_relative_to("localisation")
                    or target.resolve() != target or target.is_symlink() or target.is_junction()):
                raise ValueError(QCoreApplication.translate("deployment", "Нельзя удалить файл по выбранному пути: {0}. Повторите проверку.").format(relative))
            path = target_file(target, relative)
            if path != Path(item["path"]) or not path.is_file():
                raise ValueError(QCoreApplication.translate("deployment", "Файл исчез или изменился путь: {0}. Повторите проверку.").format(relative))
            stat = path.stat()
            if ((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != item["stat"]
                    or file_hash(path) != item["sha256"]):
                raise ValueError(QCoreApplication.translate("deployment", "Файл изменился после открытия проверки: {0}. Повторите проверку.").format(relative))
            return path

        for relative in unknown_delete_paths:
            validate_unknown_deletion(relative)
        if selected:
            if preserve_custom is None:
                raise ValueError(QCoreApplication.translate("deployment", "Не задано хранилище CUSTOM проекта"))
            stage_custom({name: candidates[name]["path"] for name in selected})
            for name in selected:
                # Explicit adoption still requires the exact previewed bytes.
                if current[name] != candidates[name]["sha256"]:
                    raise ValueError(QCoreApplication.translate("deployment", "Файл изменился после preview: {0}. Повторите Deploy.").format(name))
                previous[name] = candidates[name]["sha256"]
        # Dialogs can run a nested event loop: do not trust a manifest changed
        # since the preview. File hashes are checked below immediately before writes.
        if (file_hash(manifest_path) if manifest_path.exists() else None) != manifest_digest:
            raise ValueError(QCoreApplication.translate("deployment", "Manifest изменился во время preview; повторите Deploy"))
        if legacy_manifest and (not (target / legacy_manifest).is_file()
                                or file_hash(target / legacy_manifest) != legacy_digest):
            raise ValueError(QCoreApplication.translate("deployment", "Старый manifest изменился во время preview; повторите Deploy"))
        # Validate every conflict before touching target, including stale files.
        for relative in previous.keys() | current.keys():
            path = target_file(target, relative)
            if path.exists():
                if not path.is_file():
                    raise ValueError(QCoreApplication.translate("deployment", "Вместо файла Deploy существует папка: {0}").format(relative))
                if relative not in previous:
                    raise ValueError(QCoreApplication.translate("deployment", "Deploy не перезаписывает чужой файл: {0}").format(relative))
                if file_hash(path) != previous[relative]:
                    raise ValueError(QCoreApplication.translate("deployment", "Файл изменён вне программы; Deploy отменён: {0}").format(relative))
        if selected:
            # Save only the already verified staged bytes. No target mutation
            # can happen until the project's CUSTOM copies are durable.
            preserve_custom({name: {"path": staged / name, "sha256": current[name]} for name in selected})
        next_manifest = temporary / "next-manifest.json"
        next_manifest.write_text(json.dumps({"schema_version": 1, "project_id": project_id,
                                            "files": current, "kinds": kinds},
                                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written, moved, created_dirs = [], [], []

        def ensure_directory(path):
            missing = []
            while not path.exists():
                missing.append(path)
                path = path.parent
            for path in reversed(missing):
                path.mkdir()
                created_dirs.append(path)

        stale = sorted(previous.keys() - current.keys())
        deleted = 0
        try:
            ensure_directory(target)
            # Publish manifest last; it always describes a completed deployment.
            for relative in [*sorted(current), *stale, *sorted(unknown_delete_paths),
                             *([legacy_manifest] if legacy_manifest else []), MANIFEST_NAME]:
                destination = (validate_unknown_deletion(relative) if relative in unknown_delete_paths
                               else target_file(target, relative))
                ensure_directory(destination.parent)
                if relative in unknown_delete_paths and not destination.exists():
                    raise ValueError(QCoreApplication.translate("deployment", "Файл исчез перед удалением: {0}. Повторите проверку.").format(relative))
                if destination.exists():
                    expected = (manifest_digest if relative == MANIFEST_NAME else legacy_digest
                                if relative == legacy_manifest else unknown_preview[relative]["sha256"]
                                if relative in unknown_delete_paths else previous.get(relative))
                    if not destination.is_file() or expected is None or file_hash(destination) != expected:
                        raise ValueError(QCoreApplication.translate("deployment", "Файл изменился перед записью; Deploy отменён: {0}").format(relative))
                    saved = backup / relative
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(destination, saved)
                    moved.append((saved, destination))
                    if relative in stale or relative in unknown_delete_paths:
                        deleted += 1
                if relative in current or relative == MANIFEST_NAME:
                    source_file = next_manifest if relative == MANIFEST_NAME else staged / relative
                    os.replace(source_file, destination)
                    written.append(destination)
        except Exception:
            try:
                for path in reversed(written):
                    path.unlink()
                for saved, destination in reversed(moved):
                    os.replace(saved, destination)
                for path in reversed(created_dirs):
                    path.rmdir()
            except Exception as error:
                keep_backup = True
                raise OSError(QCoreApplication.translate("deployment", "Не удалось завершить откат Deploy. Резервные файлы сохранены: {0}").format(temporary)) from error
            raise
        return {"files": len(current), "deleted": deleted, "target": target,
                "custom": sum(kind == "CUSTOM" for kind in kinds.values())}
    finally:
        if not keep_backup:
            if temporary.parent != target.parent or not temporary.name.startswith(".sl-deploy-"):
                raise OSError(QCoreApplication.translate("deployment", "Некорректная временная папка Deploy"))
            shutil.rmtree(temporary)
