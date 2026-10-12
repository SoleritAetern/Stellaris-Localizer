from collections import Counter
from difflib import SequenceMatcher
from heapq import heappush, heapreplace


class _CloseKeyMatcher:
    """Exact get_close_matches ranking with reusable multiset upper bounds."""

    def __init__(self, keys):
        self.features = {key: (len(key), Counter(key)) for key in keys}

    def matches(self, word, available, *, n=6, cutoff=0.45):
        length, counts = len(word), Counter(word)
        bounded = []
        for key in available:
            size, other = self.features[key]
            total = length + size
            if total and 2.0 * min(length, size) / total < cutoff:
                continue
            # This is exactly SequenceMatcher.quick_ratio(): the multiset
            # intersection bounds matching blocks even when autojunk is on.
            common = sum(min(count, counts.get(char, 0)) for char, count in other.items())
            upper = 2.0 * common / total if total else 1.0
            if upper >= cutoff:
                bounded.append((upper, key))

        matcher = SequenceMatcher()  # Keep get_close_matches' autojunk=True.
        matcher.set_seq2(word)       # Preserve old-key -> new-key orientation.
        best = []
        # Evaluate the most promising bounds first. Only skip a key if even
        # its upper bound cannot enter the exact top-n, including lexical ties.
        for upper, key in sorted(bounded, reverse=True):
            if len(best) == n and (upper, key) <= best[0]:
                break
            matcher.set_seq1(key)
            score = matcher.ratio()
            if score >= cutoff:
                candidate = (score, key)
                if len(best) < n:
                    heappush(best, candidate)
                elif candidate > best[0]:
                    heapreplace(best, candidate)
        return [key for score, key in sorted(best, reverse=True)]


def _similarity_at_least(a, b, cutoff):
    """Return the unchanged similarity, or None if it cannot reach cutoff."""
    if not a or not b:
        return None
    if a == b:
        return 1.0
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    if matcher.real_quick_ratio() < cutoff or matcher.quick_ratio() < cutoff:
        return None
    score = matcher.ratio()
    return score if score >= cutoff else None

def entry_file(entry):
    if entry is None:
        return ""

    return str(
        entry.get("relative_path")
        or entry.get("file")
        or ""
    )


def similarity(a, b):
    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    return SequenceMatcher(
        None,
        a,
        b,
        autojunk=False,
    ).ratio()


def line_proximity(
    old_entry,
    new_entry,
):
    # Номер строки имеет смысл
    # только внутри одного файла.
    if (
        entry_file(old_entry)
        != entry_file(new_entry)
    ):
        return 0.0

    old_line = old_entry.get("line")
    new_line = new_entry.get("line")

    if (
        old_line is None
        or new_line is None
    ):
        return 0.0

    distance = abs(
        old_line - new_line
    )

    if distance <= 2:
        return 1.0

    if distance <= 10:
        return 0.8

    if distance <= 30:
        return 0.5

    if distance <= 100:
        return 0.2

    return 0.0

def rename_candidate_score(
    old_key,
    old_entry,
    new_key,
    new_entry,
):
    same_file = (
        entry_file(old_entry)
        == entry_file(new_entry)
    )

    line_score = line_proximity(
        old_entry,
        new_entry,
    )

    # These are the same two acceptance branches as before. Check necessary
    # conditions first, before the expensive text SequenceMatcher.ratio().
    nearby = same_file and line_score >= 0.5
    key_score = _similarity_at_least(old_key, new_key, 0.35 if nearby else 0.60)
    if key_score is None:
        return None
    text_score = _similarity_at_least(
        old_entry["text"], new_entry["text"], 0.65 if key_score >= 0.60 else 0.98,
    )
    if text_score is None:
        return None

    score = (
        text_score * 0.55
        + key_score * 0.30
        + (
            0.10
            if same_file
            else 0.0
        )
        + line_score * 0.05
    )

    return score

