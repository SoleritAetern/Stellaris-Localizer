"""Structured glossary documents; independent from translation entries/history."""

from PySide6.QtCore import QCoreApplication

import json
import re
import unicodedata
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QIODevice, QObject, QSaveFile, QStandardPaths, Signal

SCOPES = {"global": "Global / Stellaris", "project": "Project"}


@dataclass
class GlossarySection:
    section_id: str
    name: str
    scope: str
    parent_id: str | None = None


@dataclass
class GlossaryEntry:
    glossary_entry_id: str
    english: str
    russian: str = ""
    code: str | None = None
    alternatives: list[str] = field(default_factory=list)
    note: str = ""
    section_id: str | None = None
    scope: str = "project"


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


class GlossaryStore:
    def __init__(self, path, scope):
        self.path, self.scope = Path(path), scope
        self.sections, self.entries = {}, {}
        self.expected = None
        self.error = None
        self.load()

    def read(self):
        return self.path.read_bytes() if self.path.exists() else None

    def load(self):
        self.sections, self.entries, self.error = {}, {}, None
        try:
            self.expected = self.read()
            if self.expected is None:
                return
            data = json.loads(self.expected.decode("utf-8-sig"))
            if type(data.get("schema_version")) is not int or data.get("schema_version") != 1 or data.get("scope") != self.scope:
                raise ValueError(QCoreApplication.translate("glossary", "Неподдерживаемый формат глоссария"))
            sections = [GlossarySection(**item) for item in data["sections"]]
            entries = [GlossaryEntry(**item) for item in data["entries"]]
            self.validate(sections, entries)
            self.sections = {item.section_id: item for item in sections}
            self.entries = {item.glossary_entry_id: item for item in entries}
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            self.error = QCoreApplication.translate("glossary", "Не удалось загрузить глоссарий: {0}\n{1}").format(self.path, error)

    def validate(self, sections, entries):
        ids = set()
        nodes = {node.section_id: node for node in sections}
        for item in [*sections, *entries]:
            identity = item.section_id if isinstance(item, GlossarySection) else item.glossary_entry_id
            UUID(identity)
            if identity in ids or item.scope != self.scope:
                raise ValueError(QCoreApplication.translate("glossary", "Повторяющийся UUID или неверный scope"))
            ids.add(identity)
        for node in sections:
            if not isinstance(node.name, str) or not node.name.strip():
                raise ValueError(QCoreApplication.translate("glossary", "Не задано имя раздела"))
            seen, parent = {node.section_id}, node.parent_id
            while parent is not None:
                if parent in seen or parent not in nodes:
                    raise ValueError(QCoreApplication.translate("glossary", "Некорректная вложенность разделов"))
                seen.add(parent)
                parent = nodes[parent].parent_id
        for entry in entries:
            if not isinstance(entry.english, str) or not entry.english.strip():
                raise ValueError(QCoreApplication.translate("glossary", "Укажите English term"))
            if not all(isinstance(value, str) for value in (entry.russian, entry.note)):
                raise ValueError(QCoreApplication.translate("glossary", "Перевод и пояснение должны быть текстом"))
            if entry.code is not None and not isinstance(entry.code, str):
                raise ValueError(QCoreApplication.translate("glossary", "Code должен быть текстом"))
            if not isinstance(entry.alternatives, list) or not all(isinstance(a, str) for a in entry.alternatives):
                raise ValueError(QCoreApplication.translate("glossary", "Alternatives должны быть списком строк"))
            if entry.section_id is not None and entry.section_id not in nodes:
                raise ValueError(QCoreApplication.translate("glossary", "Раздел записи не найден"))

    def write(self, sections, entries):
        if self.error:
            raise ValueError(self.error + QCoreApplication.translate("glossary", "\nФайл не будет перезаписан."))
        self.validate(list(sections.values()), list(entries.values()))
        if self.read() != self.expected:
            raise ValueError(QCoreApplication.translate("glossary", "Глоссарий изменён другим экземпляром программы. Нажмите «Перечитать»."))
        payload = (json.dumps({"schema_version": 1, "scope": self.scope,
                               "sections": [asdict(s) for s in sections.values()],
                               "entries": [asdict(e) for e in entries.values()]},
                              ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        output = QSaveFile(str(self.path))
        output.setDirectWriteFallback(False)
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        if output.write(payload) != len(payload):
            output.cancelWriting()
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())
        self.expected = payload
        self.sections, self.entries = sections, entries


class Glossary(QObject):
    changed = Signal()

    def __init__(self, project_root, parent=None):
        super().__init__(parent)
        global_root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.stores = {"global": GlossaryStore(global_root / "glossary.json", "global"),
                       "project": GlossaryStore(Path(project_root) / "glossary.json", "project")}
        self.patterns = None

    def entries(self):
        return [entry for store in self.stores.values() for entry in store.entries.values()]

    def reload(self):
        for store in self.stores.values():
            store.load()
        self.notify()

    def notify(self):
        self.patterns = None
        self.changed.emit()

    def save_entry(self, entry, previous_scope=None):
        target = self.stores[entry.scope]
        entries = dict(target.entries, **{entry.glossary_entry_id: deepcopy(entry)})
        source = self.stores.get(previous_scope) if previous_scope != entry.scope else None
        if source and (source.error or source.read() != source.expected):
            raise ValueError(QCoreApplication.translate("glossary", "Исходный глоссарий недоступен или изменён. Нажмите «Перечитать»."))
        old_target = dict(target.entries)
        target.write(dict(target.sections), entries)
        if source:
            try:
                remaining = dict(source.entries)
                remaining.pop(entry.glossary_entry_id, None)
                source.write(dict(source.sections), remaining)
            except Exception:
                try:
                    target.write(dict(target.sections), old_target)
                finally:
                    self.notify()
                raise
        self.notify()

    def delete_entry(self, entry):
        store = self.stores[entry.scope]
        entries = dict(store.entries)
        entries.pop(entry.glossary_entry_id)
        store.write(dict(store.sections), entries)
        self.notify()

    def save_section(self, section):
        store = self.stores[section.scope]
        sections = dict(store.sections, **{section.section_id: section})
        store.write(sections, dict(store.entries))
        self.notify()

    def section_choices(self, scope):
        nodes = self.stores[scope].sections
        choices = [(None, QCoreApplication.translate("glossary", "Без раздела"))]
        def visit(parent, prefix=""):
            for node in sorted((s for s in nodes.values() if s.parent_id == parent), key=lambda s: s.name.casefold()):
                label = prefix + node.name
                choices.append((node.section_id, label))
                visit(node.section_id, label + " / ")
        visit(None)
        return choices

    def delete_section(self, scope, section_id):
        """Remove only the section; retain terms and lift its direct children."""
        store = self.stores[scope]
        deleted = store.sections[section_id]
        sections = dict(store.sections)
        del sections[section_id]
        for key, section in list(sections.items()):
            if section.parent_id == section_id:
                section = deepcopy(section)
                section.parent_id = deleted.parent_id
                sections[key] = section
        entries = dict(store.entries)
        for key, entry in list(entries.items()):
            if entry.section_id == section_id:
                entry = deepcopy(entry)
                entry.section_id = None
                entries[key] = entry
        store.write(sections, entries)
        self.notify()

    def match(self, english):
        if self.patterns is None:
            entries = self.entries()
            project_terms = {normalized(e.english) for e in entries if e.scope == "project"}
            self.patterns = []
            for entry in entries:
                term = normalized(entry.english)
                if not term or (entry.scope == "global" and term in project_terms):
                    continue
                pattern = (r"(?<!\w)" if term[0].isalnum() else "") + re.escape(term)
                pattern += r"(?!\w)" if term[-1].isalnum() else ""
                self.patterns.append((re.compile(pattern), entry))
        text = normalized(english)
        matches = [(found.start(), entry) for pattern, entry in self.patterns if (found := pattern.search(text))]
        return [entry for _, entry in sorted(matches, key=lambda pair: (pair[0], -len(pair[1].english)))]
