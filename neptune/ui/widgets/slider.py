"""A custom-drawn horizontal slider."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from neptune.ui import theme as T

HEIGHT = 22
GROOVE_HEIGHT = 4
HANDLE_RADIUS = 7
HANDLE_RADIUS_ACTIVE = 8
EDGE = HANDLE_RADIUS_ACTIVE + 1


class Slider(QWidget):
    """Reports a position between 0.0 and 1.0."""

    moved = Signal(float)
    released = Signal()

    def __init__(self, position: float = 0.0, parent=None):
        super().__init__(parent)
        self.setFixedHeight(HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._position = min(1.0, max(0.0, float(position)))
        self._hover = False
        self._pressed = False

    def position(self) -> float:
        return self._position

    def set_position(self, position: float) -> None:
        position = min(1.0, max(0.0, float(position)))
        if position != self._position:
            self._position = position
            self.update()

    def _track(self) -> tuple[float, float]:
        return float(EDGE), float(max(EDGE + 1, self.width() - EDGE))

    def _position_at(self, x: float) -> float:
        left, right = self._track()
        span = right - left
        if span <= 0:
            return 0.0
        return min(1.0, max(0.0, (x - left) / span))

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._pressed = True
        self.set_position(self._position_at(event.position().x()))
        self.moved.emit(self._position)

    def mouseMoveEvent(self, event) -> None:
        if self._pressed:
            self.set_position(self._position_at(event.position().x()))
            self.moved.emit(self._position)
            return
        inside = self.rect().contains(event.position().toPoint())
        if inside != self._hover:
            self._hover = inside
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._pressed = False
        self.update()
        self.released.emit()

    def wheelEvent(self, event) -> None:
        step = 0.01 if event.angleDelta().y() > 0 else -0.01
        self.set_position(self._position + step)
        self.moved.emit(self._position)
        self.released.emit()

    def keyPressEvent(self, event) -> None:
        step = 0.0
        if event.key() in (Qt.Key_Left, Qt.Key_Down):
            step = -0.01
        elif event.key() in (Qt.Key_Right, Qt.Key_Up):
            step = 0.01
        elif event.key() == Qt.Key_PageDown:
            step = -0.1
        elif event.key() == Qt.Key_PageUp:
            step = 0.1
        else:
            super().keyPressEvent(event)
            return
        self.set_position(self._position + step)
        self.moved.emit(self._position)
        self.released.emit()

    def enterEvent(self, _event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _event) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        left, right = self._track()
        middle = self.height() / 2.0
        radius = GROOVE_HEIGHT / 2.0
        enabled = self.isEnabled()

        groove = QRectF(left, middle - radius, right - left, GROOVE_HEIGHT)
        painter.setBrush(QColor(T.SURFACE_SUNKEN if enabled else T.BORDER))
        painter.drawRoundedRect(groove, radius, radius)

        handle_x = left + (right - left) * self._position
        if handle_x > left:
            fill = QRectF(left, middle - radius, handle_x - left, GROOVE_HEIGHT)
            painter.setBrush(QColor(T.ACCENT if enabled else T.BORDER_STRONG))
            painter.drawRoundedRect(fill, radius, radius)

        if enabled:
            size = HANDLE_RADIUS_ACTIVE if (self._hover or self._pressed) else HANDLE_RADIUS
            colour = QColor(T.ACCENT_BRIGHT if self._pressed else T.TEXT)
        else:
            size = HANDLE_RADIUS
            colour = QColor(T.TEXT_FAINT)

        painter.setBrush(QColor(T.BG))
        painter.drawEllipse(QPointF(handle_x, middle), size + 1.5, size + 1.5)
        painter.setBrush(colour)
        painter.drawEllipse(QPointF(handle_x, middle), size, size)
