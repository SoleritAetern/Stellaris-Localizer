"""Optional, project-local UI preferences. Never stores translation data."""

from PySide6.QtCore import QCoreApplication, QT_TRANSLATE_NOOP

import json
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QIODevice, QSaveFile, QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QAbstractItemView, QTreeWidgetItemIterator, QStyle


SCHEMA_VERSION = 1
# Only modes exposed by the permanent panel are restored. Legacy source-status
# modes remain supported by the proxy, but must not become invisible filters.
FILTERS = {"ALL", "ATTENTION", "ENGLISH_TEXT"}
EXPLORER_ROLE = int(Qt.ItemDataRole.UserRole) + 1

# Keep built-in saved titles in the original language; translate only when
# restoring their labels. File names and other stored titles stay untouched.
_GLOSSARY_TITLE = QT_TRANSLATE_NOOP("ui_state", "Глоссарий")
_ALL_FILES_TITLE = QT_TRANSLATE_NOOP("ui_state", "Все файлы")


def _builtin_tab_title(data):
    if data.get("kind") == "glossary":
        return _GLOSSARY_TITLE
    if data.get("files") is None and data.get("source", "mod") == "mod":
        return _ALL_FILES_TITLE
    return None


def capture_position(view):
    def entry_id(index):
        row = index.data(Qt.ItemDataRole.UserRole) if index.isValid() else None
        return row.get("entry_id") if row else None

    top = view.indexAt(QPoint(view.viewport().width() // 2, 1))
    return {
        "current_entry_id": entry_id(view.currentIndex()),
        "top_entry_id": entry_id(top),
        "top_offset": max(0, -view.visualRect(top).top()) if top.isValid() else 0,
    }


def restore_position(view, position, entries):
    proxy = view.model()
    view.finish_resize()

    def index_for(name):
        row = entries.get(position.get(name))
        if row is None:
            return None
        index = proxy.mapFromSource(proxy.sourceModel().index(row, 0))
        return index if index.isValid() else None

    current = index_for("current_entry_id")
    if current is not None:
        view.setCurrentIndex(current)
    top = index_for("top_entry_id")
    if top is not None:
        view.scrollTo(top, QAbstractItemView.ScrollHint.PositionAtTop)
        offset = min(position.get("top_offset", 0), max(0, view.visualRect(top).height() - 1))
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() + offset)
    elif current is not None:
        view.scrollTo(current, QAbstractItemView.ScrollHint.PositionAtCenter)


def _integer(value, default, minimum=0, maximum=20000):
    return value if type(value) is int and minimum <= value <= maximum else default


def _sizes(value, default):
    if not isinstance(value, list) or len(value) != 2:
        return default
    if any(type(n) is not int or not 0 <= n <= 20000 for n in value) or not sum(value):
        return default
    return value


def _window_rect(value):
    if (not isinstance(value, list) or len(value) != 4
            or any(type(n) is not int or abs(n) > 1000000 for n in value)
            or value[2] < 200 or value[3] < 200):
        return None
    return value


def _capture_window(window):
    maximized = bool(window.windowState() & Qt.WindowState.WindowMaximized)
    rect = window.normalGeometry() if maximized or window.isMinimized() else window.geometry()
    if not rect.isValid():
        rect = window.geometry()
    return {"window_size": [rect.width(), rect.height()],
            "window_normal_rect": [rect.x(), rect.y(), rect.width(), rect.height()],
            "window_maximized": maximized}


def _clamp_window(window, rect):
    """Clamp the outer frame, while Qt setGeometry takes client coordinates."""
    screens = QGuiApplication.screens()
    if not screens:
        return
    overlaps = [(screen.availableGeometry().intersected(rect), screen) for screen in screens]
    area, screen = max(overlaps, key=lambda item: max(0, item[0].width()) * max(0, item[0].height()))
    if area.isEmpty():
        screen = window.screen() if window.screen() in screens else QGuiApplication.primaryScreen()
    available = screen.availableGeometry()
    window.winId()  # Obtain native frame margins before choosing client geometry.
    handle = window.windowHandle()
    if handle.screen() != screen:
        handle.setScreen(screen)
    margins = handle.frameMargins()
    border = max(1, window.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth))
    left, right = max(border, margins.left()), max(border, margins.right())
    top = max(window.style().pixelMetric(QStyle.PixelMetric.PM_TitleBarHeight), margins.top())
    bottom = max(border, margins.bottom())
    width = min(rect.width(), max(1, available.width() - left - right))
    height = min(rect.height(), max(1, available.height() - top - bottom))
    x, y = rect.x() - left, rect.y() - top
    if area.isEmpty():
        x = available.x() + (available.width() - width - left - right) // 2
        y = available.y() + (available.height() - height - top - bottom) // 2
    x = max(available.left(), min(x, available.right() + 1 - width - left - right))
    y = max(available.top(), min(y, available.bottom() + 1 - height - top - bottom))
    window.setGeometry(x + left, y + top, width, height)


