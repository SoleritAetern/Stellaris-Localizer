"""Public theme API. The palette is immutable and selected before UI creation.

There is deliberately no runtime theme switching or per-row change lifecycle.
Renderers resolve colors when constructing their existing formats/layouts.
"""

from PySide6.QtCore import QTemporaryDir
from PySide6.QtGui import QColor, QIcon, QPalette

from .dark import DARK
from .roles import Role
from .qss import build_stylesheet, menu_stylesheet, write_close_icons

RADIUS = 6
SURFACE_GAP = 4
SURFACE_PADDING = 6
CONTROL_PADDING = "5px 9px"

_palette = DARK


def css(role: Role) -> str:
    return _palette.css(role)


def color(role: Role) -> QColor:
    # QColor is mutable; never expose a shared instance to a painter/highlighter.
    return QColor(css(role))


def align_form_controls(controls, buttons=()):
    """Match native control metrics after the shared QSS has been applied."""
    widgets = (*controls, *buttons)
    for widget in widgets:
        widget.ensurePolished()
    height = max(widget.sizeHint().height() for widget in widgets)
    for widget in widgets:
        widget.setFixedHeight(height)
    if buttons:
        width = max(button.sizeHint().width() for button in buttons)
        for button in buttons:
            button.setFixedWidth(width)


def style_menu(menu):
    """Compact text-only menus, including Qt's standard editor popup."""
    menu.setProperty("textOnlyMenu", True)
    menu.setStyleSheet(menu_stylesheet(_palette))
    for action in menu.actions():
        action.setIcon(QIcon())
        action.setIconVisibleInMenu(False)


def apply_dark_theme(app):
    """Apply the current palette before creating windows; retain the legacy API."""
    app.setStyle("Fusion")
    palette = QPalette()
    for qt_role, role in {
        "Window": "surface.window", "WindowText": "text.primary",
        "Base": "surface.window", "AlternateBase": "action.row_hover",
        "Text": "text.primary", "Button": "surface.control", "ButtonText": "text.primary",
        "Highlight": "selection.row.bg", "HighlightedText": "selection.row.fg",
        "PlaceholderText": "text.placeholder", "Mid": "divider",
        "Dark": "surface.sidebar", "Light": "surface.hover",
        "ToolTipBase": "surface.popover", "ToolTipText": "text.primary",
    }.items():
        palette.setColor(getattr(QPalette.ColorRole, qt_role), color(role))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, color("text.disabled"))
    app.setPalette(palette)
    assets = getattr(app, "_theme_assets", None)
    if assets is None:
        assets = QTemporaryDir()
        if not assets.isValid():
            raise RuntimeError("Cannot create temporary theme assets: " + assets.errorString())
        app._theme_assets = assets
    close_icon, workspace_close_icon = write_close_icons(_palette, assets.path())
    app.setStyleSheet(build_stylesheet(_palette, close_icon, workspace_close_icon,
                                      radius=RADIUS, gap=SURFACE_GAP, control_padding=CONTROL_PADDING))


# Compatibility for external callers. Application renderers use semantic roles.
WINDOW_BG = css('surface.window')
SIDEBAR_BG = css('surface.sidebar')
SURFACE_BG = css('surface.control')
SURFACE_HOVER = css('surface.hover')
SURFACE_SELECTED = css('surface.selected')
BORDER_SUBTLE = css('border.subtle')
TEXT_PRIMARY = css('text.primary')
TEXT_SECONDARY = css('text.secondary')
TEXT_DISABLED = css('text.disabled')
FILE_READY = css('status.ready')
DISABLED_ACCENT = css('status.disabled_entry')
FIND_MATCH = css('search.match.bg')
FIND_ACTIVE = css('search.active.bg')
ACCENT = css('action.accent')
ERROR = css('action.danger.bg')
ERROR_HOVER = css('action.danger.hover')
