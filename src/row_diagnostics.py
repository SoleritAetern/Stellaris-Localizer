"""Conservative RU warnings and exact, text-bound single-entry fixes."""

from PySide6.QtCore import QCoreApplication

import hashlib
import re
from dataclasses import dataclass

from .diagnostics import text_diagnostics
from .text_selection import localization_tokens


@dataclass(frozen=True)
class RowWarning:
    diagnostic_id: str
    code: str
    message: str
    start: int
    end: int
    fixes: tuple  # (label, start, end, replacement), applied to the original text.


def ignored_ids(value):
    return sorted({item for item in value if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)}) if isinstance(value, list) else []


def analyze_ru(entry_id, text):
    text = text or ""
    result = []
    fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()

    def add(code, message, start, end, fixes=()):
        identity = hashlib.sha256(f"{entry_id}:{fingerprint}:{code}:{start}:{end}".encode("utf-8")).hexdigest()
        result.append(RowWarning(identity, code, message, start, end, tuple(fixes)))

    diagnostics = text_diagnostics(text, file="", line=1, source="ru", scope="working", entry_id=entry_id)
    # Reuse the shared construction tokenizer; quoted bracket arguments are
    # syntax, not prose quotation marks to replace with Russian typography.
    protected = [(token.start, token.end) for token in localization_tokens(text)
                 if token.kind in ("bracket", "variable", "icon")]
    protected.extend((item.text_start, item.text_end) for item in diagnostics if item.code == "UNCLOSED_BRACKET")
    quotes = []
    slashes = 0
    for position, char in enumerate(text):
        if char == '"' and not any(start <= position < end for start, end in protected):
            quotes.append((position, bool(slashes % 2), slashes))
        slashes = slashes + 1 if char == "\\" else 0
    paired = len(quotes) % 2 == 0
    i = 0
    while i < len(quotes):
        position, escaped, slash_count = quotes[i]
        partner = quotes[i + 1] if paired and i % 2 == 0 and i + 1 < len(quotes) else None
        reliable = (partner is not None and partner[1] == escaped and slash_count <= 1
                    and partner[2] <= 1 and partner[0] > position + 1
                    and "\n" not in text[position:partner[0]])
        start, end = position - int(escaped), position + 1
        fixes = []
        if reliable:
            end = partner[0] + 1
            content = text[position + 1:partner[0] - int(escaped)]
            fixes.append((QCoreApplication.translate("row_diagnostics", "Заменить на «ёлочки»"), start, end, "«" + content + "»"))
            if not escaped:
                fixes.append((QCoreApplication.translate("row_diagnostics", "Экранировать как \\\""), start, end, '\\"' + content + '\\"'))
            i += 2
        else:
            if not escaped:
                fixes.append((QCoreApplication.translate("row_diagnostics", "Экранировать как \\\""), position, position + 1, '\\"'))
            i += 1
        add("ESCAPED_QUOTES" if escaped else "STRAIGHT_QUOTES",
            QCoreApplication.translate("row_diagnostics", "Экранированные прямые кавычки") if escaped else QCoreApplication.translate("row_diagnostics", "Прямые кавычки"),
            start, end, fixes)

    for diagnostic in diagnostics:
        if diagnostic.code not in {"UNCLOSED_VARIABLE", "UNCLOSED_ICON", "UNCLOSED_BRACKET",
                                   "UNEXPECTED_BRACKET_END"}:
            continue
        start, end = diagnostic.text_start, diagnostic.text_end
        fixes = []
        delimiter = {"UNCLOSED_VARIABLE": "$", "UNCLOSED_ICON": "£", "UNCLOSED_BRACKET": "]"}.get(diagnostic.code)
        # Only a simple unterminated token at the end has an unambiguous end.
        # Complex expressions still get a warning, never guessed repairs.
        if delimiter and re.fullmatch(r"[A-Za-z0-9_.:|\-]+", text[start + 1:]):
            fixes.append((QCoreApplication.translate("row_diagnostics", "Добавить {0}").format(delimiter), len(text), len(text), delimiter))
        add(diagnostic.code, diagnostic.message, start, end, fixes)
    return tuple(result)
