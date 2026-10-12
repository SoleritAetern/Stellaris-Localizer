"""Immutable source imports and entry identity reconciliation."""

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from src.diff import compare_indexes
from src.parser import build_key_index, canonical_entries
from src.translation_baseline import BASELINE_FIELDS, initialize_baseline, refresh_baseline
from src.view_model import build_localization_rows
from src.history import restore_translation
from src.archive_matching import find_archive_returns


def revision_record(revision_id, snapshot, label=None):
    return {"revision_id": revision_id, "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot": snapshot, "label": label}


def reconcile_entries(previous, new_entries, revision_id, *, rename_matches=None, archive_matches=None):
    # Use relative paths for move detection, including moves between subfolders.
    current = [row for row in previous if row["section"] == "CURRENT"]
    current = canonical_entries(current)
    current_ids = {row["entry_id"] for row in current}
    previous = [row for row in previous if row["section"] != "CURRENT" or row["entry_id"] in current_ids]
    old_entries = [{"key": row["key"], "text": row["new_text"], "line": row["new_line"],
                    "kind": row.get("kind", "ENTRY"),
                    "file": str(row["new_file"]), "relative_path": row["new_file"]} for row in current]
    new_entries = [dict(entry, file=str(entry["relative_path"])) for entry in canonical_entries(new_entries)]
    old_index, new_index = build_key_index(old_entries), build_key_index(new_entries)
    results = compare_indexes(old_index, new_index, {}, rename_matches=rename_matches)
    if archive_matches is None:
        effective_matches = {r["key"]: {"old_key": r["old_key"], "score": r["rename_score"]}
                             for r in results if r["source_status"] in ("RENAMED", "RENAMED_CHANGED")}
        archive_matches, _ = find_archive_returns(previous, new_entries, effective_matches)
    archived_by_id = {row["entry_id"]: row for row in previous if row["section"] == "REMOVED"}
    by_key = {result["key"]: result for result in results}
    previous_by_key = {}
    for row in current:
        previous_by_key.setdefault(row["key"], []).append(row)
    fresh = build_localization_rows(new_entries, old_index, new_index, {}, results)
    rows, used = [], set()
    for row in fresh:
        if row["section"] != "CURRENT":
            continue
        result = by_key[row["key"]]
        old_key = result["old_key"] or row["key"]
        candidates = [old for old in previous_by_key.get(row["key"], []) if old["entry_id"] not in used]
        if not candidates:
            candidates = [old for old in previous_by_key.get(old_key, []) if old["entry_id"] not in used]
        candidates.sort(key=lambda old: (old["section"] != "CURRENT",
                        (old.get("new_file") or old.get("old_file")) != row["new_file"],
                        abs((old.get("new_line") or old.get("old_line") or 1) - row["new_line"])))
        row.update(old_key=result["old_key"], rename_score=result["rename_score"])
        restoration = archive_matches.get(row["key"])
        archived = archived_by_id.get(restoration["entry_id"]) if restoration else None
        if archived is not None and archived["entry_id"] not in used:
            restore_translation(row, archived, revision_id, force_review=restoration.get("force_review", False))
            row.update(source_status="NEW", old_text=None, old_file=None, old_line=None)
            refresh_baseline(row, revision_id)
            used.add(archived["entry_id"])
        elif candidates:
            old = candidates[0]
            used.add(old["entry_id"])
            # Ignored warning IDs already bind to entry_id and the exact RU text.
            # Carry the decisions with the translation; diagnostics are recomputed.
            for name in ("entry_id", "ru_text", "translation_state", "translation_origin", "needs_review",
                         "ru_file", "ru_line", "ru_comment", "ru_comment_trailing", "ignored_diagnostics",
                         "restored_from_archive", "restored_needs_confirmation", *BASELINE_FIELDS):
                if name in old:
                    row[name] = deepcopy(old[name])
            if old["section"] == "CURRENT":
                row.update(old_text=old["new_text"], old_file=old["new_file"], old_line=old["new_line"])
                changed = old["new_text"] != row["new_text"]
                if old["key"] != row["key"]:
                    status = "RENAMED_CHANGED" if changed else "RENAMED"
                elif old["new_file"] != row["new_file"]:
                    status = "MOVED_CHANGED" if changed else "MOVED"
                else:
                    status = "CHANGED" if changed else "UNCHANGED"
                row["source_status"] = status
            else:
                # An exact key resurrects its archived logical entry, even when
                # the previous current source offered a similar rename candidate.
                row.update(source_status="NEW", old_key=old["key"], rename_score=None,
                           old_text=None, old_file=None, old_line=None)
            refresh_baseline(row, revision_id)
        else:
            row.update(entry_id=str(uuid4()), translation_state="untranslated", translation_origin="en_fallback",
                       needs_review=False)
            initialize_baseline(row, revision_id)
        rows.append(row)
    for old in previous:
        if old["entry_id"] in used:
            continue
        row = deepcopy(old)
        if old["section"] == "CURRENT":
            row.update(old_text=old["new_text"], old_file=old["new_file"], old_line=old["new_line"],
                       old_key=old["key"], new_text=None, new_file=None, new_line=None, target_ru_file=None,
                       section="REMOVED", source_status="REMOVED", position=None)
            row.update(removed_revision_id=revision_id)
        refresh_baseline(row, revision_id)
        rows.append(row)
    return rows
