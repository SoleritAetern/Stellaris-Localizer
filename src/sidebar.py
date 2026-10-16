"""Shared Explorer/Search sidebar; persistence is owned by the project UI."""

from PySide6.QtCore import QCoreApplication

from PySide6.QtCore import QPointF, QSize, Qt, Signal, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QSplitter,
    QStackedWidget, QToolButton, QVBoxLayout, QWidget, QSizePolicy,
)


from .theme import SURFACE_GAP
from .search_results import SearchResults
from .icon_buttons import HoverToolButton
from .resources import themed_svg_icon
from . import app_actions, theme


class SearchSelector(QComboBox):
    """Existing combo behavior with a theme-independent painted chevron."""

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"""
            QComboBox {{ background: {theme.css('surface.control')}; border: 1px solid {theme.css('border.subtle')};
                border-radius: 4px; padding: 4px 24px 4px 8px; }}
            QComboBox:hover {{ background: {theme.css('surface.hover')}; border-color: {theme.css('border.hover')}; }}
            QComboBox:focus {{ border-color: {theme.css('border.selector_focus')}; }}
            QComboBox:on {{ background: {theme.css('surface.selected')}; border-color: {theme.css('border.selector_focus')}; }}
            QComboBox::drop-down {{ width: 20px; border: none; }}
            QComboBox::down-arrow {{ image: none; }}
        """)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(theme.color('icon.default'), 1.5,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        x, y = self.width() - 13, self.height() / 2
        painter.drawLine(QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5))
        painter.drawLine(QPointF(x, y + 1.5), QPointF(x + 3, y - 1.5))
        painter.end()

def activity_icon(page):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(theme.color("icon.default"), 1.5))
    if page == "explorer":
        painter.drawRect(7, 6, 12, 15)
        painter.drawLine(4, 17, 4, 3)
        painter.drawLine(4, 3, 15, 3)
    else:
        painter.drawEllipse(4, 3, 12, 12)
        painter.drawLine(15, 15, 21, 21)
    painter.end()
    return QIcon(pixmap)


