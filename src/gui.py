
from PySide6.QtCore import QCoreApplication
import html
import json
from copy import deepcopy
from collections import OrderedDict, deque
from dataclasses import dataclass
from math import ceil
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QAbstractProxyModel,
    QObject,
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QItemSelectionModel,
    QItemSelection,
    QSize,
    Qt,
    QSortFilterProxyModel,
    Signal,
    QTimer,
    QSignalBlocker,
    QPointF,
    QRect,
)

from PySide6.QtGui import (
    QAction,
    QKeySequence,
    QTextDocument,
    QUndoCommand,
    QUndoStack,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextLayout,
    QTextOption,
    QPalette,
)

from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QToolTip,
    QPlainTextEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QFrame,
    QStackedWidget,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTabBar,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


from .sidebar import WorkspaceSidebar
from .global_search import RESULT_LIMIT, prepare_query, localization_matches, is_working_ru
from .global_replace import GlobalReplacementEngine, GlobalReplaceCommand, ReplaceScope, TRANSLATION_FIELDS
from .explorer import ExplorerFiles, ExplorerTree
from .paths import english_to_russian_path
from .local_find import LocalFind
from .search_selection import SearchSelectionCapture, selected_search_text
from .workspace_chrome import QuietTabBar, SplitButton, WorkspaceStatus
from .shortcuts import SHORTCUTS
from .token_inspector import TokenInspector
from .row_diagnostics_ui import RowDiagnosticsCache, RowDiagnosticsPopover, warnings_for
from .ui_messages import stored_message
from .translation_baseline import can_accept, refresh_baseline
from .row_status import row_status, row_status_tooltip
from .syntax_highlighting import syntax_ranges
from .ui_state import EXPLORER_ROLE, capture_position, restore_position
from .document_proxy import DocumentFilterProxy, is_entry
from .height_index import layout_identity
from .localization_view import LocalizationListView, ROW_APPEARANCE_ROLE, BLANK_ROW_HEIGHT
from .comment_popover import CommentPopover
from .text_selection import TokenSelectionMixin
from .whitespace_markers import BoundaryWhitespaceMixin, draw_boundary_spaces
from .text_context_menu import set_action_caption
from .translation_filters import has_latin_text
from .editor_spellcheck import EditorSpellcheck
from .adaptive_diff import is_large_change
from .icons import paint_check


from . import theme
from .theme import apply_dark_theme, SURFACE_PADDING


def build_diff_html(segments):
    parts = []

    for segment in segments:
        text = html.escape(segment["text"])
        segment_type = segment["type"]

        if segment_type == "removed":
            parts.append(
                f'<span style="color:{theme.css("diff.legacy_html.removed")}; '
                'text-decoration:line-through;">'
                f'{text}</span>'
            )

        elif segment_type == "added":
            parts.append(
                f'<span style="color:{theme.css("diff.legacy_html.added")}; '
                'font-weight:600;">'
                f'{text}</span>'
            )

        else:
            parts.append(text)

    return "".join(parts)


def build_row_html(row):
    key = html.escape(
        row["key"]
    )

    status, status_role = row_status(row)
    status = html.escape(status)

    source_diff = build_diff_html(
        row["source_diff"]
    )

    if row["ru_text"] is None:
        russian_text = (
            QCoreApplication.translate("gui", "<span style=\"color:{0};\">[НЕТ ПЕРЕВОДА]</span>").format(theme.css('editor.missing_translation'))
        )
    else:
        russian_text = html.escape(
            row["ru_text"]
        )

    # -------------------------
    # Имя файла
    # -------------------------

    file_value = (
        row["new_file"]
        or row["old_file"]
    )

    if file_value:
        file_text = str(file_value)

        # Показываем только имя файла,
        # без папок.
        file_text = (
            file_text
            .replace("\\", "/")
            .rsplit("/", 1)[-1]
        )

        file_text = html.escape(
            file_text
        )
    else:
        file_text = ""

    # -------------------------
    # HTML строки
    # -------------------------

    return f"""
    <div style="
        font-family:Segoe UI;
        margin:0px;
        padding:0px;
    ">

        <table
            width="100%"
            cellspacing="0"
            cellpadding="0"
            style="
                margin:0px;
                padding:0px;
                font-size:10px;
                color:{theme.css('legacy_row.metadata')};
            "
        >
            <tr>
                <td
                    width="34%"
                    align="left"
                    style="
                        color:{theme.css('legacy_row.key')};
                        font-weight:600;
                        padding:0px;
                    "
                >
                    {key}
                </td>

                <td
                    width="32%"
                    align="center"
                    style="
                        color:{theme.css('legacy_row.file')};
                        padding:0px;
                    "
                >
                    {file_text}
                </td>

                <td
                    width="34%"
                    align="right"
                    style="
                        color:{theme.css(status_role)};
                        padding:0px;
                    "
                >
                    {status}
                </td>
            </tr>
        </table>

        <div style="
            font-size:13px;
            margin-top:2px;
            margin-bottom:3px;
        ">
            <b>EN:</b>
            {source_diff}
        </div>

        <div style="
            font-size:13px;
            margin:0px;
        ">
            <b>RU:</b>
            {russian_text}
        </div>

    </div>
    """


@dataclass
class RowLayout:
    """All rectangles are relative to the item, including the editor area."""

    header: QRect
    source: QRect
    ru_label: QRect
    russian: QRect
    height: int
    source_layout: QTextLayout
    russian_layout: QTextLayout
    en_label: QRect
    key: QRect
    file: QRect
    status: QRect
    actions: dict
    large_change: bool = False


class RussianHighlighter(QSyntaxHighlighter):
    def __init__(self, document, syntax_ranges):
        super().__init__(document)
        self.syntax_ranges = syntax_ranges

    def highlightBlock(self, text):
        for item in self.syntax_ranges(text):
            # Qt text positions count UTF-16 code units.
            start = len(text[:item.start].encode("utf-16-le")) // 2
            length = len(text[item.start:item.start + item.length].encode("utf-16-le")) // 2
            self.setFormat(start, length, item.format)


