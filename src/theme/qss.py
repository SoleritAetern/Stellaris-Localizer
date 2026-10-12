"""One QSS template for every palette, including generated tab-close assets."""

from pathlib import Path

from .roles import Palette


def write_close_icons(palette: Palette, directory: str) -> tuple[str, str]:
    """QSS URLs need files. Keep these two tiny SVGs alive with the QApplication."""
    paths = []
    for name, size, shape, width, role in (
        ("tab-close.svg", 12, "M3 3L9 9M9 3L3 9", "1.3", "icon.tab_close"),
        ("workspace-tab-close.svg", 18, "M4 4L14 14M14 4L4 14", "1", "icon.workspace_close"),
    ):
        path = Path(directory) / name
        path.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">\n'
            f'  <path d="{shape}" fill="none" stroke="{palette.css(role)}" stroke-width="{width}" stroke-linecap="round"/>\n'
            '</svg>\n', encoding="utf-8")
        paths.append(path.as_posix())
    return tuple(paths)


def menu_stylesheet(palette: Palette) -> str:
    """Shared by application menus and Qt's standard editor popup."""
    css = palette.css
    return f"""
        QMenu {{ background: {css('surface.popover')}; color: {css('text.primary')};
            border: 1px solid {css('border.subtle')}; padding: 4px; }}
        QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
        QMenu[textOnlyMenu="true"]::item {{ padding: 4px 10px; }}
        QMenu[textOnlyMenu="true"]::indicator, QMenu[textOnlyMenu="true"]::icon {{ width: 0px; height: 0px; }}
        QMenu::item:selected {{ background: {css('selection.row.bg')}; color: {css('text.primary')}; }}
        QMenu::item:disabled {{ color: {css('text.disabled')}; }}
        QMenu::separator {{ height: 1px; background: {css('divider')}; margin: 4px; }}
    """


