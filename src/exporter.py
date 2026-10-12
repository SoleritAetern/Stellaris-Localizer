
from PySide6.QtCore import QCoreApplication
import re
import shutil
import os
import tempfile
import sys
import time
from pathlib import Path

from src.localization_document import read_documents
from src.localization_syntax import parse_entry_line, disabled_entry_parts, is_whitespace_text, escape_localization_text
from src.paths import english_to_russian_path


def _replace_export_path(source, destination):
    """Retry short-lived Windows sharing/access failures; never remove a target."""
    for attempt in range(4):
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 3:
                raise
            time.sleep(0.05 * (2 ** attempt))


def _publication_error(output_path, error, *, recovery=None):
    if getattr(error, "winerror", None) in (5, 32, 33):
        message = (
            QCoreApplication.translate("exporter", "Не удалось заменить output_ru. Папка или один из файлов используется другой программой. Закройте Проводник/редактор, использующий эту папку, и повторите экспорт.")
        )
        if error.winerror == 5:
            message += QCoreApplication.translate("exporter", "\nWindows также может возвращать этот отказ при недостаточных правах доступа к папке.")
    else:
        message = QCoreApplication.translate("exporter", "Не удалось заменить output_ru. Новый результат не опубликован.")
    message += (QCoreApplication.translate("exporter", "\n\nПредыдущий результат сохранён: {0}").format(recovery) if recovery is not None
                else QCoreApplication.translate("exporter", "\n\nПредыдущий результат не изменён."))
    message += QCoreApplication.translate("exporter", "\nПапка: {0}\nДетали: {1}").format(output_path, error)
    return OSError(message)


def build_russian_localization(new_en_path, ru_index, output_path, *, documents=None, rows=(), source_prefix=""):
    """Build completely before replacing OUTPUT; retain the previous result on failure."""
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".sl-export-", dir=output_path.parent)).resolve()
    backup, staged = temporary / "previous", temporary / "result"
    preserve_backup = False
    try:
        result = _build_russian_localization(Path(new_en_path), ru_index, staged, documents=documents, rows=rows,
                                             source_prefix=source_prefix)
        if output_path.exists():
            try:
                _replace_export_path(output_path, backup)
            except OSError as error:
                raise _publication_error(output_path, error) from error
        try:
            _replace_export_path(staged, output_path)
        except OSError as publication_error:
            if backup.exists():
                try:
                    _replace_export_path(backup, output_path)
                except Exception as error:
                    preserve_backup = True
                    failure = _publication_error(output_path, publication_error, recovery=backup)
                    failure.args = (str(failure) + QCoreApplication.translate("exporter", "\nАвтоматическое восстановление не удалось: {0}").format(error),)
                    raise failure from error
            raise _publication_error(output_path, publication_error) from publication_error
        return result
    finally:
        if not preserve_backup:
            # Only our verified staging directory is ever removed recursively.
            if temporary.parent != output_path.parent or not temporary.name.startswith(".sl-export-"):
                raise OSError(QCoreApplication.translate("exporter", "Некорректная временная папка экспорта"))
            failed = sys.exc_info()[0] is not None
            try:
                shutil.rmtree(temporary)
            except OSError as error:
                # A cleanup failure must not hide the actual publish/rollback
                # error. Never touch OUTPUT to make staging cleanup succeed.
                if not failed:
                    raise OSError(QCoreApplication.translate("exporter", "Новый output_ru опубликован, но не удалось удалить временную папку: {0}\nЗакройте использующую её программу.\nДетали: {1}").format(temporary, error)) from error


def export_to_directory(new_en_path, ru_index, output_path, *, documents=None, rows=(), source_prefix="",
                        confirm_overwrite=None):
    """Standalone export: stage fully, replace exact output files, leave other files alone."""
    from .deployment import target_file, file_hash

    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".sl-export-", dir=output_path.parent)).resolve()
    staged, backup = temporary / "result", temporary / "previous"
    keep_backup = False
    try:
        result = _build_russian_localization(Path(new_en_path), ru_index, staged, documents=documents,
                                             rows=rows, source_prefix=source_prefix)
        names = [path.relative_to(staged).as_posix() for path in staged.rglob("*") if path.is_file()]
        expected = {}
        for name in names:
            destination = target_file(output_path, name)
            if destination.exists() and not destination.is_file():
                raise ValueError(QCoreApplication.translate("exporter", "Вместо файла экспорта найдена папка: {0}").format(name))
            expected[name] = file_hash(destination) if destination.exists() else None
        existing = [name for name, digest in expected.items() if digest is not None]
        if existing and (confirm_overwrite is None or not confirm_overwrite(existing)):
            return None
        written, moved, created = [], [], []

        def ensure_directory(path):
            missing = []
            while not path.exists():
                missing.append(path)
                path = path.parent
            for path in reversed(missing):
                path.mkdir()
                created.append(path)

        try:
            for name in names:
                destination = target_file(output_path, name)
                digest = file_hash(destination) if destination.is_file() else None
                if digest != expected[name] or (destination.exists() and not destination.is_file()):
                    raise ValueError(QCoreApplication.translate("exporter", "Файл изменился после выбора экспорта: {0}. Повторите экспорт.").format(name))
                ensure_directory(destination.parent)
                if digest is not None:
                    saved = backup / name
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    _replace_export_path(destination, saved)
                    moved.append((saved, destination))
                _replace_export_path(staged / name, destination)
                written.append(destination)
        except Exception as error:
            try:
                for path in reversed(written):
                    path.unlink()
                for saved, destination in reversed(moved):
                    _replace_export_path(saved, destination)
                for path in reversed(created):
                    path.rmdir()
            except Exception as rollback_error:
                keep_backup = True
                raise OSError(QCoreApplication.translate("exporter", "Экспорт не завершён. Резервные файлы для восстановления: {0}\nОшибка публикации: {1}\nОшибка отката: {2}").format(backup, error, rollback_error)) from error
            raise OSError(QCoreApplication.translate("exporter", "Не удалось опубликовать экспорт. Предыдущие файлы сохранены.\nПроверьте права доступа и закройте программы, использующие папку.\n{0}\n{1}").format(output_path, error)) from error
        return result
    finally:
        if not keep_backup:
            failed = sys.exc_info()[0] is not None
            try:
                shutil.rmtree(temporary)
            except OSError as error:
                if not failed:
                    raise OSError(QCoreApplication.translate("exporter", "Публикация завершена или отменена, но временная папка занята: {0}\n{1}").format(temporary, error)) from error


