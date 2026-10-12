"""Shared presentation syntax; offsets remain Python character positions."""

import re

from PySide6.QtGui import QTextCharFormat, QTextLayout

from . import theme


# Order matters: later formats override earlier ones in overlapping tokens.
_PATTERNS = (
    (re.compile(r"\[[^\]\n]+\]"), "syntax.reference"),
    (re.compile(r"\$[^$\n]+\$"), "syntax.variable"),
    (re.compile(r"£[^£\s]+£"), "syntax.icon"),
    (re.compile(r"\\[nrt]"), "syntax.escape"),
    (re.compile(r"§."), "syntax.color_code"),
)


def syntax_ranges(text, offset=0):
    ranges = []
    for pattern, role in _PATTERNS:
        for match in pattern.finditer(text):
            text_format = QTextCharFormat()
            text_format.setForeground(theme.color(role))
            item = QTextLayout.FormatRange()
            item.start = offset + match.start()
            item.length = match.end() - match.start()
            item.format = text_format
            ranges.append(item)
    return ranges
