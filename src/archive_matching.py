"""Reuse the existing matcher for archived identities, without changing its scores."""

from pathlib import Path

from src.diff import find_renamed_keys, similarity
from src.parser import build_key_index


def archived_entry(row):
    path = Path(row.get("last_source_prefix", "")) / row["old_file"] if row.get("old_file") else Path("")
    return {"key": row["key"], "text": row.get("old_text") or "", "line": row.get("old_line"),
            "file": str(path), "relative_path": path, "entry_id": row["entry_id"]}


def find_archive_returns(previous, new_entries, current_matches, *, prefix=""):
    current_keys = {row["key"] for row in previous if row["section"] == "CURRENT"}
    archived = sorted((row for row in previous if row["section"] == "REMOVED"),
                      key=lambda row: row.get("removed_revision_id", 0), reverse=True)
    old_index = build_key_index([archived_entry(row) for row in archived])
    new_index = build_key_index([dict(entry, file=str(Path(prefix) / entry["relative_path"]),
                                    relative_path=Path(prefix) / entry["relative_path"])
                                for entry in new_entries if entry["key"] not in current_keys])
    automatic, reviews = {}, {}
    for key in old_index.keys() & new_index.keys():
        old, new = old_index[key][0], new_index[key][0]
        score = 1.0 if old["text"] == new["text"] else similarity(old["text"], new["text"])
        radical = score < 0.45
        info = {"old_key": key, "entry_id": old["entry_id"], "score": score, "old": old,
                "force_review": radical, "return_kind": "radical" if radical else "possible"}
        if radical or len(old_index[key]) > 1:
            info["reason"] = ("Вернувшийся ключ — текст сильно изменён" if radical
                              else "Возможное возвращение удалённой записи: несколько записей с этим ключом")
            reviews[key] = info
        else:
            automatic[key] = info
    claimed = {info["old_key"] for info in [*automatic.values(), *reviews.values()]}
    remaining_old = {key: values for key, values in old_index.items() if key not in claimed}
    remaining_new = {key: values for key, values in new_index.items()
                     if key not in automatic and key not in reviews and key not in current_matches}
    uncertain = {}
    confident = find_renamed_keys(remaining_old, remaining_new, review_candidates=uncertain)
    for destination, matches in ((automatic, confident), (reviews, uncertain)):
        for key, match in matches.items():
            old = remaining_old[match["old_key"]][0]
            # Several archived generations of one key cannot silently choose
            # a translation, even if the key-level matcher has a high score.
            target = reviews if len(remaining_old[match["old_key"]]) > 1 else destination
            target[key] = dict(match, entry_id=old["entry_id"], old=old, force_review=False,
                               return_kind="possible", reason="Возможное возвращение удалённой записи")
    return automatic, reviews
