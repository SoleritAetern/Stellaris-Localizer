"""Notebook-style glossary workspace; one reusable editor, flat painted rows."""

from PySide6.QtCore import QCoreApplication

from copy import deepcopy
from uuid import uuid4

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QSignalBlocker
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QStyledItemDelegate, QStyle,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import theme
from .glossary import GlossaryEntry, GlossarySection, SCOPES, normalized

ENTRY_ROLE = Qt.ItemDataRole.UserRole


def identity(entry):
    return entry.scope, entry.glossary_entry_id


class TermDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        explicit = index.data(Qt.ItemDataRole.SizeHintRole)
        if isinstance(explicit, QSize) and explicit.isValid():
            return explicit
        return QSize(180, 2 * option.fontMetrics.height() + 24)

    def paint(self, painter, option, index):
        entry = index.data(ENTRY_ROLE)
        if entry is None:
            return
        painter.save()
        painter.setClipRect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.fillRect(option.rect, theme.color('selection.row.bg' if selected else
                                            'surface.item_hover' if hovered else 'surface.window'))
        rect = option.rect.adjusted(12, 8, -36, -8)
        height = option.fontMetrics.height()
        font = option.font
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(theme.color('text.primary'))
        painter.drawText(QRect(rect.x(), rect.y(), rect.width(), height + 2), Qt.AlignmentFlag.AlignLeft,
                         painter.fontMetrics().elidedText(entry.english or QCoreApplication.translate("glossary_workspace", "Новая запись"), Qt.TextElideMode.ElideRight, rect.width()))
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(theme.color('text.primary' if entry.russian else 'text.secondary'))
        painter.drawText(QRect(rect.x(), rect.y() + height + 4, rect.width(), height + 2), Qt.AlignmentFlag.AlignLeft,
                         painter.fontMetrics().elidedText("→ " + (entry.russian or QCoreApplication.translate("glossary_workspace", "Перевод не выбран")),
                                                         Qt.TextElideMode.ElideRight, rect.width()))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(theme.color('icon.default'), 1.4))
        x, y = option.rect.right() - 18, option.rect.top() + 19
        path = QPainterPath()
        path.moveTo(x - 3, y - 2)
        path.lineTo(x, y + 1)
        path.lineTo(x + 3, y - 2)
        painter.drawPath(path)
        painter.restore()


