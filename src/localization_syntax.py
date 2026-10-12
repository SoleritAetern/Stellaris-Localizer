"""Shared line grammar and conservative, in-memory import repairs."""

import re
from dataclasses import asdict, dataclass


ENTRY_PATTERN = re.compile(
    r'^(?P<indent>\s*)(?P<key>[^\s:#"]+)\s*:(?P<version>\d*)\s*'
    # An unescaped quote closes the value only at a valid record ending.
    # Prefer a closing quote before an inline comment over quotes IN that
    # comment. Escape pairs remain literal and cannot supply a closing quote.
    r'"(?P<text>(?:\\[^\r\n]|[^\\\r\n])*?)"(?P<trailing>[ \t]*(?:\#[^\r\n]*)?)$',
)
PREFIX_PATTERN = re.compile(r'^\s*(?P<key>[^\s:#"]+)\s*:(?P<version>\d*)\s*(?P<value>.*)$')
VALUE_PATTERN = re.compile(r'"(?:\\.|[^"\\])*"')


def is_whitespace_text(text):
    """Empty/whitespace values are present values, not missing entries."""
    return isinstance(text, str) and (text == "" or text.isspace())


def escape_localization_text(text):
    """Serialize literal value text, including raw quotes and existing escapes.

    Retain the legacy public name. Quotes inside a value need no additional
    escaping; adding it would change the text returned by our lossless parser.
    """
    slashes = len(text) - len(text.rstrip("\\"))
    # A final odd backslash would otherwise consume the entry's closing quote.
    if slashes % 2:
        return text + "\\"
    return text


def disabled_entry_parts(raw):
    """Strictly one comment marker followed by a complete, unmodified entry.

    Return the original prefix and entry match; never normalize comment text.
    """
    marker = re.match(r'^[ \t]*#', raw)
    if marker is None:
        return None
    match, fix = parse_entry_line(raw[marker.end():])
    return (raw[:marker.end()], match) if match is not None and fix is None else None


@dataclass(frozen=True)
class AutoFix:
    code: str
    file: str
    line: int
    key: str
    source: str | None = None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, dict)
                or not all(isinstance(value.get(name), str) for name in ("code", "file", "key"))
                or type(value.get("line")) is not int or value["line"] < 1
                or value.get("source") not in (None, "old_en", "new_en", "ru")):
            return None
        return cls(**{name: value[name] for name in cls.__dataclass_fields__ if name in value})


def parse_entry_line(raw):
    """Return (match, fix_code). Never changes files, text escapes or comments.

    A missing quote with a possible comment, an incomplete escape, a trailing
    escaped quote, or ambiguous trailing whitespace is deliberately not repaired.
    Valid record endings take precedence over the conservative tail repair.
    Ambiguous multi-quote malformed lines are not truncated to the first pair.
    The caller retains the original raw line in its snapshot.
    """
    prefix = PREFIX_PATTERN.fullmatch(raw)
    if prefix is None or not prefix["value"].startswith('"'):
        return None, None
    value = prefix["value"]
    quoted = VALUE_PATTERN.match(value)
    # Keep the existing punctuation-tail repair: quoted comment text after
    # `"Text". # ...` must not become part of the value under the wider grammar.
    punctuation_comment = (quoted is not None and
                           re.match(r'[ \t]*[.,;][ \t]*#', value[quoted.end():]) is not None)
    match = None if punctuation_comment else ENTRY_PATTERN.fullmatch(raw)
    if match:
        return match, None
    if quoted is not None:
        tail = value[quoted.end():].partition("#")[0]
        if any(item.group() == '"' for item in re.finditer(r'\\.|"', tail)):
            return None, None
        normalized = raw[:prefix.start("value") + quoted.end()]
        return ENTRY_PATTERN.fullmatch(normalized), "DROPPED_ENTRY_TAIL"
    body = value[1:]
    trailing_slashes = len(body) - len(body.rstrip("\\"))
    if ("#" in body or (body and body[-1].isspace()) or body.endswith('"')
            or trailing_slashes % 2 or "\n" in body or "\r" in body):
        return None, None
    repaired = ENTRY_PATTERN.fullmatch(raw + '"')
    return (repaired, "CLOSED_ENTRY_QUOTE") if repaired else (None, None)
