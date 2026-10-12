"""Exact Mod-first lookup, with nonblocking index preparation for hover UI."""

from PySide6.QtCore import QCoreApplication

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from weakref import ref

from PySide6.QtCore import QTimer

from .localization_syntax import parse_entry_line


class LookupRequest:
    def __init__(self, key, callback):
        self.key, self.callback = key, callback

    def cancel(self):
        self.callback = None

    def finish(self, scope, definitions):
        callback, self.callback = self.callback, None
        if callback is not None:
            callback(scope, definitions)


class ExactReferences:
    def __init__(self, window):
        self._window_ref = ref(window)
        self.project = self.vanilla = None
        self._requests = []
        self._project_job = self._vanilla_job = None
        self._owns_vanilla_load = False
        self._disposed = False
        self._worker = ThreadPoolExecutor(max_workers=2, thread_name_prefix="exact-lookup")
        self._poll_timer = QTimer(window)
        self._poll_timer.setInterval(20)
        self._poll_timer.timeout.connect(self._poll)

    @property
    def window(self):
        return self._window_ref() if self._window_ref is not None else None

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self._poll_timer.stop()
        self._poll_timer.timeout.disconnect(self._poll)
        for request in self._requests:
            request.cancel()
        self._requests.clear()
        self._worker.shutdown(wait=False, cancel_futures=True)
        self._window_ref = None
        self.project = self.vanilla = None

    @staticmethod
    def _project_index(rows, documents):
        project = defaultdict(list)
        by_id = {row.get("entry_id"): row for row in rows}
        if documents:
            for document in documents:
                for element in document.elements:
                    if element.kind != "ENTRY":
                        continue
                    entry = parse_entry_line(element.raw.rstrip("\r\n"))[0]
                    if entry is not None:
                        project[entry["key"]].append((entry["text"], document.file, element.line,
                                                      by_id.get(element.entry_id)))
        else:
            for row in rows:
                if row.get("section") == "CURRENT" and row.get("kind", "ENTRY") == "ENTRY":
                    project[row["key"]].append((row["new_text"], row.get("new_file"), row.get("new_line"), row))
        return project

    @staticmethod
    def _vanilla_index(loader, rows):
        # Pure data only in the worker. Qt models are published on the UI thread.
        rows = loader() if loader is not None else rows
        index = defaultdict(list)
        for row in rows:
            index[row["key"]].append((row["new_text"], row.get("new_file"), row.get("new_line"), row))
        return rows, index

    def build(self):
        self.project = self._project_index(self.window.model.rows, self.window.documents)
        self.vanilla = None

    def lookup(self, key):
        """Synchronous compatibility API; compact popups use request()."""
        if self.project is None:
            self.build()
        definitions = self.project.get(key)
        if definitions:
            return "Project", definitions
        self.window.ensure_vanilla()
        if not self.window._vanilla_loaded:
            self.vanilla = None
            return "Vanilla", []
        if self.vanilla is None:
            _, self.vanilla = self._vanilla_index(None, self.window.vanilla_rows)
        return "Vanilla", self.vanilla.get(key, [])

    def request(self, key, callback):
        request = LookupRequest(key, callback)
        self._requests.append(request)
        self._poll_timer.start()
        return request

    def _poll(self):
        if self._disposed or self.window is None:
            return
        window = self.window
        self._requests = [request for request in self._requests if request.callback is not None]
        if self.project is None and self._requests and self._project_job is None:
            self._project_job = self._worker.submit(
                self._project_index, tuple(window.model.rows), tuple(window.documents))
        if self._project_job is not None and self._project_job.done():
            job, self._project_job = self._project_job, None
            try:
                self.project = job.result()
            except Exception as error:
                window.statusBar().showMessage(QCoreApplication.translate("exact_references", "Поиск определения недоступен: {0}").format(error), 15000)
                for request in list(self._requests):
                    request.finish("Project", [])
        if self.project is not None:
            # Return ALL mod definitions, including ambiguity. Never wait for
            # (or start) Vanilla when an exact definition exists in the mod.
            for request in list(self._requests):
                definitions = self.project.get(request.key)
                if definitions:
                    request.finish("Project", definitions)
        if self._vanilla_job is not None and self._vanilla_job.done():
            job, self._vanilla_job = self._vanilla_job, None
            try:
                rows, self.vanilla = job.result()
                if self._owns_vanilla_load and not window._vanilla_loaded:
                    window.apply_vanilla_rows(rows)
            except Exception as error:
                self.vanilla = None  # The next request may retry a failed load.
                window.statusBar().showMessage(QCoreApplication.translate("exact_references", "Справочник Stellaris недоступен: {0}").format(error), 15000)
                for request in list(self._requests):
                    request.finish("Vanilla", [])
            finally:
                if self._owns_vanilla_load:
                    window._vanilla_loading = False
                    window._vanilla_lookup_future = None
                    self._owns_vanilla_load = False
        pending = [request for request in self._requests if request.callback is not None]
        if self.project is not None and pending:
            if self.vanilla is not None:
                for request in pending:
                    request.finish("Vanilla", self.vanilla.get(request.key, []))
            elif self._vanilla_job is None and not window._vanilla_loading:
                self._owns_vanilla_load = not window._vanilla_loaded
                if self._owns_vanilla_load:
                    window._vanilla_loading = True
                self._vanilla_job = self._worker.submit(
                    self._vanilla_index, window._vanilla_loader if self._owns_vanilla_load else None,
                    window.vanilla_rows)
                if self._owns_vanilla_load:
                    window._vanilla_lookup_future = self._vanilla_job
        self._requests = [request for request in self._requests if request.callback is not None]
        if not self._requests and self._project_job is None and self._vanilla_job is None:
            self._poll_timer.stop()