def _position(value):
    value = value if isinstance(value, dict) else {}
    return {name: value.get(name) if isinstance(value.get(name), str) else None
            for name in ("current_entry_id", "top_entry_id")} | {
                "top_offset": _integer(value.get("top_offset"), 0),
            }


def _tabs(value, *, secondary=False):
    value = value if isinstance(value, dict) else {}
    tabs = value.get("tabs", [])
    result = []
    active = 0
    if isinstance(tabs, list):
        for i, tab in enumerate(tabs[:1000]):
            if not isinstance(tab, dict) or not isinstance(tab.get("title"), str):
                continue
            files = tab.get("files")
            if files is not None and (not isinstance(files, list) or not all(isinstance(p, str) for p in files)):
                continue
            source = tab.get("source", "mod")
            if source not in ("mod", "stellaris") or (not secondary and source != "mod"):
                continue
            kind = tab.get("kind", "normal")
            if kind not in ("normal", "glossary"):
                continue
            if i == value.get("active_tab"):
                active = len(result)
            result.append({"title": tab["title"], "files": files, "source": source, "kind": kind,
                           "position": _position(tab.get("position"))})
    return {"tabs": result, "active_tab": active}


def normalize(document, project_id):
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document.get("schema_version") != SCHEMA_VERSION or document.get("project_id") != project_id):
        return {}
    sidebar = document.get("sidebar")
    sidebar = sidebar if isinstance(sidebar, dict) else {}
    lower = document.get("secondary")
    lower = lower if isinstance(lower, dict) else {}
    layout = document.get("layout")
    layout = layout if isinstance(layout, dict) else {}
    explorer = document.get("explorer")
    if not isinstance(explorer, dict) or explorer.get("kind") not in ("all", "file", "folder"):
        explorer = None
    elif explorer.get("kind") != "all" and not isinstance(explorer.get("path"), str):
        explorer = None
    mode = document.get("filter")
    return {
        "schema_version": SCHEMA_VERSION, "project_id": project_id,
        "sidebar": {
            "visible": sidebar.get("visible") if type(sidebar.get("visible")) is bool else True,
            "page": sidebar.get("page") if sidebar.get("page") in ("explorer", "search") else "explorer",
            "width": _integer(sidebar.get("width"), 300, 280, 500),
            "search_show_keys": sidebar.get("search_show_keys") if type(sidebar.get("search_show_keys")) is bool else False,
        },
        "workspace": _tabs(document.get("workspace")),
        "secondary": _tabs(lower, secondary=True) | {"visible": lower.get("visible") is True},
        "explorer": explorer, "filter": mode if isinstance(mode, str) and mode in FILTERS else "ALL",
        "layout": {"horizontal": _sizes(layout.get("horizontal"), [300, 1100]),
                   "vertical": _sizes(layout.get("vertical"), [650, 250]),
                   "window_maximized": layout.get("window_maximized") is True,
                   "window_normal_rect": _window_rect(layout.get("window_normal_rect")),
                   "window_size": [_integer(n, default, 200) for n, default in zip(
                       _sizes(layout.get("window_size"), [1400, 900]), [1400, 900])]},
    }


