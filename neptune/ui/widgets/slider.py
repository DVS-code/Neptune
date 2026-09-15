"""A horizontal 0..1 slider, backed by Fluent's Slider."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from qfluentwidgets import Slider as FluentSlider

from neptune.ui import theme as T

RESOLUTION = 1000


class Slider(FluentSlider):
    """A Fluent slider with a normalized position helper.

    QFluentWidgets already exposes the Qt ``valueChanged`` signal.  An earlier
    wrapper re-emitted that signal through a Python-defined ``moved`` signal,
    but PySide6 does not register that signal reliably on this QFluent subclass
    (the meta-object reports the signal after inherited slots).  Consumers now
    listen to ``valueChanged`` directly.
    """

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

    @property
    def syncing(self) -> bool:
        """True while `set_position` moves the slider, so listeners can ignore that change."""
        return self._syncing

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

    def wheelEvent(self, event) -> None:
        """Only respond to the wheel while focused — e.g. right after a click or drag."""
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            colour = T.ACCENT if self.isEnabled() else T.TEXT_FAINT
            self.setThemeColor(colour, colour)