class SearchScope(QFrame):
    """Two independent segments mapped to the existing three search scopes."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("searchScope")
        self.setAccessibleName(QCoreApplication.translate("sidebar", "Область поиска"))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.segments = {}
        for scope, title in (("mod", QCoreApplication.translate("sidebar", "Мод")), ("vanilla", "Vanilla")):
            if self.segments:
                divider = QFrame()
                divider.setObjectName("scopeDivider")
                divider.setFixedWidth(1)
                layout.addWidget(divider)
            button = QToolButton()
            button.setObjectName(scope + "Segment")
            button.setText(title)
            button.setAccessibleName(title)
            button.setCheckable(True)
            button.setChecked(scope == "mod")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            button.clicked.connect(self.segment_clicked)
            self.segments[scope] = button
            layout.addWidget(button, 1)
        self.segments["mod"].setToolTip(QCoreApplication.translate("sidebar", "Мод: локализация и текст Glossary."))
        self.setStyleSheet(f"""
            QFrame#searchScope {{ background: {theme.css('surface.control')};
                border: 1px solid {theme.css('border.subtle')}; border-radius: 4px; }}
            QFrame#scopeDivider {{ background: {theme.css('divider')}; border: none; }}
            QFrame#searchScope QToolButton {{ background: transparent; color: {theme.css('text.secondary')};
                border: none; border-bottom: 2px solid transparent; padding: 2px 5px 0px; }}
            QToolButton#modSegment {{ border-top-left-radius: 3px; border-bottom-left-radius: 3px; }}
            QToolButton#vanillaSegment {{ border-top-right-radius: 3px; border-bottom-right-radius: 3px; }}
            QFrame#searchScope QToolButton:hover {{ background: {theme.css('surface.hover')}; }}
            QFrame#searchScope QToolButton:checked {{ background: {theme.css('surface.selected')}; color: {theme.css('text.primary')}; }}
            QFrame#searchScope QToolButton:checked:hover,
            QFrame#searchScope QToolButton:pressed {{ background: {theme.css('surface.pressed')}; color: {theme.css('text.primary')}; }}
            QFrame#searchScope QToolButton:focus {{ border-bottom-color: {theme.css('border.selector_focus')}; }}
        """)

    def segment_clicked(self):
        # Repair an unchecked last segment before publishing the new scope.
        # Toggling it selects the other source, never an empty search set.
        if not any(button.isChecked() for button in self.segments.values()):
            for button in self.segments.values():
                if button is not self.sender():
                    button.setChecked(True)
        self.changed.emit()

    def currentData(self):
        if all(button.isChecked() for button in self.segments.values()):
            return "both"
        return "mod" if self.segments["mod"].isChecked() else "vanilla"


class WorkspaceSidebar(QWidget):
    searchRequested = Signal(str, str, str)
    searchChanged = Signal()

    def __init__(self, file_tree, workspace, parent=None):
        super().__init__(parent)
        self.active_page = "explorer"
        self._sidebar_width = 300
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, SURFACE_GAP // 2, SURFACE_GAP // 2, SURFACE_GAP // 2)
        layout.setSpacing(SURFACE_GAP // 2)

        activity_bar = QWidget()
        activity_bar.setObjectName("activityBar")
        activity_bar.setFixedWidth(44)
        buttons_layout = QVBoxLayout(activity_bar)
        buttons_layout.setContentsMargins(4, 6, 4, 0)
        buttons_layout.setSpacing(5)
        self.buttons = {}
        for page, title in (("explorer", "Explorer"), ("search", "Search")):
            button = QToolButton()
            button.setAccessibleName(title)
            button.setToolTip(title)
            button.setCheckable(True)
            button.setFixedSize(36, 36)
            button.setIcon(activity_icon(page))
            button.setIconSize(QSize(22, 22))
            button.clicked.connect(lambda checked=False, page=page: self.activate_page(page))
            buttons_layout.addWidget(button)
            self.buttons[page] = button
        buttons_layout.addStretch()
        self.boosty_button = HoverToolButton(QCoreApplication.translate("sidebar", "Поддержать на Boosty"))
        self.boosty_button.setProperty("role", "action")
        self.boosty_button.setFixedSize(36, 36)
        self.boosty_button.setIconSize(QSize(22, 22))
        self.boosty_button.setIcon(themed_svg_icon("boosty.svg", 22))
        self.boosty_button.clicked.connect(app_actions.open_boosty)
        buttons_layout.addWidget(self.boosty_button)
        self.guide_button = HoverToolButton(QCoreApplication.translate("sidebar", "Инструкция"))
        self.guide_button.setProperty("role", "action")
        self.guide_button.setFixedSize(36, 36)
        self.guide_button.setIconSize(QSize(22, 22))
        self.guide_button.setIcon(themed_svg_icon("guide_book.svg", 22))
        self.guide_button.clicked.connect(lambda: app_actions.show_guide(self.window()))
        buttons_layout.addWidget(self.guide_button)
        # An action at the foot of the bar, not another checkable sidebar page.
        # AppController binds the existing Settings action and shared gear icon.
        self.settings_button = QToolButton()
        self.settings_button.setAccessibleName(QCoreApplication.translate("sidebar", "Настройки"))
        self.settings_button.setToolTip(QCoreApplication.translate("sidebar", "Настройки"))
        self.settings_button.setFixedSize(36, 36)
        self.settings_button.setIconSize(QSize(22, 22))
        self.settings_button.setEnabled(False)
        buttons_layout.addWidget(self.settings_button)
        layout.addWidget(activity_bar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("sidebarStack")
        self.stack.setMinimumWidth(280)
        self.stack.setMaximumWidth(500)
        explorer = QWidget()
        explorer_layout = QVBoxLayout(explorer)
        explorer_title = QLabel("EXPLORER")
        explorer_title.setProperty("role", "section")
        explorer_layout.addWidget(explorer_title)
        file_tree.setMinimumWidth(0)
        explorer_layout.addWidget(file_tree)
        self.stack.addWidget(explorer)

        search = QWidget()
        search_layout = QVBoxLayout(search)
        search_title = QLabel("SEARCH")
        search_title.setProperty("role", "section")
        search_layout.addWidget(search_title)
        self.search_layout = search_layout
        self.replace_expanded = False
        query_row = QHBoxLayout()
        query_row.setSpacing(4)
        self.replace_toggle = QToolButton()
        self.replace_toggle.setCheckable(True)
        self.replace_toggle.setAutoRaise(True)
        self.replace_toggle.setFixedWidth(22)
        self.replace_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.replace_toggle.setToolTip(QCoreApplication.translate("sidebar", "Раскрыть замену · только RU текущего мода"))
        self.replace_toggle.setAccessibleName(QCoreApplication.translate("sidebar", "Раскрыть замену"))
        query_row.addWidget(self.replace_toggle)
        self.query_box = QLineEdit()
        self.query_box.setPlaceholderText(QCoreApplication.translate("sidebar", "Поиск"))
        self.query_box.setAccessibleName(QCoreApplication.translate("sidebar", "Поисковый запрос"))
        query_row.addWidget(self.query_box, 1)
        search_layout.addLayout(query_row)
        self.replace_controls = QWidget()
        self.replace_controls.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        replace_layout = QHBoxLayout(self.replace_controls)
        replace_layout.setContentsMargins(26, 0, 0, 0)
        self.replacement_box = QLineEdit()
        self.replacement_box.setPlaceholderText(QCoreApplication.translate("sidebar", "Заменить на…"))
        self.replacement_box.setAccessibleName(QCoreApplication.translate("sidebar", "Текст замены"))
        replace_layout.addWidget(self.replacement_box)
        search_layout.addWidget(self.replace_controls)
        self.replace_controls.setMaximumHeight(0)
        self.replace_controls.hide()
        self.replace_animation = QPropertyAnimation(self.replace_controls, b"maximumHeight", self)
        self.replace_animation.setDuration(140)
        self.replace_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.replace_animation.finished.connect(self._finish_replace_animation)
        options_row = QHBoxLayout()
        options_row.setContentsMargins(26, 0, 0, 0)
        options_row.setSpacing(4)
        self.options_stack = QStackedWidget()
        self.options_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search_options = QWidget()
        options = QHBoxLayout(self.search_options)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(4)
        self.query_mode = SearchSelector()
        for title, mode in (("Auto", "auto"), ("Key", "key"), ("EN", "en"), ("RU", "ru")):
            self.query_mode.addItem(title, mode)
        self.query_mode.setToolTip(QCoreApplication.translate("sidebar", "Auto: кириллица — RU, иначе — EN. Key: поиск по ключу."))
        self.query_mode.setAccessibleName(QCoreApplication.translate("sidebar", "Режим поиска"))
        self.query_scope = SearchScope()
        self.query_scope.setFixedHeight(self.query_mode.sizeHint().height())
        options.addWidget(self.query_mode)
        options.addWidget(self.query_scope, 1)
        self.options_stack.addWidget(self.search_options)
        replace_target = QLabel(QCoreApplication.translate("sidebar", "RU · Мод"))
        replace_target.setProperty("role", "muted")
        replace_target.setToolTip(QCoreApplication.translate("sidebar", "Только рабочий RU. Непереведённые строки и английские заглушки исключены."))
        self.options_stack.addWidget(replace_target)
        options_row.addWidget(self.options_stack, 1)
        self.case_sensitive = QToolButton()
        self.case_sensitive.setText("Aa")
        self.case_sensitive.setCheckable(True)
        self.case_sensitive.setToolTip(QCoreApplication.translate("sidebar", "Учитывать регистр"))
        self.case_sensitive.setAccessibleName(QCoreApplication.translate("sidebar", "Учитывать регистр"))
        self.case_sensitive.setStyleSheet(f"QToolButton {{ padding: 4px; border: 1px solid {theme.css('border.subtle')}; border-radius: 4px; }}"
                                         f"QToolButton:hover {{ background: {theme.css('surface.hover')}; }}"
                                         f"QToolButton:checked {{ background: {theme.css('surface.selected')}; border-color: {theme.css('border.selector_focus')}; }}")
        options_row.addWidget(self.case_sensitive)
        search_layout.addLayout(options_row)
        self.show_keys = QCheckBox(QCoreApplication.translate("sidebar", "Показывать ключи"))
        keys_row = QHBoxLayout()
        keys_row.setContentsMargins(26, 0, 0, 0)
        keys_row.addWidget(self.show_keys)
        search_layout.addLayout(keys_row)
        self.search_status = QLabel()
        self.search_status.setWordWrap(True)
        self.search_status.setProperty("role", "muted")
        search_layout.addWidget(self.search_status)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self.request_search)
        self.query_box.textChanged.connect(self.schedule_search)
        self.query_mode.currentIndexChanged.connect(self.schedule_search)
        self.query_scope.changed.connect(self.scope_changed)
        self.case_sensitive.toggled.connect(self.schedule_search)
        self.query_box.returnPressed.connect(self.request_search)
        self.replacement_box.returnPressed.connect(self.request_search)
        self.results = SearchResults()
        self.replacement_box.textChanged.connect(
            lambda text: self.results.set_replace_preview(self.replace_expanded, text))
        self.show_keys.toggled.connect(self.results.set_show_keys)
        search_layout.addWidget(self.results)
        self.replace_toggle.toggled.connect(self.set_replace_expanded)
        self.stack.addWidget(search)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("sidebarSplitter")
        self.splitter.setOpaqueResize(False)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.stack)
        self.splitter.addWidget(workspace)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([self._sidebar_width, 1100])
        self.splitter.splitterMoved.connect(self._remember_width)
        layout.addWidget(self.splitter, 1)
        self._update_buttons()

    def scope_changed(self):
        self.search_timer.stop()
        self.searchChanged.emit()
        self.request_search()

    def schedule_search(self, *args):
        self.search_timer.stop()
        self.searchChanged.emit()
        query = self.query_box.text()
        if query:
            self.search_timer.start()

    def request_search(self):
        self.search_timer.stop()
        if self.replace_expanded:
            self.searchRequested.emit(self.query_box.text(), "ru", "mod")
        else:
            self.searchRequested.emit(self.query_box.text(), self.query_mode.currentData(),
                                      self.query_scope.currentData())

    def set_replace_expanded(self, expanded):
        expanded = bool(expanded)
        if expanded == self.replace_expanded:
            return
        self.replace_expanded = expanded
        self.results.set_replace_preview(expanded, self.replacement_box.text())
        self.replace_toggle.setChecked(expanded)
        self.replace_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.replace_toggle.setToolTip(QCoreApplication.translate("sidebar", "Свернуть замену") if expanded else QCoreApplication.translate("sidebar", "Раскрыть замену · только RU текущего мода"))
        self.options_stack.setCurrentIndex(1 if expanded else 0)
        self.replace_animation.stop()
        height = self.replace_controls.height() if not self.replace_controls.isHidden() else 0
        self.replace_controls.show()
        self.replace_animation.setStartValue(height)
        self.replace_animation.setEndValue(self.replace_controls.sizeHint().height() if expanded else 0)
        self.replace_animation.start()
        self.schedule_search()

    def _finish_replace_animation(self):
        if not self.replace_expanded:
            self.replace_controls.hide()
        else:
            self.replace_controls.setMaximumHeight(16777215)

    def state(self):
        """Values ready for future UI-state storage; no disk access here."""
        self._remember_width()
        return {"visible": not self.stack.isHidden(), "page": self.active_page,
                "width": self._sidebar_width, "search_show_keys": self.show_keys.isChecked()}

    def restore_state(self, state):
        self.show_keys.setChecked(state.get("search_show_keys", False))
        self.show_page(state["page"])
        self._sidebar_width = state["width"]
        available = self.splitter.width() - self.splitter.handleWidth()
        self.splitter.setSizes([self._sidebar_width, max(1, available - self._sidebar_width)])
        self.set_sidebar_visible(state["visible"])

    def _remember_width(self, *args):
        if not self.stack.isHidden() and self.splitter.sizes()[0] > 0:
            self._sidebar_width = self.splitter.sizes()[0]

    def _update_buttons(self):
        for page, button in self.buttons.items():
            button.setChecked(page == self.active_page and not self.stack.isHidden())

    def set_sidebar_visible(self, visible):
        if visible == self.stack.isHidden():
            if visible:
                width = self._sidebar_width
                self.stack.show()
                available = self.splitter.width() - self.splitter.handleWidth()
                self.splitter.setSizes([width, max(1, available - width)])
            else:
                self._remember_width()
                self.stack.hide()
        self._update_buttons()

    def toggle(self):
        self.set_sidebar_visible(self.stack.isHidden())

    def show_page(self, page):
        if page not in self.buttons:
            page = "explorer"
        page_index = {"explorer": 0, "search": 1}[page]
        self.active_page = page
        self.stack.setCurrentIndex(page_index)
        self.set_sidebar_visible(True)

    def activate_page(self, page):
        if page == self.active_page and not self.stack.isHidden():
            self.set_sidebar_visible(False)
        else:
            self.show_page(page)
