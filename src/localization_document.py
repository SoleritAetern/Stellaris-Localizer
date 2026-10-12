"""Lossless NEW EN document structure, separate from translation entries."""

from PySide6.QtCore import QCoreApplication

import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from src.localization_syntax import parse_entry_line, disabled_entry_parts


COMMENT_FIELDS = ("new_comment", "new_comment_trailing", "ru_comment", "ru_comment_trailing")


def inline_comment(trailing):
    return trailing.split("#", 1)[1] if "#" in trailing else None


def initial_comments(new_entry, ru_entry):
    new_tail = new_entry.get("trailing", "") if new_entry else ""
    ru_tail = ru_entry.get("trailing", "") if ru_entry else ""
    return {"new_comment": inline_comment(new_tail), "new_comment_trailing": new_tail,
            "ru_comment": inline_comment(ru_tail), "ru_comment_trailing": ru_tail}


@dataclass(frozen=True)
class DocumentElement:
    kind: str
    raw: str
    line: int
    entry_id: str | None = None


@dataclass
class LocalizationDocument:
    file: str
    elements: list[DocumentElement]

    def to_dict(self):
        return {"file": self.file, "elements": [asdict(element) for element in self.elements]}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get("file"), str):
            raise ValueError(QCoreApplication.translate("localization_document", "Некорректный документ локализации"))
        path = Path(value["file"])
        if path.is_absolute() or path.drive or ".." in path.parts:
            raise ValueError(QCoreApplication.translate("localization_document", "Документ выходит за пределы проекта"))
        elements = []
        for item in value["elements"]:
            if (item["kind"] not in ("ENTRY", "DISABLED_ENTRY", "DISABLED_SHADOW", "COMMENT", "BLANK", "HEADER", "RAW")
                    or not isinstance(item["raw"], str) or type(item["line"]) is not int or item["line"] < 1
                    or (item.get("entry_id") is not None and not isinstance(item["entry_id"], str))):
                raise ValueError(QCoreApplication.translate("localization_document", "Некорректный структурный элемент"))
            elements.append(DocumentElement(item["kind"], item["raw"], item["line"], item.get("entry_id")))
        return cls(path.as_posix(), elements)


def read_documents(root, rows=()):
    by_location = {(str(Path(row["new_file"])), row["new_line"]): row.get("entry_id")
                   for row in rows if row.get("new_file") is not None}
    documents = []
    for path in sorted(Path(root).rglob("*_l_english.yml")):
        relative = path.relative_to(root)
        elements = []
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for line, raw in enumerate(stream, 1):
                body = raw.rstrip("\r\n")
                if not body.strip():
                    kind = "BLANK"
                elif body.lstrip().startswith("#"):
                    kind = "DISABLED_ENTRY" if disabled_entry_parts(body) else "COMMENT"
                elif re.fullmatch(r'\s*l_[A-Za-z][A-Za-z0-9_]*:\s*(?:#.*)?', body):
                    kind = "HEADER"
                elif parse_entry_line(body)[0] is not None:
                    kind = "ENTRY"
                else:
                    kind = "RAW"
                elements.append(DocumentElement(kind, raw, line, by_location.get((str(relative), line))
                                                if kind in ("ENTRY", "DISABLED_ENTRY") else None))
        documents.append(LocalizationDocument(relative.as_posix(), elements))
    return classify_disabled_shadows(documents)


def classify_disabled_shadows(documents):
    active = set()
    for document in documents:
        for element in document.elements:
            if element.kind == "ENTRY":
                match = parse_entry_line(element.raw.rstrip("\r\n"))[0]
                if match is not None:
                    active.add(match["key"])
    result = []
    for document in documents:
        elements = []
        for element in document.elements:
            if element.kind in ("DISABLED_ENTRY", "DISABLED_SHADOW"):
                parts = disabled_entry_parts(element.raw.rstrip("\r\n"))
                if parts and parts[1]["key"] in active:
                    element = replace(element, kind="DISABLED_SHADOW", entry_id=None)
            elements.append(element)
        result.append(LocalizationDocument(document.file, elements))
    return result


def hydrate_comments(rows, documents, ru_root=None):
    """Backward-compatible metadata import; never change a working translation."""
    source_lines = {(document.file, element.line): element.raw for document in documents for element in document.elements}
    ru_lines = {}
    for row in rows:
        if all(name in row for name in COMMENT_FIELDS):
            continue
        new_raw = source_lines.get((Path(row["new_file"]).as_posix(), row["new_line"]), "") if row.get("new_file") else ""
        body = new_raw.rstrip("\r\n")
        disabled = disabled_entry_parts(body)
        new_match = disabled[1] if disabled else parse_entry_line(body)[0]
        ru_match = None
        if ru_root is not None and row.get("ru_file") and row.get("ru_line"):
            path = Path(ru_root) / row["ru_file"]
            if path not in ru_lines:
                with path.open("r", encoding="utf-8-sig", newline="") as stream:
                    ru_lines[path] = stream.readlines()
            if row["ru_line"] <= len(ru_lines[path]):
                body = ru_lines[path][row["ru_line"] - 1].rstrip("\r\n")
                disabled = disabled_entry_parts(body)
                ru_match = disabled[1] if disabled else parse_entry_line(body)[0]
        values = initial_comments({"trailing": new_match["trailing"]} if new_match else None,
                                  {"trailing": ru_match["trailing"]} if ru_match else None)
        for name, value in values.items():
            row.setdefault(name, value)


def structural_display(document):
    """Conservative decoration grouping; raw elements are never mutated."""
    elements = document.elements
    i = 0
    while i < len(elements):
        element = elements[i]
        title = None
        count = 1
        if element.kind == "COMMENT":
            single = re.fullmatch(r'\s*#{4,}\s*([^#\r\n]+?)\s*#{4,}\s*', element.raw)
            if single:
                title = single[1].strip()
            elif (i + 2 < len(elements) and re.fullmatch(r'\s*#{4,}\s*', element.raw)
                  and elements[i + 1].kind == elements[i + 2].kind == "COMMENT"
                  and re.fullmatch(r'\s*#{4,}\s*', elements[i + 2].raw)):
                middle = re.fullmatch(r'\s*#\s+([^#\r\n]+?)\s*', elements[i + 1].raw)
                if middle:
                    title, count = middle[1].strip(), 3
        yield element, title, "".join(item.raw for item in elements[i:i + count])
        i += count