def find_renamed_keys(
    old_index,
    new_index,
    *, review_candidates=None,
):
    exact_keys = (
        set(old_index)
        & set(new_index)
    )

    unmatched_old = (
        set(old_index)
        - exact_keys
    )

    unmatched_new = (
        set(new_index)
        - exact_keys
    )

    if (
        not unmatched_old
        or not unmatched_new
    ):
        return {}

    # Текст -> старые ключи.
    # Это позволяет очень быстро ловить
    # переименование без изменения текста.
    old_by_text = {}

    for old_key in unmatched_old:
        old_entry = old_index[
            old_key
        ][0]

        old_by_text.setdefault(
            old_entry["text"],
            [],
        ).append(
            old_key
        )

    available_old = set(
        unmatched_old
    )
    close_key_matcher = _CloseKeyMatcher(unmatched_old)

    matches = {}

    for new_key in sorted(
        unmatched_new
    ):
        new_entry = new_index[
            new_key
        ][0]

        candidates = set()

        # 1. Полностью одинаковый EN-текст.
        candidates.update(
            old_by_text.get(
                new_entry["text"],
                [],
            )
        )

        # 2. Несколько наиболее похожих
        # старых ключей.
        close_keys = close_key_matcher.matches(
            new_key,
            available_old,
            n=6,
            cutoff=0.45,
        )

        candidates.update(
            close_keys
        )

        candidates.intersection_update(
            available_old
        )

        scored = []

        for old_key in candidates:
            old_entry = old_index[
                old_key
            ][0]

            score = rename_candidate_score(
                old_key,
                old_entry,
                new_key,
                new_entry,
            )

            if score is None:
                continue

            scored.append(
                (
                    score,
                    old_key,
                )
            )

        if not scored:
            continue

        scored.sort(
            reverse=True
        )

        best_score, best_old_key = (
            scored[0]
        )

        # Не доверяем слабым совпадениям.
        if best_score < 0.78:
            if review_candidates is not None:
                review_candidates[new_key] = {"old_key": best_old_key, "score": best_score,
                                              "reason": "Низкая уверенность совпадения"}
            continue

        # Если второй кандидат почти такой же
        # хороший — случай неоднозначный.
        if len(scored) > 1:
            second_score = scored[1][0]

            if (
                best_score
                - second_score
                < 0.08
            ):
                if review_candidates is not None:
                    review_candidates[new_key] = {"old_key": best_old_key, "score": best_score,
                                                  "reason": "Несколько близких кандидатов",
                                                  "second_score": second_score}
                continue

        matches[new_key] = {
            "old_key": best_old_key,
            "score": best_score,
        }

        available_old.remove(
            best_old_key
        )

    if review_candidates is not None:
        # A later, confident match may have claimed an earlier suggestion.
        for key in list(review_candidates):
            if review_candidates[key]["old_key"] not in available_old:
                del review_candidates[key]
    return matches

