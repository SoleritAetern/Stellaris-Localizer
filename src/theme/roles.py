"""Semantic color contract shared by palettes, QSS, and custom renderers."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping, get_args
import re


Role = Literal[
    'surface.window',
    'surface.sidebar',
    'surface.control',
    'surface.hover',
    'surface.pressed',
    'surface.selected',
    'surface.popover',
    'surface.comment_popover',
    'surface.item_hover',
    'text.primary',
    'text.secondary',
    'text.disabled',
    'text.placeholder',
    'help.label',
    'help.label_hover',
    'border.subtle',
    'border.hover',
    'border.focus',
    'border.selector_focus',
    'border.find',
    'border.find_focus',
    'divider',
    'selection.row.bg',
    'selection.row.fg',
    'selection.text.bg',
    'selection.text.fg',
    'selection.occurrence.bg',
    'action.accent',
    'action.primary.bg',
    'action.danger.bg',
    'action.danger.hover',
    'action.danger.fg',
    'action.row_hover',
    'icon.default',
    'icon.hover',
    'icon.disabled',
    'icon.toolbar',
    'icon.tab_close',
    'icon.workspace_close',
    'status.ready',
    'status.warning_text',
    'status.warning_icon',
    'status.disabled_entry',
    'row.file',
    'row.source',
    'row.label',
    'row.action',
    'row.separator',
    'syntax.reference',
    'syntax.variable',
    'syntax.icon',
    'syntax.escape',
    'syntax.color_code',
    'editor.text',
    'editor.whitespace_marker',
    'spellcheck.underline',
    'editor.heading',
    'editor.missing_translation',
    'editor.gutter.bg',
    'editor.gutter.fg',
    'editor.gutter.active',
    'diff.inline.added',
    'diff.inline.removed',
    'diff.inline.added.bg',
    'diff.inline.removed.bg',
    'diff.detail.added',
    'diff.detail.removed',
    'diff.preview.equal',
    'diff.preview.added.bg',
    'diff.preview.added.fg',
    'diff.preview.removed.bg',
    'diff.preview.removed.fg',
    'diff.preview.confirmed',
    'diff.legacy_html.added',
    'diff.legacy_html.removed',
    'legacy_row.metadata',
    'legacy_row.key',
    'legacy_row.file',
    'legacy_row.status',
    'search.match.bg',
    'search.match.fg',
    'search.active.bg',
    'search.active_match.bg',
    'search.current_row.bg',
    'search.current_row.selected.bg',
    'search.replace.added',
    'search.replace.removed',
    'scrollbar.thumb',
    'scrollbar.hover',
    'tab.hover',
    'tab.selected',
    'tab.close_hover',
    'card.border',
    'card.selected_border',
    'selection.en.bg',
    'selection.ru.bg',
    'editor.current_line.bg',
    'card.bg',
    'card.hover',
    'card.selected',
    'card.unavailable_title',
    'card.unavailable_path',
]


@dataclass(frozen=True)
class Palette:
    name: str
    colors: Mapping[Role, str]

    def __post_init__(self):
        values = dict(self.colors)
        required = set(get_args(Role))
        if set(values) != required:
            raise ValueError(f"Palette roles mismatch: missing={required - values.keys()}, extra={values.keys() - required}")
        if any(not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value) for value in values.values()):
            raise ValueError("Palette colors must be #RRGGBB values")
        object.__setattr__(self, "colors", MappingProxyType(values))

    def css(self, role: Role) -> str:
        return self.colors[role]
