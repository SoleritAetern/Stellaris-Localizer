"""Mod metadata discovery. Never reads or parses localization file contents."""

from concurrent.futures import CancelledError
from dataclasses import dataclass, replace
import os
from pathlib import Path
import re

from .paths import mod_localisation

LOCAL = "LOCAL"
WORKSHOP = "WORKSHOP"


@dataclass(frozen=True)
class ModInfo:
    path: Path
    name: str
    english: int | None
    russian: int | None
    state: str = "ready"
    sources: frozenset[str] = frozenset()


def path_identity(path):
    # Callers pass resolved paths; filtering/selection must not perform disk I/O.
    return os.path.normcase(str(path))


def _metadata_value(file, field):
    try:
        text = Path(file).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError, ValueError):
        return None
    match = re.search(r'^[ \t]*' + re.escape(field) + r'[ \t]*=[ \t]*"((?:\\[^\r\n]|[^"\\\r\n])*)"',
                      text, re.MULTILINE)
    if match:
        return re.sub(r'\\(["\\])', r'\1', match.group(1)).strip() or None
    return None


def descriptor_name(root):
    """Strict automatic name; folder-name fallback belongs only to manual UI."""
    return _metadata_value(Path(root) / "descriptor.mod", "name")


def _check_cancel(cancelled):
    if cancelled():
        raise CancelledError()


def inspect_mod(path, *, manual=False, cancelled=lambda: False, name=None):
    """Read one descriptor and count the existing EN/RU filename masks."""
    _check_cancel(cancelled)
    root = Path(path).resolve()
    if not root.is_dir():
        return ModInfo(root, root.name, None, None, "unavailable") if manual else None
    name = name or descriptor_name(root)
    if not name and not manual:
        return None
    name = name or root.name
    _check_cancel(cancelled)
    try:
        # Reuse the same mod-root/containment boundary as source import.
        localisation = mod_localisation(root)
    except (OSError, ValueError):
        # A missing folder is different from an unsafe link outside the mod.
        if not (root / "localisation").exists():
            return ModInfo(root, name, 0, 0)
        return ModInfo(root, name, None, None, "unreadable")
    try:
        counts = []
        boundary = localisation.resolve()
        for pattern in ("*_l_english.yml", "*_l_russian.yml"):
            count = 0
            for file in localisation.rglob(pattern):
                _check_cancel(cancelled)
                if file.is_file() and file.resolve().is_relative_to(boundary):
                    count += 1
            counts.append(count)
        return ModInfo(root, name, *counts)
    except (OSError, RuntimeError):
        return ModInfo(root, name, None, None, "unreadable")


def _children(root, source_available=None):
    if root is not None:
        try:
            children = iter(Path(root).iterdir())
            first = next(children, None)
            # A readable but empty root is configured, even without descriptors.
            if source_available is not None:
                source_available(root)
            if first is not None:
                yield first
            yield from children
        except (OSError, ValueError):
            return


def candidate_mod_paths(mods_directory, workshop_directory=None, *, cancelled=lambda: False,
                        source_available=None):
    """Immediate mod folders in the two explicit roots; no inferred sources."""
    for root, workshop in ((mods_directory, False), (workshop_directory, True)):
        for child in _children(root, source_available):
            _check_cancel(cancelled)
            try:
                if (not workshop or (child.name.isascii() and child.name.isdecimal())) and child.is_dir():
                    yield child
            except OSError:
                continue


def _valid_mods(mods_directory, workshop_directory=None, *, cancelled=lambda: False,
                source_available=None):
    """Shared metadata-only admission rule for Settings and the catalog."""
    seen = set()
    for candidate in candidate_mod_paths(mods_directory, workshop_directory, cancelled=cancelled,
                                         source_available=source_available):
        _check_cancel(cancelled)
        try:
            root = candidate.resolve()
            identity = path_identity(root)
            if identity in seen:
                continue
            seen.add(identity)
            name = descriptor_name(root)
            if name:
                yield root, name
        except (OSError, ValueError, RuntimeError):
            # One broken descriptor/path must not discard the other mods.
            continue


def iter_mods(mods_directory, workshop_directory=None, *, cancelled=lambda: False,
              source_available=None):
    # Preserve provenance even if the same physical mod is in both explicit
    # sources. Its localization files are inspected only once per discovery.
    inspected = {}
    for source, roots in ((LOCAL, (mods_directory, None)),
                          (WORKSHOP, (None, workshop_directory))):
        available = (lambda root, source=source: source_available(source)) if source_available is not None else None
        for root, name in _valid_mods(*roots, cancelled=cancelled, source_available=available):
            try:
                identity = path_identity(root)
                info = inspected.get(identity)
                if info is None:
                    info = inspect_mod(root, name=name, cancelled=cancelled)
                if info is not None:
                    info = replace(info, sources=info.sources | {source})
                    inspected[identity] = info
                    yield info
            except (OSError, ValueError, RuntimeError):
                continue


def discover_mods(directory, *, workshop=False):
    """Count valid descriptors without walking localization files."""
    roots = (None, directory) if workshop else (directory, None)
    return sorted((root for root, _ in _valid_mods(*roots)), key=lambda root: root.name.casefold())
