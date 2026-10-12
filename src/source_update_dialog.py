"""Source update preview with explicit decisions for uncertain rename pairs."""

from PySide6.QtCore import QCoreApplication

import html
from collections import Counter

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSignalBlocker, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QSplitter, QTableView, QTextBrowser, QVBoxLayout, QWidget,
)

from src.source_update_preview import CATEGORIES
from src.ui_messages import stored_message
from src.text_diff import build_source_diff
from src import theme


def diff_styles():
    # Resolve HTML colors when rendering, just like the QTextLayout diff paths.
    return {
        "equal": f"color:{theme.css('diff.preview.equal')}",
        "added": (f"background-color:{theme.css('diff.preview.added.bg')};"
                  f"color:{theme.css('diff.preview.added.fg')}"),
        "removed": (f"background-color:{theme.css('diff.preview.removed.bg')};"
                    f"color:{theme.css('diff.preview.removed.fg')};text-decoration:line-through"),
    }


class SourceChangesModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.decisions = {}

    def replace(self, items, decisions):
        self.beginResetModel()
        self.items, self.decisions = items, dict(decisions)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return 5

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return (QCoreApplication.translate("source_update_dialog", "Категория"), QCoreApplication.translate("source_update_dialog", "Ключ / сопоставление"), "Relative path", QCoreApplication.translate("source_update_dialog", "Решение"), "EN")[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self.items[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return item
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 4:
            return theme.color("text.secondary")
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            decision = ""
            text_state = ""
            if "review_key" in item:
                value = self.decisions.get(item["review_key"])
                decision = QCoreApplication.translate("source_update_dialog", "Та же запись") if value is True else QCoreApplication.translate("source_update_dialog", "Новая") if value is False else QCoreApplication.translate("source_update_dialog", "Не выбрано")
                if value is True and item.get("archive_return"):
                    decision = QCoreApplication.translate("source_update_dialog", "Восстановить")
                text_state = QCoreApplication.translate("source_update_dialog", "Текст совпадает") if item["old_text"] == item["new_text"] else QCoreApplication.translate("source_update_dialog", "Есть изменения")
            return (QCoreApplication.translate("source_update_preview", CATEGORIES[item["category"]]), item["key"], item["file"], decision, text_state)[index.column()]


class SourceChangesFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.category = None

    def set_category(self, category):
        self.beginFilterChange()
        self.category = category
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, row, parent):
        return self.category is None or self.sourceModel().items[row]["category"] == self.category


class SourceUpdateDialog(QDialog):
    def __init__(self, analysis, parent=None):
        super().__init__(parent)
        self.analysis = analysis
        self.decisions = {}
        self.setWindowTitle(QCoreApplication.translate("source_update_dialog", "Предпросмотр обновления источников"))
        self.resize(1120, 760)
        layout = QVBoxLayout(self)
        intro = QLabel(QCoreApplication.translate("source_update_dialog", "EN сравнивается с текущим источником; для вернувшихся записей — с ранее подтверждённым текстом.\nПеревод и его подтверждение будут перенесены после применения. Категории могут пересекаться. Неподтверждённые пары пока учитываются как новые и удалённые записи."))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        filters = QHBoxLayout()
        filters.addWidget(QLabel(QCoreApplication.translate("source_update_dialog", "Показать:")))
        self.filter = QComboBox()
        filters.addWidget(self.filter, 1)
        layout.addLayout(filters)
        self.model = SourceChangesModel(self)
        self.proxy = SourceChangesFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.setColumnWidth(0, 195)
        self.table.setColumnWidth(3, 125)
        self.table.setColumnWidth(4, 140)
        for column in (1, 2):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.detail = QTextBrowser()
        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        styles = diff_styles()
        legend = QLabel(" · ".join(
            f'<span style="{styles[kind]}">{label}</span>'
            for kind, label in (("equal", QCoreApplication.translate("source_update_dialog", "Белый — без изменений")), ("added", QCoreApplication.translate("source_update_dialog", "Зелёный — добавлено")),
                                ("removed", QCoreApplication.translate("source_update_dialog", "Красный зачёркнутый — удалено")))
        ))
        legend.setWordWrap(True)
        detail_layout.addWidget(legend)
        detail_layout.addWidget(self.detail, 1)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(detail_panel)
        splitter.setSizes([430, 170])
        layout.addWidget(splitter, 1)
        self.review_controls = QWidget()
        review_panel_layout = QVBoxLayout(self.review_controls)
        review_panel_layout.setContentsMargins(0, 0, 0, 0)
        self.guidance = QLabel(QCoreApplication.translate("source_update_dialog", "Проверьте, относится ли новая запись к тому же тексту. Если смысл сохранился — выберите \"Это та же запись\"."))
        self.guidance.setWordWrap(True)
        review_panel_layout.addWidget(self.guidance)
        review_layout = QHBoxLayout()
        review_panel_layout.addLayout(review_layout)
        self.reason = QLabel()
        self.reason.setWordWrap(True)
        review_layout.addWidget(self.reason, 1)
        self.same_button = QPushButton(QCoreApplication.translate("source_update_dialog", "Это та же запись"))
        self.new_button = QPushButton(QCoreApplication.translate("source_update_dialog", "Это новая запись"))
        for button in (self.same_button, self.new_button):
            button.setCheckable(True)
            button.setAutoDefault(False)
            review_layout.addWidget(button)
        self.same_button.clicked.connect(lambda: self.choose(True))
        self.new_button.clicked.connect(lambda: self.choose(False))
        layout.addWidget(self.review_controls)
        self.pending_label = QLabel()
        layout.addWidget(self.pending_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(QCoreApplication.translate("source_update_dialog", "Отмена"))
        self.apply_button = buttons.addButton(QCoreApplication.translate("source_update_dialog", "Применить обновление"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.filter.currentIndexChanged.connect(self.change_filter)
        self.table.selectionModel().currentChanged.connect(self.show_detail)
        self.refresh()
        if analysis.reviews:
            self.filter.setCurrentIndex(self.filter.findData("confirm"))

    def refresh(self, review_key=None):
        items = self.analysis.preview_items(self.decisions)
        self.model.replace(items, self.decisions)
        counts = Counter(item["category"] for item in items)
        self.summary.setText(" · ".join(f"{QCoreApplication.translate('source_update_preview', label)}: {counts[key]}" for key, label in CATEGORIES.items()))
        category = self.filter.currentData()
        with QSignalBlocker(self.filter):
            self.filter.clear()
            self.filter.addItem(QCoreApplication.translate("source_update_dialog", "Все изменения ({0})").format(len(items)), None)
            for key, label in CATEGORIES.items():
                label = QCoreApplication.translate("source_update_preview", label)
                self.filter.addItem(f"{label} ({counts[key]})", key)
            self.filter.setCurrentIndex(max(0, self.filter.findData(category)))
        self.proxy.set_category(category)
        pending = len(self.analysis.reviews) - len(self.decisions)
        self.apply_button.setEnabled(pending == 0)
        self.pending_label.setText(QCoreApplication.translate("source_update_dialog", "Требуют решения: {0}").format(pending) if pending else QCoreApplication.translate("source_update_dialog", "Обновление готово к применению."))
        if review_key is not None:
            row = next((i for i, item in enumerate(items) if item.get("review_key") == review_key), -1)
            index = self.proxy.mapFromSource(self.model.index(row, 0))
            if index.isValid():
                self.table.setCurrentIndex(index)
                self.show_detail()
                return
        self.change_filter()

    def change_filter(self, *args):
        self.proxy.set_category(self.filter.currentData())
        self.table.setCurrentIndex(self.proxy.index(0, 0))
        self.show_detail()

    def show_detail(self, *args):
        item = self.table.currentIndex().data(Qt.ItemDataRole.UserRole)
        self.review_controls.setVisible(bool(item and "review_key" in item))
        if not item:
            self.detail.clear()
            return
        title = html.escape(item["key"] or item["file"])
        fragments = []
        styles = diff_styles()
        segments = build_source_diff(item["old_text"], item["new_text"])
        for segment in segments:
            text = html.escape(segment["text"])
            kind = segment["type"]
            style = styles[kind]
            fragments.append(f'<span style="{style}">{text}</span>')
        note = ""
        if "review_key" in item:
            if item["old_text"] == item["new_text"]:
                note = (QCoreApplication.translate("source_update_dialog", "<p style=\"color:{0};font-size:16px\"><b>✓ Английский текст не изменился</b></p>").format(theme.css('diff.preview.confirmed'))
                        + (QCoreApplication.translate("source_update_dialog", "<p>Новый EN совпадает с сохранённым подтверждённым текстом.</p>") if item.get("archive_return")
                           else QCoreApplication.translate("source_update_dialog", "<p>Вероятно, изменился только ключ.</p>")))
            else:
                added = sum(len(segment["text"]) for segment in segments if segment["type"] == "added")
                removed = sum(len(segment["text"]) for segment in segments if segment["type"] == "removed")
                note = QCoreApplication.translate("source_update_dialog", "<p>Добавлено символов: {0} · Удалено: {1}</p>").format(added, removed)
        comparison_source = (QCoreApplication.translate("source_update_dialog", "подтверждённый текст") if item.get("archive_return")
                             else QCoreApplication.translate("source_update_dialog", "текущий источник"))
        self.detail.setHtml(QCoreApplication.translate("source_update_dialog", "<h3>{0}</h3><p>{1}</p>{2}<p>EN: {3} → обновление</p><div style=\"white-space:pre-wrap;font-family:Consolas\">{4}</div>").format(title, html.escape(item['file']), note, comparison_source, ''.join(fragments)))
        if "review_key" in item:
            if item.get("archive_return"):
                self.same_button.setText(QCoreApplication.translate("source_update_dialog", "Восстановить старый перевод") if item.get("return_kind") == "radical"
                                         else QCoreApplication.translate("source_update_dialog", "Восстановить перевод"))
                self.new_button.setText(QCoreApplication.translate("source_update_dialog", "Считать новой записью"))
                self.guidance.setText(stored_message(item["reason"]) + QCoreApplication.translate("source_update_dialog", ". Выберите, восстановить ли прежний перевод и его подтверждение. Новая запись начнёт перевод заново."))
            else:
                self.same_button.setText(QCoreApplication.translate("source_update_dialog", "Это та же запись"))
                self.new_button.setText(QCoreApplication.translate("source_update_dialog", "Это новая запись"))
                self.guidance.setText(QCoreApplication.translate("source_update_dialog", "Проверьте, относится ли новая запись к тому же тексту. Если смысл сохранился — выберите \"Это та же запись\"."))
            self.reason.setText(QCoreApplication.translate("source_update_dialog", "{0}. Оценка совпадения: {1}").format(stored_message(item['reason']), f"{item['score']:.2f}"))
            value = self.decisions.get(item["review_key"])
            self.same_button.setChecked(value is True)
            self.new_button.setChecked(value is False)

    def choose(self, same):
        item = self.table.currentIndex().data(Qt.ItemDataRole.UserRole)
        if not item or "review_key" not in item:
            return
        key = item["review_key"]
        proposed = dict(self.decisions, **{key: same})
        try:
            self.analysis.resolved_matches(proposed, complete=False)
        except ValueError as error:
            QMessageBox.warning(self, QCoreApplication.translate("source_update_dialog", "Нельзя сопоставить запись"), str(error).replace(QCoreApplication.translate("source_update_dialog", "Считать новой"), QCoreApplication.translate("source_update_dialog", "Это новая запись")))
            self.show_detail()
            return
        self.decisions = proposed
        self.refresh(key)

    def accept(self):
        try:
            self.analysis.resolved_matches(self.decisions)
        except ValueError as error:
            QMessageBox.warning(self, QCoreApplication.translate("source_update_dialog", "Не все пары подтверждены"), str(error).replace(QCoreApplication.translate("source_update_dialog", "Считать новой"), QCoreApplication.translate("source_update_dialog", "Это новая запись")))
            return
        super().accept()
