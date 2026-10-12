"""Advisory, debounced file-count status for the wizard's previous EN path."""

import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import Event

from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtWidgets import QLabel

from . import theme
from .paths import previous_localisation, PreviousLocalisationNotFound


def _inspect(text, cancel, lifetime):
    """Match Project.create's previous-EN root and filename mask, without parsing."""
    if cancel.is_set() or lifetime.is_set():
        return None
    try:
        root = previous_localisation(text)
        # rglob suppresses scan errors; check root access explicitly so an
        # unavailable folder does not look like a valid folder with no EN.
        with os.scandir(root):
            pass
        count = 0
        for path in root.rglob("*_l_english.yml"):
            if cancel.is_set() or lifetime.is_set():
                return None
            # Only matching files are opened, without reading their contents.
            # A directory/broken link/unreadable match cannot be imported either.
            with path.open("rb"):
                pass
            count += 1
        return ("found" if count else "not_found"), count
    except PreviousLocalisationNotFound:
        return "no_localisation", 0
    except FileNotFoundError:
        return "missing", 0
    except (OSError, ValueError, RuntimeError):
        return "unavailable", 0


def _shutdown(executor, lifetime, *args):
    lifetime.set()
    executor.shutdown(wait=False, cancel_futures=True)


class PreviousEnglishStatus(QLabel):
    def __init__(self, field, parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="previous-en-check")
        self._lifetime = Event()
        self._cancel = Event()
        self._future = None
        self._text = ""
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._start)
        self._poll = QTimer(self)
        self._poll.setInterval(40)
        self._poll.timeout.connect(self._collect)
        self.destroyed.connect(partial(_shutdown, self._executor, self._lifetime))
        field.textChanged.connect(self.set_path)
        self.set_path(field.text())

    def set_path(self, text):
        self._debounce.stop()
        self._poll.stop()
        self._cancel.set()
        if self._future is not None:
            self._future.cancel()
        self._future = None
        self._cancel = Event()
        self._text = text.strip()
        self._render("checking" if self._text else "empty")
        if self._text and not self._lifetime.is_set():
            self._debounce.start()

    def _start(self):
        if self._lifetime.is_set():
            return
        self._future = self._executor.submit(_inspect, self._text, self._cancel, self._lifetime)
        self._poll.start()

    def _collect(self):
        if self._lifetime.is_set() or self._future is None or not self._future.done():
            return
        self._poll.stop()
        result = self._future.result()
        self._future = None
        if result is not None:
            self._render(*result)

    def stop(self, *args):
        self._debounce.stop()
        self._poll.stop()
        self._cancel.set()
        _shutdown(self._executor, self._lifetime)

    def _render(self, state, count=0):
        messages = {
            "empty": "",
            "checking": QCoreApplication.translate("previous_english_status", "Проверка английской локализации…"),
            "found": QCoreApplication.translate("previous_english_status", "✓ Английская локализация найдена · файлов: {0}").format(count),
            "not_found": QCoreApplication.translate("previous_english_status", "Английская локализация не найдена"),
            "missing": QCoreApplication.translate("previous_english_status", "! Папка или файл локализации не найдены"),
            "no_localisation": QCoreApplication.translate("paths", "Папка localisation не найдена"),
            "unavailable": QCoreApplication.translate("previous_english_status", "! Не удалось прочитать папку или файлы локализации"),
        }
        role = ("status.ready" if state == "found" else "text.secondary"
                if state in ("empty", "checking") else "status.warning_text")
        self.setStyleSheet(f"color: {theme.css(role)}; font-size: 11px;")
        self.setText(messages[state])
        self.setVisible(state != "empty")
