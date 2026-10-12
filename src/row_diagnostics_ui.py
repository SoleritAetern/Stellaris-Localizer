"""Lazy per-entry cache and a single click-opened diagnostics panel."""

from PySide6.QtCore import QCoreApplication
from .ui_messages import stored_message

import html
from weakref import ref

from PySide6.QtCore import QAbstractProxyModel, QEvent, QModelIndex, QObject, QPersistentModelIndex, Qt
from PySide6.QtGui import QCursor, QUndoCommand
from PySide6.QtWidgets import QApplication, QFrame, QTextBrowser, QToolTip, QVBoxLayout
from shiboken6 import isValid

from . import theme
from .row_diagnostics import analyze_ru, ignored_ids
from .localization_view import ROW_APPEARANCE_ROLE


class RowDiagnosticsCache(QObject):
    def __init__(self, model):
        super().__init__(model)
        self.model = model
        self.cache = {}
        model.dataChanged.connect(self.changed)

    def warnings(self, number, *, calculate=True):
        if not calculate:
            return self.cache.get(number, (None, ()))[1]
        row = self.model.rows[number]
        if row.get("section") != "CURRENT" or row.get("kind", "ENTRY") not in ("ENTRY", "DISABLED_ENTRY"):
            return ()
        text = row.get("ru_text") or ""
        ignored = tuple(ignored_ids(row.get("ignored_diagnostics", [])))
        signature = (text, ignored)
        previous = self.cache.get(number)
        if previous is None or previous[0] != signature:
            warnings = tuple(item for item in analyze_ru(row.get("entry_id"), text) if item.diagnostic_id not in ignored)
            self.cache[number] = (signature, warnings)
        return self.cache[number][1]

    def changed(self, first, last, roles=None):
        for number in range(first.row(), last.row() + 1):
            if number in self.cache:
                self.warnings(number)


def warnings_for(index, *, calculate=True):
    while index.isValid() and isinstance(index.model(), QAbstractProxyModel):
        index = index.model().mapToSource(index)
    if not index.isValid():
        return ()
    cache = getattr(index.model(), "row_diagnostics", None)
    return cache.warnings(index.row(), calculate=calculate) if cache is not None else ()


class IgnoreWarning(QUndoCommand):
    def __init__(self, model, number, diagnostic_id):
        super().__init__(QCoreApplication.translate("row_diagnostics_ui", "Игнорировать предупреждение RU"))
        self.model, self.number = model, number
        self.before = ignored_ids(model.rows[number].get("ignored_diagnostics", []))
        self.after = sorted(set(self.before) | {diagnostic_id})

    def apply(self, value):
        self.model.rows[self.number]["ignored_diagnostics"] = list(value)
        index = self.model.index(self.number, 0)
        self.model.dataChanged.emit(index, index, [Qt.ItemDataRole.UserRole, ROW_APPEARANCE_ROLE])

    def redo(self):
        self.apply(self.after)

    def undo(self):
        self.apply(self.before)


