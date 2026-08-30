"""Semantic push-button variants, all with the pointing-hand cursor Neptune uses everywhere."""

from __future__ import annotations

from PySide6.QtCore import Qt
from qfluentwidgets import PrimaryPushButton, PushButton, setCustomStyleSheet

from neptune.ui import theme as T


class Button(PushButton):
    def __init__(self, text: str = ""):
        super().__init__()
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)


class PrimaryButton(PrimaryPushButton):
    def __init__(self, text: str = ""):
        super().__init__()
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)


class DangerButton(PushButton):
    """A plain button that turns red on hover, for destructive actions."""

    def __init__(self, text: str = ""):
        super().__init__()
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        # setCustomStyleSheet, not setStyleSheet as PushButton.__init__ already applied
        hover = f"DangerButton:hover {{ border-color: {T.ERR}; color: {T.ERR}; }}"
        setCustomStyleSheet(self, hover, hover)
