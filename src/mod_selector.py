"""Read-only selection of an existing mod, shared by creation and delivery."""

from PySide6.QtCore import QCoreApplication

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, Signal, QSortFilterProxyModel
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QFrame, QLineEdit, QListView,
    QMessageBox, QPushButton, QStyledItemDelegate, QStyle, QStyleOptionComboBox,
    QVBoxLayout, QWidget, QSizePolicy,
)

from src import theme
from src.app_settings import AppSettings
from src.compound_control import CompoundControl
from src.mod_catalog import ModCatalog
from src.mod_discovery import LOCAL, descriptor_name, path_identity


MOD_INFO_ROLE = int(Qt.ItemDataRole.UserRole) + 1
CATALOG_ITEM_ROLE = MOD_INFO_ROLE + 1


def localisation_summary(info, language):
    label = "EN" if language == "english" else "RU"
    count = getattr(info, language) if info is not None else None
    if count is None:
        return QCoreApplication.translate("mod_selector", "{0}: нет данных").format(label)
    if count == 0:
        return QCoreApplication.translate("mod_selector", "{0}: нет локализации").format(label)
    return QCoreApplication.translate("mod_selector", "{0}: файлов — {1}").format(label, count)


def mod_name(root):
    # Compatibility for manually bound project paths, not automatic discovery.
    return descriptor_name(root) or Path(root).name


def russian_localisation(root):
    """Use the existing initial-RU discovery rule, without writing to the mod."""
    localisation = Path(root) / "localisation"
    if localisation.is_dir() and any(localisation.rglob("*_l_russian.yml")):
        return localisation
    return None


class HoverHelpLabel(QLabel):
    def __init__(self, text, explanation):
        super().__init__(text)
        self.setAccessibleDescription(explanation)
        self.setProperty("role", "help")
        self.preview = QLabel(explanation, self, Qt.WindowType.ToolTip)
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setWordWrap(True)
        self.preview.setMargin(8)
        self.preview.setStyleSheet(
            f"background: {theme.css('surface.popover')}; color: {theme.css('text.primary')}; "
            f"border: 1px solid {theme.css('border.subtle')};"
        )
        self.preview.hide()

    def enterEvent(self, event):
        super().enterEvent(event)
        bounds = self.screen().availableGeometry()
        self.preview.setFixedWidth(min(420, bounds.width()))
        self.preview.adjustSize()
        point = self.mapToGlobal(QPoint(0, self.height() + 3))
        if point.y() + self.preview.height() > bounds.bottom():
            point.setY(self.mapToGlobal(QPoint()).y() - self.preview.height() - 3)
        point.setX(max(bounds.left(), min(point.x(), bounds.right() - self.preview.width() + 1)))
        point.setY(max(bounds.top(), point.y()))
        self.preview.move(point)
        self.preview.show()

    def event(self, event):
        if event.type() in (QEvent.Type.Leave, QEvent.Type.Hide, QEvent.Type.MouseButtonPress):
            preview = getattr(self, "preview", None)
            if preview is not None:
                preview.hide()
        return super().event(event)


class ModOptionDelegate(QStyledItemDelegate):
    """Descriptor name first, directory name second; no widgets per option."""

    def __init__(self, parent, language):
        super().__init__(parent)
        self.language = language

    def sizeHint(self, option, index):
        # Both lines are elided to the popup viewport; long mod names must not
        # make the list horizontally scrollable.
        return QSize(1, option.fontMetrics.height() * 2 + 10)

    def paint(self, painter, option, index):
        painter.save()
        painter.setClipRect(option.rect)
        current = self.parent().currentIndex() == index
        if current or option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, theme.color('surface.selected'))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, theme.color('surface.hover'))
        rect = option.rect.adjusted(8, 3, -8, -3)
        height = option.fontMetrics.height()
        painter.setFont(option.font)
        painter.setPen(theme.color('text.primary'))
        painter.drawText(QRect(rect.x(), rect.y(), rect.width(), height), Qt.AlignmentFlag.AlignLeft,
                         option.fontMetrics.elidedText(index.data() or "", Qt.TextElideMode.ElideRight, rect.width()))
        path = index.data(Qt.ItemDataRole.UserRole)
        if path:
            font = QFont(option.font)
            font.setPointSizeF(max(8, font.pointSizeF() - 1))
            painter.setFont(font)
            painter.setPen(theme.color('text.secondary'))
            summary = localisation_summary(index.data(MOD_INFO_ROLE), self.language)
            painter.drawText(QRect(rect.x(), rect.y() + height, rect.width(), height), Qt.AlignmentFlag.AlignLeft,
                             QFontMetrics(font).elidedText(f"{Path(path).name} · {summary}", Qt.TextElideMode.ElideRight, rect.width()))
        painter.restore()


