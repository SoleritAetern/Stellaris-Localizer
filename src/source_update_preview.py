"""Read-only source analysis. Temporary bytes never become a revision until Apply."""

from PySide6.QtCore import QCoreApplication, QT_TRANSLATE_NOOP

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile

from src.deployment import file_hash
from src.diff import find_renamed_keys
from src.parser import build_key_index, parse_localization_folder
from src.paths import legacy_localisation_root
from src.source_revisions import reconcile_entries
from src.archive_matching import find_archive_returns


CATEGORIES = {
    "changed": QT_TRANSLATE_NOOP("source_update_preview", "Изменено"), "new": QT_TRANSLATE_NOOP("source_update_preview", "Новое"), "removed": QT_TRANSLATE_NOOP("source_update_preview", "Удалено"),
    "moved": QT_TRANSLATE_NOOP("source_update_preview", "Переименовано/перемещено"), "disabled": QT_TRANSLATE_NOOP("source_update_preview", "Отключено"),
    "enabled": QT_TRANSLATE_NOOP("source_update_preview", "Включено обратно"), "files_added": QT_TRANSLATE_NOOP("source_update_preview", "Добавлены файлы"),
    "files_removed": QT_TRANSLATE_NOOP("source_update_preview", "Удалены файлы"), "returned": QT_TRANSLATE_NOOP("source_update_preview", "Возвращённые записи"), "confirm": QT_TRANSLATE_NOOP("source_update_preview", "Требуют подтверждения"),
}


def source_identity(rows):
    # Working RU/baseline may be committed on Apply; source identity must not change.
    return [(row.get("entry_id"), row["key"], row["section"], row.get("kind", "ENTRY"),
             row.get("new_text"), row.get("new_file"), row.get("new_line"),
             row.get("old_text"), row.get("old_file"), row.get("old_line")) for row in rows]


