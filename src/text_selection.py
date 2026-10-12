"""Selection boundaries only; never normalize or rewrite localization text."""

import re
from functools import lru_cache
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor


@lru_cache(maxsize=256)
def construction_spans(text):
    # Precompute balanced brackets in one pass, including quoted contents. A
    # line with many unfinished '[' must not cause repeated suffix scans.
    bracket_ends, stack = {}, []
    quote, escaped = None, False
    for j, char in enumerate(text):
        if stack and quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif stack and char in "\"'":
            quote = char
        elif char == "[":
            stack.append(j)
        elif char == "]" and stack:
            bracket_ends[stack.pop()] = j + 1
    spans = []
    i = 0
    while i < len(text):
        start, end = i, None
        char = text[i]
        if char in "$£":
            closing = text.find(char, i + 1)
            if closing > i + 1 and not any(c in text[i + 1:closing] for c in "\r\n"):
                end = closing + 1
        elif char == "[":
            end = bracket_ends.get(i)
        elif char == "\\" and text[i:i + 2] in ("\\n", "\\r", "\\t"):
            end = i + 2
        elif char == "§" and i + 1 < len(text) and re.fullmatch(r"[A-Za-z0-9!]", text[i + 1]):
            end = i + 2
        if end is not None:
            spans.append((start, end))
            i = end
        else:
            i += 1
    return tuple(spans)


@dataclass(frozen=True)
class LocalizationToken:
    start: int
    end: int
    kind: str
    raw: str
    expression: str = ""
    display: str | None = None
    comma: int | None = None

    @property
    def key(self):
        if self.kind == "variable":
            return self.expression  # Deliberately no case folding or prefix matching.
        if self.kind == "bracket":
            # The bracket parser already isolates the first argument, respecting
            # quoted commas. Only a single-quoted literal is a localization
            # reference; all other bracket contents are code/dynamic expressions.
            quoted = re.fullmatch(r"'((?:\\.|[^'\\])*)'", self.expression, re.DOTALL)
            if quoted is not None:
                return quoted.group(1)  # Preserve exact spelling and whitespace.
        return None

    def with_display(self, value):
        # Preserve the entire first argument, including its original whitespace.
        first = self.raw[1:self.comma] if self.comma is not None else self.raw[1:-1]
        value = re.sub(r"[\r\n\u2028\u2029]+", " ", value)
        return "[" + first + ", " + value + "]"


@lru_cache(maxsize=512)
def localization_tokens(text):
    result = []
    for start, end in construction_spans(text):
        raw = text[start:end]
        kind = {"$": "variable", "£": "icon", "[": "bracket", "§": "color"}.get(raw[0], "escape")
        expression, display, comma = "", None, None
        if kind == "variable":
            expression = raw[1:-1]
        elif kind == "bracket":
            depth, quote, escaped = 0, None, False
            for i in range(1, len(raw) - 1):
                char = raw[i]
                if quote:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        quote = None
                elif char in "\"'":
                    quote = char
                elif char in "[({":
                    depth += 1
                elif char in "])}":
                    depth -= 1
                elif char == "," and depth == 0:
                    comma = i
                    break
            expression = raw[1:comma if comma is not None else -1].strip()
            display = raw[comma + 1:-1].strip() if comma is not None else None
        result.append(LocalizationToken(start, end, kind, raw, expression, display, comma))
    return tuple(result)


def atomic_tokens(text):
    """Complete constructions count as one token regardless of their length."""
    result, previous = [], 0
    for start, end in construction_spans(text):
        result.extend(re.findall(r"\w+|[^\w\s]", text[previous:start]))
        result.append(text[start:end])
        previous = end
    result.extend(re.findall(r"\w+|[^\w\s]", text[previous:]))
    return result


class TokenSelectionMixin:
    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseDoubleClickEvent(event)
        cursor = self.cursorForPosition(event.position().toPoint())
        text = self.toPlainText()
        qt_position = cursor.position()
        if event.position().x() < self.cursorRect(cursor).x():
            qt_position = max(0, qt_position - 1)
        position = len(text.encode("utf-16-le")[:qt_position * 2].decode("utf-16-le", errors="ignore"))
        for start, end in construction_spans(text):
            if start <= position < end:
                cursor.setPosition(len(text[:start].encode("utf-16-le")) // 2)
                cursor.setPosition(len(text[:end].encode("utf-16-le")) // 2, QTextCursor.MoveMode.KeepAnchor)
                self.setTextCursor(cursor)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)
