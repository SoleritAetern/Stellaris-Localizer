"""Compact presentation only; search and navigation remain owned by MainWindow."""

from PySide6.QtCore import QCoreApplication

from collections import OrderedDict
import html
import re

from PySide6.QtCore import QEvent, QPointF, QSize, QRect, QPersistentModelIndex, Signal, Qt
from PySide6.QtGui import QCursor, QFont, QPainter, QPainterPath, QPen, QTextCharFormat, QTextLayout
from PySide6.QtWidgets import QApplication, QAbstractItemView, QListWidget, QListWidgetItem, QMenu, QStyledItemDelegate, QStyle, QToolTip

from . import theme
from .global_search import is_working_ru, match_spans
from .paths import english_to_russian_path
from .syntax_highlighting import syntax_ranges


PRESENTATION_ROLE = int(Qt.ItemDataRole.UserRole) + 1
REPLACE_ROLE = PRESENTATION_ROLE + 1


def compact_text(text):
    return " ".join((text or "").replace(r"\n", " ").split())


def single_line(text):
    # One source character -> one display character: offsets remain exact.
    # Spaces, tabs and literal backslash-n are never stripped or collapsed.
    return text.translate({ord(char): " " for char in "\r\n\u2028\u2029"})


def snippet_part(text, query="", primary=False, *, case_sensitive=False):
    text = text or ""
    spans = match_spans(text, query, case_sensitive=case_sensitive)
    if not spans:
        start = 0
        words = list(re.finditer(r"\S+", text[:240]))
        end = min(words[9].end() if len(words) > 9 else len(text), 180)
    else:
        position, end_match = spans[0]
        before = list(re.finditer(r"\S+", text[max(0, position - 240):position]))
        start = max(0, position - 240) + before[-5].start() if len(before) >= 5 else max(0, position - 240)
        after = list(re.finditer(r"\S+", text[end_match:end_match + 240]))
        end = end_match + after[4].end() if len(after) >= 5 else min(len(text), end_match + 240)
        start = max(start, position - 90)
        end = min(end, end_match + 100)
        # Never display only a part of a matched substring at the snippet edge.
        for a, b in spans:
            if a < end < b:
                end = b
    prefix = "…" if start else ""
    display = prefix + single_line(text[start:end]) + ("…" if end < len(text) else "")
    visible_spans = [(a - start + len(prefix), b - start + len(prefix))
                     for a, b in spans if a >= start and b <= end]
    return display, visible_spans, primary


def snippet(text, query="", *, case_sensitive=False):
    return snippet_part(text, query, case_sensitive=case_sensitive)[0]


def prepared_part(text, query="", primary=False, *, case_sensitive=False):
    matches = match_spans(text, query, case_sensitive=case_sensitive)
    return single_line(text), matches, primary


def replacement_part(part, replacement):
    """Build display-only runs from existing snippet matches, never rematch RU."""
    text, matches, primary = part
    chunks, styles, anchors = [], [], []
    previous = length = 0
    for start, end in matches:
        chunks.append(text[previous:start])
        length += start - previous
        anchor_start = length
        chunks.append(text[start:end])
        styles.append((length, length + end - start, "removed"))
        length += end - start
        if replacement:
            chunks.append(replacement)
            styles.append((length, length + len(replacement), "added"))
            length += len(replacement)
        anchors.append((anchor_start, length))
        previous = end
    chunks.append(text[previous:])
    return "".join(chunks), anchors, primary, styles


def fitted_text(text, matches, metrics, width, *, with_offset=False):
    """Keep a match in view even when the available snippet width is very small."""
    position = matches[0][0] if matches else -1
    offset = 0
    if position > 0 and metrics.horizontalAdvance(text[:matches[0][1]]) > width:
        # Trim left context before eliding the right edge, preserving the match.
        start = position
        budget = max(0, width // 4)
        while start > 0 and metrics.horizontalAdvance(text[start - 1:position]) < budget:
            start -= 1
        text = ("…" if start else "") + text[start:]
        offset = start - (1 if start else 0)
    display = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(width)))
    visible = len(display) - 1 if display != text and display.endswith("…") else len(display)
    ranges = []
    for start, end in matches:
        start, end = start - offset, end - offset
        if start < visible and end > 0:
            ranges.append((max(0, start), min(end, visible)))
    return (display, ranges, offset) if with_offset else (display, ranges)


