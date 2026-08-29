"""The product name, painted with a slowly sweeping colour gradient."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from neptune.ui import theme as T

TEXT = 'NEPTUNE'
HEIGHT = 34
LETTER_SPACING = 5
FONT_SIZE = 21

FRAME_MS = 40
PHASE_STEP = 0.006
SWEEP_WIDTH = 2.0


class Wordmark(QWidget):
    """Draws the product name with an animated gradient."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

        self._phase = 0.0
        self._stops = [QColor(colour) for colour in T.WORDMARK_GRADIENT]

        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._advance)
        self._timer.start()

    def _advance(self) -> None:
        if not self.isVisible():
            return
        self._phase = (self._phase + PHASE_STEP) % 1.0
        self.update()

    def _sample(self, position: float) -> QColor:
        """Colour at `position` along a seamless loop through the palette."""
        count = len(self._stops)
        scaled = (position % 1.0) * count
        index = int(scaled)
        blend = scaled - index
        start = self._stops[index % count]
        end = self._stops[(index + 1) % count]
        return QColor(
            round(start.red() + (end.red() - start.red()) * blend),
            round(start.green() + (end.green() - start.green()) * blend),
            round(start.blue() + (end.blue() - start.blue()) * blend),
        )

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        self._timer.start()
        super().showEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        font = QFont(painter.font())
        font.setPointSize(FONT_SIZE)
        font.setWeight(QFont.Bold)
        font.setLetterSpacing(QFont.AbsoluteSpacing, LETTER_SPACING)
        painter.setFont(font)

        width = max(1, self.width())
        gradient = QLinearGradient(0.0, 0.0, width, 0.0)

        steps = 24
        for index in range(steps + 1):
            position = index / steps
            gradient.setColorAt(
                position, self._sample(self._phase + position / SWEEP_WIDTH))

        painter.setPen(QPen(gradient, 0))
        painter.drawText(QRectF(0, 0, width, self.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, TEXT)
