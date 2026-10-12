"""File-only Explorer actions and incrementally maintained status summaries."""

from PySide6.QtCore import QCoreApplication

import html
from collections import Counter, defaultdict
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRect, Qt, QTimer, QUrl, QSignalBlocker, Signal
from PySide6.QtGui import QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QHeaderView, QMenu, QMessageBox, QStyledItemDelegate,
    QToolTip, QTreeWidgetItemIterator, QTreeWidget,
)

from . import theme
from .ui_state import EXPLORER_ROLE
from .icons import paint_check

STATS_ROLE = int(Qt.ItemDataRole.UserRole) + 2


class ExplorerTree(QTreeWidget):
    sameItemClicked = Signal(object)

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.RightButton:
            if item is not None:
                with QSignalBlocker(self):
                    self.setCurrentItem(item)
            event.accept()
            return
        self._same_press = item if item is self.currentItem() else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and item is not None and item is getattr(self, "_same_press", None):
            self.sameItemClicked.emit(item)
        self._same_press = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class FileStatusDelegate(QStyledItemDelegate):
    @staticmethod
    def icon_rect(rect):
        return QRect(rect.center().x() - 9, rect.center().y() - 9, 18, 18)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        stats = index.data(STATS_ROLE)
        if not stats:
            return
        ready = stats.get("pending", 0) == 0
        rect = self.icon_rect(option.rect)
        if ready:
            paint_check(painter, rect, theme.css('status.ready'))
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(rect.topLeft())
        pen = QPen(theme.color('status.ready' if ready else 'icon.default'), 2.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # An open ring denotes work remaining, without a permanent number.
        painter.drawArc(3, 3, 12, 12, 40 * 16, 280 * 16)
        painter.drawPoint(QPointF(9, 9))
        painter.restore()

    def helpEvent(self, event, view, option, index):
        stats = index.data(STATS_ROLE)
        rect = self.icon_rect(option.rect)
        if not stats or not rect.contains(event.pos()):
            QToolTip.hideText()
            return False
        name = index.siblingAtColumn(0).data(Qt.ItemDataRole.DisplayRole)
        fields = ((QCoreApplication.translate("explorer", "Всего текущих"), "total"), ("OK", "ok"),
                  (QCoreApplication.translate("explorer", "Требуют проверки"), "review"), (QCoreApplication.translate("explorer", "Не переведены"), "untranslated"),
                  (QCoreApplication.translate("explorer", "Новые"), "new"), (QCoreApplication.translate("explorer", "Перемещённые"), "moved"),
                  (QCoreApplication.translate("explorer", "Удалённые (архив)"), "removed"), (QCoreApplication.translate("explorer", "Отключены"), "disabled"))
        lines = "<br>".join(f"{label}: {stats.get(key, 0)}" for label, key in fields)
        QToolTip.showText(event.globalPos(), f"<b>{html.escape(name)}</b><br>{lines}", view.viewport(), rect)
        return True


class ExplorerFiles(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.tree, self.model = window, window.file_tree, window.model
        self.project = None
        self.items = {}
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            metadata = item.data(0, EXPLORER_ROLE) or {}
            if metadata.get("kind") == "file":
                self.items[str(Path(metadata["path"]))] = item
            iterator += 1
        self.tree.setColumnCount(2)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 28)
        self.delegate = FileStatusDelegate(self.tree)
        self.tree.setItemDelegateForColumn(1, self.delegate)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_menu)
        self.pending_files = set()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.flush)
        self.removed_counts = Counter()
        self.rebuild()
        self.model.dataChanged.connect(self.rows_changed)
        self.model.modelReset.connect(self.rebuild)

    @staticmethod
    def row_summary(row):
        if row.get("section") != "CURRENT" or row.get("kind", "ENTRY") not in ("ENTRY", "DISABLED_ENTRY"):
            return None, Counter()
        file = row.get("new_file") or row.get("old_file")
        if file is None:
            return None, Counter()
        status, source = row.get("translation_status"), row.get("source_status")
        disabled = row.get("kind") == "DISABLED_ENTRY" or status == "DISABLED"
        counts = Counter(total=1, ok=int(status == "OK"),
                         pending=int(status != "OK" and (not disabled or status in ("NEED_UPDATE", "NEEDS_REVIEW"))),
                         disabled=int(disabled), review=int(status in ("NEED_UPDATE", "NEEDS_REVIEW")),
                         untranslated=int(not disabled and status != "OK" and row.get("translation_state") == "untranslated"),
                         new=int(source == "NEW"),
                         moved=int(source in ("MOVED", "MOVED_CHANGED", "RENAMED", "RENAMED_CHANGED")))
        return str(Path(file)), counts

    def rebuild(self):
        self.stats = defaultdict(Counter)
        self.file_rows = defaultdict(set)
        self.row_stats = []
        for number, row in enumerate(self.model.rows):
            file, counts = self.row_summary(row)
            self.row_stats.append((file, counts))
            if file is not None:
                self.stats[file].update(counts)
                self.file_rows[file].add(number)
        self.pending_files.update(self.items)
        self.flush()

    def bind_project(self, project):
        self.project = project
        self.refresh_removed()

    def refresh_removed(self):
        previous = set(self.removed_counts)
        self.removed_counts.clear()
        if self.project is not None:
            prefix = Path(self.project.current_source_prefix)
            for row in self.project.removed_entries:
                file = row.get("old_file") or row.get("new_file")
                if file is not None:
                    full = Path(row.get("last_source_prefix") or "") / file
                    if full.is_relative_to(prefix):
                        self.removed_counts[str(full.relative_to(prefix))] += 1
        self.pending_files.update(previous | set(self.removed_counts))
        self.flush()

    def rows_changed(self, first, last, roles=None):
        for number in range(first.row(), last.row() + 1):
            previous_file, previous = self.row_stats[number]
            file, counts = self.row_summary(self.model.rows[number])
            if (file, counts) == (previous_file, previous):
                continue
            if previous_file is not None:
                self.stats[previous_file].subtract(previous)
                self.file_rows[previous_file].discard(number)
                self.pending_files.add(previous_file)
            if file is not None:
                self.stats[file].update(counts)
                self.file_rows[file].add(number)
                self.pending_files.add(file)
            self.row_stats[number] = (file, counts)
        if self.pending_files:
            self.timer.start(0)

    def flush(self):
        for file in self.pending_files:
            item = self.items.get(file)
            if item is not None:
                values = dict(self.stats[file], removed=self.removed_counts[file])
                values.setdefault("total", 0)
                item.setData(1, STATS_ROLE, values)
        self.pending_files.clear()

    def open_file(self, file):
        self.window.open_workspace_tab(self.window.translation_target_path(file).name, [file])

    def search_file(self, file):
        self.open_file(file)
        self.window.local_find.set_replace_mode(False)
        self.window.local_find.open()

    def confirm_file(self, file):
        self.window.finish_project_editing()
        indexes = [self.model.index(number, 0) for number in sorted(self.file_rows[file])]
        count = len(self.model.acceptance_commands(indexes))
        if not count:
            QMessageBox.information(self.window, QCoreApplication.translate("explorer", "Подтвердить переводы файла"), QCoreApplication.translate("explorer", "Нет записей, требующих подтверждения."))
            return
        result = QMessageBox.question(
            self.window, QCoreApplication.translate("explorer", "Подтвердить переводы файла"),
            QCoreApplication.translate("explorer", "{0}\nЗаписей для подтверждения: {1}\n\nПодтвердить текущий EN для этих переводов?").format(self.window.translation_target_path(file).name, count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Yes:
            self.model.accept_translations(indexes)

    def reveal_file(self, file):
        try:
            root = self.project.resolve_path("source_mod_root") if self.project else None
            if root is None:
                raise ValueError(QCoreApplication.translate("explorer", "Для проекта не задана папка оригинального мода."))
            localization = (root / "localisation").resolve()
            path = (localization / self.project.current_source_prefix / file).resolve()
            if not path.is_relative_to(localization) or not path.is_file():
                raise ValueError(QCoreApplication.translate("explorer", "Файл недоступен в папке оригинального мода."))
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))):
                raise OSError(QCoreApplication.translate("explorer", "Не удалось открыть папку файла."))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self.window, QCoreApplication.translate("explorer", "Папка оригинального мода"), str(error))

    def show_menu(self, point):
        item = self.tree.itemAt(point)
        metadata = item.data(0, EXPLORER_ROLE) if item else None
        if not metadata or metadata.get("kind") != "file":
            return
        file = str(Path(metadata["path"]))
        target = self.window.translation_target_path(file)
        menu = QMenu(self.tree)
        menu.addAction(QCoreApplication.translate("explorer", "Открыть"), lambda: self.open_file(file))
        menu.addAction(QCoreApplication.translate("explorer", "Искать в этом файле"), lambda: self.search_file(file))
        menu.addAction(QCoreApplication.translate("explorer", "Подтвердить переводы файла…"), lambda: self.confirm_file(file))
        menu.addSeparator()
        menu.addAction(QCoreApplication.translate("explorer", "Копировать имя файла"), lambda: QApplication.clipboard().setText(target.name))
        menu.addAction(QCoreApplication.translate("explorer", "Копировать относительный путь"), lambda: QApplication.clipboard().setText(target.as_posix()))
        menu.addAction(QCoreApplication.translate("explorer", "Показать в папке оригинального мода"), lambda: self.reveal_file(file))
        menu.exec(self.tree.viewport().mapToGlobal(point))
