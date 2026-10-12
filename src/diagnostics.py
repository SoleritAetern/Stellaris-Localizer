"""Non-blocking localisation diagnostics, independent of Qt and the GUI."""

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    file: str
    line: int
    key: str | None
    message: str
    source: str | None = None
    scope: str = "source"
    entry_id: str | None = None
    text_start: int | None = None
    text_end: int | None = None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        """Ignore malformed optional diagnostic metadata without rejecting a project."""
        if not isinstance(value, dict):
            return None
        if not all(isinstance(value.get(name), str) for name in ("code", "severity", "file", "message")):
            return None
        if value["code"] in {"UNCLOSED_COLOR", "INVALID_COLOR_START", "UNEXPECTED_COLOR_END"}:
            return None  # Retired checks must not reappear from saved diagnostic metadata.
        if value["severity"] not in ("error", "warning", "info"):
            return None
        if type(value.get("line")) is not int or value["line"] < 1:
            return None
        if value.get("source") not in (None, "old_en", "new_en", "ru"):
            return None
        if value.get("scope", "source") not in ("source", "working"):
            return None
        if any(value.get(name) is not None and not isinstance(value[name], str) for name in ("key", "entry_id")):
            return None
        fields = {name: value[name] for name in cls.__dataclass_fields__ if name in value}
        start, end = value.get("text_start"), value.get("text_end")
        if ((start is None) != (end is None)
                or (start is not None and (type(start) is not int or type(end) is not int or start < 0 or end < start))):
            return None
        fields.setdefault("key", None)
        return cls(**fields)


def text_diagnostics(text, *, file, line, key=None, source=None, scope="source", entry_id=None):
    """Check each text independently; never compare EN and RU constructs."""
    text = text or ""
    context = dict(file=str(file), line=line, key=key, source=source, scope=scope, entry_id=entry_id)
    diagnostics = []
    for delimiter, code, message in (
        ("£", "UNCLOSED_ICON", "Не закрыта конструкция £...£."),
        ("$", "UNCLOSED_VARIABLE", "Не закрыта конструкция $...$."),
    ):
        opened = None
        for position, char in enumerate(text):
            if char == delimiter:
                opened = position if opened is None else None
        if opened is not None:
            diagnostics.append(Diagnostic(code, "warning", message=message, **context,
                                          text_start=opened, text_end=len(text)))

    # Decode quoting escapes for analysis only. Brackets in quoted arguments are
    # content, not delimiters; concept syntax and arbitrary argument formats pass.
    bracket_text = re.sub(r'\\(["\\])', r'\1', text)
    # Map decoded offsets back to the stored text; no fabricated caret position.
    offsets = []
    i = 0
    while i < len(text):
        offsets.append(i)
        i += 2 if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in ('"', "\\") else 1
    opened_brackets = []
    depth, quote, escaped = 0, None, False
    for position, char in enumerate(bracket_text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif depth and char in ("'", '"'):
            quote = char
        elif char == "[":
            depth += 1
            opened_brackets.append(offsets[position])
        elif char == "]":
            if depth:
                depth -= 1
                opened_brackets.pop()
            else:
                diagnostics.append(Diagnostic(
                    "UNEXPECTED_BRACKET_END", "warning", message="Закрывающая ] без открывающей [.", **context,
                    text_start=offsets[position], text_end=offsets[position] + 1,
                ))
    if depth:
        diagnostics.append(Diagnostic(
            "UNCLOSED_BRACKET", "warning", message="Не закрыта конструкция [...].", **context,
            text_start=opened_brackets[0], text_end=len(text),
        ))
    return diagnostics


def duplicate_diagnostics(entries, source=None):
    by_key = defaultdict(list)
    for entry in entries:
        by_key[entry["key"]].append(entry)
    diagnostics = []
    for key, occurrences in by_key.items():
        if len(occurrences) < 2:
            continue
        first = occurrences[0]
        message = (f"Ключ встречается {len(occurrences)} раз. "
                   f"Первое вхождение: {first['relative_path']}:{first['line']}.")
        for entry in occurrences:
            diagnostics.append(Diagnostic(
                "DUPLICATE_KEY", "warning", Path(entry["relative_path"]).as_posix(), entry["line"], key, message, source,
            ))
    return diagnostics


def working_diagnostics(rows):
    """Derived on demand, so edits/Replace/Undo cannot leave stale RU warnings.

    For current rows, line is the corresponding NEW EN template line, not a
    promised physical line in an exported file. entry_id identifies the row.
    """
    diagnostics = []
    for row in rows:
        file = row.get("target_ru_file") or row.get("ru_file") or row.get("new_file") or row.get("old_file")
        line = row.get("new_line") or row.get("ru_line") or row.get("old_line") or 1
        diagnostics.extend(text_diagnostics(
            row.get("ru_text"), file=Path(file).as_posix() if file else "", line=line,
            key=row.get("key"), source="ru", scope="working", entry_id=row.get("entry_id"),
        ))
    return diagnostics
