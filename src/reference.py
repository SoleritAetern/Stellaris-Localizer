def build_reference_rows(
    english_entries,
    russian_index,
):
    rows = []

    for position, english_entry in enumerate(
        english_entries,
        start=1,
    ):
        key = english_entry["key"]

        russian_entries = russian_index.get(
            key,
            [],
        )

        russian_entry = (
            russian_entries[0]
            if russian_entries
            else None
        )

        rows.append({
            "position": position,
            "section": "STELLARIS",

            "key": key,

            "source_status": "STELLARIS",

            "translation_status": (
                "OFFICIAL"
                if russian_entry
                else "NO_RUSSIAN"
            ),

            "old_text": None,

            "new_text": (
                english_entry["text"]
            ),

            "source_diff": [
                {
                    "type": "equal",
                    "text": (
                        english_entry["text"]
                    ),
                }
            ],

            "ru_text": (
                russian_entry["text"]
                if russian_entry
                else None
            ),

            "old_file": None,

            "new_file": (
                english_entry[
                    "relative_path"
                ]
            ),

            "ru_file": (
                russian_entry[
                    "relative_path"
                ]
                if russian_entry
                else None
            ),

            "target_ru_file": None,

            "old_line": None,

            "new_line": (
                english_entry["line"]
            ),

            "ru_line": (
                russian_entry["line"]
                if russian_entry
                else None
            ),
        })

    return rows