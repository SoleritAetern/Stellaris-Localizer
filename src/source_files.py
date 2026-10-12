"""Read immutable source context for diagnostics; no editable file workflow."""

from PySide6.QtCore import QCoreApplication

from pathlib import Path


def read_snapshot_line(project, diagnostic):
    relative = Path(diagnostic.file)
    if not diagnostic.file or relative.is_absolute():
        raise OSError(QCoreApplication.translate("source_files", "Недоступный путь снимка"))
    roots = {name: folder.resolve() for name in ("new_en", "old_en", "initial_ru")
             if (folder := project.snapshot_path(name)) is not None}
    candidate = (project.root / relative).resolve()
    if not any(candidate.is_relative_to(root) for root in roots.values()):
        name = {"new_en": "new_en", "old_en": "old_en", "ru": "initial_ru"}.get(diagnostic.source)
        root = roots.get(name)
        if root is None:
            raise OSError(QCoreApplication.translate("source_files", "Снимок этого источника отсутствует"))
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            raise OSError(QCoreApplication.translate("source_files", "Путь выходит за пределы снимка"))
    with candidate.open("r", encoding="utf-8-sig", newline="") as stream:
        for line, raw in enumerate(stream, 1):
            if line == diagnostic.line:
                return raw.rstrip("\r\n")
    raise OSError(QCoreApplication.translate("source_files", "Строка отсутствует в снимке"))