class RussianTextEdit(BoundaryWhitespaceMixin, TokenSelectionMixin, QPlainTextEdit):
    editingFinished = Signal()
    editingCancelled = Signal()
    editingAccepted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loaded = False
        self.search_selection_capture = SearchSelectionCapture(self)
        self.closing = False
        self._pointer_drag = False
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(0)
        self.spellcheck = EditorSpellcheck(self)

    def contextMenuEvent(self, event):
        self.spellcheck.context_menu(event)

    def begin_pointer_drag(self):
        # The first press was delivered to the list before this editor existed.
        # Route the rest of that gesture to the real editor as well.
        self._pointer_drag = True
        self.viewport().grabMouse()

    def end_pointer_drag(self):
        if self._pointer_drag and self.mouseGrabber() is self.viewport():
            self.viewport().releaseMouse()
        self._pointer_drag = False

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.end_pointer_drag()

    def hideEvent(self, event):
        self.end_pointer_drag()
        super().hideEvent(event)

    def set_row_palette(self, palette):
        self.setPalette(palette)
        # Explicit colors also override the application's inherited stylesheet.
        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {palette.color(QPalette.ColorRole.Base).name()};
                color: {palette.color(QPalette.ColorRole.Text).name()};
                selection-background-color: {theme.css('selection.ru.bg')};
                selection-color: {theme.css('selection.text.fg')};
                border: none;
                padding: 0px;
                margin: 0px;
            }}
        """)

    @staticmethod
    def localization_text(text):
        return (text.replace("\r\n", "\\n").replace("\r", "\\n")
                .replace("\n", "\\n").replace("\u2028", "\\n")
                .replace("\u2029", "\\n"))

    def insertFromMimeData(self, source):
        # Paste/drop plain text only; physical newlines must not enter a yml value.
        self.insertPlainText(self.localization_text(source.text()))

    def content_height(self, width):
        # QPlainTextDocumentLayout.documentSize().height() counts lines, not pixels.
        # Measure the actual editor blocks at the exact width reserved by RowLayout.
        if self.width() != width:
            self.resize(width, self.height())
        block = self.document().begin()
        height = 0.0
        while block.isValid():
            height += self.blockBoundingRect(block).height()
            block = block.next()
        return ceil(max(height, QFontMetricsF(self.font()).height())) + 2

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
                event.accept()
                return True
            if any(event.matches(key) for key in (
                QKeySequence.StandardKey.Copy, QKeySequence.StandardKey.Cut,
                QKeySequence.StandardKey.Paste, QKeySequence.StandardKey.Undo,
                QKeySequence.StandardKey.Redo, QKeySequence.StandardKey.SelectAll,
            )):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.editingAccepted.emit()
            else:
                self.editingFinished.emit()
            return

        if event.key() == Qt.Key.Key_Escape:
            self.editingCancelled.emit()
            return

        super().keyPressEvent(event)

class EditTranslationCommand(QUndoCommand):
    def __init__(
        self,
        model,
        row_number,
        new_text,
    ):
        super().__init__(QCoreApplication.translate("gui", "Изменение перевода"))

        self.model = model
        self.row_number = row_number

        row = model.rows[row_number]

        self.old_text = row["ru_text"]
        self.old_status = row["translation_status"]
        self.old_metadata = {
            field: row[field]
            for field in ("translation_state", "translation_origin", "needs_review")
            if field in row
        }
        self.new_metadata = (
            dict(self.old_metadata, translation_state="edited", translation_origin="manual")
            if self.old_metadata else None
        )

        self.new_text = new_text
        self.new_status = "EDITED"

    def redo(self):
        self.model.apply_translation(
            self.row_number,
            self.new_text,
            self.new_status,
            self.new_metadata,
        )

    def undo(self):
        self.model.apply_translation(
            self.row_number,
            self.old_text,
            self.old_status,
            self.old_metadata,
        )

class AcceptTranslationCommand(QUndoCommand):
    def __init__(self, model, row):
        super().__init__(QCoreApplication.translate("gui", "Подтвердить перевод"))
        self.model, self.row = model, row
        data = model.rows[row]
        self.before = {name: data[name] for name in
                       ("accepted_source_text", "accepted_revision_id", "translation_state", "translation_origin")}
        self.before["accepted_source_comment"] = data.get("accepted_source_comment")
        self.before["restored_needs_confirmation"] = data.get("restored_needs_confirmation", False)
        self.after = dict(accepted_source_text=data["new_text"],
                          accepted_source_comment=data.get("new_comment"),
                          accepted_revision_id=data["current_revision_id"], translation_state="translated",
                          translation_origin="manual", restored_needs_confirmation=False)

    def apply(self, values):
        row = self.model.rows[self.row]
        previous_diff = row["source_diff"]
        row.update(values)
        refresh_baseline(row)
        index = self.model.index(self.row, 0)
        roles = [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole]
        if row["source_diff"] == previous_diff:
            roles.append(ROW_APPEARANCE_ROLE)
        self.model.dataChanged.emit(index, index, roles)

    def redo(self):
        self.apply(self.after)

    def undo(self):
        self.apply(self.before)


class EditCommentCommand(QUndoCommand):
    def __init__(self, model, row, value):
        super().__init__(QCoreApplication.translate("gui", "Изменение комментария RU"))
        self.model, self.row = model, row
        data = model.rows[row]
        self.old = (data.get("ru_comment"), data.get("ru_comment_trailing", ""))
        self.new = (value, " # " + value if value is not None else "")

    def apply(self, values):
        data = self.model.rows[self.row]
        same_geometry = (data.get("ru_comment") is None) == (values[0] is None)
        data["ru_comment"], data["ru_comment_trailing"] = values
        index = self.model.index(self.row, 0)
        roles = [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole]
        if same_geometry:
            roles.append(ROW_APPEARANCE_ROLE)
        self.model.dataChanged.emit(index, index, roles)

    def redo(self):
        self.apply(self.new)

    def undo(self):
        self.apply(self.old)


class LocalizationListModel(
    QAbstractListModel
):
    translationBatchAboutToChange = Signal()
    translationBatchChanged = Signal()

    def __init__(
        self,
        rows,
        undo_stack,
    ):
        super().__init__()

        self.rows = rows
        self.undo_stack = undo_stack
        self.row_diagnostics = RowDiagnosticsCache(self)

    def layout_metadata(self, row_number):
        """Python-only identity/kind access; never transport a row through Qt."""
        row = self.rows[row_number]
        return layout_identity(row), row.get("kind", "ENTRY")

    def filter_metadata(self, row_number):
        row = self.rows[row_number]
        return row["new_file"] or row["old_file"], row["source_status"], row["translation_status"]

    def filter_has_latin_text(self, row_number):
        row = self.rows[row_number]
        return is_entry(row) and has_latin_text(row.get("ru_text") or "")

    def filter_search_text(self, row_number):
        # Only the explicit text filter needs text. Keep its existing semantics.
        row = self.rows[row_number]
        return " ".join([
            row["key"] or "", row["new_text"] or "", row["old_text"] or "",
            row["ru_text"] or "", str(row["new_file"] or ""), str(row["old_file"] or ""),
        ]).lower()

    def accept_translation(self, index):
        if not index.isValid() or index.model() is not self:
            return False
        row = self.rows[index.row()]
        if not can_accept(row):
            return False
        command = AcceptTranslationCommand(self, index.row())
        if command.before == command.after:
            return False
        self.undo_stack.push(command)
        return True

    def set_comment(self, index, value):
        if not index.isValid() or index.model() is not self:
            return False
        if value is not None:
            value = " ".join(value.replace("\u2028", "\n").replace("\u2029", "\n").splitlines())
        if self.rows[index.row()].get("ru_comment") == value:
            return False
        self.undo_stack.push(EditCommentCommand(self, index.row(), value))
        return True

    def acceptance_commands(self, indexes):
        commands = []
        for index in indexes:
            if index.isValid() and index.model() is self and can_accept(self.rows[index.row()]):
                command = AcceptTranslationCommand(self, index.row())
                if command.before != command.after:
                    commands.append(command)
        return commands

    def accept_translations(self, indexes):
        commands = self.acceptance_commands(indexes)
        if not commands:
            return
        self.undo_stack.beginMacro(QCoreApplication.translate("gui", "Подтвердить переводы"))
        try:
            for command in commands:
                self.undo_stack.push(command)
        finally:
            self.undo_stack.endMacro()

    def rowCount(
        self,
        parent=QModelIndex(),
    ):
        return len(self.rows)

    def data(
        self,
        index,
        role=Qt.ItemDataRole.DisplayRole,
    ):
        if not index.isValid():
            return None

        row = self.rows[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            return build_row_html(row)

        if role == Qt.ItemDataRole.UserRole:
            return row

        if role == Qt.ItemDataRole.ToolTipRole and "accepted_source_text" in row:
            return (QCoreApplication.translate("gui", "Перевод ещё не подтверждён.") if row.get("accepted_source_text") is None else
                    QCoreApplication.translate("gui", "EN сравнивается с текстом, для которого был подтверждён перевод."))

        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )

    def setData(
        self,
        index,
        value,
        role=Qt.ItemDataRole.EditRole,
    ):
        if (
            not index.isValid()
            or role != Qt.ItemDataRole.EditRole
        ):
            return False

        value = value.replace(
            "\r\n",
            "\\n",
        )

        value = value.replace(
            "\r",
            "\\n",
        )

        value = value.replace(
            "\n",
            "\\n",
        )

        row = self.rows[index.row()]

        if row["ru_text"] == value:
            return False

        command = EditTranslationCommand(
            self,
            index.row(),
            value,
        )

        self.undo_stack.push(
            command
        )

        return True

    def apply_translation(
        self,
        row_number,
        text,
        status,
        metadata=None,
    ):
        row = self.rows[row_number]

        row["ru_text"] = text
        row["translation_status"] = status
        if metadata:
            row.update(metadata)
        if "accepted_source_text" in row:
            refresh_baseline(row)

        index = self.index(
            row_number,
            0,
        )

        self.dataChanged.emit(
            index,
            index,
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.UserRole,
            ],
        )

    @staticmethod
    def translation_identity(row):
        return row.get("entry_id") or layout_identity(row)

    def paste_translations(self, targets):
        """Prepare every identity/text pair before pushing one atomic command."""
        snapshots = []
        for number, identity, text in targets:
            row = self.rows[number]
            if self.translation_identity(row) != identity:
                raise ValueError(QCoreApplication.translate("gui", "Запись изменилась до вставки. Повторите выделение."))
            value = text.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
            if row["ru_text"] == value:
                continue
            before = {key: deepcopy(row[key]) for key in TRANSLATION_FIELDS if key in row}
            updated = dict(row, ru_text=value, translation_status="EDITED")
            if any(key in row for key in ("translation_state", "translation_origin", "needs_review")):
                updated.update(translation_state="edited", translation_origin="manual")
            if "accepted_source_text" in updated:
                refresh_baseline(updated)
            after = {key: deepcopy(updated[key]) for key in TRANSLATION_FIELDS if key in updated}
            snapshots.append((number, identity, before, after))
        if snapshots:
            command = GlobalReplaceCommand(self, snapshots, ReplaceScope.ALL)
            command.setText(QCoreApplication.translate("gui", "Вставка {0} переводов").format(len(targets)))
            self.undo_stack.push(command)

    def apply_translation_batch(self, snapshots):
        """Apply all RU snapshots before notifying any view; no text normalization."""
        if not snapshots:
            return
        resolved = []
        by_identity = None
        for number, identity, values in snapshots:
            if not 0 <= number < len(self.rows) or self.translation_identity(self.rows[number]) != identity:
                if by_identity is None:
                    by_identity = {}
                    for current_number, row in enumerate(self.rows):
                        current_id = self.translation_identity(row)
                        if current_id in by_identity:
                            raise ValueError(QCoreApplication.translate("gui", "Повторяющийся entry_id. Batch Undo/Redo недоступен."))
                        by_identity[current_id] = current_number
                number = by_identity.get(identity)
                if number is None:
                    raise ValueError(QCoreApplication.translate("gui", "Структура модели изменилась. Batch Undo/Redo недоступен."))
            resolved.append((number, deepcopy(values)))
        self.translationBatchAboutToChange.emit()
        try:
            for number, values in resolved:
                row = self.rows[number]
                for key in TRANSLATION_FIELDS:
                    if key in values:
                        row[key] = values[key]
                    else:
                        row.pop(key, None)
            # Retain narrow data notifications; proxies refilter once at the end.
            numbers = sorted(number for number, _ in resolved)
            start = end = numbers[0]
            for number in numbers[1:] + [None]:
                if number is not None and number == end + 1:
                    end = number
                    continue
                self.dataChanged.emit(self.index(start, 0), self.index(end, 0),
                                      [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole])
                start = end = number
        finally:
            self.translationBatchChanged.emit()

class LocalizationFilterProxy(
    QSortFilterProxyModel
):
    def __init__(self):
        super().__init__()

        self.mode = "ALL"
        self.search_text = ""
        self.selected_files = None
        self._batch_connections = []
        self._english_snapshot = frozenset()

    def setSourceModel(self, model):
        if model is self.sourceModel():
            return
        for connection in self._batch_connections:
            QObject.disconnect(connection)
        self._batch_connections = []
        # A different source model starts a new snapshot; identities must never
        # leak from the previous project/model into the replacement.
        self._english_snapshot = self._english_entries(model) if self.mode == "ENGLISH_TEXT" else frozenset()
        super().setSourceModel(model)
        if model is not None and hasattr(model, "translationBatchAboutToChange"):
            self._batch_connections = [
                model.translationBatchAboutToChange.connect(self._begin_translation_batch),
                model.translationBatchChanged.connect(self._end_translation_batch),
            ]

    def _begin_translation_batch(self):
        self._dynamic_before_batch = self.dynamicSortFilter()
        self._batch_filter_change = self.mode != "ENGLISH_TEXT"
        if self._batch_filter_change:
            # Capture the existing mapping before the source changes any RU.
            self.beginFilterChange()
        self.setDynamicSortFilter(False)

    def _end_translation_batch(self):
        self.setDynamicSortFilter(self._dynamic_before_batch)
        if self._batch_filter_change:
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    @staticmethod
    def _english_entries(model):
        if model is None:
            return frozenset()
        return frozenset(model.translation_identity(row)
                         for number, row in enumerate(model.rows)
                         if model.filter_has_latin_text(number))

    def _change_mode(self, mode):
        if mode == self.mode:
            return
        # Snapshot the semantic condition once, independently of file/search
        # restrictions. Those can change without reevaluating edited RU text.
        self._english_snapshot = self._english_entries(self.sourceModel()) if mode == "ENGLISH_TEXT" else frozenset()
        self.mode = mode
        self.setDynamicSortFilter(mode != "ENGLISH_TEXT")

    def layout_metadata(self, row_number):
        source = self.mapToSource(self.index(row_number, 0))
        return self.sourceModel().layout_metadata(source.row())

    def set_mode(self, mode):
        if self.mode == mode:
            return
        self.beginFilterChange()
        self._change_mode(mode)
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def set_search_text(self, text):
        self.beginFilterChange()
        self.search_text = (
            text.lower().strip()
        )

        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def set_file_filter(self, file_paths):
        if self.selected_files == file_paths:
            return
        self.beginFilterChange()
        self.selected_files = file_paths
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def set_filters(self, mode, file_paths):
        """Restore file and status together with a single filtering pass."""
        if self.mode == mode and self.selected_files == file_paths:
            return
        self.beginFilterChange()
        self._change_mode(mode)
        self.selected_files = file_paths
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(
        self,
        source_row,
        source_parent,
    ):
        if self.mode == "ALL" and self.selected_files is None and not self.search_text:
            return True

        source_model = self.sourceModel()
        row_file, source_status, translation_status = source_model.filter_metadata(source_row)

        # Фильтр по файлу
        if self.selected_files is not None:
            if str(row_file) not in self.selected_files:
                return False

        # Поиск
        if self.search_text:
            searchable = source_model.filter_search_text(source_row)

            if (
                self.search_text
                not in searchable
            ):
                return False

        if self.mode == "ALL":
            return True

        if self.mode == "ATTENTION":
            return translation_status not in (
                "OK",
                "NONE",
            )

        if self.mode == "ENGLISH_TEXT":
            return source_model.translation_identity(source_model.rows[source_row]) in self._english_snapshot

        if self.mode == "CHANGED":
            return source_status in (
                "CHANGED",
                "MOVED_CHANGED",
            )

        if self.mode == "NEW":
            return source_status == "NEW"

        if self.mode == "MOVED":
            return source_status in (
                "MOVED",
                "MOVED_CHANGED",
            )

        if self.mode == "REMOVED":
            return source_status in (
                "REMOVED",
                "NO_SOURCE",
            )

        return True


class LocalizationDelegate(
    QStyledItemDelegate
):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Formats resolve semantic colors on creation. The startup palette is
        # immutable, so cached QTextLayouts need no theme key or repaint pass.
        self.layout_cache = OrderedDict()
        self.editor = None
        self.edit_index = QPersistentModelIndex()
        self._edit_size = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_editor)

    def clear_height_cache(self):
        self.layout_cache.clear()

    @staticmethod
    def layout_identity(row):
        return layout_identity(row)

    def cached_layout(self, identity, signature):
        cached = self.layout_cache.get(identity)
        if cached is not None and cached[0] == signature:
            self.layout_cache.move_to_end(identity)
            return cached[1]
        return None

    def remember_layout(self, identity, signature, layout):
        # One variant per element: old text/width/font variants cannot accumulate.
        self.layout_cache[identity] = (signature, layout)
        self.layout_cache.move_to_end(identity)
        if len(self.layout_cache) > 8192:
            self.layout_cache.popitem(last=False)

    def is_editing(self, index):
        return self.editor is not None and self.edit_index == index

    def createEditor(self, parent, option, index):
        if not is_entry(index.data(Qt.ItemDataRole.UserRole)):
            return None
        editor = RussianTextEdit(parent)
        editor.viewport().setMouseTracking(True)
        editor.setFont(self.body_font(option))
        palette = QPalette(option.palette)
        if self.parent().selectionModel().isSelected(index):
            palette.setColor(QPalette.ColorRole.Base, option.palette.highlight().color())
            palette.setColor(QPalette.ColorRole.Text, option.palette.highlightedText().color())
        editor.set_row_palette(palette)
        editor.highlighter = RussianHighlighter(editor.document(), self.syntax_ranges)
        self.parent().occurrences.attach(editor)
        source_index = index
        while isinstance(source_index.model(), QAbstractProxyModel):
            source_index = source_index.model().mapToSource(source_index)
        editor.source_index = QPersistentModelIndex(source_index)
        self.editor = editor
        self.edit_index = QPersistentModelIndex(index)
        self._edit_size = None
        editor.editingFinished.connect(lambda: self.commit_and_close(editor))
        editor.editingAccepted.connect(lambda: self.commit_and_accept(editor))
        editor.editingCancelled.connect(lambda: self.cancel_editor(editor))
        editor.textChanged.connect(self.schedule_editor_refresh)
        editor.cursorPositionChanged.connect(self.schedule_editor_refresh)
        return editor

    def setEditorData(self, editor, index):
        # dataChanged from either panel must not overwrite a live draft.
        if editor.loaded:
            return
        row = index.data(Qt.ItemDataRole.UserRole)
        editor.setPlainText(editor.localization_text(row["ru_text"] or ""))
        editor.loaded = True
        editor.selectAll()
        self.schedule_editor_refresh()

    def setModelData(self, editor, model, index):
        source = editor.source_index
        if source.isValid():
            source.model().setData(
                QModelIndex(source), editor.toPlainText(), Qt.ItemDataRole.EditRole,
            )

    def eventFilter(self, editor, event):
        if isinstance(editor, RussianTextEdit):
            if event.type() == QEvent.Type.KeyPress and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape,
            ):
                # These keys belong to RussianTextEdit, not the default delegate.
                return False
            if editor.closing and event.type() == QEvent.Type.FocusOut:
                return False
        return super().eventFilter(editor, event)

    def commit_and_accept(self, editor):
        source = QModelIndex(editor.source_index)
        model = source.model()
        if not source.isValid() or not hasattr(model, "accept_translation"):
            self.commit_and_close(editor)
            return
        if editor.localization_text(editor.toPlainText()) == model.rows[source.row()]["ru_text"]:
            self.commit_and_close(editor)
            model.accept_translation(source)
            return
        model.undo_stack.beginMacro(QCoreApplication.translate("gui", "Сохранить и подтвердить перевод"))
        try:
            self.commit_and_close(editor)
            model.accept_translation(source)
        finally:
            model.undo_stack.endMacro()

    def commit_and_close(self, editor):
        if editor is not self.editor or editor.closing:
            return
        editor.closing = True
        editor.spellcheck.stop()
        self.commitData.emit(editor)
        self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)

    def cancel_editor(self, editor):
        if editor is not self.editor or editor.closing:
            return
        editor.closing = True
        editor.spellcheck.stop()
        self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.RevertModelCache)

    def destroyEditor(self, editor, index):
        editor.spellcheck.stop()
        if editor is self.editor:
            self._refresh_timer.stop()
            previous_index = QModelIndex(self.edit_index)
            self.editor = None
            self.edit_index = QPersistentModelIndex()
            self._edit_size = None
            if previous_index.isValid():
                self.sizeHintChanged.emit(previous_index)
            self.parent().viewport().update()
        super().destroyEditor(editor, index)

    def schedule_editor_refresh(self):
        self._refresh_timer.start(0)

    def refresh_editor(self):
        editor = self.editor
        if editor is None or editor.closing or not self.edit_index.isValid():
            return
        view = self.parent()
        index = QModelIndex(self.edit_index)
        option = QStyleOptionViewItem()
        view.initViewItemOption(option)
        option.rect = view.visualRect(index)
        layout = self.row_layout(option, index)
        size = (layout.russian.width(), layout.height)
        if size != self._edit_size:
            self._edit_size = size
            # The virtual view updates this height and the visible neighbours only.
            view.relayout_index(index, layout.height)
        view.updateEditorGeometries()
        # The outer list owns scrolling, including rows taller than its viewport.
        editor.verticalScrollBar().setValue(0)
        editor.horizontalScrollBar().setValue(0)
        cursor = editor.cursorRect().translated(editor.pos())
        scroll = view.verticalScrollBar()
        if cursor.top() < 0:
            scroll.setValue(scroll.value() + cursor.top())
        elif cursor.bottom() >= view.viewport().height():
            scroll.setValue(scroll.value() + cursor.bottom() - view.viewport().height() + 1)
        view.viewport().update()

    def updateEditorGeometry(self, editor, option, index):
        layout = self.row_layout(option, index)
        rect = layout.russian.translated(option.rect.topLeft())
        # A pending item layout must never allow the editor into the next item.
        available = max(0, option.rect.bottom() - rect.top() - 5)
        rect.setHeight(min(rect.height(), available))
        editor.setGeometry(rect)
        if rect.height() < layout.russian.height():
            self.schedule_editor_refresh()

    def get_layout_width(
        self,
        option,
    ):
        view = self.parent()

        if (
            view is not None
            and hasattr(
                view,
                "layout_width",
            )
        ):
            width = (
                view.layout_width()
                - 12
            )

        else:
            width = (
                option.rect.width()
                - 12
            )

        return max(
            width,
            100,
        )


    def body_font(
        self,
        option,
    ):
        font = QFont(
            "Consolas"
        )

        size = (
            option.font.pointSizeF()
        )

        if size > 0:
            font.setPointSizeF(
                size + 1
            )
        else:
            font.setPointSizeF(
                11
            )

        return font

    def meta_font(
        self,
        option,
        bold=False,
    ):
        font = QFont(
            option.font
        )

        size = font.pointSizeF()

        if size > 0:
            font.setPointSizeF(
                max(
                    size - 1.0,
                    8.0,
                )
            )

        if bold:
            font.setWeight(
                QFont.Weight.DemiBold
            )

        return font


    def source_plain_text(
        self,
        row,
    ):
        return (
            "EN: "
            + "".join(
                segment["text"]
                for segment
                in row["source_diff"]
            )
        )


    def russian_plain_text(
        self,
        row,
    ):
        return (
            "RU: "
            + (
                row["ru_text"]
                if row["ru_text"] is not None
                else QCoreApplication.translate("gui", "[НЕТ ПЕРЕВОДА]")
            )
        )


    syntax_ranges = staticmethod(syntax_ranges)

    def create_source_layout(
        self,
        row,
        font,
        width,
        normal_color,
        *,
        clean=False,
    ):
        if clean:
            text = row.get("new_text") or ""
            return self.create_text_layout(text, font, width, self.syntax_ranges(text))
        # Сначала собираем ПОЛНЫЙ EN-текст.
        source_text = "".join(
            segment["text"]
            for segment in row["source_diff"]
        )

        text = source_text

        ranges = []

        # -------------------------
        # Синтаксис Stellaris
        # -------------------------

        ranges.extend(
            self.syntax_ranges(
                source_text,
                offset=0,
            )
        )

        # -------------------------
        # OLD -> NEW diff
        #
        # Добавляем ПОСЛЕ синтаксиса,
        # чтобы красный/зелёный diff
        # имел приоритет.
        # -------------------------

        position = 0

        for segment in row["source_diff"]:
            segment_text = (
                segment["text"]
            )

            start = position

            position += len(
                segment_text
            )

            kind = segment["type"]

            if kind == "equal":
                continue

            text_format = (
                QTextCharFormat()
            )

            if kind == "removed":
                text_format.setBackground(theme.color("diff.inline.removed.bg"))
                text_format.setForeground(
                    theme.color("diff.inline.removed")
                )

                text_format.setFontStrikeOut(
                    True
                )

            elif kind == "added":
                text_format.setBackground(theme.color("diff.inline.added.bg"))
                text_format.setForeground(
                    theme.color("diff.inline.added")
                )

                text_format.setFontWeight(
                    QFont.Weight.DemiBold
                )

            format_range = (
                QTextLayout.FormatRange()
            )

            format_range.start = start

            format_range.length = len(
                segment_text
            )

            format_range.format = (
                text_format
            )

            ranges.append(
                format_range
            )

        return self.create_text_layout(
            text,
            font,
            width,
            ranges,
        )

    def create_russian_layout(self, row, font, width, normal_color):
        text = row["ru_text"] if row["ru_text"] is not None else QCoreApplication.translate("gui", "[НЕТ ПЕРЕВОДА]")
        ranges = self.syntax_ranges(text)
        if row["ru_text"] is None:
            missing = QTextLayout.FormatRange()
            missing.start = 0
            missing.length = len(text)
            missing.format.setForeground(theme.color("editor.missing_translation"))
            ranges.append(missing)
        return self.create_text_layout(text, font, width, ranges)

    def row_layout(self, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        width = self.get_layout_width(option)
        font = self.body_font(option)
        meta_font = self.meta_font(option)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        color = (option.palette.highlightedText() if selected else option.palette.text()).color()
        active = self.is_editing(index)
        identity = self.layout_identity(row)
        signature = (
            width, font.toString(), meta_font.toString(), row["key"], row.get("new_text"),
            row["ru_text"], tuple((part["type"], part["text"]) for part in row["source_diff"]),
            row.get("new_comment") is not None, row.get("ru_comment") is not None,
            row.get("kind", "ENTRY"),
        )
        if not active:
            cached = self.cached_layout(identity, signature)
            if cached is not None:
                return cached

        large_change = is_large_change(tuple((part["type"], part["text"]) for part in row["source_diff"]),
                                       row.get("new_text"))
        header = QRect(6, 4, width, max(20, QFontMetrics(meta_font).height()))
        action_width = 24
        metadata_width = max(0, width - 4 * action_width)
        left_width = max(action_width, int(metadata_width * 0.40))
        key_font = self.meta_font(option)
        key_font.setWeight(QFont.Weight.DemiBold)
        key_text = ("# " if row.get("kind") == "DISABLED_ENTRY" else "") + (row["key"] or "")
        key_width = min(max(0, left_width - action_width), QFontMetrics(key_font).horizontalAdvance(key_text) + 4)
        key_rect = QRect(header.x(), header.y(), key_width, header.height())
        actions = {"copy_key": QRect(key_rect.x() + key_width, header.y(), action_width, header.height())}
        for i, action in enumerate(("copy_en", "comment", "diagnostics", "confirm")):
            actions[action] = QRect(header.x() + metadata_width + i * action_width, header.y(), action_width, header.height())
        center_width = max(0, (metadata_width - left_width) // 2)
        file_rect = QRect(header.x() + left_width, header.y(), center_width, header.height())
        status_rect = QRect(file_rect.x() + center_width, header.y(),
                            max(0, metadata_width - left_width - center_width), header.height())
        if large_change:
            actions["diff"] = QRect(status_rect.right() - action_width + 1, header.y(), action_width, header.height())
            status_rect.setRight(actions["diff"].left() - 1)
        label_font = QFont(font)
        label_font.setBold(True)
        label_width = ceil(max(QFontMetricsF(label_font).horizontalAdvance(label) for label in ("EN: ", "RU: ")))
        source_width = max(1, width - label_width - (20 if row.get("new_comment") is not None else 0))
        source_layout, source_height = self.create_source_layout(row, font, source_width, color, clean=large_change)
        source = QRect(6 + label_width, header.bottom() + 3, source_width,
                       ceil(max(source_height, QFontMetricsF(font).height())))
        en_label = QRect(6, source.y(), label_width, ceil(QFontMetricsF(label_font).height()))
        ru_width = max(1, width - label_width - (20 if row.get("ru_comment") is not None else 0))
        # Reserve one pixel for the editor's caret in both display and edit modes.
        russian_layout, ru_height = self.create_russian_layout(row, font, max(1, ru_width - 1), color)
        ru_height = ceil(max(ru_height, QFontMetricsF(font).height())) + 2
        if active:
            ru_height = self.editor.content_height(ru_width)
        ru_y = source.bottom() + 7
        ru_label = QRect(6, ru_y, label_width, ceil(QFontMetricsF(label_font).height()))
        russian = QRect(6 + label_width, ru_y, ru_width, ru_height)
        result = RowLayout(
            header, source, ru_label, russian, russian.bottom() + 7,
            source_layout, russian_layout,
            en_label, key_rect, file_rect, status_rect, actions, large_change,
        )
        if not active:
            self.remember_layout(identity, signature, result)
        return result

    def create_text_layout(
        self,
        text,
        font,
        width,
        formats=None,
    ):
        layout = QTextLayout(
            text,
            font,
        )

        text_option = QTextOption()

        text_option.setWrapMode(
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
        )

        layout.setTextOption(
            text_option
        )

        if formats:
            layout.setFormats(
                formats
            )

        layout.beginLayout()

        y = 0.0

        while True:
            line = (
                layout.createLine()
            )

            if not line.isValid():
                break

            line.setLineWidth(
                width
            )

            line.setPosition(
                QPointF(
                    0,
                    y,
                )
            )

            y += line.height()

        layout.endLayout()

        return (
            layout,
            y,
        )


    def structural_layout(self, option, row):
        if row["kind"] == "BLANK":
            return None, BLANK_ROW_HEIGHT
        width = self.get_layout_width(option)
        text = f"──── {row['section_title']} ────" if row.get("section_title") else row["text"]
        font = self.meta_font(option)
        identity = self.layout_identity(row)
        signature = (width, font.toString(), text)
        cached = self.cached_layout(identity, signature)
        if cached is not None:
            return cached
        layout, height = self.create_text_layout(text, font, width)
        result = (layout, ceil(height) + 12)
        self.remember_layout(identity, signature, result)
        return result

    def comment_rects(self, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if not is_entry(row):
            return []
        layout = self.row_layout(option, index)
        return [(QRect(layout.header.right() - 15, rect.y(), 16, 16).translated(option.rect.topLeft()), text, label)
                for rect, text, label in ((layout.source, row.get("new_comment"), "EN"),
                                          (layout.russian, row.get("ru_comment"), "RU")) if text is not None]

    def helpEvent(self, event, view, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if is_entry(row):
            layout = self.row_layout(option, index)
            point = event.pos() - option.rect.topLeft()
            tips = {"copy_key": QCoreApplication.translate("gui", "Копировать ключ"), "copy_en": QCoreApplication.translate("gui", "Копировать EN"),
                    "diff": QCoreApplication.translate("gui", "Английский текст значительно изменён. Нажмите Δ для подробного сравнения предыдущего и текущего EN."),
                    "comment": QCoreApplication.translate("gui", "Комментарий RU") + (QCoreApplication.translate("gui", " — комментарий EN изменён после подтверждения") if row.get("source_comment_changed") else ""),
                    "confirm": QCoreApplication.translate("gui", "Подтвердить перевод") if can_accept(row) else QCoreApplication.translate("gui", "Подтверждение недоступно для этой записи")}
            for name, rect in layout.actions.items():
                if rect.contains(point):
                    if name == "diagnostics":
                        warnings = warnings_for(index)
                        if warnings:
                            QToolTip.showText(event.globalPos(), "\n".join(stored_message(item.message) for item in warnings[:4]), view)
                            return True
                        continue
                    if name == "comment":
                        return True  # The delayed popover owns comment previews.
                    QToolTip.showText(event.globalPos(), tips[name], view)
                    return True
            if layout.key.contains(point):
                QToolTip.showText(event.globalPos(), "<p>" + html.escape(row["key"]) + "</p>", view)
                return True
            if layout.status.contains(point):
                text = html.escape(row_status_tooltip(row)).replace("\n", "<br>")
                QToolTip.showText(event.globalPos(), "<p>" + text + "</p>", view)
                return True
            if layout.file.contains(point):
                file = row.get("new_file") or row.get("old_file")
                line = row.get("new_line") if row.get("new_file") is not None else row.get("old_line")
                text = Path(file).as_posix() if file is not None else ""
                if line is not None:
                    text += f" : {line}"
                QToolTip.showText(event.globalPos(), "<p>" + html.escape(text) + "</p>", view)
                return True
        for rect, text, label in self.comment_rects(option, index):
            if rect.contains(event.pos()):
                return True
        return super().helpEvent(event, view, option, index)

    def action_at(self, option, index, point):
        if not is_entry(index.data(Qt.ItemDataRole.UserRole)):
            return None
        layout = self.row_layout(option, index)
        for name, rect in layout.actions.items():
            if name == "diagnostics" and not warnings_for(index, calculate=False):
                continue
            if rect.translated(option.rect.topLeft()).contains(point):
                return name
        for rect, _, label in self.comment_rects(option, index):
            if rect.contains(point):
                return "inline_comment_" + label
        return None

    def text_area_at(self, option, index, point):
        if not is_entry(index.data(Qt.ItemDataRole.UserRole)):
            return None
        layout = self.row_layout(option, index)
        point = point - option.rect.topLeft()
        if layout.source.contains(point):
            return "EN"
        if layout.russian.contains(point):
            local = QPointF(point - layout.russian.topLeft())
            # Blank space to the right/below the rendered text belongs to row
            # selection, not single-click editing. Reuse the measured layout.
            for number in range(layout.russian_layout.lineCount()):
                line = layout.russian_layout.lineAt(number)
                if line.naturalTextRect().adjusted(-2, 0, 2, 0).contains(local):
                    return "RU"
            if not layout.russian_layout.text() and local.x() <= 8:
                return "RU"  # Small caret target for an intentionally empty RU.
        return None

    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if row is None:
            return
        if not is_entry(row):
            painter.save()
            painter.setClipRect(option.rect)
            painter.fillRect(option.rect, option.palette.base())
            if row["kind"] == "COMMENT":
                layout, _ = self.structural_layout(option, row)
                painter.setPen(option.palette.placeholderText().color())
                layout.draw(painter, QPointF(option.rect.x() + 6, option.rect.y() + 6))
            painter.restore()
            return
        layout = self.row_layout(option, index)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        find = getattr(self.parent(), "local_find", None)
        color = (option.palette.highlightedText() if selected else option.palette.text()).color()
        meta_color = color if selected else option.palette.placeholderText().color()
        painter.save()
        try:
            painter.setClipRect(option.rect)
            painter.fillRect(option.rect, option.palette.highlight() if selected else option.palette.base())
            if find is not None and find.is_current_row(row):
                # A paint-only cue, independent of currentIndex/row selection.
                # Keep an explicitly selected row distinguishable as well.
                painter.fillRect(option.rect, theme.color(
                    "search.current_row.selected.bg" if selected else "search.current_row.bg"))
            painter.translate(option.rect.topLeft())
            disabled = row.get("kind") == "DISABLED_ENTRY"
            if disabled:
                painter.fillRect(QRect(0, 3, 2, max(0, option.rect.height() - 6)), theme.color("status.disabled_entry"))
            header = layout.header
            file_name = str(row["new_file"] or row["old_file"] or "").replace("\\", "/").rsplit("/", 1)[-1]
            line = row.get("new_line") if row.get("new_file") is not None else row.get("old_line")
            if line is not None:
                file_name += f" : {line}"
            status, status_role = row_status(row)
            meta_font = self.meta_font(option)
            key_font = self.meta_font(option)
            key_font.setWeight(QFont.Weight.DemiBold)
            for rect, text, font, elide, alignment in (
                (layout.key, ("# " if disabled else "") + (row["key"] or ""),
                 key_font, Qt.TextElideMode.ElideRight, Qt.AlignmentFlag.AlignLeft),
                (layout.file, file_name,
                 meta_font, Qt.TextElideMode.ElideMiddle, Qt.AlignmentFlag.AlignHCenter),
                (layout.status, status,
                 meta_font, Qt.TextElideMode.ElideLeft, Qt.AlignmentFlag.AlignRight),
            ):
                painter.setPen(color if rect == layout.key else meta_color)
                if rect == layout.file and not disabled:
                    painter.setPen(theme.color("row.file"))
                if rect == layout.status:
                    painter.setPen(theme.color(status_role))
                painter.setFont(font)
                metrics = QFontMetrics(font)
                shown = metrics.elidedText(text, elide, max(0, rect.width() - 4))
                if rect == layout.key and find is not None:
                    find.paint_key(painter, rect, shown, metrics, row)
                painter.drawText(rect, alignment | Qt.AlignmentFlag.AlignVCenter, shown)
            # Only the plain EN foreground changes; cached syntax/diff formats
            # and search highlights keep their semantic colors and geometry.
            painter.setPen(color if disabled else theme.color("row.source"))
            layout.source_layout.draw(painter, QPointF(layout.source.topLeft()),
                                      self.parent().occurrences.formats(layout.source_layout)
                                      + (find.selections(row, "new_text", layout) if find else []))
            draw_boundary_spaces(painter, layout.source_layout, QPointF(layout.source.topLeft()))
            label_font = self.body_font(option) if disabled else QFont(meta_font)
            label_font.setBold(disabled)
            painter.setPen(color if disabled else theme.color("row.label"))
            painter.setFont(label_font)
            painter.drawText(layout.en_label, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "EN:")
            painter.drawText(layout.ru_label, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "RU:")
            painter.setPen(color)
            if not self.is_editing(index):
                layout.russian_layout.draw(painter, QPointF(layout.russian.topLeft()),
                                           (self.parent().occurrences.formats(layout.russian_layout)
                                            if row["ru_text"] is not None else [])
                                           + (find.selections(row, "ru_text", layout) if find else []))
                draw_boundary_spaces(painter, layout.russian_layout, QPointF(layout.russian.topLeft()))
            painter.setPen(meta_color)
            for rect, _, _ in self.comment_rects(option, index):
                rect = rect.translated(-option.rect.topLeft()).adjusted(2, 2, -2, -3)
                painter.drawRoundedRect(rect, 2, 2)
                painter.drawLine(rect.left() + 3, rect.center().y(), rect.right() - 3, rect.center().y())
            hovered = getattr(self.parent(), "hover_action", lambda index: None)(index)
            painter.setFont(meta_font)
            for name, rect in layout.actions.items():
                if name == "diagnostics" and not warnings_for(index, calculate=False):
                    continue
                enabled = name != "confirm" or can_accept(row)
                painter.setPen(color if enabled and (hovered == name or selected) else meta_color)
                if not disabled:
                    painter.setPen(theme.color("icon.hover" if enabled and hovered == name else "row.action"))
                if name == "confirm" and row["translation_status"] == "OK":
                    painter.setPen(theme.color("status.ready"))
                if hovered == name and enabled:
                    painter.fillRect(rect.adjusted(1, 1, -1, -1), option.palette.alternateBase())
                icon = rect.center()
                if name == "copy_key":
                    painter.drawRoundedRect(QRect(icon.x() - 5, icon.y() - 5, 8, 9), 1, 1)
                    painter.drawLine(icon.x() - 2, icon.y() - 7, icon.x() + 5, icon.y() - 7)
                    painter.drawLine(icon.x() + 5, icon.y() - 7, icon.x() + 5, icon.y() + 2)
                elif name == "copy_en":
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "EN")
                elif name == "diff":
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Δ")
                elif name == "comment":
                    painter.drawRoundedRect(QRect(icon.x() - 6, icon.y() - 5, 12, 9), 2, 2)
                    painter.drawLine(icon.x() - 3, icon.y() + 4, icon.x() - 3, icon.y() + 7)
                    if row.get("source_comment_changed"):
                        painter.drawText(rect.adjusted(12, -3, 3, -6), Qt.AlignmentFlag.AlignRight, "*")
                elif name == "diagnostics":
                    painter.setPen(theme.color("status.warning_icon"))
                    painter.drawLine(icon.x(), icon.y() - 6, icon.x() - 6, icon.y() + 5)
                    painter.drawLine(icon.x() - 6, icon.y() + 5, icon.x() + 6, icon.y() + 5)
                    painter.drawLine(icon.x() + 6, icon.y() + 5, icon.x(), icon.y() - 6)
                    painter.drawLine(icon.x(), icon.y() - 2, icon.x(), icon.y() + 1)
                    painter.drawPoint(icon.x(), icon.y() + 3)
                else:
                    paint_check(painter, rect, painter.pen().color())
            painter.setPen(option.palette.mid().color() if disabled else theme.color("row.separator"))
            painter.drawLine(0, option.rect.height() - 1, option.rect.width(), option.rect.height() - 1)
        finally:
            painter.restore()

    def sizeHint(self, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if row is None:
            return QSize(100, 30)
        if not is_entry(row):
            return QSize(self.get_layout_width(option) + 12,
                         self.structural_layout(option, row)[1])
        # Exact measurements already concern only visible rows + overscan.
        # Painting reads the cached warnings and never performs text analysis.
        warnings_for(index)
        layout = self.row_layout(option, index)
        return QSize(layout.header.width() + 12, layout.height)


class MainWindow(QMainWindow):
    closed = Signal()

    def __init__(
        self,
        rows,
        save_callback=None,
        vanilla_rows=None,
        export_callback=None,
        project_name=None,
        documents=None,
        source_revisions=None,
        deploy_callback=None,
        vanilla_loader=None,
        source_prefix="",
    ):
        super().__init__()

        self.rows = rows
        self.documents = documents or []
        self.source_revisions = source_revisions or []
        self.source_prefix = Path(source_prefix)
        self.vanilla_rows = (
            vanilla_rows or []
        )
        self._vanilla_loader = vanilla_loader
        self._vanilla_loaded = vanilla_loader is None
        self._vanilla_loading = False
        self._vanilla_lookup_future = None
        self.ui_state = None
        self.glossary = None
        self.entry_rows = {row["entry_id"]: i for i, row in enumerate(rows) if row.get("entry_id")}

        # -------------------------
        # Индексы глобального поиска
        # -------------------------

        self.mod_search_index = []
        self.vanilla_search_index = []

        self.mod_exact_keys = {}
        self.vanilla_exact_keys = {}



        self.save_callback = save_callback
        self.export_callback = export_callback
        self.deploy_callback = deploy_callback
        self.project_title = (
            f"{project_name} — Stellaris Localizer" if project_name else "Stellaris Localizer"
        )
        self.has_unsaved_changes = False
        self.undo_stack = QUndoStack(
            self
        )

        self.setWindowTitle(
            self.project_title
        )

        self.resize(
            1400,
            900,
        )

        # -------------------------
        # Модель локализации
        # -------------------------

        self.model = LocalizationListModel(
            rows,
            self.undo_stack,
        )
        self.model.source_revisions = self.source_revisions

        self.proxy_model = (
            LocalizationFilterProxy()
        )

        self.proxy_model.setSourceModel(
            self.model
        )
        if documents:
            self.proxy_model = DocumentFilterProxy(self.proxy_model, documents, self)

        # -------------------------
        # Поиск
        # -------------------------

        # -------------------------
        # Фильтры
        # -------------------------

        self.button_group = QButtonGroup(
            self
        )

        self.button_group.setExclusive(
            True
        )

        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(3)

        filters = [
            (QCoreApplication.translate("gui", "Все"), "ALL"),
            (
                QCoreApplication.translate("gui", "Требуют внимания"),
                "ATTENTION",
            ),
            (QCoreApplication.translate("gui", "Недоперевод"), "ENGLISH_TEXT"),
        ]

        for button_text, mode in filters:
            button = QPushButton(
                button_text
            )

            button.setCheckable(True)
            button.setProperty("filter_mode", mode)
            button.setProperty("role", "filter")

            if mode == "ALL":
                button.setChecked(True)

            button.clicked.connect(
                lambda checked,
                selected_mode=mode:
                self.proxy_model.set_mode(
                    selected_mode
                )
            )

            self.button_group.addButton(
                button
            )

            filter_layout.addWidget(
                button
            )

        filter_layout.addStretch()
        self.save_action = QAction(
            self
        )

        self.save_action.setShortcut(
            QKeySequence(SHORTCUTS["save"])
        )

        self.save_action.triggered.connect(
            self.save_changes
        )

        self.addAction(
            self.save_action
        )

        # -------------------------
        # Clipboard actions
        # -------------------------

        self.copy_action = QAction(
            self
        )

        self.copy_action.setShortcut(
            QKeySequence.StandardKey.Copy
        )

        self.copy_action.triggered.connect(
            lambda: self.workspace_edit_action("copy", self.copy_selected_text)
        )

        self.addAction(
            self.copy_action
        )


        self.paste_action = QAction(
            self
        )

        self.paste_action.setShortcut(
            QKeySequence.StandardKey.Paste
        )

        self.paste_action.triggered.connect(
            lambda: self.workspace_edit_action("paste", self.paste_selected_text)
        )

        self.addAction(
            self.paste_action
        )


        self.copy_with_keys_action = QAction(
            self
        )

        self.copy_with_keys_action.setShortcut(
            QKeySequence(
                "Ctrl+Shift+C"
            )
        )

        self.copy_with_keys_action.triggered.connect(
            self.copy_selected_with_keys
        )

        self.addAction(
            self.copy_with_keys_action
        )


        self.copy_key_action = QAction(
            self
        )

        self.copy_key_action.setShortcut(
            QKeySequence(
                "Alt+C"
            )
        )

        self.copy_key_action.triggered.connect(
            self.copy_current_key
        )

        self.addAction(
            self.copy_key_action
        )


        # -------------------------
        # Undo / Redo
        # -------------------------

        self.undo_action = QAction(
            QCoreApplication.translate("gui", "Отменить"),
            self,
        )

        self.undo_action.setShortcut(
            QKeySequence(SHORTCUTS["undo"])
        )

        self.undo_action.triggered.connect(
            lambda: self.workspace_edit_action("undo", self.undo_stack.undo)
        )

        self.addAction(
            self.undo_action
        )


        self.redo_action = QAction(
            QCoreApplication.translate("gui", "Вернуть"),
            self,
        )

        self.redo_action.setShortcut(
            QKeySequence(SHORTCUTS["redo"])
        )

        self.redo_action.triggered.connect(
            lambda: self.workspace_edit_action("redo", self.undo_stack.redo)
        )

        self.addAction(
            self.redo_action
        )

        self.undo_stack.cleanChanged.connect(
            self.update_dirty_state
        )

        self.global_search_action = QAction(
            QCoreApplication.translate("gui", "Глобальный поиск"),
            self,
        )

        self.global_search_action.setShortcut(
            QKeySequence(
                SHORTCUTS["search_project"]
            )
        )

        self.global_search_action.triggered.connect(
            self.toggle_global_search
        )

        self.addAction(
            self.global_search_action
        )


        # -------------------------
        # Основной список
        # -------------------------

        self.list_view = LocalizationListView()

        self.list_view.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
        )

        self.list_view.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )

        self.delegate = (
            LocalizationDelegate(
                self.list_view
            )
        )

        self.list_view.setModel(
            self.proxy_model
        )

        self.list_view.setItemDelegate(
            self.delegate
        )

        self.list_view.setWordWrap(True)

        self.list_view.setVerticalScrollMode(
            QListView.ScrollMode.ScrollPerPixel
        )

        # -------------------------
        # Правая часть:
        # основная рабочая панель
        # -------------------------

        right_layout = QVBoxLayout()

        right_layout.setContentsMargins(
            SURFACE_PADDING,
            2,
            SURFACE_PADDING,
            SURFACE_PADDING,
        )

        # -------------------------
        # Основные вкладки
        # -------------------------

        self.tab_bar = QuietTabBar()
        self.tab_bar.setObjectName("workspaceTabs")
        self.tab_bar.setFixedHeight(29)

        self.tab_bar.setMovable(
            True
        )

        self.tab_bar.setTabsClosable(
            True
        )

        self.tab_bar.setExpanding(
            False
        )

        self.tab_bar.setUsesScrollButtons(
            True
        )

        self.tab_bar.currentChanged.connect(
            self.workspace_tab_changed
        )

        self.tab_bar.tabCloseRequested.connect(
            self.close_workspace_tab
        )

        self.current_tab_index = -1
        self.closed_workspace_tabs = deque(maxlen=20)
        self.reopen_tab_action = QAction(QCoreApplication.translate("gui", "Восстановить закрытую вкладку"), self)
        self.reopen_tab_action.setShortcut(QKeySequence(SHORTCUTS["reopen_tab"]))
        self.reopen_tab_action.triggered.connect(self.reopen_closed_workspace_tab)
        self.addAction(self.reopen_tab_action)

        # Кнопка открытия справа

        self.open_right_button = SplitButton()

        self.open_right_button.clicked.connect(
            self.toggle_secondary_panel
        )

        primary_tab_layout = QHBoxLayout()

        primary_tab_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        primary_tab_layout.addWidget(
            self.tab_bar,
            1,
        )

        primary_tab_layout.addWidget(
            self.open_right_button
        )

        right_layout.addLayout(
            primary_tab_layout
        )
        self.workspace_status = WorkspaceStatus(self)
        right_layout.setSpacing(2)

        self.editor_stack = QStackedWidget()
        self.localization_page = QWidget()
        localization_layout = QVBoxLayout(self.localization_page)
        localization_layout.setContentsMargins(0, 0, 0, 0)
        self.local_find = LocalFind(self)
        localization_layout.addLayout(filter_layout)
        localization_layout.addWidget(self.list_view)
        self.editor_stack.addWidget(self.localization_page)
        right_layout.addWidget(self.editor_stack)

        right_widget = QFrame()
        right_widget.setObjectName("workspaceSurface")

        right_widget.setLayout(
            right_layout
        )

        # -------------------------
        # Вторая панель
        # -------------------------

        # Only the panel shell/tabs exist at startup. Heavy models and the view
        # are connected on first use, including restoration of a visible panel.
        self.secondary_proxy_model = None
        self.secondary_list_view = None
        self.secondary_delegate = None
        self.vanilla_model = None
        self.vanilla_proxy_model = None

        self.secondary_tab_bar = QuietTabBar()

        self.secondary_tab_bar.setMovable(
            True
        )

        self.secondary_tab_bar.setTabsClosable(
            True
        )

        self.secondary_tab_bar.setExpanding(
            False
        )

        self.secondary_tab_bar.setUsesScrollButtons(
            True
        )

        self.secondary_tab_bar.currentChanged.connect(
            self.secondary_tab_changed
        )

        self.secondary_tab_bar.tabCloseRequested.connect(
            self.close_secondary_tab
        )

        self.pin_preview_button = QPushButton(
            QCoreApplication.translate("gui", "Закрепить")
        )

        self.pin_preview_button.clicked.connect(
            self.pin_secondary_preview
        )

        self.pin_preview_button.hide()

        self.secondary_current_tab_index = -1

        secondary_layout = QVBoxLayout()

        secondary_layout.setContentsMargins(
            SURFACE_PADDING,
            SURFACE_PADDING,
            SURFACE_PADDING,
            SURFACE_PADDING,
        )

        secondary_tabs_layout = QHBoxLayout()

        secondary_tabs_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        secondary_tabs_layout.addWidget(
            self.secondary_tab_bar,
            1,
        )

        secondary_tabs_layout.addWidget(
            self.pin_preview_button
        )

        secondary_layout.addLayout(
            secondary_tabs_layout
        )

        self.secondary_widget = QFrame()
        self.secondary_widget.setObjectName("bottomSurface")

        self.secondary_widget.setMinimumHeight(
            120
        )

        self.secondary_widget.setLayout(
            secondary_layout
        )

        self.secondary_widget.hide()
        self.open_right_button.watch(self.secondary_widget)

        # -------------------------
        # Разделитель двух панелей
        # -------------------------

        self.workspace_splitter = QSplitter(
            Qt.Orientation.Vertical
        )
        self.workspace_splitter.setObjectName("workspaceSplitter")

        self.workspace_splitter.setOpaqueResize(
            False
        )

        self.workspace_splitter.addWidget(
            right_widget
        )

        self.workspace_splitter.addWidget(
            self.secondary_widget
        )

        self.workspace_splitter.setStretchFactor(
            0,
            1,
        )

        self.workspace_splitter.setStretchFactor(
            1,
            1,
        )

        # -------------------------
        # Левая панель файлов
        # -------------------------

        self.file_tree = ExplorerTree()

        self.file_tree.setHeaderHidden(True)

        self.file_tree.setMinimumWidth(
            280
        )

        self.file_tree.setMaximumWidth(
            500
        )

        # Считаем количество строк
        # для каждого файла.

        file_counts = {str(Path(document.file)): 0 for document in self.documents}

        for row in rows:
            if row["section"] != "CURRENT":
                continue
            row_file = (
                row["new_file"]
                if row["new_file"]
                else row["old_file"]
            )

            if row_file is None:
                continue

            file_path = str(row_file)

            file_counts[file_path] = (
                file_counts.get(
                    file_path,
                    0,
                )
                + 1
            )

        # Корень "Все файлы"

        all_files = sorted(
            file_counts.keys()
        )

        root_item = QTreeWidgetItem(
            [
                QCoreApplication.translate("gui", "Все файлы ({0})").format(len(rows))
            ]
        )

        root_item.setData(
            0,
            Qt.ItemDataRole.UserRole,
            None,
        )

        self.file_tree.addTopLevelItem(
            root_item
        )
        root_item.setData(0, EXPLORER_ROLE, {"kind": "all", "path": None})

        # Здесь будем помнить уже
        # созданные папки.

        folder_items = {}

        # Создаём дерево.

        for file_path in all_files:
            # Display the same target path that export derives, while retaining
            # source identities in both item roles and all file-filter payloads.
            path = self.source_prefix / file_path
            target_path = self.translation_target_path(file_path)

            parent_item = None
            accumulated_parts = []

            # Все части пути,
            # кроме имени файла.

            for source_folder, folder_name in zip(path.parts[:-1], target_path.parts[:-1]):
                accumulated_parts.append(
                    source_folder
                )

                folder_key = str(
                    Path(
                        *accumulated_parts
                    )
                )

                if (
                    folder_key
                    not in folder_items
                ):
                    folder_item = (
                        QTreeWidgetItem(
                            [folder_name]
                        )
                    )

                    folder_item.setData(
                        0,
                        Qt.ItemDataRole.UserRole,
                        [],
                    )

                    folder_items[
                        folder_key
                    ] = folder_item
                    relative_parts = accumulated_parts[len(self.source_prefix.parts):]
                    if relative_parts:
                        # Keep old folder selections compatible with ui-state.
                        folder_state = {"kind": "folder", "path": str(Path(*relative_parts))}
                    else:
                        # A legacy source prefix is a display parent only; it
                        # was not part of the stored document/file identities.
                        folder_state = {"kind": "folder", "path": ".", "source_prefix": folder_key}
                    folder_item.setData(0, EXPLORER_ROLE, folder_state)

                    if parent_item is None:
                        self.file_tree.addTopLevelItem(
                            folder_item
                        )
                    else:
                        parent_item.addChild(
                            folder_item
                        )

                parent_item = (
                    folder_items[
                        folder_key
                    ]
                )

                existing_files = (
                    parent_item.data(
                        0,
                        Qt.ItemDataRole.UserRole,
                    )
                    or []
                )

                if (
                    file_path
                    not in existing_files
                ):
                    existing_files.append(
                        file_path
                    )

                parent_item.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    existing_files,
                )

            # Сам файл

            file_name = target_path.name

            file_item = QTreeWidgetItem(
                [
                    file_name
                ]
            )

            file_item.setData(
                0,
                Qt.ItemDataRole.UserRole,
                [file_path],
            )
            file_item.setData(0, EXPLORER_ROLE, {"kind": "file", "path": file_path})
            file_item.setToolTip(0, QCoreApplication.translate("gui", "Перевод: {0}\nИсходник: {1}").format(target_path.as_posix(), path.as_posix()))

            if parent_item is None:
                self.file_tree.addTopLevelItem(
                    file_item
                )
            else:
                parent_item.addChild(
                    file_item
                )

        # Показываем дерево раскрытым
        # на первом этапе.

        self.file_tree.expandAll()

        self.file_tree.setCurrentItem(
            root_item
        )

        self.file_tree.currentItemChanged.connect(
            self.file_selected
        )
        self.file_tree.sameItemClicked.connect(lambda item: self.file_selected(item, None))

        # -------------------------
        # Splitter
        # -------------------------

        self.sidebar = WorkspaceSidebar(self.file_tree, self.workspace_splitter)
        self.setCentralWidget(self.sidebar)
        self.global_search_box = self.sidebar.query_box
        self.global_search_results = self.sidebar.results
        self._search_generation = 0
        self._search_signatures = set()
        self._search_data_version = 0
        self._pending_search = None
        self._search_work_timer = QTimer(self)
        self._search_work_timer.setSingleShot(True)
        self._search_work_timer.timeout.connect(self._run_global_search)
        self.sidebar.searchChanged.connect(self.search_conditions_changed)
        self.model.dataChanged.connect(self.invalidate_search_reuse)
        self.model.modelReset.connect(self.invalidate_search_reuse)
        self.sidebar.searchRequested.connect(self.perform_global_search)
        self.global_replacement = GlobalReplacementEngine(self.model, self)
        self.global_replacement.applied.connect(self.refresh_after_global_replace)
        self.global_search_results.replaceRequested.connect(self.replace_global_search_target)
        self.global_search_results.openEditorRequested.connect(self.open_global_search_result_in_editor)
        self.global_search_results.openPanelRequested.connect(self.open_global_search_result_data)
        self.global_search_results.itemActivated.connect(self.open_global_search_result)
        self.global_search_results.itemClicked.connect(self.open_global_search_result)

        self.toggle_sidebar_action = QAction(QCoreApplication.translate("gui", "Показать/скрыть Sidebar"), self)
        self.toggle_sidebar_action.setShortcut(QKeySequence(SHORTCUTS["sidebar"]))
        self.toggle_sidebar_action.triggered.connect(self.sidebar.toggle)
        self.addAction(self.toggle_sidebar_action)
        self.connect_localization_view_actions(self.list_view)
        self.tab_bar.tabMoved.connect(lambda old, new: self.track_moved_tab(False, old, new))
        self.secondary_tab_bar.tabMoved.connect(lambda old, new: self.track_moved_tab(True, old, new))
        self.accept_translation_action = QAction(QCoreApplication.translate("gui", "Подтвердить перевод"), self)
        self.accept_translation_action.setShortcuts([QKeySequence("Ctrl+Return"), QKeySequence("Ctrl+Enter")])
        self.accept_translation_action.triggered.connect(self.accept_current_translation)
        self.addAction(self.accept_translation_action)
        self.replace_file_action = self.local_find.actions_by_shortcut[SHORTCUTS["replace"]]
        self.replace_file_action.setText(QCoreApplication.translate("gui", "Заменить в текущей вкладке"))
        self.replace_project_action = QAction(QCoreApplication.translate("gui", "Замена во всём проекте"), self)
        self.replace_project_action.setShortcut(QKeySequence(SHORTCUTS["replace_project"]))
        self.replace_project_action.triggered.connect(lambda: self.open_replace("project"))
        self.addAction(self.replace_project_action)
        self.sidebar_escape = QAction(self.sidebar)
        self.sidebar_escape.setShortcut(QKeySequence("Esc"))
        self.sidebar_escape.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.sidebar_escape.triggered.connect(lambda: self.sidebar.set_sidebar_visible(False))
        self.sidebar.addAction(self.sidebar_escape)

        self.explorer_files = ExplorerFiles(self)
        self.token_inspector = TokenInspector(self)
        self.token_inspector.register(self.list_view)
        self.row_diagnostics_popover = RowDiagnosticsPopover(self)

    def connect_localization_view_actions(self, view):
        if hasattr(self, "token_inspector"):
            self.token_inspector.register(view)
        view.comment_popover = CommentPopover(view, self.model)
        view.rowActionHovered.connect(view.comment_popover.hover)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        view.customContextMenuRequested.connect(lambda pos: self.show_entry_menu(view, pos))
        view.rowActionRequested.connect(lambda action, index: self.row_action(view, action, index))

    def ensure_vanilla(self):
        if self._vanilla_loaded:
            return
        if self._vanilla_loading:
            # Existing synchronous callers (Search/navigation) still need ready
            # rows on return. Reuse an in-flight hover load instead of exposing
            # an empty reference or starting another loader/cache write.
            if self._vanilla_lookup_future is not None:
                try:
                    rows, _ = self._vanilla_lookup_future.result()
                    self.apply_vanilla_rows(rows)
                except (OSError, UnicodeError, ValueError) as error:
                    self.statusBar().showMessage(QCoreApplication.translate("gui", "Справочник Stellaris недоступен: {0}").format(error), 15000)
            return
        self._vanilla_loading = True
        try:
            rows = self._vanilla_loader()
            self.apply_vanilla_rows(rows)
        except (OSError, UnicodeError, ValueError) as error:
            self.statusBar().showMessage(QCoreApplication.translate("gui", "Справочник Stellaris недоступен: {0}").format(error), 15000)
        finally:
            self._vanilla_loading = False

    def apply_vanilla_rows(self, rows):
        """Publish results of the existing loader on the UI thread."""
        if self.vanilla_model is not None:
            self.vanilla_model.beginResetModel()
            self.vanilla_model.rows = rows
            self.vanilla_model.endResetModel()
        self.vanilla_rows = rows
        self._vanilla_loaded = True

    def ensure_secondary_view(self):
        """Materialize once; hiding the panel never discards its models/editor."""
        if self.secondary_list_view is not None:
            return False
        entry_filter = LocalizationFilterProxy()
        entry_filter.setSourceModel(self.model)
        self.secondary_proxy_model = (DocumentFilterProxy(entry_filter, self.documents, self)
                                      if self.documents else entry_filter)
        self.vanilla_model = LocalizationListModel(self.vanilla_rows, self.undo_stack)
        self.vanilla_proxy_model = LocalizationFilterProxy()
        self.vanilla_proxy_model.setSourceModel(self.vanilla_model)

        view = LocalizationListView(self.secondary_widget)
        view.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.secondary_delegate = LocalizationDelegate(view)
        view.setItemDelegate(self.secondary_delegate)
        view.setModel(self.secondary_proxy_model)
        view.setWordWrap(True)
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.connect_localization_view_actions(view)
        self.secondary_list_view = view
        self.secondary_widget.layout().addWidget(view, 1)
        return True

    def entry_menu(self, view, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if not is_entry(row):
            return None
        if not view.selectionModel().isSelected(index):
            view.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        menu = QMenu(view)
        for label, mode, binding in (
            (QCoreApplication.translate("gui", "Копировать"), "text", self.copy_action),
            (QCoreApplication.translate("gui", "Копировать ключ"), "key", self.copy_key_action),
            (QCoreApplication.translate("gui", "Копировать вместе с ключом"), "with_key", self.copy_with_keys_action),
        ):
            action = menu.addAction(label)
            set_action_caption(action, label, binding.shortcut())
            action.triggered.connect(lambda checked=False, mode=mode: self.copy_entries(view, mode))
        source = index
        while isinstance(source.model(), QAbstractProxyModel):
            source = source.model().mapToSource(source)
        if source.model() is self.model:
            menu.addSeparator()
            accept = menu.addAction(QCoreApplication.translate("gui", "Подтвердить перевод"))
            # Both Return and keypad Enter are registered on the real action.
            set_action_caption(accept, QCoreApplication.translate("gui", "Подтвердить перевод"), self.accept_translation_action.shortcuts()[-1])
            targets = [QPersistentModelIndex(self.entry_source(item)) for item in view.selectionModel().selectedRows()]
            accept.setEnabled(any(item.isValid() and item.model() is self.model
                                  and can_accept(item.data(Qt.ItemDataRole.UserRole)) for item in targets))
            def confirm_entry():
                view.finish_editing()
                self.model.accept_translations([QModelIndex(item) for item in targets])
            accept.triggered.connect(confirm_entry)
            label = QCoreApplication.translate("gui", "Изменить комментарий") if row.get("ru_comment") is not None else QCoreApplication.translate("gui", "Добавить комментарий")
            comment_index = QPersistentModelIndex(index)
            menu.addAction(label).triggered.connect(
                lambda: QTimer.singleShot(0, view, lambda: self.edit_ru_comment(view, QModelIndex(comment_index))))
            if row.get("ru_comment") is not None:
                menu.addAction(QCoreApplication.translate("gui", "Удалить комментарий")).triggered.connect(lambda: self.model.set_comment(source, None))
        theme.style_menu(menu)
        return menu

    @staticmethod
    def entry_source(index):
        while index.isValid() and isinstance(index.model(), QAbstractProxyModel):
            index = index.model().mapToSource(index)
        return index

    def active_localization_view(self):
        focus = QApplication.focusWidget()
        return (self.secondary_list_view if self.secondary_list_view is not None and focus is not None and
                (focus is self.secondary_list_view or self.secondary_list_view.isAncestorOf(focus)) else self.list_view)

    def copy_entries(self, view, mode="text"):
        rows = [row for index in sorted(view.selectionModel().selectedRows(), key=lambda item: item.row())
                if is_entry(row := index.data(Qt.ItemDataRole.UserRole))]
        if not rows:
            return
        texts = []
        for row in rows:
            text = row["new_text"] if row["new_text"] is not None else row.get("old_text") or ""
            texts.append(row["key"] if mode == "key" else f'{row["key"]}: "{text}"' if mode == "with_key" else text)
        QApplication.clipboard().setText("\n".join(texts))
        self.statusBar().showMessage(QCoreApplication.translate("gui", "Скопировано строк: {0}").format(len(texts)), 3000)

    def edit_ru_comment(self, view, index, action="comment"):
        view.comment_popover.open(index, action, edit=True)

    def row_action(self, view, action, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if not is_entry(row):
            return
        if action == "copy_key":
            QApplication.clipboard().setText(row["key"])
        elif action == "copy_en":
            QApplication.clipboard().setText(row["new_text"] if row["new_text"] is not None else row.get("old_text") or "")
        elif action == "diff":
            view.show_detailed_diff(index)
        elif action == "diagnostics":
            self.row_diagnostics_popover.open(view, index)
        elif action == "comment" or action.startswith("inline_comment_"):
            self.edit_ru_comment(view, index, action)
        else:
            source = self.entry_source(index)
            if not source.isValid() or source.model() is not self.model:
                return
            if action == "confirm":
                # A status filter may remove this row and reset the document
                # proxy. Keep the other selected entries selected in that case.
                selected = [QPersistentModelIndex(self.entry_source(item))
                            for item in view.selectionModel().selectedRows()]
                current = QPersistentModelIndex(self.entry_source(view.currentIndex()))
                view.finish_editing()
                self.model.accept_translation(source)
                chain, proxy = [], view.model()
                while isinstance(proxy, QAbstractProxyModel):
                    chain.append(proxy)
                    proxy = proxy.sourceModel()
                def displayed(item):
                    result = QModelIndex(item)
                    for proxy in reversed(chain):
                        result = proxy.mapFromSource(result)
                    return result
                remaining = [item for value in selected if (item := displayed(value)).isValid()]
                selection_model = view.selectionModel()
                if (len(remaining) != len(selection_model.selectedRows())
                        or any(not selection_model.isSelected(item) for item in remaining)):
                    selection = QItemSelection()
                    for item in remaining:
                        selection.select(item, item)
                    selection_model.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                restored_current = displayed(current)
                if restored_current.isValid() and restored_current != view.currentIndex():
                    selection_model.setCurrentIndex(restored_current, QItemSelectionModel.SelectionFlag.NoUpdate)

    def accept_current_translation(self, view=None):
        if not isinstance(view, LocalizationListView):
            view = self.active_localization_view()
        source = view.currentIndex()
        while source.isValid() and isinstance(source.model(), QAbstractProxyModel):
            source = source.model().mapToSource(source)
        if not source.isValid() or source.model() is not self.model:
            return
        view.finish_editing()
        if not can_accept(self.model.rows[source.row()]):
            return
        self.model.accept_translation(source)

    def show_entry_menu(self, view, pos):
        menu = self.entry_menu(view, view.indexAt(pos))
        if menu is not None:
            menu.exec(view.viewport().mapToGlobal(pos))
            menu.deleteLater()

    def selected_replace_rows(self):
        # Map the main working view's selection through its filter. The official
        # reference model and the search result list never provide replace targets.
        return frozenset(
            self.proxy_model.mapToSource(index).row()
            for index in self.list_view.selectionModel().selectedRows()
        )

    def track_moved_tab(self, secondary, old, new):
        name = "secondary_current_tab_index" if secondary else "current_tab_index"
        current = getattr(self, name)
        if current == old:
            current = new
        elif old < current <= new:
            current -= 1
        elif new <= current < old:
            current += 1
        setattr(self, name, current)
        if not secondary:
            pinned = next((i for i in range(self.tab_bar.count())
                           if (self.tab_bar.tabData(i) or {}).get("pinned")), 0)
            if pinned > 0:
                # Reposition synchronously without a second tab switch or state save.
                with QSignalBlocker(self.tab_bar):
                    self.tab_bar.moveTab(pinned, 0)
                self.current_tab_index = 0 if current == pinned else current + 1 if 0 <= current < pinned else current
                self.tab_bar.refresh_close_buttons()

    def restore_saved_position(self, view, bar, index, data, *, immediate=False):
        position = data.get("position")
        if (not isinstance(position, dict) or data.get("source", "mod") != "mod"
                or not (position.get("current_entry_id") or position.get("top_entry_id"))):
            return False
        def apply():
            if bar.currentIndex() == index and (bar.tabData(index) or {}).get("position") == position:
                restore_position(view, position, self.entry_rows)
        if immediate:
            apply()
        else:
            QTimer.singleShot(0, view, apply)
        return True

    def current_replace_file(self):
        data = self.tab_bar.tabData(self.tab_bar.currentIndex()) or {}
        files = data.get("files")
        if files is not None and len(files) == 1:
            return Path(next(iter(files)))
        row = self.list_view.currentIndex().data(Qt.ItemDataRole.UserRole)
        return (row["new_file"] or row["old_file"]) if row else None

    def selected_translation_text(self):
        """Read the shortcut snapshot before any focus/editor lifecycle change."""
        return selected_search_text(self)

    def finish_project_editing(self):
        self.list_view.finish_editing()
        if self.secondary_list_view is not None:
            self.secondary_list_view.finish_editing()

    def open_replace(self, scope):
        query = self.selected_translation_text()
        self.finish_project_editing()
        self.sidebar.show_page("search")
        self.sidebar.set_replace_expanded(True)
        if query is not None and query != self.global_search_box.text():
            self.global_search_box.setText(query)
        self.sidebar.replacement_box.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.sidebar.replacement_box.selectAll()

    def translation_target_path(self, source_file):
        """Derived display path, using precisely the export mapping and prefix."""
        return english_to_russian_path(self.source_prefix / source_file)

    def file_selected(
        self,
        current,
        previous,
    ):
        if current is None:
            return

        file_paths = current.data(
            0,
            Qt.ItemDataRole.UserRole,
        )

        title = current.text(0)

        # Убираем счётчик:
        # file.yml (350) -> file.yml
        if " (" in title:
            title = title.rsplit(
                " (",
                1,
            )[0]

        self.open_workspace_tab(
            title,
            file_paths,
        )

    def update_dirty_state(self, clean):
        self.has_unsaved_changes = not clean
        self.setWindowTitle(self.project_title + ("" if clean else " *"))

    def save_changes(self):
        if self.glossary is not None and not self.glossary.save():
            return False
        if self.save_callback is None:
            return False
        self.finish_project_editing()
        try:
            result = self.save_callback(self.rows)
        except Exception as error:
            QMessageBox.critical(self, QCoreApplication.translate("gui", "Не удалось сохранить проект"), str(error))
            return False
        self.undo_stack.setClean()
        self.statusBar().showMessage(
            QCoreApplication.translate("gui", "Проект сохранён: {0}").format(result.get('project_path', '')), 8000,
        )
        if self.ui_state is not None:
            self.ui_state.save()
        return True

    def export_changes(self):
        if self.export_callback is None or not self.save_changes():
            return
        try:
            result = self.export_callback()
        except Exception as error:
            QMessageBox.critical(self, QCoreApplication.translate("gui", "Проект сохранён, но экспорт не выполнен"), str(error))
            return
        if result is None:
            return
        self.statusBar().showMessage(
            QCoreApplication.translate("gui", "Экспортировано файлов: {0} | Без перевода: {1} | {2}").format(result['files'], result['missing'], result['output_path']), 8000,
        )

    def deploy_changes(self):
        if self.deploy_callback is None or not self.save_changes():
            return
        try:
            result = self.deploy_callback()
        except Exception as error:
            QMessageBox.critical(self, QCoreApplication.translate("gui", "Не удалось загрузить локализацию в папку mod"), str(error))
            return
        if result is not None:
            removed = QCoreApplication.translate("gui", " | Удалено файлов: {0}").format(result['deleted']) if result["deleted"] else ""
            self.statusBar().showMessage(QCoreApplication.translate("gui", "Записано файлов: {0}{1} | {2}").format(result['files'], removed, result['target']), 12000)

    def closeEvent(self, event):
        if self.glossary is not None and not self.glossary.prepare_close():
            event.ignore()
            return
        self.finish_project_editing()
        if not self.undo_stack.isClean():
            choice = QMessageBox.question(
                self, QCoreApplication.translate("gui", "Закрыть проект"), QCoreApplication.translate("gui", "Сохранить изменения перед закрытием?"),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                if self.glossary is not None:
                    self.glossary.cancel_close()
                event.ignore()
                return
            if choice == QMessageBox.StandardButton.Save and not self.save_changes():
                if self.glossary is not None:
                    self.glossary.cancel_close()
                event.ignore()
                return
        event.accept()
        self.workspace_status.dispose()
        self.sidebar.search_timer.stop()
        self.invalidate_global_search()
        if self.ui_state is not None:
            self.ui_state.save()
        inspector = getattr(self, "token_inspector", None)
        if inspector is not None:
            inspector.dispose()
        diagnostics_popover = getattr(self, "row_diagnostics_popover", None)
        if diagnostics_popover is not None:
            diagnostics_popover.dispose()
        self.closed.emit()

    def get_selected_proxy_indexes(self):
        indexes = (
            self.list_view
            .selectionModel()
            .selectedIndexes()
        )

        indexes.sort(
            key=lambda index: index.row()
        )

        return indexes

    def open_secondary_preview(
        self,
        source_row,
    ):
        if (
            source_row < 0
            or source_row >= len(self.rows)
        ):
            return

        row = self.rows[source_row]

        file_path = (
            row["new_file"]
            or row["old_file"]
        )

        if file_path is None:
            file_paths = None
        else:
            file_paths = [
                str(file_path)
            ]

        normalized_files = (
            self.normalize_tab_files(
                file_paths
            )
        )

        preview_index = None

        # Ищем единственную временную
        # вкладку глобального поиска.
        for index in range(
            self.secondary_tab_bar.count()
        ):
            data = (
                self.secondary_tab_bar.tabData(
                    index
                )
            )

            if (
                data
                and data.get("kind")
                == "preview"
            ):
                preview_index = index
                break

        title = self.translation_target_path(file_path).name if file_path is not None else QCoreApplication.translate("gui", "Без файла")

        # -------------------------
        # Preview ещё не существует
        # -------------------------

        if preview_index is None:
            self.add_secondary_tab(
                title,
                file_paths,
                source_row=source_row,
                kind="preview",
                source="mod",
            )

            return

        # -------------------------
        # Preview уже существует
        # -------------------------

        # Сначала сохраняем старое состояние,
        # пока мы ещё НЕ записали новый source_row.
        if (
            preview_index
            == self.secondary_current_tab_index
        ):
            self.save_secondary_tab_state()

        new_data = {
            "files": normalized_files,
            "scroll": None,
            "source_row": source_row,
            "kind": "preview",
            "source": "mod",
        }

        self.secondary_tab_bar.setTabText(
            preview_index,
            title,
        )

        self.secondary_tab_bar.setTabData(
            preview_index,
            new_data,
        )

        self.show_secondary_panel()

        # Если вкладка была другой —
        # переключение само вызовет
        # secondary_tab_changed().
        if (
            self.secondary_tab_bar.currentIndex()
            != preview_index
        ):
            self.secondary_tab_bar.setCurrentIndex(
                preview_index
            )

        # Если Preview уже была активной,
        # currentChanged не сработает,
        # поэтому применяем новое состояние вручную.
        else:
            self.apply_secondary_tab(
                preview_index
            )

    def open_secondary_vanilla_preview(
        self,
        source_row,
    ):
        self.ensure_vanilla()
        if (
            source_row < 0
            or source_row >= len(self.vanilla_rows)
        ):
            return

        row = self.vanilla_rows[source_row]

        file_path = row["new_file"]

        if file_path is None:
            file_paths = None
        else:
            file_paths = [
                str(file_path)
            ]

        normalized_files = (
            self.normalize_tab_files(
                file_paths
            )
        )

        preview_index = None

        # Используем ту же единственную
        # временную Preview-вкладку.
        for index in range(
            self.secondary_tab_bar.count()
        ):
            data = (
                self.secondary_tab_bar.tabData(
                    index
                )
            )

            if (
                data
                and data.get("kind")
                == "preview"
            ):
                preview_index = index
                break

        title = Path(file_path).name if file_path is not None else "Vanilla"

        if preview_index is None:
            self.add_secondary_tab(
                title,
                file_paths,
                source_row=source_row,
                kind="preview",
                source="stellaris",
            )
            return

        if (
            preview_index
            == self.secondary_current_tab_index
        ):
            self.save_secondary_tab_state()

        new_data = {
            "files": normalized_files,
            "scroll": None,
            "source_row": source_row,
            "kind": "preview",
            "source": "stellaris",
        }

        self.secondary_tab_bar.setTabText(
            preview_index,
            title,
        )

        self.secondary_tab_bar.setTabData(
            preview_index,
            new_data,
        )

        self.show_secondary_panel()

        if (
            self.secondary_tab_bar.currentIndex()
            != preview_index
        ):
            self.secondary_tab_bar.setCurrentIndex(
                preview_index
            )

        else:
            self.apply_secondary_tab(
                preview_index
            )

    def workspace_edit_action(self, command, fallback):
        if self.glossary is not None and (self.tab_bar.tabData(self.tab_bar.currentIndex()) or {}).get("kind") == "glossary":
            getattr(self.glossary.page().current_editor(), command)()
        else:
            fallback()

    def open_glossary_search_result(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("source") == "glossary":
            self.open_global_search_result(item)

    def open_global_search_result(
        self,
        item,
    ):
        data = item.data(
            Qt.ItemDataRole.UserRole
        )

        self.open_global_search_result_data(data)

    def open_global_search_result_in_editor(self, data):
        if isinstance(data, dict) and data.get("source") == "mod" and data.get("entry_id"):
            self.open_workspace_entry(data["entry_id"])

    def open_global_search_result_data(self, data):
        if not isinstance(
            data,
            dict,
        ):
            return

        source = data.get(
            "source"
        )
        if source == "glossary" and self.glossary is not None:
            self.open_glossary_tab()
            self.glossary.page().open_match(data)
            return

        index = data.get(
            "index"
        )

        if index is None:
            return

        if source == "mod":
            self.open_secondary_preview(
                index
            )

        elif source == "stellaris":
            self.open_secondary_vanilla_preview(
                index
            )

    def toggle_global_search(self):
        query = self.selected_translation_text()
        self.sidebar.show_page("search")
        self.sidebar.set_replace_expanded(False)
        if query is not None and query != self.global_search_box.text():
            self.global_search_box.setText(query)
        self.global_search_box.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.global_search_box.selectAll()

    def invalidate_search_reuse(self, *args):
        self._search_data_version += 1
        self._search_signatures.clear()

    def prepare_global_replace(self, scope, **target):
        """Prepare from the current sidebar fields; no translation writes."""
        self.finish_project_editing()
        return self.global_replacement.prepare(
            self.sidebar.query_box.text(), self.sidebar.replacement_box.text(), scope,
            case_sensitive=self.sidebar.case_sensitive.isChecked(), **target)

    def replace_global_search_target(self, target):
        if not self.sidebar.replace_expanded:
            return
        scope = target["scope"]
        arguments = {}
        if scope in ("ENTRY", "OCCURRENCE"):
            arguments["entry_id"] = target["entry_id"]
        elif scope == "FILE":
            arguments["file"] = target["file"]
        try:
            if scope == "OCCURRENCE":
                self.finish_project_editing()
                query, _ = prepare_query(self.sidebar.query_box.text(), "ru",
                                        case_sensitive=self.sidebar.case_sensitive.isChecked())
                number = self.entry_rows.get(target["entry_id"])
                row = self.model.rows[number] if number is not None else None
                if (row is None or row.get("entry_id") != target["entry_id"]
                        or row.get("ru_text") != target["ru_snapshot"]
                        or query != target["query"]
                        or self.sidebar.case_sensitive.isChecked() != target["case_sensitive"]):
                    raise ValueError(QCoreApplication.translate("gui", "Совпадение изменилось. Повторите поиск."))
                arguments["occurrence"] = target["occurrence"]
            plan = self.prepare_global_replace(scope, **arguments)
            if not plan.changes:
                self.sidebar.search_status.setText(QCoreApplication.translate("gui", "Нет изменений: совпадений нет или замена не меняет текст."))
                return
            if scope in ("FILE", "ALL") and not self.confirm_global_replace(plan):
                return
            self.global_replacement.apply(plan)
        except (ValueError, TypeError) as error:
            QMessageBox.warning(self, QCoreApplication.translate("gui", "Замена не выполнена"), str(error))

    def confirm_global_replace(self, plan):
        counts = plan.summary
        details = (QCoreApplication.translate("gui", "Заменить совпадения: {0}\nЗаписей: {1}\n").format(counts['occurrences'], counts['entries']))
        if plan.scope == "FILE":
            details += QCoreApplication.translate("gui", "Файл: {0}").format(plan.changes[0].file)
        else:
            details += QCoreApplication.translate("gui", "Файлов: {0}").format(counts['files'])
        # Quoted literals preserve visible leading/trailing spaces; control
        # characters are escaped for display only, never for replacement.
        query = json.dumps(self.sidebar.query_box.text(), ensure_ascii=False)
        replacement = json.dumps(self.sidebar.replacement_box.text(), ensure_ascii=False)
        dialog = QMessageBox(self)
        dialog.setWindowTitle(QCoreApplication.translate("gui", "Подтвердить замену"))
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"{details}\n\n{query} → {replacement}")
        apply_button = dialog.addButton(QCoreApplication.translate("gui", "Заменить"), QMessageBox.ButtonRole.AcceptRole)
        cancel_button = dialog.addButton(QCoreApplication.translate("gui", "Отмена"), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel_button)
        dialog.exec()
        return dialog.clickedButton() == apply_button

    def refresh_after_global_replace(self):
        # dataChanged already invalidated reuse; request_search handles the list.
        self.sidebar.request_search()  # One refresh after the complete command, not per row.

    def search_signature(self, text=None, mode=None, scope=None, replacing=None):
        s = self.sidebar
        replacing = s.replace_expanded if replacing is None else replacing
        text = s.query_box.text() if text is None else text
        mode = mode or s.query_mode.currentData()
        scope = scope or s.query_scope.currentData()
        if replacing:
            mode, scope = "ru", "mod"
        sensitive = s.case_sensitive.isChecked()
        query, mode = prepare_query(text, mode, case_sensitive=sensitive)
        page = getattr(self.glossary, "_page", None)
        documents = tuple(editor.document().revision() for editor in page.editors.values()) if page else ()
        return (query, mode, scope, sensitive, replacing, self._search_data_version, documents,
                id(self.vanilla_rows) if scope in ("vanilla", "both") else None)

    def search_conditions_changed(self):
        if self.search_signature() not in self._search_signatures:
            self.invalidate_global_search()

    def invalidate_global_search(self):
        self._search_signatures.clear()
        self._search_generation += 1
        self._search_work_timer.stop()
        self._pending_search = None
        self.global_search_results.clear()
        self.sidebar.search_status.clear()

    def perform_global_search(self, text, search_mode="auto", scope="mod"):
        self.sidebar.search_timer.stop()
        signature = self.search_signature(text, search_mode, scope)
        if signature in self._search_signatures:
            return  # Keep the actual items, selection and scroll position intact.
        self.invalidate_global_search()
        query, mode, scope, sensitive, replacing = signature[:5]
        if not query:
            return
        load = scope in ("vanilla", "both") and not self._vanilla_loaded
        self._pending_search = (self._search_generation, text, query, mode, scope, load, replacing, sensitive)
        self.sidebar.search_status.setText(QCoreApplication.translate("gui", "Загрузка Vanilla…") if load else QCoreApplication.translate("gui", "Поиск…"))
        # Yield normally to Qt; no nested processEvents or synchronous repaint.
        self._search_work_timer.start(0)

    def _run_global_search(self):
        request = self._pending_search
        if request is None:
            return
        generation, text, query, mode, scope, load, replacing, sensitive = request
        if generation != self._search_generation:
            return
        if load:
            self.ensure_vanilla()
            if generation != self._search_generation:
                return
            if not self._vanilla_loaded and scope == "vanilla":
                self.sidebar.search_status.setText(QCoreApplication.translate("gui", "Vanilla недоступна. Проверьте папку игры и повторите поиск."))
                self._pending_search = None
                return
            self._pending_search = (generation, text, query, mode, scope, False, replacing, sensitive)
            self.sidebar.search_status.setText(QCoreApplication.translate("gui", "Поиск…"))
            self._search_work_timer.start(0)
            return

        sources = []
        if scope in ("mod", "both"):
            sources.append(("mod", self.rows))
        if scope in ("vanilla", "both") and self._vanilla_loaded:
            sources.append(("stellaris", self.vanilla_rows))
        matches = []
        for match in localization_matches(sources, query, mode, working_ru_only=replacing, case_sensitive=sensitive):
            matches.append(match)
            if len(matches) > RESULT_LIMIT:
                break
        glossary_matches = []
        if not replacing and scope in ("mod", "both") and len(matches) <= RESULT_LIMIT and self.glossary is not None:
            glossary_matches = self.glossary.page().search_text(
                text, limit=RESULT_LIMIT + 1 - len(matches), case_sensitive=sensitive)
        if generation != self._search_generation:
            return
        truncated = len(matches) + len(glossary_matches) > RESULT_LIMIT
        signature = self.search_signature(text, mode, scope, replacing)
        if scope == "mod" or self._vanilla_loaded:
            self._search_signatures.add(signature)
        # A normal RU/mod result also proves Replace equivalence when every
        # collected row (including the 151st sentinel) is eligible and there are
        # no Glossary hits. No extra scan is needed, even for truncated results.
        if not replacing and mode == "ru" and scope == "mod" and not glossary_matches and all(
                is_working_ru(row) for _, _, row in matches):
            self._search_signatures.add(signature[:4] + (True,) + signature[5:])
        matches = matches[:RESULT_LIMIT]
        glossary_matches = glossary_matches[:RESULT_LIMIT - len(matches)]
        self._pending_search = None

        self.global_search_results.populate(matches, glossary_matches, query, mode, case_sensitive=sensitive,
                                            mod_display_path=self.translation_target_path)
        count = len(matches) + len(glossary_matches)
        status = (QCoreApplication.translate("gui", "{0}+ результатов · показаны первые {1}").format(RESULT_LIMIT, RESULT_LIMIT) if truncated
                  else QCoreApplication.translate("gui", "Результатов: {0}").format(count))
        if scope == "both" and not self._vanilla_loaded:
            status += QCoreApplication.translate("gui", " · Vanilla недоступна")
        self.sidebar.search_status.setText(status)
        if not count:
            item = QListWidgetItem(QCoreApplication.translate("gui", "Ничего не найдено."))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.global_search_results.addItem(item)

    def copy_selected_text(self):
        self.copy_entries(self.active_localization_view(), "text")

    def copy_selected_with_keys(self):
        self.copy_entries(self.active_localization_view(), "with_key")

    def copy_current_key(self):
        self.copy_entries(self.active_localization_view(), "key")


    def normalize_tab_files(
        self,
        file_paths,
    ):
        if file_paths is None:
            return None

        return tuple(
            sorted(
                str(path)
                for path in file_paths
            )
        )


    def open_glossary_tab(self):
        if self.glossary is None:
            return
        for i in range(self.tab_bar.count()):
            if (self.tab_bar.tabData(i) or {}).get("kind") == "glossary":
                self.tab_bar.setCurrentIndex(i)
                return
        with QSignalBlocker(self.tab_bar):
            i = self.tab_bar.insertTab(0, QCoreApplication.translate("gui", "Глоссарий"))
            self.tab_bar.setTabData(i, {"kind": "glossary", "files": None, "pinned": True})
            if self.current_tab_index >= 0:
                self.current_tab_index += 1
        self.tab_bar.setCurrentIndex(i)
        if self.current_tab_index != i:
            self.workspace_tab_changed(i)

    def add_workspace_tab(
        self,
        title,
        file_paths,
        *,
        initial_position=None,
    ):
        normalized_files = (
            self.normalize_tab_files(
                file_paths
            )
        )

        tab_data = {
            "files": normalized_files,
            "scroll": 0,
            "source_row": None,
        }
        if initial_position is not None:
            tab_data["scroll"] = initial_position["scroll"]
            tab_data["position"] = deepcopy(initial_position.get("position"))

        index = self.tab_bar.addTab(
            title
        )

        self.tab_bar.setTabData(
            index,
            tab_data,
        )

        self.tab_bar.setCurrentIndex(
            index
        )

        # The first addTab emits currentChanged before its data is installed.
        if self.current_tab_index != index and self.tab_bar.currentIndex() == index:
            self.workspace_tab_changed(index)

        return index


    def open_workspace_tab(
        self,
        title,
        file_paths,
        *,
        initial_position=None,
    ):
        normalized_files = (
            self.normalize_tab_files(
                file_paths
            )
        )

        # Проверяем, не открыта ли
        # такая вкладка уже.
        for index in range(
            self.tab_bar.count()
        ):
            data = self.tab_bar.tabData(
                index
            )

            if not data:
                continue

            if (
                data.get("kind", "normal") == "normal" and data.get("files")
                == normalized_files
            ):
                self.tab_bar.setCurrentIndex(
                    index
                )
                return

        self.add_workspace_tab(
            title,
            normalized_files,
            initial_position=initial_position,
        )


    def open_workspace_entry(self, entry_id):
        """Open an exact project entry through ordinary file tabs/position restore."""
        number = self.entry_rows.get(entry_id)
        if number is None:
            return
        row = self.model.rows[number]
        file = row.get("new_file") or row.get("old_file")
        if file is None:
            return
        self.open_workspace_tab(self.translation_target_path(file).name, [file])
        index = self.tab_bar.currentIndex()
        data = self.tab_bar.tabData(index) or {}
        if data.get("kind", "normal") != "normal" or data.get("files") != self.normalize_tab_files([file]):
            return  # A pending editor may have declined the tab switch.
        if not self.proxy_model.mapFromSource(self.model.index(number, 0)).isValid():
            # An explicit jump must reveal its target even under a status filter.
            for button in self.button_group.buttons():
                if button.property("filter_mode") == "ALL":
                    button.click()
                    break
        data["position"] = {"current_entry_id": entry_id, "top_entry_id": entry_id, "top_offset": 0}
        self.tab_bar.setTabData(index, data)
        self.restore_saved_position(self.list_view, self.tab_bar, index, data)

    def save_current_tab_state(
        self,
    ):
        index = self.current_tab_index

        if (
            index < 0
            or index >= self.tab_bar.count()
        ):
            return

        data = self.tab_bar.tabData(
            index
        )

        if data is None:
            return

        if data.get("kind") == "glossary":
            return
        data["position"] = capture_position(self.list_view)

        data["scroll"] = (
            self.list_view
            .verticalScrollBar()
            .value()
        )

        current_proxy_index = (
            self.list_view.currentIndex()
        )

        if current_proxy_index.isValid():
            source_index = (
                self.proxy_model
                .mapToSource(
                    current_proxy_index
                )
            )

            data["source_row"] = (
                source_index.row()
            )

        self.tab_bar.setTabData(
            index,
            data,
        )


    def workspace_tab_changed(
        self,
        index,
        *,
        restore_position=True,
    ):
        if index < 0:
            return

        previous = self.tab_bar.tabData(self.current_tab_index) if self.current_tab_index >= 0 else None
        if previous and previous.get("kind") == "glossary" and self.glossary is not None:
            if not self.glossary.can_leave():
                with QSignalBlocker(self.tab_bar):
                    self.tab_bar.setCurrentIndex(self.current_tab_index)
                return
        self.list_view.finish_editing()

        # Сохраняем позицию
        # предыдущей вкладки.
        self.save_current_tab_state()

        data = self.tab_bar.tabData(
            index
        )

        if data is None:
            return

        self.current_tab_index = index
        if data.get("kind") == "glossary" and self.glossary is not None:
            self.local_find.close_find()
            self.editor_stack.setCurrentWidget(self.glossary.page())
            return
        position = data.get("position")
        if (restore_position and not self.list_view.isVisible()
                and isinstance(position, dict) and data.get("source", "mod") == "mod"
                and (position.get("current_entry_id") or position.get("top_entry_id"))):
            # Show supplies the final Qt geometry, but intermediate old-filter,
            # file-start and current-selection viewports need no text layouts.
            # Restore synchronously inside this scope, not in a later UI cycle.
            with self.list_view.defer_materialization():
                self.editor_stack.setCurrentWidget(self.localization_page)
                files = data.get("files")
                self.proxy_model.set_file_filter(set(files) if files is not None else None)
                self.restore_saved_position(self.list_view, self.tab_bar, index, data, immediate=True)
            return
        self.editor_stack.setCurrentWidget(self.localization_page)


        file_paths = data.get(
            "files"
        )

        if file_paths is None:
            self.proxy_model.set_file_filter(
                None
            )
        else:
            self.proxy_model.set_file_filter(
                set(file_paths)
            )

        if not restore_position:
            return
        if self.restore_saved_position(self.list_view, self.tab_bar, index, data):
            return

        # После фильтрации возвращаем
        # позицию выбранной строки.
        source_row = data.get(
            "source_row"
        )

        if source_row is not None:
            source_index = (
                self.model.index(
                    source_row,
                    0,
                )
            )

            proxy_index = (
                self.proxy_model
                .mapFromSource(
                    source_index
                )
            )

            if proxy_index.isValid():
                self.list_view.setCurrentIndex(
                    proxy_index
                )

        scroll_value = data.get(
            "scroll",
            0,
        )

        view = self.list_view
        QTimer.singleShot(
            0,
            view,
            lambda value=scroll_value:
            view
            .verticalScrollBar()
            .setValue(value),
        )


    def close_workspace_tab(
        self,
        index,
    ):
        if not 0 <= index < self.tab_bar.count():
            return
        data = self.tab_bar.tabData(index) or {}
        if data.get("kind") == "glossary" or data.get("pinned"):
            return

        self.save_current_tab_state()
        # Read again after capturing the active tab's latest pixel position.
        data = self.tab_bar.tabData(index) or {}
        files = data.get("files")
        if (data.get("kind", "normal") == "normal" and data.get("source", "mod") == "mod"
                and files is not None and len(files) == 1):
            self.closed_workspace_tabs.append({
                "files": tuple(files), "scroll": data.get("scroll", 0),
                "position": deepcopy(data.get("position")),
            })
        if index == self.current_tab_index:
            self.current_tab_index = -1
        elif index < self.current_tab_index:
            self.current_tab_index -= 1

        self.tab_bar.removeTab(
            index
        )

        if self.tab_bar.count() == 0:
            self.open_glossary_tab()

    def reopen_closed_workspace_tab(self):
        skipped = False
        while self.closed_workspace_tabs:
            snapshot = self.closed_workspace_tabs.pop()
            file = snapshot["files"][0]
            # The existing file map includes empty structural documents too.
            # Check current source identity, never the existence of exported RU.
            if str(Path(file)) not in self.explorer_files.items:
                skipped = True
                continue
            self.open_workspace_tab(self.translation_target_path(file).name, snapshot["files"],
                                    initial_position=snapshot)
            break
        if skipped:
            self.statusBar().showMessage(QCoreApplication.translate("gui", "Пропущены закрытые вкладки файлов, которых больше нет в источнике."), 5000)

    def toggle_secondary_panel(self):
        if not self.secondary_widget.isHidden():
            if self.secondary_list_view is not None:
                self.secondary_list_view.finish_editing()
            self.save_secondary_tab_state()
            self.secondary_widget.hide()
        else:
            self.open_current_tab_secondary()
        self.open_right_button.setChecked(not self.secondary_widget.isHidden())

    def show_secondary_panel(self):
        if (self.secondary_tab_bar.tabData(self.secondary_tab_bar.currentIndex()) or {}).get("kind") != "glossary":
            self.ensure_secondary_view()
        self.secondary_widget.show()

        self.workspace_splitter.setSizes(
            [650, 250]
        )


    def add_secondary_tab(
        self,
        title,
        file_paths,
        source_row=None,
        kind="normal",
        source="mod",
    ):
        normalized_files = (
            self.normalize_tab_files(
                file_paths
            )
        )

        tab_data = {
            "files": normalized_files,
            "scroll": None,
            "source_row": source_row,
            "kind": kind,
            "source": source,
        }

        index = (
            self.secondary_tab_bar.addTab(
                title
            )
        )

        self.secondary_tab_bar.setTabData(
            index,
            tab_data,
        )

        self.show_secondary_panel()

        if self.secondary_tab_bar.currentIndex() != index:
            self.secondary_tab_bar.setCurrentIndex(index)
        else:
            # The first addTab emits currentChanged before tabData exists.
            # Apply once now, after the lazy view and complete tab data exist.
            self.secondary_tab_changed(index)

        return index

    def open_secondary_tab(
        self,
        title,
        file_paths,
    ):
        normalized_files = (
            self.normalize_tab_files(
                file_paths
            )
        )

        # Не создаём дубликат
        # обычной вкладки.

        for index in range(
            self.secondary_tab_bar.count()
        ):
            data = (
                self.secondary_tab_bar.tabData(
                    index
                )
            )

            if not data:
                continue

            if (
                data.get("kind") == "normal"
                and data.get("files")
                == normalized_files
            ):
                self.show_secondary_panel()

                self.secondary_tab_bar.setCurrentIndex(
                    index
                )

                # A restored but hidden panel has tab metadata, not an applied
                # view. Selecting its already-current tab emits no Qt signal.
                if self.secondary_current_tab_index != index:
                    self.secondary_tab_changed(index)

                return

        self.add_secondary_tab(
            title,
            normalized_files,
            kind="normal",
        )


    def open_current_tab_secondary(self):
        index = self.tab_bar.currentIndex()

        if index < 0:
            return

        data = self.tab_bar.tabData(
            index
        )

        if data is None:
            return

        title = self.tab_bar.tabText(
            index
        )

        if data.get("kind") == "glossary" and self.glossary is not None:
            self.glossary.open_context()
            return
        self.open_secondary_tab(
            title,
            data.get("files"),
        )


    def save_secondary_tab_state(
        self,
    ):
        # Unmaterialized saved tabs must retain their stored position unchanged.
        if self.secondary_list_view is None:
            return
        index = (
            self.secondary_current_tab_index
        )

        if (
            index < 0
            or index >= self.secondary_tab_bar.count()
        ):
            return

        data = (
            self.secondary_tab_bar.tabData(
                index
            )
        )

        if data is None:
            return

        if data.get("kind") == "glossary":
            return
        data["position"] = capture_position(self.secondary_list_view)

        source = data.get(
            "source",
            "mod",
        )

        if source == "stellaris":
            proxy_model = self.vanilla_proxy_model
        else:
            proxy_model = self.secondary_proxy_model

        data["scroll"] = (
            self.secondary_list_view
            .verticalScrollBar()
            .value()
        )

        current_index = (
            self.secondary_list_view.currentIndex()
        )

        if (
            current_index.isValid()
            and self.secondary_list_view.model()
            is proxy_model
        ):
            source_index = (
                proxy_model.mapToSource(
                    current_index
                )
            )

            data["source_row"] = (
                source_index.row()
            )

        self.secondary_tab_bar.setTabData(
            index,
            data,
        )

    def apply_secondary_tab(
        self,
        index,
        *,
        restore_position=True,
    ):
        if (
            index < 0
            or index >= self.secondary_tab_bar.count()
        ):
            return

        data = (
            self.secondary_tab_bar.tabData(
                index
            )
        )

        if data is None:
            return

        if data.get("kind") == "glossary" and self.glossary is not None:
            if self.secondary_list_view is not None:
                self.secondary_list_view.finish_editing()
                self.secondary_list_view.hide()
            self.secondary_current_tab_index = index
            self.pin_preview_button.hide()
            self.glossary.show_context()
            return
        if self.glossary is not None and self.glossary.context is not None:
            self.glossary.context.hide()
        self.ensure_secondary_view()
        self.secondary_list_view.show()
        self.secondary_list_view.finish_editing()
        self.secondary_current_tab_index = index

        source = data.get(
            "source",
            "mod",
        )

        # Выбираем источник данных.
        if source == "stellaris":
            source_model = self.vanilla_model
            self.ensure_vanilla()
            proxy_model = self.vanilla_proxy_model
        else:
            source_model = self.model
            proxy_model = self.secondary_proxy_model

        # Нижний QListView может переключаться
        # между модом и Stellaris.
        if (
            self.secondary_list_view.model()
            is not proxy_model
        ):
            self.secondary_list_view.setModel(
                proxy_model
            )

        file_paths = data.get(
            "files"
        )

        if file_paths is None:
            proxy_model.set_file_filter(
                None
            )
        else:
            proxy_model.set_file_filter(
                set(file_paths)
            )

        # Кнопка закрепления показывается
        # только для временного Preview.
        if hasattr(
            self,
            "pin_preview_button",
        ):
            self.pin_preview_button.setVisible(
                data.get("kind")
                == "preview"
            )

        if not restore_position:
            return
        if self.restore_saved_position(self.secondary_list_view, self.secondary_tab_bar, index, data):
            return

        source_row = data.get(
            "source_row"
        )

        if source_row is None:
            return

        source_index = source_model.index(
            source_row,
            0,
        )

        proxy_index = (
            proxy_model.mapFromSource(
                source_index
            )
        )

        if not proxy_index.isValid():
            return

        self.secondary_list_view.setCurrentIndex(
            proxy_index
        )

        scroll_value = data.get(
            "scroll"
        )

        view = self.secondary_list_view
        if scroll_value is None:
            target = QPersistentModelIndex(proxy_index)

            def center():
                if (not target.isValid() or target.model() is not view.model()
                        or target != view.currentIndex()):
                    return
                view.scrollTo(
                    QModelIndex(target),
                    QAbstractItemView.ScrollHint.PositionAtCenter,
                )

            QTimer.singleShot(0, view, center)

        else:
            QTimer.singleShot(
                0,
                view,
                lambda value=scroll_value:
                view
                .verticalScrollBar()
                .setValue(value),
            )

    def secondary_tab_changed(
        self,
        index,
    ):
        if index < 0:
            return

        # Сохраняем состояние предыдущей вкладки.
        self.save_secondary_tab_state()

        self.apply_secondary_tab(
            index
        )

    def close_secondary_tab(
        self,
        index,
    ):
        self.save_secondary_tab_state()
        if index == self.secondary_current_tab_index:
            self.secondary_current_tab_index = -1
        elif index < self.secondary_current_tab_index:
            self.secondary_current_tab_index -= 1

        self.secondary_tab_bar.removeTab(
            index
        )

        if (
            self.secondary_tab_bar.count()
            == 0
        ):
            self.secondary_current_tab_index = -1

            self.secondary_widget.hide()

    def pin_secondary_preview(self):
        index = (
            self.secondary_tab_bar
            .currentIndex()
        )

        if index < 0:
            return

        data = (
            self.secondary_tab_bar
            .tabData(index)
        )

        if not data:
            return

        if data.get("kind") != "preview":
            return

        source_row = data.get(
            "source_row"
        )

        if source_row is None:
            return

        if (
            source_row < 0
            or source_row >= len(self.rows)
        ):
            return

        # После закрепления эта вкладка
        # перестаёт считаться временной.
        data["kind"] = "normal"

        self.secondary_tab_bar.setTabData(
            index,
            data,
        )

        # Keep the file display title when promoting a preview to a normal tab.

        self.pin_preview_button.hide()

    def get_selected_proxy_indexes(self):
        indexes = (
            self.list_view
            .selectionModel()
            .selectedIndexes()
        )

        indexes.sort(
            key=lambda index: index.row()
        )

        return indexes


    def copy_selected_text(self):
        self.copy_entries(self.active_localization_view(), "text")

    def copy_selected_with_keys(self):
        self.copy_entries(self.active_localization_view(), "with_key")

    def copy_current_key(self):
        self.copy_entries(self.active_localization_view(), "key")

    def paste_selected_text(self):
        proxy_indexes = (
            self.get_selected_proxy_indexes()
        )

        if not proxy_indexes:
            return

        clipboard_text = (
            QApplication
            .clipboard()
            .text()
        )

        clipboard_text = (
            clipboard_text
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )

        lines = clipboard_text.split("\n")

        # Пустые строки между переводами
        # не считаем отдельными переводами.
        lines = [
            line
            for line in lines
            if line.strip() != ""
        ]

        selected_count = len(
            proxy_indexes
        )

        pasted_count = len(
            lines
        )

        if selected_count != pasted_count:
            QMessageBox.warning(
                self,
                QCoreApplication.translate("gui", "Количество строк не совпадает"),
                (
                    QCoreApplication.translate("gui", "Вставка отменена.\n\nВыбрано записей: {0}\nСтрок в буфере: {1}").format(selected_count, pasted_count)
                ),
            )

            return

        # Freeze the visual order and identities before the first model write.
        targets = []
        for proxy_index, text in zip(proxy_indexes, lines):
            source = self.entry_source(proxy_index)
            if not source.isValid() or source.model() is not self.model:
                return
            row = self.model.rows[source.row()]
            targets.append((source.row(), self.model.translation_identity(row), text))
        try:
            self.model.paste_translations(targets)
        except ValueError as error:
            QMessageBox.warning(self, QCoreApplication.translate("gui", "Вставка отменена"), str(error))
            return

        self.statusBar().showMessage(
            QCoreApplication.translate("gui", "Вставлено переводов: {0}").format(pasted_count),
            4000,
        )

    def build_global_search_indexes(
        self,
    ):
        self.mod_search_index.clear()
        self.vanilla_search_index.clear()

        self.mod_exact_keys.clear()
        self.vanilla_exact_keys.clear()

        # -------------------------
        # Текущий мод
        # -------------------------

        for source_row, row in enumerate(
            self.rows
        ):
            key = row["key"] or ""

            new_text = (
                row["new_text"] or ""
            )

            old_text = (
                row["old_text"] or ""
            )

            ru_text = (
                row["ru_text"] or ""
            )

            file_path = (
                row["new_file"]
                or row["old_file"]
                or ""
            )

            key_lower = key.lower()

            searchable = " ".join(
                (
                    key,
                    new_text,
                    old_text,
                    ru_text,
                    str(file_path),
                )
            ).lower()

            self.mod_search_index.append(
                (
                    source_row,
                    key_lower,
                    searchable,
                )
            )

            self.mod_exact_keys.setdefault(
                key_lower,
                [],
            ).append(
                source_row
            )

        # -------------------------
        # Vanilla Stellaris
        # -------------------------

        for source_row, row in enumerate(
            self.vanilla_rows
        ):
            key = row["key"] or ""

            en_text = (
                row["new_text"] or ""
            )

            ru_text = (
                row["ru_text"] or ""
            )

            file_path = (
                row["new_file"]
                or ""
            )

            key_lower = key.lower()

            searchable = " ".join(
                (
                    key,
                    en_text,
                    ru_text,
                    str(file_path),
                )
            ).lower()

            self.vanilla_search_index.append(
                (
                    source_row,
                    key_lower,
                    searchable,
                )
            )

            self.vanilla_exact_keys.setdefault(
                key_lower,
                [],
            ).append(
                source_row
            )



def run_gui(
    rows,
    save_callback=None,
):
    from .app_settings import AppSettings
    from .i18n import initialize_ui_language

    app = QApplication.instance()

    if app is None:
        app = QApplication([])

    initialize_ui_language(app, AppSettings().ui_language)
    window = MainWindow(
        rows,
        save_callback=save_callback,
    )

    window.show()

    return app.exec()
