"""A pill switch, backed by Fluent's SwitchButton."""
from __future__ import annotations

from PySide6.QtCore import Signal
from qfluentwidgets import SwitchButton


class Toggle(SwitchButton):

    toggled_value = Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setOnText('')
        self.setOffText('')
        self.setChecked(checked)
        self.checkedChanged.connect(self.toggled_value.emit)

    def value(self) -> bool:
        return self.isChecked()

    def set_value(self, checked: bool, notify: bool = False) -> None:
        checked = bool(checked)
        if checked == self.isChecked():
            return
        if notify:
            self.setChecked(checked)
        else:
            self.blockSignals(True)
            self.setChecked(checked)
            self.blockSignals(False)