class TermList(QListWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setItemDelegate(TermDelegate(self))
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setMouseTracking(True)
        self.setSpacing(2)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and item is not None:
            if event.position().x() >= self.visualItemRect(item).right() - 32:
                self.page.edit_item(item, details=True)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if item is not None and event.button() == Qt.MouseButton.LeftButton:
            rect = self.visualItemRect(item)
            if event.position().x() < rect.right() - 32:
                field = "english" if event.position().y() < rect.center().y() else "russian"
                self.page.edit_item(item, field=field)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            self.page.delete_entry()
            return
        if event.key() in (Qt.Key.Key_F2, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.page.edit_item(self.currentItem())
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.page.position_editor()


class TermEditor(QFrame):
    def __init__(self, page):
        super().__init__(page.list.viewport())
        self.page = page
        self.original = None
        self.is_new = False
        self.setObjectName("termEditor")
        self.setStyleSheet(f"QFrame#termEditor {{ background: {theme.css('surface.control')}; border: none; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 8)
        layout.setSpacing(5)
        basic = QFormLayout()
        basic.setVerticalSpacing(4)
        self.english, self.russian = QLineEdit(), QLineEdit()
        self.russian.setPlaceholderText(QCoreApplication.translate("glossary_workspace", "Перевод не выбран"))
        basic.addRow("English", self.english)
        basic.addRow("Russian", self.russian)
        layout.addLayout(basic)
        self.details = QWidget()
        fields = QFormLayout(self.details)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setVerticalSpacing(5)
        self.code = QLineEdit()
        self.alternatives, self.note = QPlainTextEdit(), QPlainTextEdit()
        self.alternatives.setPlaceholderText(QCoreApplication.translate("glossary_workspace", "Каждый вариант с новой строки"))
        self.alternatives.setFixedHeight(62)
        self.note.setFixedHeight(100)
        self.scope, self.section = QComboBox(), QComboBox()
        for scope in ("project", "global"):
            self.scope.addItem(SCOPES[scope], scope)
        for label, widget in (("Code", self.code), (QCoreApplication.translate("glossary_workspace", "Альтернативы"), self.alternatives),
                              (QCoreApplication.translate("glossary_workspace", "Заметка"), self.note), ("Scope", self.scope), (QCoreApplication.translate("glossary_workspace", "Раздел"), self.section)):
            fields.addRow(label, widget)
        layout.addWidget(self.details)
        actions = QHBoxLayout()
        actions.addStretch()
        save = QToolButton()
        save.setText(QCoreApplication.translate("glossary_workspace", "Готово"))
        save.setToolTip(QCoreApplication.translate("glossary_workspace", "Сохранить изменение · Enter / Ctrl+Enter"))
        save.clicked.connect(page.save_entry)
        cancel = QToolButton()
        cancel.setText(QCoreApplication.translate("glossary_workspace", "Отмена"))
        cancel.setToolTip(QCoreApplication.translate("glossary_workspace", "Отменить изменение · Esc"))
        cancel.clicked.connect(page.cancel_edit)
        actions.addWidget(save)
        actions.addWidget(cancel)
        layout.addLayout(actions)
        self.scope.currentIndexChanged.connect(self.load_sections)
        for widget in (self.english, self.russian, self.code, self.alternatives, self.note,
                       self.scope, self.section, save, cancel):
            widget.installEventFilter(self)
        self.hide()

    def load_sections(self):
        previous = self.section.currentData()
        self.section.clear()
        for key, name in self.page.glossary.section_choices(self.scope.currentData()):
            self.section.addItem(name, key)
        self.section.setCurrentIndex(max(0, self.section.findData(previous)))

    def load(self, entry, details, is_new=False):
        self.original = deepcopy(entry)
        self.is_new = is_new
        self.english.setText(entry.english)
        self.russian.setText(entry.russian)
        self.code.setText(entry.code or "")
        self.alternatives.setPlainText("\n".join(entry.alternatives))
        self.note.setPlainText(entry.note)
        self.scope.setCurrentIndex(self.scope.findData(entry.scope))
        self.load_sections()
        self.section.setCurrentIndex(max(0, self.section.findData(entry.section_id)))
        self.details.setVisible(details)
        self.layout().activate()

    def value(self):
        value = deepcopy(self.original)
        value.english, value.russian = self.english.text(), self.russian.text()
        if not self.details.isHidden():
            if self.code.text() != (value.code or ""):
                value.code = self.code.text() or None
            if self.alternatives.toPlainText() != "\n".join(value.alternatives):
                value.alternatives = [line for line in self.alternatives.toPlainText().splitlines() if line.strip()]
            value.note = self.note.toPlainText()
            value.section_id, value.scope = self.section.currentData(), self.scope.currentData()
        return value

    def dirty(self):
        return self.original is not None and (self.is_new or self.value() != self.original)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.page.cancel_edit()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if watched not in (self.note, self.alternatives) or event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    self.page.save_entry()
                    return True
        return super().eventFilter(watched, event)


class GlossaryPage(QWidget):
    def __init__(self, glossary, parent=None):
        super().__init__(parent)
        self.glossary = glossary
        self.items = {}
        self.editing = None
        self.section_key = ("all", None, None)
        self.section_signature = None
        self._saving = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        splitter = QSplitter()
        layout.addWidget(splitter)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)
        label = QLabel(QCoreApplication.translate("glossary_workspace", "Разделы"))
        label.setProperty("role", "section")
        left_layout.addWidget(label)
        self.sections = QTreeWidget()
        self.sections.setHeaderHidden(True)
        self.sections.setMinimumWidth(150)
        self.sections.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sections.customContextMenuRequested.connect(self.section_menu)
        self.sections.currentItemChanged.connect(self.section_changed)
        left_layout.addWidget(self.sections)
        add_section = QPushButton(QCoreApplication.translate("glossary_workspace", "+ Раздел"))
        add_section.setFlat(True)
        add_section.clicked.connect(lambda: self.new_section(False))
        left_layout.addWidget(add_section, 0, Qt.AlignmentFlag.AlignLeft)
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(QCoreApplication.translate("glossary_workspace", "Поиск в глоссарии…"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_entries)
        toolbar.addWidget(self.search, 1)
        add = QToolButton()
        add.setText("+")
        add.setToolTip(QCoreApplication.translate("glossary_workspace", "Новая запись"))
        add.clicked.connect(self.new_entry)
        toolbar.addWidget(add)
        right_layout.addLayout(toolbar)
        self.list = TermList(self)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.entry_menu)
        right_layout.addWidget(self.list)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setProperty("role", "muted")
        right_layout.addWidget(self.notice)
        splitter.addWidget(right)
        splitter.setCollapsible(0, False)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([190, 750])
        self.editor = TermEditor(self)
        self.list.verticalScrollBar().valueChanged.connect(self.position_editor)
        glossary.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        if self._saving:
            return
        entries = {identity(entry): entry for entry in self.glossary.entries()}
        for key in list(self.items):
            if key not in entries and key != self.editing:
                self.list.takeItem(self.list.row(self.items.pop(key)))
        for key, entry in entries.items():
            item = self.items.get(key)
            if item is None:
                item = QListWidgetItem()
                self.items[key] = item
                self.list.addItem(item)
            if item.data(ENTRY_ROLE) != entry:
                item.setText(entry.english)
                item.setData(ENTRY_ROLE, entry)
                item.setToolTip(f"{entry.english}\n→ {entry.russian or QCoreApplication.translate("glossary_workspace", "Перевод не выбран")}")
        self.refresh_sections()
        errors = [store.error for store in self.glossary.stores.values() if store.error]
        self.notice.setText("\n".join(errors))
        self.notice.setVisible(bool(errors))
        self.filter_entries()

    def refresh_sections(self):
        signature = tuple((scope, node.section_id, node.name, node.parent_id)
                          for scope, store in self.glossary.stores.items() for node in store.sections.values())
        if signature == self.section_signature:
            return
        expanded = {key for key, node in getattr(self, "nodes", {}).items() if node.isExpanded()}
        self.section_signature = signature
        self.nodes = {}
        with QSignalBlocker(self.sections):
            self.sections.clear()
            for kind, label in (("all", QCoreApplication.translate("glossary_workspace", "Все")), ("none", QCoreApplication.translate("glossary_workspace", "Без раздела"))):
                node = QTreeWidgetItem([label])
                key = (kind, None, None)
                node.setData(0, ENTRY_ROLE, key)
                self.sections.addTopLevelItem(node)
                self.nodes[key] = node
            for scope in ("project", "global"):
                store = self.glossary.stores[scope]
                def insert(section_id):
                    key = ("section", scope, section_id)
                    if key in self.nodes:
                        return self.nodes[key]
                    section = store.sections[section_id]
                    node = QTreeWidgetItem([section.name + (" · Global" if scope == "global" and not section.parent_id else "")])
                    node.setData(0, ENTRY_ROLE, key)
                    node.setToolTip(0, SCOPES[scope])
                    self.nodes[key] = node
                    if section.parent_id:
                        insert(section.parent_id).addChild(node)
                    else:
                        self.sections.addTopLevelItem(node)
                    node.setExpanded(key in expanded)
                    return node
                for section in sorted(store.sections.values(), key=lambda value: value.name.casefold()):
                    insert(section.section_id)
            if self.section_key not in self.nodes:
                self.section_key = ("all", None, None)
            self.sections.setCurrentItem(self.nodes[self.section_key])

    def filter_entries(self, *args):
        query = normalized(self.search.text())
        kind, scope, section_id = self.section_key
        allowed = {section_id}
        if kind == "section":
            # Include nested sections, without changing the saved hierarchy.
            pending = [section_id]
            children = {}
            for node in self.glossary.stores[scope].sections.values():
                children.setdefault(node.parent_id, []).append(node.section_id)
            while pending:
                for child in children.get(pending.pop(), []):
                    allowed.add(child)
                    pending.append(child)
        for key, item in self.items.items():
            entry = item.data(ENTRY_ROLE)
            if query:
                visible = query in normalized("\n".join([entry.english, entry.russian, entry.code or "", *entry.alternatives, entry.note]))
            else:
                visible = kind == "all" or (kind == "none" and entry.section_id is None) or (
                    kind == "section" and entry.scope == scope and entry.section_id in allowed)
            item.setHidden(not visible and key != self.editing)
        self.position_editor()

    def section_changed(self, current, previous):
        target = tuple(current.data(0, ENTRY_ROLE)) if current else ("all", None, None)
        if not self.resolve_draft():
            with QSignalBlocker(self.sections):
                self.sections.setCurrentItem(self.nodes.get(self.section_key))
            return
        self.section_key = target
        self.filter_entries()

    def destination(self):
        kind, scope, section = self.section_key
        return (scope, section) if kind == "section" else ("project", None)

    def position_editor(self, *args):
        editor = getattr(self, "editor", None)
        item = self.items.get(self.editing)
        if editor is None or item is None:
            return
        rect = self.list.visualItemRect(item)
        editor.setGeometry(rect.adjusted(2, 1, -2, -1))
        editor.raise_()

    def edit_item(self, item, details=False, field="english", is_new=False):
        if item is None:
            return
        key = identity(item.data(ENTRY_ROLE))
        if key == self.editing:
            if details:
                self.save_entry()
            else:
                getattr(self.editor, field).setFocus()
            return
        if not self.resolve_draft():
            return
        item = self.items.get(key)
        if item is None:
            return
        self.editing = key
        self.editor.load(item.data(ENTRY_ROLE), details, is_new)
        item.setHidden(False)
        self.list.setCurrentItem(item)
        item.setSizeHint(QSize(180, self.editor.sizeHint().height() + 2))
        self.list.doItemsLayout()
        self.list.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)
        self.position_editor()
        self.editor.show()
        getattr(self.editor, field).setFocus()

    def new_entry(self):
        if not self.resolve_draft():
            return
        scope, section = self.destination()
        entry = GlossaryEntry(str(uuid4()), "", section_id=section, scope=scope)
        item = QListWidgetItem()
        item.setData(ENTRY_ROLE, entry)
        self.items[identity(entry)] = item
        self.list.insertItem(0, item)
        self.edit_item(item, is_new=True)

    def save_entry(self):
        if self.editing is None:
            return True
        value = self.editor.value()
        original = self.editor.original
        if not self.editor.dirty():
            self.cancel_edit()
            return True
        existing = self.glossary.stores[original.scope].entries.get(original.glossary_entry_id)
        if not self.editor.is_new and existing != original:
            QMessageBox.warning(self, QCoreApplication.translate("glossary_workspace", "Запись изменена"), QCoreApplication.translate("glossary_workspace", "Запись уже изменена или удалена. Отмените черновик и откройте её заново."))
            return False
        try:
            self._saving = True
            self.glossary.save_entry(value, None if self.editor.is_new else original.scope)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, QCoreApplication.translate("glossary_workspace", "Глоссарий не сохранён"), str(error))
            return False
        finally:
            self._saving = False
        item = self.items.pop(self.editing)
        self.items[identity(value)] = item
        item.setData(ENTRY_ROLE, value)
        item.setText(value.english)
        item.setToolTip(f"{value.english}\n→ {value.russian or QCoreApplication.translate("glossary_workspace", "Перевод не выбран")}")
        self.editor.is_new = False
        self.editing = identity(value)
        self.cancel_edit()
        # The changed item is already updated; neither entries nor sections
        # need to be rebuilt after an edit performed by this page.
        return True

    def cancel_edit(self):
        if self.editing is None:
            return
        key, self.editing = self.editing, None
        item = self.items.get(key)
        if item is not None and (self.editor.is_new or key[1] not in self.glossary.stores[key[0]].entries):
            self.list.takeItem(self.list.row(self.items.pop(key)))
        elif item is not None:
            item.setData(Qt.ItemDataRole.SizeHintRole, None)
        self.editor.hide()
        self.editor.original = None
        self.list.setFocus()
        self.list.doItemsLayout()
        self.filter_entries()

    def resolve_draft(self):
        if self.editing is None:
            return True
        if not self.editor.dirty():
            self.cancel_edit()
            return True
        answer = QMessageBox.question(self, QCoreApplication.translate("glossary_workspace", "Глоссарий"), QCoreApplication.translate("glossary_workspace", "Сохранить изменения записи глоссария?"),
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.save_entry()
        self.cancel_edit()
        return True

    def delete_entry(self):
        item = self.list.currentItem()
        if item is None:
            return
        key = identity(item.data(ENTRY_ROLE))
        if not self.resolve_draft():
            return
        entry = self.glossary.stores[key[0]].entries.get(key[1])
        if entry and QMessageBox.question(self, QCoreApplication.translate("glossary_workspace", "Удалить запись"), QCoreApplication.translate("glossary_workspace", "Удалить «{0}» из глоссария?").format(entry.english)) == QMessageBox.StandardButton.Yes:
            self.operation(lambda: self.glossary.delete_entry(entry))

    def entry_menu(self, point):
        item = self.list.itemAt(point)
        menu = QMenu(self)
        if item:
            self.list.setCurrentItem(item)
            menu.addAction(QCoreApplication.translate("glossary_workspace", "Изменить English"), lambda: self.edit_item(item))
            menu.addAction(QCoreApplication.translate("glossary_workspace", "Изменить Russian"), lambda: self.edit_item(item, field="russian"))
            menu.addAction(QCoreApplication.translate("glossary_workspace", "Подробности / переместить в раздел…"), lambda: self.edit_item(item, details=True))
            menu.addAction(QCoreApplication.translate("glossary_workspace", "Удалить"), self.delete_entry)
            menu.addSeparator()
        menu.addAction(QCoreApplication.translate("glossary_workspace", "Перечитать"), self.reload)
        menu.exec(self.list.viewport().mapToGlobal(point))
        menu.deleteLater()

    def operation(self, callback):
        try:
            callback()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, QCoreApplication.translate("glossary_workspace", "Глоссарий не сохранён"), str(error))

    def section_menu(self, point):
        node = self.sections.itemAt(point)
        if node is None or tuple(node.data(0, ENTRY_ROLE))[0] != "section":
            return
        self.sections.setCurrentItem(node)
        if self.section_key != tuple(node.data(0, ENTRY_ROLE)):
            return
        menu = QMenu(self)
        menu.addAction(QCoreApplication.translate("glossary_workspace", "Новый подраздел"), lambda: self.new_section(True))
        menu.addAction(QCoreApplication.translate("glossary_workspace", "Переименовать"), self.rename_section)
        menu.addAction(QCoreApplication.translate("glossary_workspace", "Удалить"), self.delete_section)
        menu.exec(self.sections.viewport().mapToGlobal(point))
        menu.deleteLater()

    def new_section(self, subsection=False):
        if not self.resolve_draft():
            return
        scope, parent = self.destination()
        if not subsection:
            label, accepted = QInputDialog.getItem(self, QCoreApplication.translate("glossary_workspace", "Новый раздел"), "Scope", [SCOPES["project"], SCOPES["global"]], editable=False)
            if not accepted:
                return
            scope, parent = ("project" if label == SCOPES["project"] else "global"), None
        name, accepted = QInputDialog.getText(self, QCoreApplication.translate("glossary_workspace", "Новый подраздел") if subsection else QCoreApplication.translate("glossary_workspace", "Новый раздел"), QCoreApplication.translate("glossary_workspace", "Название"))
        if accepted:
            self.operation(lambda: self.glossary.save_section(GlossarySection(str(uuid4()), name, scope, parent)))

    def rename_section(self):
        if not self.resolve_draft():
            return
        _, scope, key = self.section_key
        node = deepcopy(self.glossary.stores[scope].sections[key])
        name, accepted = QInputDialog.getText(self, QCoreApplication.translate("glossary_workspace", "Переименовать раздел"), QCoreApplication.translate("glossary_workspace", "Название"), text=node.name)
        if accepted:
            node.name = name
            self.operation(lambda: self.glossary.save_section(node))

    def delete_section(self):
        if not self.resolve_draft():
            return
        _, scope, key = self.section_key
        if QMessageBox.question(self, QCoreApplication.translate("glossary_workspace", "Удалить раздел"), QCoreApplication.translate("glossary_workspace", "Удалить раздел?\nЗаписи останутся без раздела. Подразделы будут подняты на уровень выше.")) == QMessageBox.StandardButton.Yes:
            self.operation(lambda: self.glossary.delete_section(scope, key))

    def reload(self):
        if self.resolve_draft():
            self.glossary.reload()