class ModNameFilter(QSortFilterProxyModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.query = ""
        self.setDynamicSortFilter(True)

    def set_query(self, query):
        self.beginFilterChange()
        self.query = query.casefold()
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, row, parent):
        index = self.sourceModel().index(row, 0, parent)
        return bool(index.data(CATALOG_ITEM_ROLE)) and self.query in (index.data() or "").casefold()

    def lessThan(self, left, right):
        return ((left.data() or "").casefold(), left.data(Qt.ItemDataRole.UserRole) or "") < (
            (right.data() or "").casefold(), right.data(Qt.ItemDataRole.UserRole) or "")


class ModPopup(QFrame):
    def __init__(self, combo, language):
        super().__init__(combo, Qt.WindowType.Popup)
        self.combo = combo
        self.loading = False
        self.sources_available = None
        self.setObjectName("modPickerPopup")
        self.setStyleSheet(f"QFrame#modPickerPopup {{ background: {theme.css('surface.popover')}; "
                          f"border: 1px solid {theme.css('border.subtle')}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        self.search = QLineEdit()
        self.search.setPlaceholderText(QCoreApplication.translate("mod_selector", "Поиск по названию…"))
        self.search.setAccessibleName(self.search.placeholderText())
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.proxy = ModNameFilter(self)
        self.proxy.setSourceModel(combo.model())
        self.proxy.sort(0)
        self.list = QListView()
        self.list.setModel(self.proxy)
        self.list.setUniformItemSizes(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMouseTracking(True)
        self.list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.list.setItemDelegate(ModOptionDelegate(self.list, language))
        layout.addWidget(self.list, 1)
        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(10, 12, 10, 12)
        self.empty_text = QLabel()
        self.empty_text.setWordWrap(True)
        self.empty_text.setProperty("role", "muted")
        empty_layout.addWidget(self.empty_text)
        layout.addWidget(self.empty, 1)
        self.notice = QLabel()
        self.notice.setProperty("role", "muted")
        layout.addWidget(self.notice)
        self.search.textChanged.connect(self._query_changed)
        self.list.clicked.connect(self.choose)
        self.list.activated.connect(self.choose)
        self.search.installEventFilter(self)
        self.list.installEventFilter(self)

    def _query_changed(self, query):
        self.proxy.set_query(query)
        self.list.setCurrentIndex(self.proxy.index(0, 0))
        self.list.scrollToTop()
        self.refresh_notice()

    def set_loading(self, loading, sources_available=None):
        self.loading = loading
        self.sources_available = sources_available
        self.refresh_notice()

    def refresh_notice(self, *args):
        has_items = self.proxy.rowCount() > 0
        no_sources = not self.loading and self.sources_available is False
        if self.loading:
            text = QCoreApplication.translate("mod_selector", "Поиск модов…")
        elif no_sources:
            text = QCoreApplication.translate("mod_selector", "Папки модов не настроены или недоступны.\nУкажите расположение модов в Настройках или выберите мод вручную.")
        elif self.search.text() and any(self.combo.itemData(i, CATALOG_ITEM_ROLE)
                                       for i in range(self.combo.count())):
            text = QCoreApplication.translate("mod_selector", "Совпадений нет")
        else:
            text = QCoreApplication.translate("mod_selector", "Моды не найдены. Выберите мод вручную.")
        self.list.setVisible(has_items)
        self.empty.setVisible(not has_items)
        self.empty_text.setText(text)
        self.notice.setText(text if self.loading else "")
        self.notice.setVisible(self.loading and has_items)
        if self.isVisible():
            self.update_geometry()

    def update_geometry(self):
        """Fit actual content, capped at nine two-line results and screen space."""
        self.ensurePolished()
        bounds = self.combo.screen().availableGeometry()
        width = min(max(self.combo.width(), 460), bounds.width())
        margins = self.layout().contentsMargins()
        frame = self.frameWidth() * 2
        inner_width = max(1, width - margins.left() - margins.right() - frame)
        count = self.proxy.rowCount()
        if count:
            row_height = self.list.sizeHintForRow(0)
            body = min(count, 9) * row_height + self.list.frameWidth() * 2
        else:
            empty_margins = self.empty.layout().contentsMargins()
            text_width = max(1, inner_width - empty_margins.left() - empty_margins.right())
            body = max(self.empty_text.heightForWidth(text_width), self.empty_text.sizeHint().height())
            body += empty_margins.top() + empty_margins.bottom()
        height = (margins.top() + margins.bottom() + frame + self.search.sizeHint().height()
                  + self.layout().spacing() + body)
        if self.loading and count:
            height += self.layout().spacing() + self.notice.sizeHint().height()
        below = self.combo.mapToGlobal(QPoint(0, self.combo.height()))
        above = self.combo.mapToGlobal(QPoint()).y()
        space_below = bounds.bottom() + 1 - below.y()
        space_above = above - bounds.top()
        height = min(height, max(1, space_below, space_above))
        y = below.y() if height <= space_below else above - height
        x = max(bounds.left(), min(below.x(), bounds.right() + 1 - width))
        self.setGeometry(QRect(x, max(bounds.top(), y), width, height))

    def choose(self, index):
        if index.isValid():
            self.combo.setCurrentIndex(self.proxy.mapToSource(index).row())
            self.combo.hidePopup()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.combo.hidePopup()
                return True
            if watched is self.search and event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = 0 if event.key() == Qt.Key.Key_Down else self.proxy.rowCount() - 1
                self.list.setCurrentIndex(self.proxy.index(row, 0))
                self.list.setFocus()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.choose(self.proxy.index(0, 0) if watched is self.search else self.list.currentIndex())
                return True
        return super().eventFilter(watched, event)

    def hideEvent(self, event):
        # Clicking outside the custom popup must reset QComboBox's pressed state,
        # just like choosing an item or pressing Esc.
        super().hideEvent(event)
        QComboBox.hidePopup(self.combo)

    def mousePressEvent(self, event):
        # Qt replays outside-popup presses to the widget underneath. Suppress
        # only a press on our own opener, which would otherwise open us again.
        on_opener = self.combo.rect().contains(self.combo.mapFromGlobal(event.globalPosition().toPoint()))
        if not self.rect().contains(event.position().toPoint()) and on_opener:
            self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay, True)
            self.combo.hidePopup()
            event.accept()
            return
        super().mousePressEvent(event)


class ModComboBox(QComboBox):
    """Paint a themed chevron in the existing native drop-down area."""

    def __init__(self, language):
        super().__init__()
        self.setObjectName("modSelectorCombo")
        self.popup = ModPopup(self, language)

    def showPopup(self):
        if self.popup.isVisible():
            self.hidePopup()
            return
        self.popup.search.clear()
        self.popup.refresh_notice()
        self.popup.update_geometry()
        selected = self.popup.proxy.mapFromSource(self.model().index(self.currentIndex(), 0))
        self.popup.list.setCurrentIndex(selected)
        if selected.isValid():
            self.popup.list.scrollTo(selected)
        self.popup.show()
        self.popup.search.setFocus(Qt.FocusReason.PopupFocusReason)

    def hidePopup(self):
        self.popup.hide()
        super().hidePopup()

    def mousePressEvent(self, event):
        # Also handle direct (non-replayed) presses, e.g. with keyboard-opened
        # popups on platforms that route the press straight to the combo.
        if event.button() == Qt.MouseButton.LeftButton and self.popup.isVisible():
            self.hidePopup()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                           QStyle.SubControl.SC_ComboBoxArrow, self)
        role = ("icon.disabled" if not self.isEnabled() else
                "icon.hover" if self.underMouse() or self.hasFocus() else "icon.default")
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(theme.color(role), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        x, y = arrow.center().x(), arrow.center().y()
        painter.drawLine(QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5))
        painter.drawLine(QPointF(x, y + 1.5), QPointF(x + 3, y - 1.5))
        painter.end()


