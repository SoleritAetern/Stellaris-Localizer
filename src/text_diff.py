import re
from difflib import SequenceMatcher

from .diff import similarity


WORD_REFINEMENT_SIMILARITY = 0.60


def tokenize(text):
    if not text:
        return []

    # Complete Stellaris constructs provide anchors, not indivisible diff units.
    # Incomplete constructs fall through to ordinary words/punctuation. Keep
    # whitespace as tokens too: inserting a space must not replace a whole word.
    return re.findall(r'\$[^$\r\n]+\$|£[^£\r\n]+£|\[[^\[\]\r\n]*\]|§[A-Za-z0-9!]|\w+|\s+|[^\w\s]', text)


def build_source_diff(old_text, new_text):
    if old_text is None and new_text is None:
        return []

    if old_text is None:
        return [
            {
                "type": "added",
                "text": new_text,
            }
        ]

    if new_text is None:
        return [
            {
                "type": "removed",
                "text": old_text,
            }
        ]

    if old_text == new_text:
        return [
            {
                "type": "equal",
                "text": new_text,
            }
        ]

    old_tokens = tokenize(old_text)
    new_tokens = tokenize(new_text)

    matcher = SequenceMatcher(
        None,
        old_tokens,
        new_tokens,
        autojunk=False,
    )

    segments = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():

        if tag == "equal":
            segments.append({
                "type": "equal",
                "text": "".join(
                    new_tokens[j1:j2]
                ),
            })

        elif tag == "delete":
            segments.append({
                "type": "removed",
                "text": "".join(
                    old_tokens[i1:i2]
                ),
            })

        elif tag == "insert":
            segments.append({
                "type": "added",
                "text": "".join(
                    new_tokens[j1:j2]
                ),
            })

        elif tag == "replace":
            # Refine changed segments only (including changed constructs).
            before = "".join(old_tokens[i1:i2])
            after = "".join(new_tokens[j1:j2])
            # Ordinary word pairs need more than a coincidental shared suffix
            # (Understood -> Goodbye). Keep constructs and punctuation on their
            # existing refinement path, including edits within $...$ / [...].
            if (re.fullmatch(r"\w+", before) and re.fullmatch(r"\w+", after)
                    and similarity(before, after) < WORD_REFINEMENT_SIMILARITY):
                segments.append({"type": "removed", "text": before})
                segments.append({"type": "added", "text": after})
                continue
            for operation, a, b, c, d in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
                if operation == "equal":
                    segments.append({"type": "equal", "text": after[c:d]})
                else:
                    if a != b:
                        segments.append({"type": "removed", "text": before[a:b]})
                    if c != d:
                        segments.append({"type": "added", "text": after[c:d]})

    return segments


def format_diff_for_terminal(segments):
    parts = []

    for segment in segments:
        segment_type = segment["type"]
        text = segment["text"]

        if segment_type == "removed":
            parts.append(
                f"[-{text}-]"
            )

        elif segment_type == "added":
            parts.append(
                f"[+{text}+]"
            )

        else:
            parts.append(text)

    return "".join(parts)
