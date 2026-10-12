"""One themed outline around an existing field and its trailing action."""

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy


class CompoundControl(QFrame):
    def __init__(self, field, action):
        super().__init__()
        self.setObjectName("compoundControl")
        self.field = field
        self.action = action
        self.setFocusProxy(field)
        self.setProperty("focused", False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(field, 1)
        layout.addWidget(action)
        for widget in (field, action):
            widget.setSizePolicy(QSizePolicy.Policy.Expanding if widget is field
                                 else QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            focused = self.field.hasFocus() or self.action.hasFocus()
            if focused != self.property("focused"):
                self.setProperty("focused", focused)
                self.style().unpolish(self)
                self.style().polish(self)
                self.update()
        return super().eventFilter(watched, event)