def compare_indexes(
    old_index,
    new_index,
    ru_index,
    has_old_source=True,
    *, rename_matches=None,
):
    results = []

    # -------------------------
    # Сначала ищем переименования
    # -------------------------

    if rename_matches is not None:
        pass  # Preview has already resolved (or explicitly rejected) candidates.
    elif has_old_source:
        rename_matches = (
            find_renamed_keys(
                old_index,
                new_index,
            )
        )
    else:
        rename_matches = {}

    renamed_old_keys = {
        match["old_key"]
        for match
        in rename_matches.values()
    }

    # -------------------------
    # Общий список ключей
    # -------------------------

    if has_old_source:
        all_keys = (
            set(old_index)
            | set(new_index)
            | set(ru_index)
        )
    else:
        all_keys = (
            set(new_index)
            | set(ru_index)
        )

    for key in sorted(all_keys):

        # Старый ключ уже был сопоставлен
        # с новым как RENAMED.
        # Отдельную REMOVED-запись
        # для него больше не создаём.
        if key in renamed_old_keys:
            continue

        # -------------------------
        # Переименованный ключ
        # -------------------------

        if key in rename_matches:
            rename_info = (
                rename_matches[key]
            )

            old_key = rename_info[
                "old_key"
            ]

            old_entry = old_index[
                old_key
            ][0]

            new_entry = new_index[
                key
            ][0]

            # Если RU уже вручную перевели
            # на новый ключ — используем его.
            #
            # Иначе переносим RU со старого ключа.
            ru_entries = ru_index.get(
                key,
                [],
            )

            if not ru_entries:
                ru_entries = ru_index.get(
                    old_key,
                    [],
                )

            ru_entry = (
                ru_entries[0]
                if ru_entries
                else None
            )

            if (
                old_entry["text"]
                == new_entry["text"]
            ):
                source_status = (
                    "RENAMED"
                )

                translation_status = (
                    "OK"
                    if ru_entry
                    else "MISSING"
                )

            else:
                source_status = (
                    "RENAMED_CHANGED"
                )

                translation_status = (
                    "NEEDS_REVIEW"
                    if ru_entry
                    else "MISSING"
                )

            results.append({
                "key": key,

                "old_key": old_key,
                "new_key": key,

                "source_status": (
                    source_status
                ),

                "translation_status": (
                    translation_status
                ),

                "old": old_entry,
                "new": new_entry,
                "ru": ru_entry,

                "rename_score": (
                    rename_info["score"]
                ),
            })

            continue

        # -------------------------
        # Обычное сравнение
        # -------------------------

        old_entries = old_index.get(
            key,
            [],
        )

        new_entries = new_index.get(
            key,
            [],
        )

        ru_entries = ru_index.get(
            key,
            [],
        )

        old_entry = (
            old_entries[0]
            if old_entries
            else None
        )

        new_entry = (
            new_entries[0]
            if new_entries
            else None
        )

        ru_entry = (
            ru_entries[0]
            if ru_entries
            else None
        )

        # -------------------------
        # Источник
        # -------------------------

        if not has_old_source:
            if new_entry:
                source_status = (
                    "NO_HISTORY"
                )
            else:
                source_status = (
                    "NO_SOURCE"
                )

        elif not old_entry and new_entry:
            source_status = "NEW"

        elif old_entry and not new_entry:
            source_status = "REMOVED"

        elif old_entry and new_entry:
            old_text = old_entry["text"]
            new_text = new_entry["text"]

            old_file = old_entry["file"]
            new_file = new_entry["file"]

            if old_text == new_text:
                if old_file == new_file:
                    source_status = (
                        "UNCHANGED"
                    )
                else:
                    source_status = (
                        "MOVED"
                    )

            else:
                if old_file == new_file:
                    source_status = (
                        "CHANGED"
                    )
                else:
                    source_status = (
                        "MOVED_CHANGED"
                    )

        else:
            source_status = "NO_SOURCE"

        # -------------------------
        # Перевод
        # -------------------------

        if source_status == "NEW":
            if ru_entry:
                translation_status = "OK"
            else:
                translation_status = (
                    "UNTRANSLATED"
                )

        elif source_status in (
            "CHANGED",
            "MOVED_CHANGED",
        ):
            if ru_entry:
                translation_status = (
                    "NEEDS_REVIEW"
                )
            else:
                translation_status = (
                    "MISSING"
                )

        elif source_status in (
            "UNCHANGED",
            "MOVED",
            "NO_HISTORY",
        ):
            if ru_entry:
                translation_status = "OK"
            else:
                translation_status = (
                    "MISSING"
                )

        elif source_status == "REMOVED":
            if ru_entry:
                translation_status = (
                    "ORPHAN"
                )
            else:
                translation_status = (
                    "NONE"
                )

        else:
            translation_status = (
                "ORPHAN"
            )

        results.append({
            "key": key,

            "old_key": (
                key
                if old_entry
                else None
            ),

            "new_key": (
                key
                if new_entry
                else None
            ),

            "source_status": (
                source_status
            ),

            "translation_status": (
                translation_status
            ),

            "old": old_entry,
            "new": new_entry,
            "ru": ru_entry,

            "rename_score": None,
        })

    return results
