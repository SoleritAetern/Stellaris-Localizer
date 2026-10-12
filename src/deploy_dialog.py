"""Review synchronisation decisions; no filesystem changes in the dialog."""

from PySide6.QtCore import QCoreApplication

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFrame, QHeaderView, QLabel, QMenu, QMessageBox,
    QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from . import theme


class _ElidedPathLabel(QLabel):
    """A single-line path that never sets the dialog's minimum width."""

    def __init__(self, text):
        super().__init__(text)
        self._full_text = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("role", "muted")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setToolTip(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, self.contentsRect().width()))


class DeployPreviewDialog(QDialog):
    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle(QCoreApplication.translate("deploy_dialog", "Синхронизация русского мода"))
        self.resize(900, 570)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        explanation = QLabel(
            QCoreApplication.translate("deploy_dialog", "Сравните изменения перед загрузкой перевода.")
        )
        layout.addWidget(explanation)
        self.target_label = _ElidedPathLabel(QCoreApplication.translate("deploy_dialog", "Папка: {0}").format(plan['target']))
        layout.addWidget(self.target_label)
        self.summary = QLabel()
        self.summary.setProperty("role", "muted")
        layout.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setProperty("role", "review")
        self.tree.setMouseTracking(True)
        self.tree.setIndentation(18)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.tree.setHeaderLabels([QCoreApplication.translate("deploy_dialog", "Файл"), QCoreApplication.translate("deploy_dialog", "Действие")])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setMinimumSectionSize(120)
        self.tree.setColumnWidth(1, 330)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_file_menu)
        layout.addWidget(self.tree, 1)
        self.groups = {name: QTreeWidgetItem(self.tree) for name in ("updated", "stale", "custom", "decisions")}
        self.decisions = {}
        custom = set(plan["custom"])
        stale = set(plan["stale"])
        for name in plan["updated"]:
            if name not in custom:
                QTreeWidgetItem(self.groups["updated"], [name, QCoreApplication.translate("deploy_dialog", "Будет обновлён")])
        for name in plan["stale"]:
            QTreeWidgetItem(self.groups["stale"], [name, QCoreApplication.translate("deploy_dialog", "Устаревший — будет удалён")])
        for name in plan["custom"]:
            QTreeWidgetItem(self.groups["custom"], [name, QCoreApplication.translate("deploy_dialog", "Пользовательский — будет сохранён")])
        for name, candidate in sorted(plan["candidates"].items()):
            # Existing stale GENERATED use the same keep/delete/leave controls
            # as unclassified files; missing stale files need no decision.
            if name in stale or name in custom:
                continue
            item = QTreeWidgetItem(self.groups["decisions"], [name, ""])
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            detail = str(candidate["path"])
            if candidate["origin"] == "initial RU":
                detail += QCoreApplication.translate("deploy_dialog", "\nФайл из сохранённой исходной локализации; в папке русского мода его сейчас нет.")
            if name in plan.get("stale_candidates", ()):
                detail += QCoreApplication.translate("deploy_dialog", "\nФайл больше не создаётся текущим проектом.")
            item.setToolTip(0, detail)
            choice = QComboBox()
            choice.addItem(QCoreApplication.translate("deploy_dialog", "Выберите действие…"), None)
            choice.addItem(QCoreApplication.translate("deploy_dialog", "Сохранить как пользовательский"), "keep")
            choice.addItem(QCoreApplication.translate("deploy_dialog", "Удалить"), "delete")
            choice.addItem(QCoreApplication.translate("deploy_dialog", "Оставить без изменений"), "leave")
            if name in plan.get("stale_candidates", ()):
                choice.setCurrentIndex(2)  # Proposed deletion, still requires Deploy confirmation.
            if candidate["origin"] != "Deploy target":
                deletion = choice.model().item(2)
                deletion.setEnabled(False)
                deletion.setToolTip(QCoreApplication.translate("deploy_dialog", "В папке русского мода этого файла нет. Исходный снимок проекта не удаляется."))
            choice.setItemData(1, QCoreApplication.translate("deploy_dialog", "Копия сохранится в проекте и будет включаться в следующие обновления русского мода."),
                               Qt.ItemDataRole.ToolTipRole)
            choice.setItemData(3, QCoreApplication.translate("deploy_dialog", "Файл не будет изменён или скопирован в проект."), Qt.ItemDataRole.ToolTipRole)
            self.tree.setItemWidget(item, 1, choice)
            self.decisions[name] = choice
        for key, label in (("updated", QCoreApplication.translate("deploy_dialog", "Будут обновлены")), ("stale", QCoreApplication.translate("deploy_dialog", "Будут удалены устаревшие")),
                           ("custom", QCoreApplication.translate("deploy_dialog", "Пользовательские файлы")), ("decisions", QCoreApplication.translate("deploy_dialog", "Требуют решения"))):
            group = self.groups[key]
            group.setText(0, label)
            group.setText(1, str(group.childCount()))
            group.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            font = QFont(self.tree.font())
            font.setWeight(QFont.Weight.DemiBold)
            group.setFont(0, font)
            group.setForeground(1, theme.color("text.secondary"))
            group.setExpanded(key == "decisions" and group.childCount() > 0)
            for row in range(group.childCount()):
                item = group.child(row)
                item.setForeground(1, theme.color("text.secondary"))
                if not item.toolTip(0):
                    item.setToolTip(0, item.text(0))
                item.setToolTip(1, item.text(1))
        footer = QFrame()
        footer.setProperty("role", "dialogFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 12, 0, 0)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(QCoreApplication.translate("deploy_dialog", "Отмена"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setProperty("role", "secondary")
        self.deploy_button = buttons.addButton(QCoreApplication.translate("deploy_dialog", "Загрузить в папку mod"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.deploy_button.setProperty("role", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer_layout.addWidget(buttons)
        layout.addWidget(footer)
        for choice in self.decisions.values():
            choice.currentIndexChanged.connect(self.update_summary)
        self.update_summary()

    def selected_custom(self):
        return sorted(name for name, choice in self.decisions.items() if choice.currentData() == "keep")

    def selected_decisions(self):
        return {
            "custom_paths": self.selected_custom(),
            "unknown_delete_paths": sorted(name for name, choice in self.decisions.items()
                                           if choice.currentData() == "delete"),
            "unchanged_paths": sorted(name for name, choice in self.decisions.items()
                                      if choice.currentData() == "leave"),
        }

    def update_summary(self, *args):
        decisions = self.selected_decisions()
        unresolved = sum(choice.currentData() not in ("keep", "delete", "leave") for choice in self.decisions.values())
        self.deploy_button.setEnabled(unresolved == 0)
        self.summary.setText(
            QCoreApplication.translate("deploy_dialog", "Обновятся {0}    Удалятся {1}    Пользовательские {2}    Требуют решения {3}").format(self.groups['updated'].childCount(), len(self.plan['stale']) + len(decisions['unknown_delete_paths']), len(self.plan['custom']) + len(self.selected_custom()), unresolved)
        )

    def accept(self):
        if self.deploy_button.isEnabled():
            super().accept()

    def show_file_menu(self, position):
        item = self.tree.itemAt(position)
        name = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if name not in self.decisions:
            return
        path = Path(self.plan["candidates"][name]["path"])
        menu = QMenu(self)
        menu.addAction(QCoreApplication.translate("deploy_dialog", "Открыть файл")).triggered.connect(lambda: self.open_path(path))
        menu.addAction(QCoreApplication.translate("deploy_dialog", "Показать в проводнике")).triggered.connect(lambda: self.open_path(path.parent))
        menu.exec(self.tree.viewport().mapToGlobal(position))
        menu.deleteLater()

    def open_path(self, path):
        if not path.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, QCoreApplication.translate("deploy_dialog", "Не удалось открыть"), QCoreApplication.translate("deploy_dialog", "Не удалось открыть: {0}").format(path))


def review_deploy(plan, parent):
    # An empty decision list is not consent to deploy. Even ordinary updates
    # must be reviewed and explicitly accepted in the modal dialog.
    dialog = DeployPreviewDialog(plan, parent)
    result = dialog.selected_decisions() if dialog.exec() == QDialog.DialogCode.Accepted else None
    dialog.deleteLater()
    return result


def review_takeover(plan, parent):
    box = QMessageBox(parent)
    box.setWindowTitle(QCoreApplication.translate("deploy_dialog", "Передача управления переводом"))
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(QCoreApplication.translate("deploy_dialog", "Эта папка перевода связана с проектом Stellaris Localizer, который больше не найден.\n\nТекущий проект может принять управление этой папкой.\nПользовательские файлы будут сохранены."))
    box.setInformativeText(str(plan["target"]))
    accept = box.addButton(QCoreApplication.translate("deploy_dialog", "Продолжить"), QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(QCoreApplication.translate("deploy_dialog", "Отмена"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    accepted = box.clickedButton() == accept
    box.deleteLater()
    return accepted
