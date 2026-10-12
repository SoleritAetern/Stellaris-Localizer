"""Text predicates for Translation View, independent of row status semantics."""

from functools import lru_cache
from unicodedata import name

from .text_selection import construction_spans


def _contains_latin_letters(text):
    # No word-length or language heuristic: even one letter is significant.
    return any(char.isalpha() and (char.isascii() or "LATIN" in name(char, ""))
               for char in text)


@lru_cache(maxsize=4096)
def has_latin_text(text):
    """Detect Latin letters outside the existing complete technical tokens.

    Cache by RU content, so edits/undo cannot reuse a stale result. This is
    called by the filter proxy, never by painting or row materialization.
    """
    previous = 0
    for start, end in construction_spans(text):
        if _contains_latin_letters(text[previous:start]):
            return True
        previous = end
    return _contains_latin_letters(text[previous:])
