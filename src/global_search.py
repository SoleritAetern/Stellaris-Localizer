"""Shared read-only search; display limits belong to the UI, not match semantics."""

import re
from dataclasses import dataclass


RESULT_LIMIT = 150


def prepare_query(text, mode, *, case_sensitive=False):
    query = text
    if len(query) >= 2 and query[0] == query[-1] == '"':
        query = query[1:-1]
    if mode == "auto":
        # Cyrillic wins for mixed queries; numbers/punctuation default to EN.
        mode = "ru" if re.search(r"[\u0400-\u052f]", query) else "en"
    return query if case_sensitive else query.lower(), mode


def is_working_ru(row):
    """An actual current working translation, not copied source fallback."""
    return (row.get("kind", "ENTRY") in ("ENTRY", "DISABLED_ENTRY")
            and row.get("section", "CURRENT") == "CURRENT"
            and row.get("translation_origin") not in ("en_fallback", "none")
            and row.get("translation_state") != "untranslated")


def localization_matches(sources, query, mode, *, working_ru_only=False, case_sensitive=False):
    """Yield exact keys before partial keys; callers stop at limit + 1.

    Key mode must examine keys across the chosen sources before yielding partial
    matches. Text modes can stop without traversing the remainder of the dataset.
    Vanilla's duplicate-definition display policy is retained.
    """
    if not query:
        return
    seen = set()
    for exact in ((True, False) if mode == "key" else (None,)):
        for source, rows in sources:
            if working_ru_only and (source != "mod" or mode != "ru"):
                continue
            for row_index, row in enumerate(rows):
                if working_ru_only and not is_working_ru(row):
                    continue
                key = row.get("key") or ""
                if not case_sensitive:
                    key = key.lower()
                if mode == "key":
                    matches = key == query if exact else query in key and key != query
                else:
                    text = (row.get("ru_text") or "") if mode == "ru" else (
                        row.get("new_text") if row.get("new_text") is not None else row.get("old_text") or "")
                    matches = query in (text if case_sensitive else text.lower())
                if not matches:
                    continue
                if source == "stellaris":
                    fingerprint = (row["key"], str(row.get("new_file") or ""),
                                   row.get("new_text") or "", row.get("ru_text") or "")
                    if fingerprint in seen:
                        continue
                    seen.add(fingerprint)
                yield source, row_index, row


@dataclass(frozen=True)
class WorkingRuMatch:
    entry_id: str | None
    row: int
    text: str
    spans: tuple[tuple[int, int], ...]  # Non-overlapping Python character offsets.

    @property
    def occurrence_count(self):
        return len(self.spans)


def match_spans(original, query, *, case_sensitive=False):
    """Exact, non-overlapping source offsets with the shared Aa semantics."""
    if not query:
        return ()
    searched = original if case_sensitive else original.lower()
    needle = query if case_sensitive else query.lower()
    positions = None
    if len(searched) != len(original):
        positions = [i for i, char in enumerate(original) for _ in char.lower()]
    spans = []
    start = searched.find(needle)
    while start >= 0:
        end = start + len(needle)
        span = (positions[start], positions[end - 1] + 1) if positions is not None else (start, end)
        if not spans or span[0] >= spans[-1][1]:
            spans.append(span)
        start = searched.find(needle, end)
    return tuple(spans)


def full_replacement_matches(rows, text, *, case_sensitive=False):
    """Read-only, unlimited query for a future replacement operation.

    Uses exactly the live RU search normalization, case matching and eligibility.
    Never called during live search. Count entries with len(result), occurrences
    with sum(match.occurrence_count for match in result). A future mutation must
    revalidate each entry_id/text; displayed rows are never a replacement plan.
    """
    query, mode = prepare_query(text, "ru", case_sensitive=case_sensitive)
    result = []
    for _, row_index, row in localization_matches([("mod", rows)], query, mode,
                                                 working_ru_only=True, case_sensitive=case_sensitive):
        original = row.get("ru_text") or ""
        spans = match_spans(original, query, case_sensitive=case_sensitive)
        result.append(WorkingRuMatch(row.get("entry_id"), row_index, original, spans))
    return tuple(result)
