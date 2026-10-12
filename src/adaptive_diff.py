"""Presentation policy only: accepted baseline and exact diff remain unchanged."""

from difflib import SequenceMatcher
from functools import lru_cache

from src.text_selection import atomic_tokens


LARGE_MIN_CHANGED_TOKENS = 8
LARGE_TOKEN_FRACTION = 0.45
LARGE_CHARACTER_FRACTION = 0.35
FRAGMENTED_MIN_HUNKS = 6
FRAGMENTED_TOKEN_FRACTION = 0.30
FRAGMENTED_CHARACTER_FRACTION = 0.25


@lru_cache(maxsize=2048)
def is_large_change(segments, current_text):
    """Called on RowLayout cache misses, not in a full-model pass."""
    if current_text is None:
        return False
    old = "".join(text for kind, text in segments if kind != "added")
    if not old or old == current_text:
        return False
    old_tokens, new_tokens = atomic_tokens(old), atomic_tokens(current_text)
    changed, hunks = 0, 0
    for kind, a, b, c, d in SequenceMatcher(None, old_tokens, new_tokens).get_opcodes():
        if kind != "equal":
            changed += b - a + d - c
            hunks += 1
    if changed < LARGE_MIN_CHANGED_TOKENS:
        return False  # A long variable/icon/expression alone is not a rewrite.
    token_fraction = changed / max(1, len(old_tokens) + len(new_tokens))
    character_fraction = sum(len(text) for kind, text in segments if kind != "equal") / max(1, len(old) + len(current_text))
    return ((token_fraction >= LARGE_TOKEN_FRACTION and character_fraction >= LARGE_CHARACTER_FRACTION)
            or (hunks >= FRAGMENTED_MIN_HUNKS and token_fraction >= FRAGMENTED_TOKEN_FRACTION
                and character_fraction >= FRAGMENTED_CHARACTER_FRACTION))
