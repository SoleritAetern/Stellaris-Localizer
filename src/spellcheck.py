"""Offline Russian spelling, called only for the currently edited RU text."""

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import re

from .resources import asset_path
from .text_selection import construction_spans


_WORDS = re.compile(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*")


def russian_words(text):
    """Yield Python offsets; syntax parsing stays owned by construction_spans."""
    previous = 0
    for start, end in (*construction_spans(text), (len(text), len(text))):
        for match in _WORDS.finditer(text, previous, start):
            yield match.start(), match.end(), match.group()
        previous = end


class RussianDictionary:
    """One lazily loaded dictionary, accessed by one serialized worker."""

    def __init__(self):
        self.dictionary = None

    def load(self):
        if self.dictionary is None:
            from spylls.hunspell import Dictionary
            self.dictionary = Dictionary.from_files(str(asset_path("dictionaries", "ru_RU", "ru_RU")))
        return self.dictionary

    @lru_cache(maxsize=4096)
    def lookup(self, word):
        return self.load().lookup(word)

    def check(self, text, accepted, cancelled):
        errors = []
        for start, end, word in russian_words(text):
            if cancelled.is_set():
                break
            if word.casefold() not in accepted and not self.lookup(word):
                errors.append((start, end, word))
        return errors

    def suggestions(self, word, cancelled):
        if cancelled.is_set():
            return []
        result = []
        for suggestion in self.load().suggest(word):
            if cancelled.is_set():
                break
            if suggestion not in result:
                result.append(suggestion)
            if len(result) == 5:
                break
        return result


# No project data or background scan is stored here. Threads are started only
# when an active editor submits a debounced check or requests suggestions.
dictionary = RussianDictionary()
worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ru-spellcheck")