def tooltip(key, en, ru, path):
    # Rich Qt tooltip supplies wrapping. Soft break opportunities also constrain
    # long unbroken keys/tokens without modifying any stored localization text.
    def escaped(value):
        text = str(value).replace("\r\n", "\n").replace("\r", "\n")
        return "<br>".join("".join(html.escape(line[i:i + 32]) + "&#8203;"
                                   for i in range(0, len(line), 32))
                           for line in text.split("\n"))

    def highlighted(value):
        text = str(value or "—")
        # Adapt the shared QTextCharFormats to HTML, preserving their overlap
        # precedence. No diff or search-match formats enter this presentation.
        events = {0: [], len(text): []}
        formats = syntax_ranges(text)
        for order, item in enumerate(formats):
            events.setdefault(item.start, []).append((order, True))
            events.setdefault(item.start + item.length, []).append((order, False))
        positions = sorted(events)
        active, parts = set(), []
        for start, end in zip(positions, positions[1:]):
            for order, entering in events[start]:
                if entering:
                    active.add(order)
                else:
                    active.discard(order)
            part = escaped(text[start:end])
            if active:
                foreground = formats[max(active)].format.foreground().color().name()
                part = f'<span style="color:{foreground};">{part}</span>'
            parts.append(part)
        return "".join(parts)

    logical_path = english_to_russian_path(path).as_posix() if path else "—"
    primary = theme.css("text.primary")
    secondary = theme.css("text.secondary")
    label = theme.css("row.label")
    return ('<qt><table width="460" cellspacing="0" cellpadding="0"><tr><td>'
            f'<p style="margin:0; color:{primary};"><b>{escaped(key or "—")}</b></p>'
            f'<p style="margin-top:4px; margin-bottom:12px; color:{secondary};"><small>{escaped(logical_path)}</small></p>'
            f'<p style="margin:0; color:{label};"><small>EN</small></p>'
            f'<p style="margin-top:4px; margin-bottom:0; white-space:pre-wrap; color:{theme.css("row.source")};">{highlighted(en)}</p>'
            f'<p style="margin-top:16px; margin-bottom:0; color:{label};"><small>RU</small></p>'
            f'<p style="margin-top:4px; margin-bottom:0; white-space:pre-wrap; color:{primary};">{highlighted(ru)}</p>'
            '</td></tr></table></qt>')


class SearchResultDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        data = index.data(PRESENTATION_ROLE) or {}
        kind = data.get("kind")
        height = max(22, option.fontMetrics.height() + (10 if kind == "source" else 6))
        return QSize(0, height + (6 if kind == "file" else 2 if kind == "result" else 0))

    def paint(self, painter, option, index):
        data = index.data(PRESENTATION_ROLE)
        if not data:
            return super().paint(painter, option, index)
        painter.save()
        painter.setClipRect(option.rect)
        kind = data["kind"]
        if kind == "result" and (option.state & QStyle.StateFlag.State_Selected
                                 or index == self.parent().currentIndex()):
            painter.fillRect(option.rect, theme.color('surface.selected'))
            painter.fillRect(QRect(option.rect.left(), option.rect.top(), 2, option.rect.height()),
                             theme.color('action.accent'))
        elif option.state & QStyle.StateFlag.State_MouseOver and kind == "result":
            painter.fillRect(option.rect, theme.color('surface.hover'))
        rect = option.rect.adjusted(8 if kind == "source" else 14 if kind == "file" else 28,
                                    6 if kind == "file" else 0, -6, 0)
        action_rect = self.parent().replace_action_rect(index, option.rect)
        if not action_rect.isEmpty():
            rect.setRight(action_rect.left() - 4)
        if kind != "result":
            font = QFont(option.font)
            font.setBold(kind == "source")
            source_header = kind == "source" and data["text"] in (QCoreApplication.translate("search_results", "МОД"), "VANILLA")
            if source_header:
                font.setWeight(QFont.Weight.DemiBold)
                if font.pixelSize() > 0:
                    font.setPixelSize(max(1, font.pixelSize() - 1))
                else:
                    font.setPointSizeF(max(1.0, font.pointSizeF() - 0.75))
            painter.setFont(font)
            painter.setPen(theme.color('text.primary' if kind == "source" and not source_header else 'text.secondary'))
            metrics = painter.fontMetrics()
            count = f' · {data["count"]}' if kind == "file" and "count" in data else ""
            filename_width = max(0, rect.width() - metrics.horizontalAdvance(count))
            title = metrics.elidedText(data["text"], Qt.TextElideMode.ElideRight, filename_width)
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, title)
            if count:
                count_rect = QRect(rect)
                count_rect.setLeft(rect.left() + metrics.horizontalAdvance(title))
                painter.setPen(theme.color('text.placeholder'))
                painter.drawText(count_rect, Qt.AlignmentFlag.AlignVCenter, count)
        else:
            presentation = self.parent().replacement_previews.get(index.row(), data)
            parts = presentation.get("key_parts", presentation["parts"]) if self.parent().show_keys else presentation["parts"]
            width = max(0, rect.width())
            gap = 8
            if len(parts) == 1:
                widths = [width]
            else:
                key_width = min(option.fontMetrics.horizontalAdvance(parts[0][0]), int(width * .36))
                remaining = max(0, width - key_width - gap * (len(parts) - 1))
                widths = [key_width] + [remaining // (len(parts) - 1)] * (len(parts) - 1)
            x = rect.left()
            for part, part_width in zip(parts, widths):
                text, prepared_matches, primary = part[:3]
                display, matches, offset = fitted_text(text, prepared_matches, option.fontMetrics, part_width, with_offset=True)
                if not display:
                    x += part_width + gap
                    continue
                layout = QTextLayout(display, option.font)
                formats = []
                if len(part) == 4:
                    # Only offset/clipping work here; runs were prepared on input.
                    visible_end = len(display) - 1 if display.endswith("…") else len(display)
                    runs = [(max(0, start - offset), min(end - offset, visible_end), kind)
                            for start, end, kind in part[3]
                            if start - offset < visible_end and end - offset > 0]
                else:
                    runs = [(start, end, "match") for start, end in matches]
                for start, end, run_kind in runs:
                    fmt = QTextLayout.FormatRange()
                    fmt.start = len(display[:start].encode("utf-16-le")) // 2
                    fmt.length = len(display[start:end].encode("utf-16-le")) // 2
                    fmt.format = QTextCharFormat()
                    if run_kind == "match":
                        fmt.format.setBackground(theme.color('search.match.bg'))
                        fmt.format.setForeground(theme.color('search.match.fg'))
                    else:
                        # Same diff colors as the existing detailed EN comparison.
                        fmt.format.setForeground(theme.color("search.replace.removed" if run_kind == "removed" else "search.replace.added"))
                        fmt.format.setFontStrikeOut(run_kind == "removed")
                    formats.append(fmt)
                layout.setFormats(formats)
                layout.beginLayout()
                line = layout.createLine()
                line.setLineWidth(max(1, part_width))
                layout.endLayout()
                painter.setPen(theme.color('text.primary' if primary else 'text.secondary'))
                layout.draw(painter, QPointF(x, rect.top() + (rect.height() - line.height()) / 2))
                x += part_width + gap
        if not action_rect.isEmpty():
            self.paint_replace_action(painter, action_rect, index.data(REPLACE_ROLE)["scope"])
        painter.restore()

    def paint_replace_action(self, painter, rect, scope):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = rect.contains(self.parent().viewport().mapFromGlobal(QCursor.pos()))
        if hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.color('surface.selected'))
            painter.drawRoundedRect(rect, 3, 3)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(theme.color('icon.hover' if hovered else 'icon.default'), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.translate(rect.center().x() - 7, rect.center().y() - 7)
        path = QPainterPath(QPointF(1, 4))
        path.lineTo(12, 4)
        path.moveTo(9, 1); path.lineTo(12, 4); path.lineTo(9, 7)
        path.moveTo(12, 10); path.lineTo(1, 10)
        path.moveTo(4, 7); path.lineTo(1, 10); path.lineTo(4, 13)
        if scope == "ALL":
            path.moveTo(0, 16); path.lineTo(13, 16)
        painter.drawPath(path)


class SearchResults(QListWidget):
    replaceRequested = Signal(object)
    openEditorRequested = Signal(object)
    openPanelRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_keys = False
        self.replace_preview_active = False
        self.replacement_text = ""
        self.replacement_previews = {}
        self._pressed_replace = None
        self.setWordWrap(False)
        self.setAlternatingRowColors(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.update_scroll_step()
        self.setMouseTracking(True)
        self.setItemDelegate(SearchResultDelegate(self))

    def clear(self):
        self.replacement_previews = {}
        # Keep an invalidated persistent index until release so a refresh during
        # a press cannot turn the release into ordinary result navigation.
        super().clear()

    def replace_action_rect(self, index, rect):
        if not self.replace_preview_active or not index.isValid():
            return QRect()
        target = index.data(REPLACE_ROLE)
        if not target:
            return QRect()
        if target["scope"] == "ENTRY" and not (
                self.selectionModel().isSelected(index)
                or rect.contains(self.viewport().mapFromGlobal(QCursor.pos()))):
            return QRect()
        return QRect(rect.right() - 25, rect.center().y() - 10, 22, 20)

    def action_at(self, position):
        index = self.indexAt(position)
        if self.replace_action_rect(index, self.visualRect(index)).contains(position):
            return index
        return None

    def mousePressEvent(self, event):
        self._pressed_replace = None
        if event.button() == Qt.MouseButton.RightButton:
            item = self.itemAt(event.position().toPoint())
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsSelectable:
                self.setCurrentItem(item)
            event.accept()
            return
        index = self.action_at(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and index is not None:
            self._pressed_replace = QPersistentModelIndex(index)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        pressed = self._pressed_replace
        self._pressed_replace = None
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and pressed is not None:
            index = self.action_at(event.position().toPoint())
            event.accept()
            if pressed.isValid() and index is not None and pressed == index:
                target = index.data(REPLACE_ROLE)
                self.replaceRequested.emit(target)
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.action_at(event.position().toPoint()) is not None:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            index = self.action_at(event.pos())
            if index is not None:
                title = {"ENTRY": QCoreApplication.translate("search_results", "Заменить в этой строке"), "FILE": QCoreApplication.translate("search_results", "Заменить в этом файле…"),
                         "ALL": QCoreApplication.translate("search_results", "Заменить всё в моде…")}[index.data(REPLACE_ROLE)["scope"]]
                QToolTip.showText(event.globalPos(), title, self.viewport())
                return True
        return super().viewportEvent(event)

    def contextMenuEvent(self, event):
        item = self.currentItem() if event.reason() == event.Reason.Keyboard else self.itemAt(event.pos())
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(data, dict) or data.get("source") not in ("mod", "stellaris"):
            return super().contextMenuEvent(event)
        target = item.data(REPLACE_ROLE) if item is not None else None
        index = QPersistentModelIndex(self.indexFromItem(item))
        menu = QMenu(self)
        editor = menu.addAction(QCoreApplication.translate("search_results", "Открыть в редакторе"))
        editor.setEnabled(data["source"] == "mod" and bool(data.get("entry_id")))
        panel = menu.addAction(QCoreApplication.translate("search_results", "Открыть в нижней панели"))
        menu.addSeparator()
        copy_key = menu.addAction(QCoreApplication.translate("search_results", "Копировать ключ"))
        replace = None
        if self.replace_preview_active and target and target.get("occurrence") is not None:
            menu.addSeparator()
            replace = menu.addAction(QCoreApplication.translate("search_results", "Заменить это совпадение"))
        chosen = menu.exec(event.globalPos())
        menu.deleteLater()
        # A debounced refresh can replace the results while the menu is open.
        if index.isValid() and index.data(Qt.ItemDataRole.UserRole) == data:
            if chosen == editor:
                self.openEditorRequested.emit(data)
            elif chosen == panel:
                self.openPanelRequested.emit(data)
            elif chosen == copy_key:
                QApplication.clipboard().setText(data["key"])
            elif replace is not None and chosen == replace:
                self.replaceRequested.emit(dict(target, scope="OCCURRENCE"))
        event.accept()

    def set_replace_preview(self, active, replacement):
        if (self.replace_preview_active, self.replacement_text) == (bool(active), replacement):
            return
        self.replace_preview_active, self.replacement_text = bool(active), replacement
        self.prepare_replacement_previews()
        self.viewport().update()

    def prepare_replacement_previews(self):
        self.replacement_previews = {}
        if not self.replace_preview_active:
            return
        replacement = single_line(self.replacement_text)
        for i in range(self.count()):
            data = self.item(i).data(PRESENTATION_ROLE) or {}
            if not data.get("replaceable"):
                continue
            self.replacement_previews[i] = {
                "parts": [replacement_part(data["parts"][0], replacement)],
                "key_parts": [data["key_parts"][0], replacement_part(data["key_parts"][1], replacement)],
            }

    def update_scroll_step(self):
        self.verticalScrollBar().setSingleStep(max(4, self.fontMetrics().height() // 2))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.update_scroll_step()

    def set_show_keys(self, visible):
        self.show_keys = bool(visible)
        # Only presentation changes: retain results, selection and scroll position.
        for i in range(self.count()):
            item = self.item(i)
            data = item.data(PRESENTATION_ROLE) or {}
            if "key_parts" in data:
                parts = data["key_parts"] if self.show_keys else data["parts"]
                item.setText(" ".join(part[0] for part in parts))
        self.viewport().update()

    def add_header(self, text, kind, path="", replace_target=None, *, count=None):
        item = QListWidgetItem(f"{text} · {count}" if count is not None else text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        presentation = {"kind": kind, "text": text}
        if count is not None:
            presentation["count"] = count
        item.setData(PRESENTATION_ROLE, presentation)
        if replace_target:
            item.setData(REPLACE_ROLE, replace_target)
        item.setToolTip(path)
        self.addItem(item)

    def populate(self, matches, glossary_matches, query, mode, *, case_sensitive=False,
                 mod_display_path=english_to_russian_path):
        self.clear()
        for source, title in (("mod", QCoreApplication.translate("search_results", "МОД")), ("stellaris", "VANILLA")):
            files = OrderedDict()
            for item_source, row_index, row in matches:
                if item_source == source:
                    path = str(row.get("new_file") or row.get("old_file") or "")
                    files.setdefault(path, []).append((row_index, row))
            if not files:
                continue
            replace_source = source == "mod" and mode == "ru"
            self.add_header(title, "source",
                            replace_target={"scope": "ALL"} if replace_source else None)
            for path, rows in files.items():
                display_path = mod_display_path(path).as_posix() if source == "mod" and path else path
                filename = display_path.replace("\\", "/").rsplit("/", 1)[-1] or QCoreApplication.translate("search_results", "Без файла")
                self.add_header(filename, "file", display_path,
                                {"scope": "FILE", "file": path} if replace_source and path else None,
                                count=len(rows))
                for row_index, row in rows:
                    key = row.get("key") or ""
                    en = row.get("new_text") if row.get("new_text") is not None else row.get("old_text") or ""
                    ru = row.get("ru_text") or ""
                    key_part = prepared_part(compact_text(key), query if mode == "key" else "", True,
                                             case_sensitive=case_sensitive)
                    data = {"kind": "result", "replaceable": source == "mod" and mode == "ru" and is_working_ru(row)}
                    if mode == "key":
                        parts = [key_part, prepared_part("· EN: " + (snippet(en) or "—")),
                                 prepared_part("· RU: " + (snippet(ru) or "—"))]
                    else:
                        preview, spans, _ = snippet_part(ru if mode == "ru" else en, query, True,
                                                         case_sensitive=case_sensitive)
                        parts = [(preview, spans, True)]
                        # The key separator is outside the RU text/match ranges.
                        data["key_parts"] = [key_part, ("· " + preview, [(a + 2, b + 2) for a, b in spans], False)]
                    data["parts"] = parts
                    visible_parts = data.get("key_parts", parts) if self.show_keys else parts
                    item = QListWidgetItem(" ".join(part[0] for part in visible_parts))
                    item.setData(Qt.ItemDataRole.UserRole, {"source": source, "index": row_index,
                                                          "entry_id": row.get("entry_id"), "key": key})
                    item.setData(PRESENTATION_ROLE, data)
                    if data["replaceable"] and row.get("entry_id"):
                        target = {"scope": "ENTRY", "entry_id": row["entry_id"]}
                        source_spans = match_spans(ru, query, case_sensitive=case_sensitive)
                        if len(source_spans) == 1:
                            target.update(occurrence=source_spans[0], ru_snapshot=ru,
                                          query=query, case_sensitive=case_sensitive)
                        item.setData(REPLACE_ROLE, target)
                    item.setToolTip(tooltip(key, en, ru, display_path))
                    self.addItem(item)
        for scope, title in (("project", "Glossary · Project"), ("global", "Glossary · Global")):
            selected = [match for match in glossary_matches if match["scope"] == scope]
            if not selected:
                continue
            self.add_header(f"{title} · {len(selected)}", "source")
            for match in selected:
                text = match["snippet"]
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, match)
                item.setData(PRESENTATION_ROLE, {"kind": "result", "parts": [prepared_part(text, query, True, case_sensitive=case_sensitive)]})
                item.setToolTip(f"<qt>{html.escape(match['snippet'])}</qt>")
                self.addItem(item)
        self.prepare_replacement_previews()
