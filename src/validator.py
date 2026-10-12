from collections import defaultdict

from src.parser import parse_localization_folder
from src.paths import english_to_russian_path


def validate_build(new_en_path, output_ru_path):
    issues = []

    new_files, new_entries = parse_localization_folder(
        new_en_path,
        language="english",
        )
    ru_files, ru_entries = parse_localization_folder(
        output_ru_path,
        language="russian",
        )

    # Каких русских файлов мы ожидаем от структуры NEW EN
    expected_files = {
        english_to_russian_path(
            file_path.relative_to(new_en_path)
        )
        for file_path in new_files
    }

    # Какие файлы реально были созданы
    actual_files = {
        file_path.relative_to(output_ru_path)
        for file_path in ru_files
    }

    missing_files = expected_files - actual_files
    extra_files = actual_files - expected_files

    for file_path in sorted(missing_files):
        issues.append(
            f"MISSING FILE | {file_path}"
        )

    for file_path in sorted(extra_files):
        issues.append(
            f"EXTRA FILE | {file_path}"
        )

    # Собираем ключи NEW EN по будущим русским файлам
    expected_keys = defaultdict(list)

    for entry in new_entries:
        russian_path = english_to_russian_path(
            entry["relative_path"]
        )

        expected_keys[russian_path].append(
            entry["key"]
        )

    # Собираем реально созданные русские ключи
    actual_keys = defaultdict(list)

    for entry in ru_entries:
        actual_keys[entry["relative_path"]].append(
            entry["key"]
        )

    # Проверяем каждый файл
    for file_path in sorted(expected_files):
        expected = expected_keys[file_path]
        actual = actual_keys[file_path]

        if expected == actual:
            continue

        expected_set = set(expected)
        actual_set = set(actual)

        missing_keys = expected_set - actual_set
        extra_keys = actual_set - expected_set

        for key in sorted(missing_keys):
            issues.append(
                f"MISSING KEY | {file_path} | {key}"
            )

        for key in sorted(extra_keys):
            issues.append(
                f"EXTRA KEY | {file_path} | {key}"
            )

        # Если набор ключей одинаковый, но порядок другой
        if (
            not missing_keys
            and not extra_keys
            and expected != actual
        ):
            issues.append(
                f"WRONG ORDER | {file_path}"
            )

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "new_files": len(new_files),
        "ru_files": len(ru_files),
        "new_entries": len(new_entries),
        "ru_entries": len(ru_entries),
    }