class UiStateStore:
    def __init__(self, project_path, project_id):
        self.path = Path(project_path).parent / "ui-state.json"
        self.project_id = project_id

    def load(self):
        try:
            return normalize(json.loads(self.path.read_text(encoding="utf-8")), self.project_id)
        except (OSError, ValueError, UnicodeError, RecursionError):
            return {}

    def save(self, document):
        payload = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        output = QSaveFile(str(self.path))
        output.setDirectWriteFallback(False)
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        if output.write(payload) != len(payload):
            output.cancelWriting()
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())


class ProjectUiState(QObject):
    def __init__(self, window, project):
        super().__init__(window)
        self.window = window
        self.store = UiStateStore(project.file_path, project.project_id)
        self.vertical_sizes = [650, 250]
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._restore_layout)
        self._pending = None
        self._initial_state = None
        self._loaded_state = None
        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self._restore_after_paint)
        window.workspace_splitter.splitterMoved.connect(self._remember_vertical)

    def _remember_vertical(self, *args):
        sizes = self.window.workspace_splitter.sizes()
        if not self.window.secondary_widget.isHidden() and len(sizes) == 2 and sizes[1] > 0:
            self.vertical_sizes = sizes

    def capture(self):
        # Closing before deferred restoration must not replace the saved tabs
        # and position with the temporary, safe startup state.
        if self._initial_state is not None or self._pending is not None:
            state = self._initial_state if self._initial_state is not None else self._pending
            return state | {"layout": state["layout"] | _capture_window(self.window)}
        w = self.window
        w.save_current_tab_state()
        w.save_secondary_tab_state()
        self._remember_vertical()

        def tabs(bar, secondary=False):
            result, active = [], 0
            for i in range(bar.count()):
                data = bar.tabData(i) or {}
                if data.get("kind", "normal") not in ("normal", "glossary"):
                    continue
                if i == bar.currentIndex():
                    active = len(result)
                result.append({"title": _builtin_tab_title(data) or bar.tabText(i), "files": data.get("files"), "kind": data.get("kind", "normal"),
                               "source": data.get("source", "mod"),
                               "position": data.get("position", _position(None))})
            return {"tabs": result, "active_tab": active}

        item = w.file_tree.currentItem()
        return {
            "schema_version": SCHEMA_VERSION, "project_id": self.store.project_id,
            "sidebar": w.sidebar.state(), "workspace": tabs(w.tab_bar),
            "explorer": item.data(0, EXPLORER_ROLE) if item else None,
            "filter": w.proxy_model.mode,
            "secondary": tabs(w.secondary_tab_bar, True) | {"visible": not w.secondary_widget.isHidden()},
            "layout": {"horizontal": w.sidebar.splitter.sizes(), "vertical": self.vertical_sizes,
                       **_capture_window(w)},
        }

    def save(self):
        try:
            self.store.save(self.capture())
            return True
        except Exception as error:
            self.window.statusBar().showMessage(QCoreApplication.translate("ui_state", "Не удалось сохранить состояние интерфейса: {0}").format(error), 15000)
            return False

    def show_window(self):
        self._loaded_state = self.store.load()
        layout = self._loaded_state.get("layout", {})
        saved = layout.get("window_normal_rect")
        if saved:
            rect = QRect(*saved)
        else:
            size = layout.get("window_size", [self.window.width(), self.window.height()])
            screen = self.window.screen() or QGuiApplication.primaryScreen()
            rect = QRect(0, 0, *size)
            if screen is not None:
                rect.moveCenter(screen.availableGeometry().center())
        _clamp_window(self.window, rect)
        if layout.get("window_maximized", False):
            self.window.showMaximized()
        else:
            self.window.show()
            # Recheck using real platform decorations once the window is mapped.
            _clamp_window(self.window, self.window.geometry())

    def restore(self):
        state = self._loaded_state if self._loaded_state is not None else self.store.load()
        self._loaded_state = None
        if not state:
            return
        self._initial_state = state
        # The initial system tab can hide the localization viewport entirely.
        self.window.installEventFilter(self)
        self.window.update()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint and self._initial_state is not None:
            watched.removeEventFilter(self)
            # Let the first window paint finish before applying filters.
            self._restore_timer.start(0)
        return super().eventFilter(watched, event)

    def _restore_after_paint(self):
        state = self._initial_state
        if state is None or not self.window.isVisible():
            return
        try:
            self._restore(state)
        except Exception as error:
            self.window.statusBar().showMessage(QCoreApplication.translate("ui_state", "Состояние интерфейса восстановлено не полностью: {0}").format(error), 15000)
        finally:
            self._initial_state = None

    def _restore(self, state):
        w = self.window
        w.current_tab_index = -1
        w.secondary_current_tab_index = -1
        for bar, saved, secondary in ((w.tab_bar, state["workspace"], False),
                                      (w.secondary_tab_bar, state["secondary"], True)):
            with QSignalBlocker(bar):
                while bar.count():
                    bar.removeTab(0)
                if not secondary:
                    i = bar.addTab(QCoreApplication.translate("ui_state", "Глоссарий"))
                    bar.setTabData(i, {"files": None, "kind": "glossary", "pinned": True})
                active = 0
                seen = {}
                for original_index, tab in enumerate(saved["tabs"]):
                    files = w.normalize_tab_files(tab["files"])
                    kind = tab.get("kind", "normal")
                    identity = (kind, files, tab["source"])
                    if not secondary and kind == "glossary":
                        i = 0
                    elif not secondary and identity in seen:
                        i = seen[identity]
                    else:
                        builtin_title = _builtin_tab_title(tab)
                        i = bar.addTab(QCoreApplication.translate("ui_state", builtin_title) if builtin_title else tab["title"])
                        bar.setTabData(i, {"files": files, "kind": kind, "source": tab["source"],
                                          "position": tab["position"], "source_row": None,
                                          "scroll": None if secondary else 0})
                        seen[identity] = i
                    if original_index == saved["active_tab"]:
                        active = i
                bar.setCurrentIndex(active)
        mode = state["filter"]
        # The document proxy should see only the final file + status mapping,
        # not thousands of intermediate rowsRemoved notifications.
        files = (w.tab_bar.tabData(w.tab_bar.currentIndex()) or {}).get("files")
        w.proxy_model.set_filters(mode, set(files) if files is not None else None)
        w.workspace_tab_changed(w.tab_bar.currentIndex(), restore_position=False)
        for button in w.button_group.buttons():
            button.setChecked(button.property("filter_mode") == mode)
        with QSignalBlocker(w.file_tree):
            iterator = QTreeWidgetItemIterator(w.file_tree)
            while iterator.value():
                item = iterator.value()
                if item.data(0, EXPLORER_ROLE) == state["explorer"]:
                    w.file_tree.setCurrentItem(item)
                    w.file_tree.scrollToItem(item)
                    break
                iterator += 1
        if state["secondary"]["visible"] and w.secondary_tab_bar.count():
            w.apply_secondary_tab(w.secondary_tab_bar.currentIndex(), restore_position=False)
        w.secondary_widget.setVisible(state["secondary"]["visible"] and bool(w.secondary_tab_bar.count()))
        self.vertical_sizes = state["layout"]["vertical"]
        self._pending = state
        # The window is shown before restore() is called. Defer final geometry
        # until the new tabs, filters and visibility have reached the layout.
        self._layout_timer.start(0)

    def _restore_layout(self):
        state, self._pending = self._pending, None
        if state is None:
            return
        w = self.window
        w.centralWidget().layout().activate()
        w.sidebar.splitter.setSizes(state["layout"]["horizontal"])
        w.sidebar.restore_state(state["sidebar"])
        w.workspace_splitter.setSizes(self.vertical_sizes)
        if not w.isMaximized() and not w.isMinimized():
            _clamp_window(w, w.geometry())
        for view, bar in ((w.list_view, w.tab_bar), (w.secondary_list_view, w.secondary_tab_bar)):
            if view is None:
                continue
            data = bar.tabData(bar.currentIndex()) or {}
            if data.get("kind", "normal") == "normal" and data.get("source", "mod") == "mod" and "position" in data:
                restore_position(view, data["position"], w.entry_rows)