class ModSelector(QWidget):
    selectionChanged = Signal()

    def __init__(self, mods_directory, parent=None, *, current=None, settings=None,
                 catalog=None, language="russian", compound=False, source_kind=LOCAL):
        super().__init__(parent)
        self.settings = settings if settings is not None else AppSettings()
        self.mods_directory = Path(mods_directory) if mods_directory is not None else None
        self.language = language
        self.source_kind = source_kind
        self.catalog = catalog if catalog is not None else ModCatalog(self)
        self.placeholder = (QCoreApplication.translate("mod_selector", "Выберите исходный мод…") if language == "english"
                            else QCoreApplication.translate("mod_selector", "Выберите мод перевода…"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.setup_hint = QWidget()
        setup_layout = QHBoxLayout(self.setup_hint)
        setup_layout.setContentsMargins(0, 0, 0, 0)
        setup_label = QLabel(QCoreApplication.translate("mod_selector", "Папки модов не настроены или недоступны."))
        setup_label.setProperty("role", "muted")
        setup_label.setWordWrap(True)
        setup_layout.addWidget(setup_label, 1)
        self.settings_button = QPushButton(QCoreApplication.translate("mod_selector", "Открыть настройки"))
        self.settings_button.setFlat(True)
        self.settings_button.clicked.connect(self.open_settings)
        setup_layout.addWidget(self.settings_button)
        layout.addWidget(self.setup_hint)
        self.combo = ModComboBox(language)
        self.combo.setMinimumContentsLength(28)
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.addItem(self.placeholder, None)
        self.browse = QPushButton(QCoreApplication.translate("mod_selector", "Найти") if compound
                                  else QCoreApplication.translate("mod_selector", "Найти вручную…"))
        self.browse.setToolTip(QCoreApplication.translate("mod_selector", "Выберите корневую папку мода, если его нет в списке."))
        if compound:
            self.control = CompoundControl(self.combo, self.browse)
            layout.addWidget(self.control)
        else:
            controls = QHBoxLayout()
            controls.addWidget(self.combo, 1)
            controls.addWidget(self.browse, 0, Qt.AlignmentFlag.AlignRight)
            layout.addLayout(controls)
        self.details = QLabel()
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setWordWrap(True)
        self.details.setProperty("role", "muted")
        layout.addWidget(self.details)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.combo.currentIndexChanged.connect(self._selected)
        self.browse.clicked.connect(self.choose_manually)
        self.catalog.reset.connect(self._reset)
        self.catalog.added.connect(self._add_batch)
        self.catalog.inspected.connect(self._inspected)
        self.catalog.loadingChanged.connect(self._loading_changed)
        self.setup_hint.hide()
        if catalog is None:
            self.reload(mods_directory)
        else:
            self._add_batch(list(self.catalog.items.values()))
            self._loading_changed(self.catalog.loading)
        if current:
            self.set_path(current)

    def reload(self, mods_directory):
        self.mods_directory = Path(mods_directory) if mods_directory is not None else None
        self.catalog.start(self.mods_directory, self.settings.workshop_directory)

    def _reset(self):
        self.mods_directory = self.catalog.mods_directory
        current = self.path()
        name = self.selected_name()
        blocked = self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(self.placeholder, None)
        if current:
            self._add(name, current)
            self.combo.setCurrentIndex(1)
        self.combo.blockSignals(blocked)
        self._selected()

    def _index_for(self, path):
        identity = path_identity(path)
        return next((i for i in range(1, self.combo.count())
                     if path_identity(self.combo.itemData(i)) == identity), -1)

    def _add_batch(self, items, *, discovered=True):
        current = self.path()
        updated = False
        blocked = self.combo.blockSignals(True)
        for info in items:
            if discovered and self.source_kind not in info.sources:
                continue
            index = self._index_for(info.path)
            if index < 0:
                self._add(info.name, info.path, info)
                index = self.combo.count() - 1
            else:
                self.combo.setItemText(index, info.name)
                self.combo.setItemData(index, info, MOD_INFO_ROLE)
            if discovered:
                self.combo.setItemData(index, True, CATALOG_ITEM_ROLE)
            updated |= current is not None and path_identity(current) == path_identity(info.path)
        self.combo.blockSignals(blocked)
        self._loading_changed(self.catalog.loading)
        if updated:
            self._selected()

    def _inspected(self, info):
        # A manually chosen nonstandard folder is not an automatic candidate in
        # the other selector. Only update an already present/selected item.
        if self._index_for(info.path) >= 0:
            self._add_batch([info], discovered=False)

    def _loading_changed(self, loading):
        available = None if loading else self.source_kind in self.catalog.available_sources
        self.combo.popup.set_loading(loading, available)
        self.setup_hint.setVisible(available is False)

    def open_settings(self):
        # Local import: Settings reuses FolderRow from project_dialog.
        from src.settings_dialog import SettingsDialog

        self.combo.hidePopup()
        dialog = SettingsDialog(self.settings, self.window())
        dialog.tabs.setCurrentIndex(0)  # Existing Paths page.
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.reload(self.settings.mods_directory)
        finally:
            dialog.deleteLater()

    def _add(self, name, path, info=None):
        self.combo.addItem(name, str(path))
        # A retained/manual selection is not an automatic discovery result.
        self.combo.setItemData(self.combo.count() - 1, False, CATALOG_ITEM_ROLE)
        self.combo.setItemData(self.combo.count() - 1, str(path), Qt.ItemDataRole.ToolTipRole)
        self.combo.setItemData(self.combo.count() - 1, info, MOD_INFO_ROLE)

    def path(self):
        value = self.combo.currentData()
        return Path(value) if value else None

    def selected_name(self):
        return self.combo.currentText() if self.path() else ""

    def set_path(self, path):
        if path is None:
            self.combo.setCurrentIndex(0)
            return
        path = Path(path).resolve()
        index = self._index_for(path)
        if index < 0:
            info = self.catalog.items.get(path_identity(path))
            self._add(info.name if info else path.name, path, info)
            index = self.combo.count() - 1
        self.combo.setCurrentIndex(index)

    def choose_manually(self):
        folder = QFileDialog.getExistingDirectory(self, QCoreApplication.translate("mod_selector", "Выберите корневую папку мода"),
                                                   str(self.path() or self.mods_directory or ""))
        if folder:
            self.set_path(folder)

    def _selected(self, *args):
        path = self.path()
        self.details.setText(f"{self.selected_name()}\n{path}" if path else "")
        self.details.setVisible(path is not None)
        success = False
        try:
            if path is None:
                message = ""
            elif not path.is_dir():
                message = QCoreApplication.translate("mod_selector", "Папка мода недоступна")
            else:
                info = self.combo.currentData(MOD_INFO_ROLE)
                if info is None:
                    message = QCoreApplication.translate("mod_selector", "Проверка локализации…")
                    self.catalog.inspect_path(path)
                elif info.state != "ready":
                    message = QCoreApplication.translate("mod_selector", "Не удалось проверить локализацию выбранного мода")
                else:
                    message = localisation_summary(info, self.language)
                    success = self.language == "russian" or bool(info.english)
        except OSError:
            message = QCoreApplication.translate("mod_selector", "Не удалось прочитать локализацию выбранного мода")
        self.status.setText(message)
        self.status.setVisible(bool(message))
        self.status.setStyleSheet(f"color: {theme.css('status.ready') if success else theme.css('text.secondary')}; font-size: 11px;")
        self.selectionChanged.emit()

    def hideEvent(self, event):
        self.combo.hidePopup()
        super().hideEvent(event)


class ModTargetDialog(QDialog):
    def __init__(self, mods_directory, parent=None, *, current=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle(QCoreApplication.translate("mod_selector", "Папка русского мода"))
        self.resize(650, 220)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(QCoreApplication.translate("mod_selector", "Выберите существующий русский мод для загрузки перевода.")))
        self.selector = ModSelector(mods_directory, self, current=current, settings=settings)
        self.finished.connect(self.selector.catalog.stop)
        layout.addWidget(self.selector)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(QCoreApplication.translate("mod_selector", "Выбрать"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(QCoreApplication.translate("mod_selector", "Отмена"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        path = self.selector.path()
        if path is None or not path.is_dir():
            QMessageBox.warning(self, QCoreApplication.translate("mod_selector", "Папка не выбрана"), QCoreApplication.translate("mod_selector", "Выберите существующую папку русского мода."))
            return
        super().accept()
