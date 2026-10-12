"""Upgrade older working documents from unchanged snapshots, preserving edits."""

from uuid import uuid4

from src.diff import compare_indexes
from src.localization_document import read_documents, classify_disabled_shadows, inline_comment
from src.localization_syntax import parse_entry_line, disabled_entry_parts
from pathlib import Path
from src.parser import build_key_index, parse_localization_folder
from src.translation_baseline import BASELINE_FIELDS, initialize_baseline, refresh_baseline
from src.view_model import build_localization_rows

NORMALIZATION_VERSION = 3
DISABLED_SHADOW_VERSION = 1


def upgrade_disabled_shadows(project):
    """Repair pre-canonical projects in memory without accepting a new baseline."""
    project.documents = classify_disabled_shadows(project.documents)
    shadows, active = {}, {}
    for document in project.documents:
        for element in document.elements:
            location = (str(Path(document.file)), element.line)
            if element.kind == "DISABLED_SHADOW":
                shadows[location] = disabled_entry_parts(element.raw.rstrip("\r\n"))[1]["key"]
            elif element.kind == "ENTRY":
                match = parse_entry_line(element.raw.rstrip("\r\n"))[0]
                if match is not None:
                    active[location] = match
    if not shadows:
        return
    shadow_keys = set(shadows.values())
    rows, displaced = [], {}
    for row in project.entries:
        location = (str(row.get("new_file")), row.get("new_line"))
        if row["section"] == "CURRENT" and location in shadows:
            displaced.setdefault(row["key"], row)
        else:
            rows.append(row)
    # Older key-only representations may have attached their only working row
    # to the disabled alternative. Reuse that identity for the actual entry.
    present = {row["key"] for row in rows if row["section"] == "CURRENT"}
    for (file, line), match in active.items():
        if match["key"] not in present and match["key"] in displaced:
            row = displaced[match["key"]]
            row.update(new_file=Path(file), new_line=line)
            rows.append(row)
            present.add(row["key"])
    ru_index = {}
    if any(row["key"] in shadow_keys and row.get("translation_origin") == "initial_ru" for row in rows):
        root = project.snapshot_path("initial_ru")
        if root is not None:
            ru_index = build_key_index(parse_localization_folder(root, "russian")[1])
    for row in rows:
        if row["section"] != "CURRENT" or row["key"] not in shadow_keys:
            continue
        match = active.get((str(row.get("new_file")), row.get("new_line")))
        if match is None:
            continue
        row.update(kind="ENTRY", new_text=match["text"], new_comment=inline_comment(match["trailing"]),
                   new_comment_trailing=match["trailing"])
        if row.get("translation_origin") == "en_fallback":
            row["ru_text"] = row["new_text"]
        elif row.get("translation_origin") == "initial_ru" and ru_index.get(row["key"]):
            imported = ru_index[row["key"]][0]
            row.update(ru_text=imported["text"], ru_file=imported["relative_path"], ru_line=imported["line"])
        # Manual RU, comments, identity and accepted source remain untouched.
        refresh_baseline(row, project.current_revision_id)
    project.entries = rows
    by_location = {(str(row.get("new_file")), row.get("new_line")): row["entry_id"]
                   for row in rows if row["section"] == "CURRENT"}
    from dataclasses import replace
    for document in project.documents:
        document.elements = [replace(element, entry_id=by_location.get((str(Path(document.file)), element.line)))
                             if element.kind in ("ENTRY", "DISABLED_ENTRY") else element
                             for element in document.elements]


def upgrade_normalization(project):
    indexes = {}
    new_entries = []
    for name in ("new_en", "old_en", "initial_ru"):
        folder = project.snapshot_path(name)
        if folder is None:
            indexes[name] = {}
            continue
        _, entries = parse_localization_folder(folder, "russian" if name == "initial_ru" else "english")
        indexes[name] = build_key_index(entries)
        if name == "new_en":
            new_entries = entries
    results = compare_indexes(indexes["old_en"], indexes["new_en"], indexes["initial_ru"],
                              has_old_source=project.snapshot_path("old_en") is not None)
    templates = build_localization_rows(new_entries, indexes["old_en"], indexes["new_en"], indexes["initial_ru"], results)
    results_by_key = {result["key"]: result for result in results}
    existing = {(row["new_file"], row["new_line"]): row for row in project.entries if row["section"] == "CURRENT"}
    used = set()
    rows = []
    for template in templates:
        if template["section"] != "CURRENT":
            continue
        row = existing.get((template["new_file"], template["new_line"]))
        if row is None:
            result = results_by_key[template["key"]]
            archived = next((old for old in project.entries if old["section"] == "REMOVED"
                             and old["entry_id"] not in used
                             and old["key"] in (template["key"], result["old_key"])), None)
            row = template
            row.update(old_key=result["old_key"], rename_score=result["rename_score"])
            if archived is not None:
                for field in ("entry_id", "ru_text", "translation_state", "translation_origin", "needs_review",
                              *BASELINE_FIELDS, "ru_comment", "ru_comment_trailing"):
                    if field in archived:
                        row[field] = archived[field]
                refresh_baseline(row, project.current_revision_id)
            else:
                imported = result["ru"] is not None
                row.update(entry_id=str(uuid4()), translation_state="translated" if imported else "untranslated",
                           translation_origin="initial_ru" if imported else "en_fallback",
                           needs_review=result["translation_status"] == "NEEDS_REVIEW")
                initialize_baseline(row, project.current_revision_id)
        row["kind"] = template.get("kind", "ENTRY")
        refresh_baseline(row, project.current_revision_id)
        used.add(row["entry_id"])
        rows.append(row)
    # Keep any record not matched by the upgrade: no destructive rematching or
    # new source revision occurs while opening an old project.
    rows.extend(row for row in project.entries if row["entry_id"] not in used)
    project.entries = rows
    project.documents = read_documents(project.snapshot_path("new_en"), rows)
