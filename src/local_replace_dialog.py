"""Small confirmation shared by all local replacement targets."""

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout


def confirm_replace_all(parent, count):
    dialog = QDialog(parent)
    dialog.setWindowTitle(QCoreApplication.translate("local_replace_dialog", "Заменить всё?"))
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 16, 18, 14)
    layout.setSpacing(16)
    label = QLabel(QCoreApplication.translate("local_replace_dialog", "В текущем наборе будет заменено совпадений: {0}.").format(count))
    label.setTextFormat(Qt.TextFormat.PlainText)
    layout.addWidget(label)
    buttons = QDialogButtonBox()
    replace = buttons.addButton(QCoreApplication.translate("local_replace_dialog", "Заменить всё"), QDialogButtonBox.ButtonRole.AcceptRole)
    replace.setProperty("role", "primary")
    cancel = buttons.addButton(QCoreApplication.translate("local_replace_dialog", "Отмена"), QDialogButtonBox.ButtonRole.RejectRole)
    cancel.setProperty("role", "secondary")
    for button in (replace, cancel):
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
    cancel.setDefault(True)
    cancel.setFocus()
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.adjustSize()
    try:
        return dialog.exec() == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()
