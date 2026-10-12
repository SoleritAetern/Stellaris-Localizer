"""Display document structure while keeping source indexes entry-only."""

from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QAbstractProxyModel, QModelIndex, Qt
from src.localization_document import structural_display
from src.height_index import layout_identity


def is_entry(row):
    return row is not None and row.get("kind", "ENTRY") in ("ENTRY", "DISABLED_ENTRY")


class DocumentFilterProxy(QAbstractProxyModel):
    def __init__(self, entry_filter, documents, parent=None):
        super().__init__(parent)
        self.entry_filter = entry_filter
        self.documents = documents
        self.items = []
        self.source_to_display = {}
        self._filter_update_depth = 0
        super().setSourceModel(entry_filter.sourceModel())
        for signal in (entry_filter.modelReset, entry_filter.rowsInserted, entry_filter.rowsRemoved, entry_filter.layoutChanged):
            signal.connect(self.rebuild)
        entry_filter.dataChanged.connect(self._data_changed)
        source = self.sourceModel()
        if hasattr(source, "translationBatchAboutToChange"):
            source.translationBatchAboutToChange.connect(self._begin_translation_batch)
            source.translationBatchChanged.connect(self._end_translation_batch)
        self.rebuild()

    def _begin_translation_batch(self):
        self._filter_update_depth += 1

    def _end_translation_batch(self):
        self._filter_update_depth -= 1
        if not self._filter_update_depth and self.mode != "ENGLISH_TEXT":
            self.rebuild()

    @property
    def mode(self):
        return self.entry_filter.mode

    @property
    def search_text(self):
        return self.entry_filter.search_text

    @property
    def selected_files(self):
        return self.entry_filter.selected_files

    def set_mode(self, value):
        if value != self.mode:
            with self.batch_filter_update():
                self.entry_filter.set_mode(value)

    def set_search_text(self, value):
        if value.lower().strip() != self.search_text:
            with self.batch_filter_update():
                self.entry_filter.set_search_text(value)

    def set_file_filter(self, value):
        if value != self.selected_files:
            with self.batch_filter_update():
                self.entry_filter.set_file_filter(value)

    def set_filters(self, mode, file_paths):
        if mode != self.mode or file_paths != self.selected_files:
            with self.batch_filter_update():
                self.entry_filter.set_filters(mode, file_paths)

    @contextmanager
    def batch_filter_update(self):
        # A row-filter change emits a removal/insertion signal per contiguous
        # group. Rebuilding here for every intermediate mapping is quadratic.
        self._filter_update_depth += 1
        try:
            yield
        finally:
            self._filter_update_depth -= 1
            if not self._filter_update_depth:
                self.rebuild()

    def rebuild(self, *args):
        if self._filter_update_depth:
            return
        filtered = [self.entry_filter.mapToSource(self.entry_filter.index(i, 0)).row()
                    for i in range(self.entry_filter.rowCount())]
        allowed = set(filtered)
        by_id = {row.get("entry_id"): i for i, row in enumerate(self.sourceModel().rows) if row.get("entry_id")}
        items, used = [], set()
        if self.mode == "ALL" and not self.search_text:
            for document in self.documents:
                # File filter stores native path strings.
                if self.selected_files is not None and str(Path(document.file)) not in self.selected_files:
                    continue
                for element, title, raw in structural_display(document):
                    if element.kind in ("ENTRY", "DISABLED_ENTRY"):
                        source_row = by_id.get(element.entry_id)
                        if source_row in allowed:
                            items.append((source_row, None))
                            used.add(source_row)
                    elif element.kind in ("COMMENT", "BLANK"):
                        items.append((None, {"kind": element.kind, "raw": raw, "file": document.file,
                                             "line": element.line, "section_title": title,
                                             "element_id": f"{document.file}#{element.line}:{element.kind}",
                                             "text": raw.rstrip("\r\n")}))
        items.extend((row, None) for row in filtered if row not in used)
        if items == self.items:
            return
        self.beginResetModel()
        self.items = items
        self.source_to_display = {source: display for display, (source, _) in enumerate(items) if source is not None}
        self.endResetModel()

    def _data_changed(self, top, bottom, roles):
        for i in range(top.row(), bottom.row() + 1):
            index = self.mapFromSource(self.entry_filter.mapToSource(self.entry_filter.index(i, 0)))
            if index.isValid():
                self.dataChanged.emit(index, index, roles)

    def index(self, row, column, parent=QModelIndex()):
        return self.createIndex(row, column) if not parent.isValid() and column == 0 and 0 <= row < len(self.items) else QModelIndex()

    def parent(self, index):
        return QModelIndex()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return 1

    def layout_metadata(self, row_number):
        source, structural = self.items[row_number]
        if source is not None:
            return self.sourceModel().layout_metadata(source)
        return layout_identity(structural), structural["kind"]

    def mapToSource(self, index):
        if not index.isValid() or index.model() is not self or not 0 <= index.row() < len(self.items):
            return QModelIndex()
        source, _ = self.items[index.row()]
        return self.sourceModel().index(source, 0) if source is not None else QModelIndex()

    def mapFromSource(self, index):
        if not index.isValid() or index.model() is not self.sourceModel():
            return QModelIndex()
        return self.index(self.source_to_display.get(index.row(), -1), 0)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        source = self.mapToSource(index)
        if source.isValid():
            return source.data(role)
        row = self.items[index.row()][1]
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return row["text"]
        return None

    def flags(self, index):
        source = self.mapToSource(index)
        return self.sourceModel().flags(source) if source.isValid() else Qt.ItemFlag.NoItemFlags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        source = self.mapToSource(index)
        return self.sourceModel().setData(source, value, role) if source.isValid() else False