def _build_russian_localization(new_en_path, ru_index, output_path, *, documents=None, rows=(), source_prefix=""):
    missing_keys = []
    output_path.mkdir(parents=True, exist_ok=False)
    documents = documents if documents is not None else read_documents(new_en_path)
    by_id = {row["entry_id"]: row for row in rows if row.get("entry_id")}
    english_files = []
    outputs = set()
    for document in documents:
        relative_path = Path(document.file)
        if relative_path.is_absolute() or relative_path.drive or ".." in relative_path.parts:
            raise ValueError(QCoreApplication.translate("exporter", "Документ выходит за пределы папки экспорта"))
        english_files.append(new_en_path / relative_path)
        source_relative = Path(source_prefix) / relative_path
        if source_relative.is_absolute() or source_relative.drive or ".." in source_relative.parts:
            raise ValueError(QCoreApplication.translate("exporter", "Путь localisation выходит за пределы экспорта"))
        russian_relative_path = english_to_russian_path(source_relative)
        identity = russian_relative_path.as_posix().casefold()
        if identity in outputs:
            raise ValueError(QCoreApplication.translate("exporter", "Несколько исходных файлов преобразуются в один RU-файл: {0}").format(russian_relative_path))
        outputs.add(identity)
        russian_file = output_path / russian_relative_path
        russian_file.parent.mkdir(parents=True, exist_ok=True)
        output_lines = []
        for element in document.elements:
            raw = element.raw
            if element.kind == "HEADER":
                output_lines.append(re.sub(r"l_english(?=:)", "l_russian", raw, count=1))
                continue
            if element.kind not in ("ENTRY", "DISABLED_ENTRY"):
                output_lines.append(raw)
                continue
            body = raw.rstrip("\r\n")
            ending = raw[len(body):]
            disabled = disabled_entry_parts(body) if element.kind == "DISABLED_ENTRY" else None
            match = disabled[1] if disabled else parse_entry_line(body)[0]
            if match is None:
                output_lines.append(raw)
                continue
            row = by_id.get(element.entry_id)
            if row is not None and row.get("section") == "REMOVED":
                continue
            candidates = ru_index.get(match["key"], [])
            translation = row["ru_text"] if row is not None else (candidates[0]["text"] if candidates else None)
            if is_whitespace_text(match["text"]):
                translation = match["text"]
            if translation is None:
                translation = match["text"]
                missing_keys.append({"key": match["key"], "file": russian_relative_path})
            trailing = match["trailing"]
            if row is not None and "ru_comment" in row:
                trailing = (row.get("ru_comment_trailing") or "") if row["ru_comment"] is not None else ""
            elif candidates and "comment" in candidates[0]:
                trailing = candidates[0].get("comment_trailing") or ""
            # Versions are insignificant; preserve indentation, comments and line endings.
            prefix = disabled[0] if disabled else ""
            translation = escape_localization_text(translation)
            output_lines.append(prefix + f'{match["indent"]}{match["key"]}: "{translation}"' + trailing + ending)
        with russian_file.open("w", encoding="utf-8-sig", newline="") as stream:
            stream.writelines(output_lines)
    return english_files, missing_keys


def build_ru_index_from_rows(rows):
    ru_index = {}

    for row in rows:
        if row["new_text"] is None:
            continue

        if row["ru_text"] is None:
            continue

        key = row["key"]

        ru_index[key] = [
            {
                "key": key,
                "text": row["ru_text"],
                **({"comment": row["ru_comment"], "comment_trailing": row.get("ru_comment_trailing", "")}
                   if "ru_comment" in row else {}),
            }
        ]

    return ru_index