class RowDiagnosticsPopover(QFrame):
    def __init__(self, window):
        super().__init__(window)
        self.owner = ref(window)
        self.disposed = False
        self.app = None
        self.view = None
        self.display = QPersistentModelIndex()
        self.source = QPersistentModelIndex()
        self.snapshot = ""
        self.actions = {}
        self.applying = False
        self.setObjectName("rowDiagnostics")
        self.setStyleSheet(f"QFrame#rowDiagnostics {{ background: {theme.css('surface.popover')}; border: 1px solid {theme.css('border.subtle')}; border-radius: 6px; }}"
                          "QFrame#rowDiagnostics QTextBrowser { background: transparent; border: none; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 8, 9, 8)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        layout.addWidget(self.browser)
        self.connections = [self.browser.anchorClicked.connect(self.apply_action),
                            window.model.dataChanged.connect(self.changed)]
        weak_self = ref(self)
        def destroyed(*args):
            popup = weak_self()
            if popup is not None:
                popup.dispose()
        self.connections.append(window.destroyed.connect(destroyed))
        self.hide()

    def alive(self):
        owner = self.owner() if self.owner else None
        return not self.disposed and isValid(self) and owner is not None and isValid(owner)

    def dispose(self):
        if self.disposed:
            return
        self.close_panel()
        self.disposed = True
        for connection in self.connections:
            if connection:
                QObject.disconnect(connection)
        self.connections.clear()
        self.owner = None

    def close_panel(self):
        if self.app is not None and isValid(self.app) and isValid(self):
            self.app.removeEventFilter(self)
        self.app = None
        if self.alive():
            self.hide()
        self.view = None
        self.display = self.source = QPersistentModelIndex()
        self.actions.clear()

    def open(self, view, index):
        if not self.alive():
            return
        if self.isVisible() and self.view is view and self.display == index:
            self.close_panel()
            return
        source = index
        while isinstance(source.model(), QAbstractProxyModel):
            source = source.model().mapToSource(source)
        source = QPersistentModelIndex(source)
        display = QPersistentModelIndex(index)
        view.finish_editing()
        if not source.isValid() or not display.isValid() or source.model() is not self.owner().model:
            return
        self.view, self.display, self.source = view, display, source
        self.refresh()
        self.app = QApplication.instance()
        self.app.installEventFilter(self)
        QToolTip.hideText()
        self.show()
        self.raise_()

    def refresh(self):
        if not self.alive() or not self.source.isValid():
            return
        model = self.source.model()
        self.snapshot = model.rows[self.source.row()].get("ru_text") or ""
        warnings = model.row_diagnostics.warnings(self.source.row())
        self.actions = {}
        parts = [QCoreApplication.translate("row_diagnostics_ui", "<b>Предупреждений: {0}</b>").format(len(warnings))]
        if not warnings:
            parts.append(QCoreApplication.translate("row_diagnostics_ui", "<p>Предупреждений нет</p>"))
        for warning in warnings:
            parts.append(f"<p><b>{html.escape(stored_message(warning.message))}</b><br>"
                         f"<span style='white-space:pre-wrap'>{html.escape(self.snapshot[warning.start:warning.end][:220])}</span></p>")
            for fix in (*warning.fixes, None):
                identity = str(len(self.actions))
                self.actions[identity] = (warning.diagnostic_id, fix)
                label = fix[0] if fix else QCoreApplication.translate("row_diagnostics_ui", "Игнорировать")
                parts.append(f'<a style="color:{theme.css('text.primary')}" href="action:{identity}">{html.escape(label)}</a><br>')
        self.browser.setHtml("".join(parts))
        self.reposition()

    def reposition(self):
        if not self.alive() or self.view is None or not isValid(self.view) or not self.display.isValid():
            return
        owner = self.owner()
        index = QModelIndex(self.display)
        option = self.view._option(index)
        area = self.view.itemDelegate().row_layout(option, index).actions["diagnostics"].translated(option.rect.topLeft())
        viewport = self.view.viewport()
        point = owner.mapFromGlobal(viewport.mapToGlobal(area.bottomLeft()))
        bounds = owner.rect().adjusted(8, 8, -8, -8)
        width = min(400, bounds.width())
        self.browser.document().setTextWidth(max(1, width - 38))
        self.browser.setFixedHeight(min(300, max(60, int(self.browser.document().size().height()) + 12)))
        self.resize(width, min(bounds.height(), self.sizeHint().height()))
        y = point.y() + 4
        if y + self.height() > bounds.bottom():
            y = point.y() - area.height() - self.height() - 4
        self.move(max(bounds.left(), min(point.x(), bounds.right() - self.width())), max(bounds.top(), y))

    def changed(self, first, last, roles=None):
        if self.alive() and self.isVisible() and not self.applying and self.source.isValid():
            if first.row() <= self.source.row() <= last.row():
                self.refresh()

    def apply_action(self, url):
        if not self.alive() or not self.source.isValid():
            return
        action = self.actions.get(url.toString().removeprefix("action:"))
        model, number = self.source.model(), self.source.row()
        if action is None:
            return
        if (model.rows[number].get("ru_text") or "") != self.snapshot:
            self.refresh()
            return
        diagnostic_id, fix = action
        if diagnostic_id not in {item.diagnostic_id for item in model.row_diagnostics.warnings(number)}:
            self.refresh()
            return
        self.applying = True
        try:
            if fix is None:
                model.undo_stack.push(IgnoreWarning(model, number, diagnostic_id))
            else:
                label, start, end, replacement = fix
                model.setData(model.index(number, 0), self.snapshot[:start] + replacement + self.snapshot[end:], Qt.ItemDataRole.EditRole)
        finally:
            self.applying = False
        self.refresh()

    def eventFilter(self, watched, event):
        if not self.alive():
            return False
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.close_panel()
            return True
        if event.type() == QEvent.Type.MouseButtonPress and not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            # The row action owns toggling the already-open warning icon.
            if self.view is not None and isValid(self.view) and watched is self.view.viewport():
                index, action = self.view._action_at(self.view.viewport().mapFromGlobal(QCursor.pos()))
                if action == "diagnostics":
                    return False
            self.close_panel()
        elif event.type() == QEvent.Type.Resize and watched is self.owner():
            self.reposition()
        return False
