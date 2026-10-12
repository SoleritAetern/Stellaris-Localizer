import re
from pathlib import Path

from src.diagnostics import Diagnostic, text_diagnostics, duplicate_diagnostics
from src.localization_syntax import AutoFix, ENTRY_PATTERN, PREFIX_PATTERN, VALUE_PATTERN, parse_entry_line, disabled_entry_parts


HEADER_PATTERN = re.compile(r'^l_[A-Za-z][A-Za-z0-9_]*:\s*(?:#.*)?$')
# Restrict this classification to unmistakable known language header tokens.
INCOMPLETE_HEADER_PATTERN = re.compile(
    r'^l_(?:english|russian|french|german|spanish|polish|braz_por|simp_chinese|japanese|korean)\s*(?:#.*)?$'
)


def _parse_file(file_path, root_path, diagnostics, source, auto_fixes):
    entries = []
    file_path, root_path = Path(file_path), Path(root_path)
    relative_path = file_path.relative_to(root_path)

    with file_path.open("r", encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            line_without_ending = line.rstrip("\r\n")
            clean_line = line_without_ending.strip()

            if clean_line == "":
                continue

            disabled = disabled_entry_parts(line_without_ending)
            if clean_line.startswith("#") and disabled is None:
                continue

            if HEADER_PATTERN.fullmatch(clean_line):
                continue

            match, fix_code = (disabled[1], None) if disabled else parse_entry_line(line_without_ending)

            if match:
                if fix_code:
                    auto_fixes.append(AutoFix(fix_code, relative_path.as_posix(), line_number, match["key"], source))
                entries.append({
                    "kind": "DISABLED_ENTRY" if disabled else "ENTRY",
                    "key": match.group("key"),
                    "text": match.group("text"),
                    "line": line_number,
                    "file": file_path.name,
                    "path": file_path,
                    "relative_path": relative_path,
                    "trailing": match.group("trailing"),
                })
                if diagnostics is not None:
                    diagnostics.extend(text_diagnostics(
                        match["text"], file=relative_path.as_posix(), line=line_number, key=match["key"], source=source,
                    ))

            elif diagnostics is not None:
                prefix = PREFIX_PATTERN.match(line_without_ending)
                key = prefix["key"] if prefix else None
                severity = "error"
                if prefix and prefix["value"].startswith('"'):
                    if VALUE_PATTERN.match(prefix["value"]) is None:
                        code, message = "UNCLOSED_QUOTE", "Нет закрывающей кавычки текста локализации."
                    else:
                        code, message = "MALFORMED_ENTRY", "Не удалось распознать запись локализации."
                elif INCOMPLETE_HEADER_PATTERN.fullmatch(clean_line) or re.fullmatch(r'l_\s*:\s*(?:#.*)?', clean_line):
                    code, message = "INVALID_HEADER", "Некорректный языковой заголовок."
                    key = None
                elif prefix or ":" in clean_line.split("#", 1)[0] or '"' in clean_line.split("#", 1)[0]:
                    code, message = "MALFORMED_ENTRY", 'Повреждённая запись; ожидается key:0 "Text".'
                else:
                    code, message, severity = "UNKNOWN", "Нераспознанная строка локализации.", "warning"
                diagnostics.append(Diagnostic(code, severity, relative_path.as_posix(), line_number, key, message, source))

    return entries


def canonical_entries(entries):
    """An active key owns translation state; disabled alternatives are structure."""
    entries = list(entries)
    active = {entry["key"] for entry in entries if entry.get("kind", "ENTRY") == "ENTRY"}
    return [entry for entry in entries if entry.get("kind") != "DISABLED_SHADOW"
            and not (entry.get("kind") == "DISABLED_ENTRY" and entry["key"] in active)]


def parse_localization_file(file_path, root_path, *, diagnostics=None, source=None, auto_fixes=None):
    """Keep the entries API; callers may supply a list to collect diagnostics."""
    collected = diagnostics if diagnostics is not None else []
    fixes = auto_fixes if auto_fixes is not None else []
    entries = canonical_entries(_parse_file(file_path, root_path, collected, source, fixes))
    collected.extend(duplicate_diagnostics(entries, source))
    return entries


def parse_localization_folder(folder_path, language=None, *, diagnostics=None, source=None, auto_fixes=None,
                              collect_diagnostics=True):
    """Parse entries normally; reference-only callers can skip unused diagnostics."""
    all_entries = []
    collected = (diagnostics if diagnostics is not None else []) if collect_diagnostics else None
    fixes = auto_fixes if auto_fixes is not None else []

    if language == "english":
        pattern = "*_l_english.yml"

    elif language == "russian":
        pattern = "*_l_russian.yml"

    else:
        pattern = "*.yml"

    files = sorted(Path(folder_path).rglob(pattern))

    for file_path in files:
        file_entries = _parse_file(
            file_path,
            folder_path,
            collected,
            source,
            fixes,
        )

        all_entries.extend(file_entries)

    # Once for the entire source, including duplicates located in different files.
    all_entries = canonical_entries(all_entries)
    if collected is not None:
        collected.extend(duplicate_diagnostics(all_entries, source))
    return files, all_entries

def build_key_index(entries):
    index = {}

    for entry in canonical_entries(entries):
        key = entry["key"]

        if key not in index:
            index[key] = []

        index[key].append(entry)

    return index
