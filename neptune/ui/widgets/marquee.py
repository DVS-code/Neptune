"""A label that scrolls its text when it is too long to fit."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QLabel

STEP_MS = 30
"""Repaint interval. 30 ms is ~33 fps, smooth enough to read and cheap to run."""

PIXELS_PER_STEP = 1
GAP = 36
"""Blank space between the end of the text and where it starts again, so the two
ends do not run together while the loop wraps."""

PAUSE_MS = 1500
"""How long the start of the name is held still before scrolling begins, so a
glance is enough to read a name that fits in the first few words."""


class MarqueeLabel(QLabel):
    """Shows text in full, scrolling horizontally only when it overflows.

    Text that fits is painted normally and the timer never runs, so the common case
    costs nothing. Only an overflowing name scrolls, and it pauses at the start of
    each pass so the beginning stays readable.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._offset = 0.0
        self._paused = True
        self._timer = QTimer(self)
        self._timer.setInterval(STEP_MS)
        self._timer.timeout.connect(self._advance)
        self._pause_timer = QTimer(self)
        self._pause_timer.setSingleShot(True)
        self._pause_timer.timeout.connect(self._resume)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt's own casing
        if text == self.text():
            return
        super().setText(text)
        self._restart()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._restart()

    def _text_width(self) -> int:
        return QFontMetrics(self.font()).horizontalAdvance(self.text())

    def _overflows(self) -> bool:
        return self._text_width() > self._available()

    def _available(self) -> int:
        margins = self.contentsMargins()
        return max(0, self.width() - margins.left() - margins.right())

    def _restart(self) -> None:
        self._offset = 0.0
        self._timer.stop()
        self._pause_timer.stop()
        if self._overflows():
            self._paused = True
            self._pause_timer.start(PAUSE_MS)
        self.update()

    def _resume(self) -> None:
        if self._overflows():
            self._paused = False
            self._timer.start()

    def _advance(self) -> None:
        span = self._text_width() + GAP
        self._offset += PIXELS_PER_STEP
        if self._offset >= span:
            # A full pass has wrapped: hold at the start again before the next one.
            self._offset = 0.0
            self._timer.stop()
            self._paused = True
            self._pause_timer.start(PAUSE_MS)
        self.update()

    def paintEvent(self, event) -> None:
        if not self._overflows():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        margins = self.contentsMargins()
        left = margins.left() - int(self._offset)
        baseline = (
            self.height() + QFontMetrics(self.font()).ascent() - QFontMetrics(self.font()).descent()
        ) // 2
        text = self.text()
        painter.drawText(left, baseline, text)
        # The second copy is what makes the wrap continuous rather than a jump back.
        painter.drawText(left + self._text_width() + GAP, baseline, text)