class SourceUpdateAnalysis:
    def __init__(self, project, source, staged):
        self.project_id = project.project_id
        self.project_path = project.file_path
        self.revision_id = project.current_revision_id
        self.previous = deepcopy([*project.entries, *project.removed_entries])
        self.identity = source_identity(self.previous)
        self.source, self.staged = source, staged
        _, prefix = legacy_localisation_root(source)
        self.prefix = "" if prefix == "." else prefix
        self.diagnostics, self.fixes = [], []
        files, self.entries = parse_localization_folder(staged, "english", diagnostics=self.diagnostics,
                                                        source="new_en", auto_fixes=self.fixes)
        self.hashes = {path.relative_to(staged): file_hash(path) for path in files}
        old = [{"key": row["key"], "text": row["new_text"], "line": row["new_line"],
                "file": str(row["new_file"]), "relative_path": row["new_file"]}
               for row in self.previous if row["section"] == "CURRENT"]
        new = [dict(entry, file=str(entry["relative_path"])) for entry in self.entries]
        self.reviews = {}
        self.automatic_matches = find_renamed_keys(build_key_index(old), build_key_index(new),
                                                  review_candidates=self.reviews)
        self.automatic_archive, archive_reviews = find_archive_returns(
            self.previous, self.entries, self.automatic_matches, prefix=self.prefix)
        archive_keys = set(self.automatic_archive) | set(archive_reviews)
        self.automatic_matches = {key: match for key, match in self.automatic_matches.items() if key not in archive_keys}
        self.reviews = {key: match for key, match in self.reviews.items() if key not in archive_keys}
        self.reviews.update({key: dict(match, archive_return=True) for key, match in archive_reviews.items()})
        self.review_keys = set(self.reviews) | {match["old_key"] for match in self.reviews.values()}
        while True:
            expanded = set(self.review_keys)
            for key, match in {**self.automatic_matches, **self.automatic_archive}.items():
                if key in expanded or match["old_key"] in expanded:
                    expanded.update((key, match["old_key"]))
            if expanded == self.review_keys:
                break
            self.review_keys = expanded
        self.review_previous = [row for row in self.previous if row["key"] in self.review_keys]
        self.review_entries = [entry for entry in self.entries if entry["key"] in self.review_keys]
        provisional = reconcile_entries(self.previous, self.entries, self.revision_id + 1,
                                         rename_matches=self.automatic_matches, archive_matches=self.automatic_archive)
        self.base_rows = [row for row in provisional if row["key"] not in self.review_keys]
        self.previous_by_id = {row["entry_id"]: row for row in self.previous}
        self.old_by_key = build_key_index(old)
        self.new_by_key = build_key_index(new)
        old_files = {(Path(project.current_source_prefix) / doc.file).as_posix() for doc in project.documents}
        new_files = {(Path(self.prefix) / path).as_posix() for path in self.hashes}
        self.added_files, self.removed_files = sorted(new_files - old_files), sorted(old_files - new_files)
        self.old_prefix = project.current_source_prefix

    def validate(self, project):
        if (project.project_id != self.project_id or project.file_path != self.project_path
                or project.current_revision_id != self.revision_id
                or source_identity([*project.entries, *project.removed_entries]) != self.identity):
            raise ValueError(QCoreApplication.translate("source_update_preview", "Источник проекта изменился после анализа. Повторите обновление источников."))
        self.validate_bytes(self.staged)

    def validate_bytes(self, root):
        actual = {path.relative_to(root): file_hash(path) for path in root.rglob("*_l_english.yml")}
        if actual != self.hashes:
            raise ValueError(QCoreApplication.translate("source_update_preview", "Подготовленные файлы изменились после анализа. Повторите обновление источников."))

    def resolved_matches(self, decisions, *, complete=True):
        if not set(decisions) <= self.reviews.keys():
            raise ValueError(QCoreApplication.translate("source_update_preview", "Неизвестное решение о переименовании."))
        if complete and (set(decisions) != set(self.reviews) or any(type(v) is not bool for v in decisions.values())):
            raise ValueError(QCoreApplication.translate("source_update_preview", "Подтвердите каждую неоднозначную пару или выберите «Считать новой»."))
        matches = deepcopy(self.automatic_matches)
        used = {match["old_key"] for match in matches.values()}
        for key, same in decisions.items():
            if not same:
                continue
            match = self.reviews[key]
            if match.get("archive_return"):
                continue
            if match["old_key"] in used:
                raise ValueError(QCoreApplication.translate("source_update_preview", "Запись {0} уже сопоставлена с другим ключом. Выберите «Считать новой».").format(match['old_key']))
            used.add(match["old_key"])
            matches[key] = {"old_key": match["old_key"], "score": match["score"]}
        self.resolved_archive_matches(decisions, complete=False)
        return matches

    def resolved_archive_matches(self, decisions, *, complete=True):
        if complete and set(decisions) != set(self.reviews):
            raise ValueError(QCoreApplication.translate("source_update_preview", "Не все возвращающиеся записи подтверждены."))
        matches = deepcopy(self.automatic_archive)
        used = {match["entry_id"] for match in matches.values()}
        for key, same in decisions.items():
            match = self.reviews[key]
            if not same or not match.get("archive_return"):
                continue
            if match["entry_id"] in used:
                raise ValueError(QCoreApplication.translate("source_update_preview", "Эта удалённая запись уже связана с другим новым ключом."))
            used.add(match["entry_id"])
            matches[key] = match
        return matches

    def preview_items(self, decisions):
        matches = self.resolved_matches(decisions, complete=False)
        archive_matches = self.resolved_archive_matches(decisions, complete=False)
        # Only reconsider the uncertain keys when a decision changes.
        rows = self.base_rows + reconcile_entries(self.review_previous, self.review_entries, self.revision_id + 1,
                    rename_matches={key: value for key, value in matches.items() if key in self.review_keys},
                    archive_matches={key: value for key, value in archive_matches.items() if key in self.review_keys})
        items = []
        for row in rows:
            old = self.previous_by_id.get(row["entry_id"])
            if row["section"] == "CURRENT" and old and old["section"] == "REMOVED":
                item = self.item("returned", old, row)
                item.update(old_text=old.get("accepted_source_text"), archive_return=True)
                items.append(item)
                continue
            old = old if old and old["section"] == "CURRENT" else None
            if row["section"] != "CURRENT":
                if old:
                    items.append(self.item("removed", old, None))
                continue
            if old is None:
                items.append(self.item("new", None, row))
                continue
            if old["new_text"] != row["new_text"]:
                items.append(self.item("changed", old, row))
            if (old["key"] != row["key"] or
                    Path(self.old_prefix) / old["new_file"] != Path(self.prefix) / row["new_file"]):
                items.append(self.item("moved", old, row))
            if old.get("kind", "ENTRY") != row.get("kind", "ENTRY"):
                category = "disabled" if row.get("kind") == "DISABLED_ENTRY" else "enabled"
                items.append(self.item(category, old, row))
        for category, paths in (("files_added", self.added_files), ("files_removed", self.removed_files)):
            items.extend({"category": category, "key": "", "file": path, "old_text": None, "new_text": None}
                         for path in paths)
        for key, match in self.reviews.items():
            old = match["old"] if match.get("archive_return") else self.old_by_key[match["old_key"]][0]
            new = self.new_by_key[key][0]
            old_path = (Path("" if match.get("archive_return") else self.old_prefix) / old["relative_path"]).as_posix()
            new_path = (Path(self.prefix) / new["relative_path"]).as_posix()
            archived = self.previous_by_id.get(match.get("entry_id"))
            items.append({"category": "confirm", "key": f"{match['old_key']} → {key}",
                          "file": f"{old_path} → {new_path}", "old_text": old["text"], "new_text": new["text"],
                          "review_key": key, "reason": match["reason"], "score": match["score"],
                          **({"archive_return": True, "return_kind": match["return_kind"],
                              "old_text": archived.get("accepted_source_text")} if archived else {})})
        return items

    def item(self, category, old, new):
        old_path = (Path(old.get("last_source_prefix", self.old_prefix)) /
                    (old["new_file"] or old["old_file"])).as_posix() if old and (old["new_file"] or old["old_file"]) else ""
        new_path = (Path(self.prefix) / new["new_file"]).as_posix() if new else ""
        return {"category": category,
                "key": f"{old['key']} → {new['key']}" if category == "moved" else (new or old)["key"],
                "file": f"{old_path} → {new_path}" if category == "moved" else new_path or old_path,
                "old_text": (old["new_text"] if old["section"] == "CURRENT" else old["old_text"]) if old else None,
                "new_text": new["new_text"] if new else None}


@contextmanager
def analyze_source_update(project, new_en=None):
    source, files = project.source_update_files(new_en)
    # Outside the project: cancelling cannot leave a revision or change ui-state.
    with tempfile.TemporaryDirectory(prefix="sl-source-preview-") as temporary:
        staged = Path(temporary).resolve() / "new_en"
        staged.mkdir()
        for file in files:
            target = staged / file.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, target)
        yield SourceUpdateAnalysis(project, source, staged)
