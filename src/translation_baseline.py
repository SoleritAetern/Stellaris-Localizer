"""Translation freshness is independent from source movement and edit history."""

from src.text_diff import build_source_diff
from src.localization_syntax import is_whitespace_text

BASELINE_FIELDS = ("accepted_source_text", "accepted_revision_id", "accepted_source_comment")


def has_translation(row):
    if row.get("translation_state") == "translated" and row.get("translation_origin") == "manual":
        return True  # Explicit acceptance also permits EN-equivalent/empty RU.
    return (row.get("translation_state") != "untranslated"
            and row.get("translation_origin") not in ("en_fallback", "none")
            and row.get("ru_text") is not None)


def can_accept(row):
    return ("accepted_source_text" in row and row.get("section") == "CURRENT"
            and row.get("kind", "ENTRY") in ("ENTRY", "DISABLED_ENTRY") and row.get("new_text") is not None)


def refresh_baseline(row, revision_id=None):
    if revision_id is not None:
        row["current_revision_id"] = revision_id
    translated = has_translation(row)
    current = row.get("section") == "CURRENT"
    if current and is_whitespace_text(row.get("new_text")):
        # This is an exact source value requiring no translation. Keep the
        # accepted history/origin intact if a later revision adds real text.
        row.update(ru_text=row["new_text"], needs_review=False, translation_status="OK",
                   source_diff=[{"type": "equal", "text": row["new_text"]}],
                   source_comment_changed=row.get("accepted_source_comment") != row.get("new_comment"))
        return
    forced = row.get("restored_needs_confirmation", False)
    # and/or return operands: an absent optional flag must be False, not None.
    row["needs_review"] = current and (forced or ((translated or row.get("restored_from_archive", False))
                                                  and row["accepted_source_text"] != row["new_text"]))
    row["source_comment_changed"] = row.get("accepted_source_comment") != row.get("new_comment")
    if not current:
        row["translation_status"] = "ORPHAN" if translated else "NONE"
    elif forced or (row.get("restored_from_archive") and row["needs_review"]):
        row["translation_status"] = "NEED_UPDATE"
    elif not translated:
        row["translation_status"] = "UNTRANSLATED" if row["source_status"] == "NEW" else "MISSING"
    else:
        row["translation_status"] = "NEED_UPDATE" if row["needs_review"] else "OK"
    if current and (translated or row.get("restored_from_archive")):
        row["source_diff"] = build_source_diff(row["accepted_source_text"], row["new_text"])
    elif current:
        row["source_diff"] = [{"type": "equal", "text": row["new_text"]}]
    else:
        row["source_diff"] = build_source_diff(row.get("old_text"), None)


def initialize_baseline(row, revision_id=1, *, legacy=False):
    accepted = None
    accepted_revision = None
    if has_translation(row):
        if row.get("old_text") is not None and (not legacy or row.get("needs_review") or row["section"] == "REMOVED"):
            accepted = row["old_text"]
        elif not row.get("needs_review"):
            accepted = row.get("new_text")
            accepted_revision = revision_id if accepted is not None else None
    comment = row.get("new_comment") if accepted_revision is not None else row.get("old_comment")
    row.update(accepted_source_text=accepted, accepted_revision_id=accepted_revision,
               accepted_source_comment=comment if accepted is not None else None)
    refresh_baseline(row, revision_id)