def build_stylesheet(palette: Palette, close_icon: str, workspace_close_icon: str,
                     *, radius: int, gap: int, control_padding: str) -> str:
    css = palette.css
    return f"""
        QMainWindow, QWidget#startWindow {{ background: {css('surface.window')}; }}
        QDialog {{ background: {css('surface.popover')}; }}
        QTextBrowser[role="documentation"] {{
            background: {css('surface.popover')}; color: {css('text.primary')}; border: none;
            selection-background-color: {css('selection.text.bg')}; selection-color: {css('selection.text.fg')};
        }}
        QWidget#activityBar {{ background: {css('surface.sidebar')}; }}
        QStackedWidget#sidebarStack, QFrame#workspaceSurface, QFrame#bottomSurface {{
            border: 1px solid {css('border.subtle')}; border-radius: {radius}px;
            margin: {gap // 2}px; padding: 0;
            background: {css('surface.sidebar')};
        }}
        QStackedWidget#sidebarStack {{ background: {css('surface.sidebar')}; }}
        QFrame#localFind {{ background: {css('surface.control')}; border: 1px solid {css('border.subtle')}; border-radius: {radius}px; }}
        QFrame#localFind QToolButton {{ color: {css('text.primary')}; border: none; padding: 3px; }}
        QFrame#localFind QToolButton:hover {{ background: {css('surface.hover')}; }}
        QFrame#workspaceStatus {{ background: {css('surface.control')}; border: 1px solid {css('border.subtle')}; border-radius: 4px; }}
        QFrame#workspaceStatus QLabel {{ color: {css('text.secondary')}; font-size: 11px; }}
        QPushButton#attentionCount {{ color: {css('text.secondary')}; background: transparent; padding: 1px 6px; font-size: 11px; }}
        QPushButton#attentionCount:hover {{ color: {css('text.primary')}; background: {css('surface.control')}; }}
        QToolButton#splitPanelButton {{ border: none; border-radius: 4px; background: transparent; }}
        QToolButton#splitPanelButton:hover, QToolButton#splitPanelButton:checked {{ background: {css('surface.selected')}; }}
        QLabel[role="muted"], QLabel[role="section"] {{ color: {css('text.secondary')}; }}
        QLabel[role="hoverCaption"] {{
            color: {css('text.secondary')}; background: {css('surface.popover')};
            border: 1px solid {css('border.subtle')}; border-radius: 4px; padding: 3px 6px;
        }}
        QLabel[role="help"] {{ color: {css('text.secondary')}; }}
        QLabel[role="help"]:hover {{ color: {css('text.primary')}; }}
        QDialog#newProjectDialog QLabel[role="help"], QDialog#settingsDialog QLabel[role="help"],
        QDialog#projectSettingsDialog QLabel[role="help"] {{
            color: {css('help.label')}; font-weight: 400;
            border-bottom: 1px dotted {css('help.label')}; padding-bottom: 1px;
        }}
        QDialog#newProjectDialog QLabel[role="help"]:hover, QDialog#settingsDialog QLabel[role="help"]:hover,
        QDialog#projectSettingsDialog QLabel[role="help"]:hover {{
            color: {css('help.label_hover')}; border-bottom-color: {css('help.label_hover')};
        }}
        QLabel[role="section"] {{ font-size: 11px; font-weight: 600; padding: 4px 0; }}
        QTreeWidget, QListWidget, QListView {{
            border: none; background: {css('surface.window')}; outline: none;
            selection-background-color: {css('selection.row.bg')};
            selection-color: {css('selection.row.fg')};
        }}
        QStackedWidget#sidebarStack QTreeWidget,
        QStackedWidget#sidebarStack QListWidget {{ background: {css('surface.sidebar')}; }}
        QTreeWidget::item {{ padding: 4px 2px; border-radius: 4px; }}
        QTreeWidget::item:hover {{ background: {css('surface.item_hover')}; }}
        QTreeWidget::item:selected {{ background: {css('selection.row.bg')}; }}
        QTreeWidget[role="review"]::item {{ padding: 5px 8px; border-radius: 0; }}
        QTreeWidget[role="review"] QHeaderView::section {{
            background: {css('surface.popover')}; color: {css('text.secondary')};
            border: none; border-bottom: 1px solid {css('divider')}; padding: 6px 8px;
        }}
        QTreeWidget[role="review"] QComboBox {{ color: {css('text.secondary')}; }}
        QFrame[role="dialogFooter"] {{ border: none; border-top: 1px solid {css('divider')}; }}
        QStackedWidget#sidebarStack QTreeWidget::item,
        QStackedWidget#sidebarStack QListWidget::item,
        QStackedWidget#sidebarStack QListView::item {{ border: none; border-radius: 0; }}
        QLineEdit, QComboBox {{
            color: {css('text.primary')}; background: {css('surface.control')};
            border: 1px solid {css('border.subtle')}; border-radius: {radius}px;
            padding: {control_padding}; selection-background-color: {css('selection.text.bg')};
        }}
        QLineEdit:hover, QComboBox:hover {{ border-color: {css('border.hover')}; }}
        QLineEdit:focus, QComboBox:focus {{ border-color: {css('border.focus')}; }}
        QLineEdit:disabled, QComboBox:disabled {{
            color: {css('text.disabled')}; background: {css('surface.control')};
            border-color: {css('border.subtle')};
        }}
        QComboBox::drop-down {{ border: none; width: 18px; }}
        QComboBox#modSelectorCombo::down-arrow {{ image: none; }}
        QPushButton {{
            color: {css('text.primary')}; background: {css('surface.control')};
            border: 1px solid transparent; border-radius: {radius}px; padding: {control_padding};
        }}
        QPushButton:hover {{ background: {css('surface.hover')}; }}
        QPushButton:pressed, QPushButton:checked {{ background: {css('surface.pressed')}; }}
        QPushButton:focus {{ border-color: {css('border.focus')}; }}
        QPushButton:disabled {{ color: {css('text.disabled')}; }}
        QPushButton[role="icon"] {{ padding: 0; }}
        QFrame#compoundControl {{
            background: {css('surface.control')}; border: 1px solid {css('border.subtle')};
            border-radius: {radius}px;
        }}
        QFrame#compoundControl:hover {{ border-color: {css('border.hover')}; }}
        QFrame#compoundControl[focused="true"] {{ border-color: {css('border.focus')}; }}
        QFrame#compoundControl:disabled {{ border-color: {css('border.subtle')}; }}
        QFrame#compoundControl > QLineEdit, QFrame#compoundControl > QComboBox {{
            border: none; background: transparent; border-radius: {max(0, radius - 1)}px;
        }}
        QFrame#compoundControl > QPushButton {{
            border: none; border-left: 1px solid {css('border.subtle')};
            background: transparent; border-radius: 0;
            border-top-right-radius: {max(0, radius - 1)}px;
            border-bottom-right-radius: {max(0, radius - 1)}px;
        }}
        QFrame#compoundControl > QPushButton:hover {{ background: {css('surface.hover')}; }}
        QFrame#compoundControl > QPushButton:pressed {{ background: {css('surface.pressed')}; }}
        QFrame#compoundControl > QPushButton:disabled {{ background: transparent; }}
        QPushButton[role="filter"] {{ background: transparent; color: {css('text.secondary')}; border-radius: 0;
            border: none; border-bottom: 2px solid transparent; padding: 4px 7px; }}
        QPushButton[role="filter"]:hover {{ background: {css('surface.control')}; color: {css('text.primary')}; }}
        QPushButton[role="filter"]:checked {{ background: transparent; color: {css('text.primary')}; border-bottom-color: {css('action.accent')}; }}
        QPushButton[role="primary"] {{ background: {css('action.primary.bg')}; }}
        QPushButton[role="primary"]:hover {{ background: {css('surface.hover')}; }}
        QPushButton[role="secondary"] {{ border-color: {css('border.subtle')}; }}
        QPushButton[role="secondary"]:hover {{ border-color: {css('border.hover')}; }}
        QPushButton[role="secondary"]:focus {{ border-color: {css('border.focus')}; }}
        QPushButton[role="danger"] {{ background: {css('action.danger.bg')}; color: {css('action.danger.fg')}; }}
        QPushButton[role="danger"]:hover {{ background: {css('action.danger.hover')}; }}
        QWidget#activityBar QToolButton {{
            background: transparent; border: none; border-radius: {radius}px;
        }}
        QWidget#activityBar QToolButton:hover {{ background: {css('surface.control')}; }}
        QWidget#activityBar QToolButton:checked {{ background: {css('surface.selected')}; }}
        QWidget#activityBar QToolButton[role="action"]:focus {{
            border: 1px solid {css('border.focus')};
        }}
        QWidget#activityBar QToolButton[role="action"]:pressed {{ background: {css('surface.pressed')}; }}
        QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
        QTabBar::tab {{
            color: {css('text.secondary')}; background: transparent; border: none;
            border-radius: {radius}px; padding: 6px 10px; margin: 2px 2px 4px 0;
        }}
        QTabBar::tab:hover {{ background: {css('tab.hover')}; color: {css('text.primary')}; }}
        QTabBar::tab:selected {{ background: {css('tab.selected')}; color: {css('text.primary')}; }}
        QFrame#workspaceSurface QTabBar::tab, QFrame#bottomSurface QTabBar::tab {{
            border-radius: 0; border: none; border-bottom: 2px solid transparent;
            margin: 0; padding: 6px 10px; background: transparent;
        }}
        QFrame#workspaceSurface QTabBar::tab:hover, QFrame#bottomSurface QTabBar::tab:hover {{
            background: {css('surface.control')};
        }}
        QFrame#workspaceSurface QTabBar::tab:selected, QFrame#bottomSurface QTabBar::tab:selected {{
            background: {css('surface.window')}; border-bottom-color: {css('action.accent')}; color: {css('text.primary')};
        }}
        QTabBar::close-button {{ image: url("{close_icon}"); subcontrol-position: right; width: 12px; height: 12px; }}
        QTabBar::close-button:hover {{ background: {css('tab.close_hover')}; border-radius: 3px; }}
        QFrame#workspaceSurface QTabBar#workspaceTabs::tab {{ padding: 2px 9px; margin: 0; }}
        QTabBar#workspaceTabs::close-button {{ image: url("{workspace_close_icon}"); width: 18px; height: 18px; }}
        QSplitter::handle {{ background: {css('divider')}; width: 1px; height: 1px; }}
        QSplitter::handle:hover {{ background: {css('border.hover')}; }}
        QSplitter#sidebarSplitter::handle, QSplitter#workspaceSplitter::handle {{ background: {css('surface.window')}; }}
        QSplitter#sidebarSplitter::handle:hover, QSplitter#workspaceSplitter::handle:hover {{ background: {css('border.subtle')}; }}
        QStatusBar {{ color: {css('text.secondary')}; border-top: 1px solid {css('border.subtle')}; }}
        QStatusBar::item {{ border: none; }}
        QStatusBar QLabel {{ color: {css('text.secondary')}; font-size: 11px; }}
        QMenuBar {{ background: {css('surface.window')}; color: {css('text.secondary')}; }}
        QMenuBar::item:selected {{ background: {css('surface.control')}; border-radius: 4px; }}
        {menu_stylesheet(palette)}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
        QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
        QScrollBar::handle {{ background: {css('scrollbar.thumb')}; border-radius: 4px; }}
        QScrollBar::handle:vertical {{ min-height: 28px; }}
        QScrollBar#translationScrollBar:vertical {{ width: 12px; }}
        QScrollBar#translationScrollBar::handle:vertical {{ min-height: 36px; }}
        QScrollBar::handle:horizontal {{ min-width: 28px; }}
        QScrollBar::handle:hover {{ background: {css('scrollbar.hover')}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QToolTip, QLabel#scrollPositionTip {{ background: {css('surface.popover')}; color: {css('text.primary')}; border: 1px solid {css('border.subtle')}; padding: 4px; }}
    """
