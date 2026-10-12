"""On-demand Vanilla reference with a disposable, application-wide JSON cache."""

from PySide6.QtCore import QCoreApplication
import hashlib
import json
import os
from pathlib import Path
import tempfile
from time import perf_counter

from PySide6.QtCore import QStandardPaths

from .parser import parse_localization_folder, build_key_index
from .reference import build_reference_rows


# Bump whenever the cache format OR parser/reference semantics change.
CACHE_VERSION = 2


class VanillaReference:
    def __init__(self, root, *, cache_directory=None):
        self.root = Path(root)
        self.cache_directory = cache_directory
        self.timings = {}
        self.cache_status = "not_requested"
        self.cache_warning = None

    def manifest(self):
        if not self.root.is_dir():
            raise OSError(QCoreApplication.translate("vanilla_reference", "Папка недоступна: {0}").format(self.root))
        result = []
        for language in ("english", "russian"):
            for path in sorted(self.root.rglob(f"*_l_{language}.yml")):
                info = path.stat()
                result.append([path.relative_to(self.root).as_posix(), info.st_size, info.st_mtime_ns])
        return result

    @staticmethod
    def encode(entries):
        return [[e["key"], e["text"], e["relative_path"].as_posix(), e["line"]] for e in entries]

    @staticmethod
    def decode(records):
        if not isinstance(records, list):
            raise ValueError("Invalid Vanilla cache records")
        paths, entries = {}, []
        for record in records:
            if (not isinstance(record, list) or len(record) != 4
                    or not all(isinstance(value, str) for value in record[:3])
                    or type(record[3]) is not int or record[3] < 1):
                raise ValueError("Invalid Vanilla cache entry")
            key, text, name, line = record
            if name not in paths:
                paths[name] = Path(name)
            entries.append({"key": key, "text": text, "relative_path": paths[name], "line": line})
        return entries

    def write_cache(self, path, document):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.stem + "-", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load(self):
        started = perf_counter()
        self.timings = {}
        self.cache_warning = None
        self.root = self.root.resolve()
        root_id = os.path.normcase(str(self.root))
        directory = self.cache_directory
        if directory is None:
            directory = Path(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppLocalDataLocation)) / "cache" / "vanilla"
        path = Path(directory) / (hashlib.sha256(root_id.encode("utf-8")).hexdigest() + ".json")
        manifest = self.manifest()
        self.timings["manifest_ms"] = (perf_counter() - started) * 1000
        begin = perf_counter()
        en = ru = None
        self.cache_status = "miss"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(document, dict) and document.get("cache_version") == CACHE_VERSION
                    and document.get("vanilla_root") == root_id and document.get("file_manifest") == manifest):
                en = self.decode(document["parsed_data"]["english"])
                ru = self.decode(document["parsed_data"]["russian"])
                self.cache_status = "hit"
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            en = ru = None  # Missing, stale or corrupt cache is never fatal.
        self.timings["cache_load_ms"] = (perf_counter() - begin) * 1000
        if en is None or ru is None:
            begin = perf_counter()
            _, en = parse_localization_folder(self.root, "english", collect_diagnostics=False)
            _, ru = parse_localization_folder(self.root, "russian", collect_diagnostics=False)
            self.timings["parse_ms"] = (perf_counter() - begin) * 1000
            begin = perf_counter()
            # Do not publish a cache built across an ongoing game update.
            if self.manifest() == manifest:
                try:
                    self.write_cache(path, {"cache_version": CACHE_VERSION, "vanilla_root": root_id,
                                           "file_manifest": manifest,
                                           "parsed_data": {"english": self.encode(en), "russian": self.encode(ru)}})
                except (OSError, UnicodeError, ValueError) as error:
                    self.cache_warning = str(error)  # Reference remains usable without a cache.
            self.timings["cache_write_ms"] = (perf_counter() - begin) * 1000
        begin = perf_counter()
        rows = build_reference_rows(en, build_key_index(ru))
        self.timings["reference_rows_ms"] = (perf_counter() - begin) * 1000
        self.timings["total_ms"] = (perf_counter() - started) * 1000
        return rows
