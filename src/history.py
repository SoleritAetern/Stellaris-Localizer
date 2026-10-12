"""Retention metadata and archive operations, independent of translation baselines."""

from PySide6.QtCore import QCoreApplication, QT_TRANSLATE_NOOP

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import shutil

from src.deployment import target_file
from src.translation_baseline import BASELINE_FIELDS, refresh_baseline


HISTORY_FIELDS = ("removed_revision_id", "last_source_prefix",
                  "restored_from_archive", "restored_needs_confirmation")


def separate_removed(project):
    archived = list(project.removed_entries)
    archived.extend(row for row in project.entries if row["section"] == "REMOVED")
    project.entries = [row for row in project.entries if row["section"] == "CURRENT"]
    for row in archived:
        row.setdefault("removed_revision_id", project.current_revision_id)
        row.setdefault("last_source_prefix", project.current_source_prefix)
    project.removed_entries = archived


def remember_revision_files(project, revision):
    if "source_files" not in revision and revision.get("snapshot_available", True):
        root = target_file(project.root.resolve(), Path(revision["snapshot"]).as_posix())
        revision["source_files"] = [(Path(revision.get("localisation_prefix", "")) / path.relative_to(root)).as_posix()
                                    for path in sorted(root.rglob("*_l_english.yml"))]


def rotate_history(project, *, prune_removed=False):
    """Keep the existing current + two previous snapshot window, without pins.

    Older revision records remain as references for accepted baselines and the
    generated-file inventory used by deploy; they contain no localization text.
    Physical cleanup still waits for a successful atomic project save.
    """
    current = project.current_revision_id
    previous = [r["revision_id"] for r in reversed(project.source_revisions)
                if r["revision_id"] != current][:2]
    keep = {current, *previous}
    for revision in project.source_revisions:
        revision.pop("pinned", None)
        if revision["revision_id"] not in keep:
            remember_revision_files(project, revision)
            revision["snapshot_available"] = False
    for row in (*project.entries, *project.removed_entries):
        row.pop("archive_pinned", None)
    if prune_removed:
        project.removed_entries = [row for row in project.removed_entries
                                  if row["removed_revision_id"] >= current - 2]


def cleanup_snapshots(project):
    """Called only AFTER atomic project save; failures leave harmless extra copies."""
    errors = []
    root = project.root.resolve()
    protected = [project.snapshot_path(name) for name in ("new_en", "old_en", "initial_ru")]
    protected.extend(root / revision["snapshot"] for revision in project.source_revisions
                     if revision.get("snapshot_available", True))
    for revision in project.source_revisions:
        if revision.get("snapshot_available", True):
            continue
        try:
            path = target_file(root, Path(revision["snapshot"]).as_posix())
            try:
                path.stat()
            except FileNotFoundError:
                continue
            resolved = path.resolve()
            if (revision["revision_id"] == project.current_revision_id or not resolved.is_relative_to(root / "sources")
                    or resolved == root / "sources" or any(p is not None and
                        (resolved.is_relative_to(p.resolve()) or p.resolve().is_relative_to(resolved)) for p in protected)):
                raise ValueError(QT_TRANSLATE_NOOP("history", "Небезопасный путь очистки snapshot"))
            for child in path.rglob("*"):
                if child.is_symlink() or child.is_junction():
                    raise ValueError(QT_TRANSLATE_NOOP("history", "В snapshot обнаружена ссылка; очистка остановлена"))
            shutil.rmtree(resolved)
        except (OSError, ValueError) as error:
            errors.append(f"Revision {revision['revision_id']}: {error}")
    project.history_cleanup_errors = errors


def linkable(row):
    return (row["section"] == "CURRENT" and row.get("kind", "ENTRY") == "ENTRY"
            and row["source_status"] == "NEW" and row.get("translation_origin") == "en_fallback"
            and row.get("translation_state") == "untranslated" and row.get("accepted_source_text") is None
            and not row.get("restored_from_archive") and not row.get("ru_comment"))


def restore_translation(row, archived, revision_id, *, force_review=False):
    # Restore text-bound ignore decisions only with their original entry identity.
    for name in ("entry_id", "ru_text", "translation_state", "translation_origin", "needs_review",
                 "ru_file", "ru_line", "ru_comment", "ru_comment_trailing", "ignored_diagnostics", *BASELINE_FIELDS):
        if name in archived:
            row[name] = deepcopy(archived[name])
    row["restored_from_archive"] = True
    row["restored_needs_confirmation"] = bool(force_review or archived.get("restored_needs_confirmation"))
    row["old_key"] = archived["key"]
    refresh_baseline(row, revision_id)


def link_removed(project, archived_id, current_id):
    archived = next(row for row in project.removed_entries if row["entry_id"] == archived_id)
    current = next(row for row in project.entries if row["entry_id"] == current_id)
    if not linkable(current):
        raise ValueError(QCoreApplication.translate("history", "Выбранная запись уже переведена или связана с историей."))
    restore_translation(current, archived, project.current_revision_id)
    for document in project.documents:
        document.elements = [replace(item, entry_id=archived_id) if item.entry_id == current_id else item
                             for item in document.elements]
    project.removed_entries = [row for row in project.removed_entries if row["entry_id"] != archived_id]
