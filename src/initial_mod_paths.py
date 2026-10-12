"""One-time root suggestions. Never used by the ordinary mod catalog."""

import os
from pathlib import Path

from .mod_discovery import _children, _metadata_value, _valid_mods, path_identity


def _steam_locations():
    # Bounded hints only: no disk traversal or libraryfolders scan.
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                value, _ = winreg.QueryValueEx(key, "SteamPath")
                if isinstance(value, str) and value:
                    yield Path(value)
        except OSError:
            pass
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        value = os.environ.get(variable)
        if value:
            yield Path(value) / "Steam"


def _workshop_hint(path):
    if path is not None:
        path = Path(path)
        for parent in (path, *path.parents):
            if parent.name.casefold() == "steamapps":
                return parent / "workshop" / "content" / "281990"
    return None


def detect_initial_roots(documents, local_root, game_root, *, find_local, find_workshop):
    """Return only roots with a named descriptor; stop at the first valid mod."""
    standard_local = Path(documents) / "Paradox Interactive" / "Stellaris" / "mod"
    local = None
    if find_local and next(_valid_mods(standard_local), None) is not None:
        local = standard_local.resolve()
    if not find_workshop:
        return local, None

    def candidates():
        yield _workshop_hint(game_root)
        # Launcher references are hints here only, not catalog results.
        for root in (local_root, standard_local):
            for file in _children(root):
                if file.suffix.casefold() != ".mod":
                    continue
                value = _metadata_value(file, "path")
                if value:
                    try:
                        path = Path(value).expanduser()
                        yield _workshop_hint(path if path.is_absolute() else file.parent / path)
                    except (OSError, ValueError, RuntimeError):
                        continue
        for steam in _steam_locations():
            yield steam / "steamapps" / "workshop" / "content" / "281990"

    seen = set()
    for candidate in candidates():
        if candidate is None:
            continue
        try:
            root = candidate.resolve()
            identity = path_identity(root)
            if identity in seen:
                continue
            seen.add(identity)
            if next(_valid_mods(None, root), None) is not None:
                return local, root
        except (OSError, ValueError, RuntimeError):
            continue
    return local, None
