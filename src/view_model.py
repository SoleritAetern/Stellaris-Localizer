from src.paths import english_to_russian_path
from src.text_diff import build_source_diff
from src.localization_document import initial_comments
from src.parser import canonical_entries
from src.localization_syntax import is_whitespace_text


def build_localization_rows(
    new_entries,
    old_index,
    new_index,
    ru_index,
    comparison_results,
):
    rows = []

    comparison_by_key = {
        result["key"]: result
        for result in comparison_results
    }

    # Сначала вся актуальная локализация
    # строго в порядке NEW EN.
    for position, new_entry in enumerate(
        canonical_entries(new_entries),
        start=1,
    ):
        key = new_entry["key"]

        result = comparison_by_key[key]

        old_entry = result["old"]
        ru_entry = result["ru"]

        target_ru_file = english_to_russian_path(
            new_entry["relative_path"]
        )

        rows.append({
            **initial_comments(new_entry, ru_entry),
            "kind": new_entry.get("kind", "ENTRY"),
            "old_comment": initial_comments(old_entry, None)["new_comment"],
            "position": position,
            "section": "CURRENT",

            "key": key,

            "source_status": result["source_status"],
            "translation_status": result[
                "translation_status"
            ],

            "old_text": (
                old_entry["text"]
                if old_entry
                else None
            ),

            "new_text": new_entry["text"],

            "source_diff": (
                [
                    {
                        "type": "equal",
                        "text": new_entry["text"],
                    }
                ]
                if result["source_status"]
                == "NO_HISTORY"
                else build_source_diff(
                    old_entry["text"]
                    if old_entry
                    else None,
                    new_entry["text"],
                )
            ),

            "ru_text": (
                ru_entry["text"]
                if ru_entry
                else new_entry["text"]
            ),

            "old_file": (
                old_entry["relative_path"]
                if old_entry
                else None
            ),

            "new_file": new_entry["relative_path"],

            "ru_file": (
                ru_entry["relative_path"]
                if ru_entry
                else None
            ),

            "target_ru_file": target_ru_file,

            "old_line": (
                old_entry["line"]
                if old_entry
                else None
            ),

            "new_line": new_entry["line"],

            "ru_line": (
                ru_entry["line"]
                if ru_entry
                else None
            ),
        })

        if is_whitespace_text(new_entry["text"]):
            rows[-1].update(ru_text=new_entry["text"], translation_status="OK",
                            source_diff=[{"type": "equal", "text": new_entry["text"]}])

    # Потом отдельно сохраняем удалённые
    # и лишние русские ключи.
    extra_results = []

    for result in comparison_results:
        if result["source_status"] in (
            "REMOVED",
            "NO_SOURCE",
        ):
            extra_results.append(result)

    extra_results.sort(
        key=lambda result: (
            str(
                result["old"]["relative_path"]
                if result["old"]
                else result["ru"]["relative_path"]
            ),
            (
                result["old"]["line"]
                if result["old"]
                else result["ru"]["line"]
            ),
        )
    )

    for result in extra_results:
        old_entry = result["old"]
        ru_entry = result["ru"]

        rows.append({
            **initial_comments(None, ru_entry),
            "position": None,
            "section": "REMOVED",

            "key": result["key"],

            "source_status": result["source_status"],
            "translation_status": result[
                "translation_status"
            ],

            "old_text": (
                old_entry["text"]
                if old_entry
                else None
            ),

            "new_text": None,

            "source_diff": build_source_diff(
                old_entry["text"]
                if old_entry
                else None,
                None,
            ),

            "ru_text": (
                ru_entry["text"]
                if ru_entry
                else None
            ),

            "old_file": (
                old_entry["relative_path"]
                if old_entry
                else None
            ),

            "new_file": None,

            "ru_file": (
                ru_entry["relative_path"]
                if ru_entry
                else None
            ),

            "target_ru_file": None,

            "old_line": (
                old_entry["line"]
                if old_entry
                else None
            ),

            "new_line": None,

            "ru_line": (
                ru_entry["line"]
                if ru_entry
                else None
            ),
        })

    return rows
