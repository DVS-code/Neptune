"""A horizontal 0..1 slider, backed by Fluent's Slider."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from qfluentwidgets import Slider as FluentSlider

RESOLUTION = 1000


class Slider(FluentSlider):
    """Reports a position between 0.0 and 1.0."""

    moved = Signal(float)
    released = Signal()

    def __init__(self, position: float = 0.0, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setRange(0, RESOLUTION)
        # Qt's defaults (1 / 10) would step 0.1% per arrow key and ~0.3% per wheel notch
        # against this resolution; 1% / 10% matches the feel of the slider this replaced.
        self.setSingleStep(RESOLUTION // 100)
        self.setPageStep(RESOLUTION // 10)
        self.setCursor(Qt.PointingHandCursor)
        self._syncing = False
        self.set_position(position)
        self.valueChanged.connect(self._on_value_changed)
        self.sliderReleased.connect(self.released.emit)

    def position(self) -> float:
        return self.value() / RESOLUTION

    def set_position(self, position: float) -> None:
        position = min(1.0, max(0.0, float(position)))
        value = round(position * RESOLUTION)
        if value == self.value():
            return
        self._syncing = True
        try:
            self.setValue(value)
        finally:
            self._syncing = False

    def _on_value_changed(self, value: int) -> None:
        if self._syncing:
            return
        self.moved.emit(value / RESOLUTION)

    def wheelEvent(self, event) -> None:
        """Only respond to the wheel while focused — e.g. right after a click or drag."""
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)
