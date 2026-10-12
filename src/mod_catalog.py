"""One cancellable, dialog-local mod catalog; workers never access Qt widgets."""

from concurrent.futures import CancelledError, ThreadPoolExecutor
from functools import partial
from queue import Empty, SimpleQueue
from threading import Event
from time import perf_counter

from PySide6.QtCore import QObject, QTimer, Signal

from .mod_discovery import inspect_mod, iter_mods, path_identity


def _shutdown(executor, lifetime, *args):
    lifetime.set()
    executor.shutdown(wait=False, cancel_futures=True)


def _discover(roots, messages, cancel, lifetime):
    cancelled = lambda: cancel.is_set() or lifetime.is_set()
    started = flushed = perf_counter()
    batch = []
    sources = set()
    try:
        for info in iter_mods(*roots, cancelled=cancelled, source_available=sources.add):
            batch.append(info)
            if len(batch) >= 12 or perf_counter() - flushed >= .08:
                messages.put(("batch", batch))
                batch = []
                flushed = perf_counter()
    except CancelledError:
        return
    if not cancelled():
        if batch:
            messages.put(("batch", batch))
        messages.put(("done", (perf_counter() - started, frozenset(sources))))


def _inspect(path, messages, cancel, lifetime):
    if cancel.is_set() or lifetime.is_set():
        return
    try:
        info = inspect_mod(path, manual=True, cancelled=lambda: cancel.is_set() or lifetime.is_set())
    except CancelledError:
        return
    except (OSError, ValueError, RuntimeError):
        from .mod_discovery import ModInfo
        info = ModInfo(path, path.name, None, None, "unavailable")
    if not cancel.is_set() and not lifetime.is_set():
        messages.put(("inspected", info))


class ModCatalog(QObject):
    reset = Signal()
    added = Signal(object)
    inspected = Signal(object)
    loadingChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = {}
        self.loading = False
        self.elapsed = None
        self.sources_available = None
        self.available_sources = frozenset()
        self.mods_directory = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mod-discovery")
        self._lifetime = Event()
        self._cancel = Event()
        self._messages = SimpleQueue()
        self._pending = set()
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._collect)
        # The teardown callback captures only Python resources, never `self`.
        self.destroyed.connect(partial(_shutdown, self._executor, self._lifetime))

    def start(self, mods_directory, workshop_directory=None):
        if self._lifetime.is_set():
            return
        self._cancel.set()
        self._cancel = Event()
        # Old generations write into their old queue, which UI no longer reads.
        self._messages = SimpleQueue()
        self._pending.clear()
        self.items.clear()
        self.loading = True
        self.elapsed = None
        self.sources_available = None
        self.available_sources = frozenset()
        self.mods_directory = mods_directory
        self.reset.emit()
        self.loadingChanged.emit(True)
        self._executor.submit(_discover, (mods_directory, workshop_directory),
                              self._messages, self._cancel, self._lifetime)
        self._timer.start()

    def inspect_path(self, path):
        identity = path_identity(path)
        if self._lifetime.is_set() or identity in self._pending:
            return
        self._pending.add(identity)
        self._executor.submit(_inspect, path, self._messages, self._cancel, self._lifetime)
        self._timer.start()

    def _collect(self):
        # Bounded delivery keeps popup input responsive even after a fast scan.
        for _ in range(4):
            try:
                kind, value = self._messages.get_nowait()
            except Empty:
                break
            if kind == "batch":
                # A later source may add membership to an already known path.
                fresh = []
                for item in value:
                    identity = path_identity(item.path)
                    if self.items.get(identity) != item:
                        self.items[identity] = item
                        fresh.append(item)
                if fresh:
                    self.added.emit(fresh)
            elif kind == "done":
                self.loading = False
                self.elapsed, self.available_sources = value
                self.sources_available = bool(self.available_sources)
                self.loadingChanged.emit(False)
            else:
                self._pending.discard(path_identity(value.path))
                self.inspected.emit(value)
        if not self.loading and not self._pending:
            self._timer.stop()

    def stop(self, *args):
        self._timer.stop()
        self._cancel.set()
        _shutdown(self._executor, self._lifetime)
