"""Small draggable 0..1 transition-curve editor."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from neptune.ui import theme as T

DEFAULT_CURVE = (0.0, 0.10, 0.50, 0.90, 1.0)
GRAPH_HEIGHT = 172
MARGIN_LEFT = 54
MARGIN_RIGHT = 14
MARGIN_TOP = 12
MARGIN_BOTTOM = 26
HANDLE_RADIUS = 5.0
HIT_RADIUS = 12.0


def normalise_curve(values) -> list[float]:
    """Return a safe five-point curve with fixed start and finish."""
    try:
        curve = [float(value) for value in values]
    except (TypeError, ValueError):
        curve = []
    if len(curve) != len(DEFAULT_CURVE) or any(value != value for value in curve):
        curve = list(DEFAULT_CURVE)
    curve = [max(0.0, min(1.0, value)) for value in curve]
    curve[0] = 0.0
    curve[-1] = 1.0
    return curve


def curve_value(values, progress: float) -> float:
    """Piecewise-linear lookup used by both the editor and the animation thread."""
    curve = normalise_curve(values)
    progress = max(0.0, min(1.0, float(progress)))
    position = progress * (len(curve) - 1)
    low = min(len(curve) - 2, int(position))
    weight = position - low
    return curve[low] * (1.0 - weight) + curve[low + 1] * weight


class TransitionCurve(QWidget):
    """Five fixed-time handles; drag the middle handles to shape transition progress."""

    changed = Signal(list)

    def __init__(self, values=None, parent=None):
        super().__init__(parent)
        self._values = normalise_curve(values or DEFAULT_CURVE)
        self._drag_index: int | None = None
        self._hover_index: int | None = None
        self.setFixedHeight(GRAPH_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setToolTip(
            "Drag the middle points. Left-to-right is elapsed time; bottom-to-top is "
            "progress from the raised camber to the lowered camber."
        )

    def values(self) -> list[float]:
        return list(self._values)

    def set_values(self, values, notify: bool = False) -> None:
        self._values = normalise_curve(values)
        self.update()
        if notify:
            self.changed.emit(self.values())

    def reset(self) -> None:
        self.set_values(DEFAULT_CURVE, notify=True)

    def _plot(self) -> QRectF:
        return QRectF(
            MARGIN_LEFT,
            MARGIN_TOP,
            max(1.0, self.width() - MARGIN_LEFT - MARGIN_RIGHT),
            max(1.0, self.height() - MARGIN_TOP - MARGIN_BOTTOM),
        )

    def _point(self, index: int) -> QPointF:
        plot = self._plot()
        x = plot.left() + plot.width() * index / (len(self._values) - 1)
        y = plot.bottom() - plot.height() * self._values[index]
        return QPointF(x, y)

    def _nearest_handle(self, position) -> int | None:
        best = None
        distance = HIT_RADIUS * HIT_RADIUS
        for index in range(1, len(self._values) - 1):
            point = self._point(index)
            candidate = (point.x() - position.x()) ** 2 + (point.y() - position.y()) ** 2
            if candidate <= distance:
                best = index
                distance = candidate
        return best

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        plot = self._plot()

        painter.setPen(QPen(QColor(T.GRID), 1))
        for step in range(5):
            y = plot.top() + plot.height() * step / 4
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for step in range(len(self._values)):
            x = plot.left() + plot.width() * step / (len(self._values) - 1)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        path = QPainterPath(self._point(0))
        for index in range(1, len(self._values)):
            path.lineTo(self._point(index))

        fill = QPainterPath(path)
        fill.lineTo(plot.bottomRight())
        fill.lineTo(plot.bottomLeft())
        fill.closeSubpath()
        gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
        gradient.setColorAt(0.0, QColor(168, 85, 247, 54))
        gradient.setColorAt(1.0, QColor(168, 85, 247, 4))
        painter.fillPath(fill, gradient)
        painter.setPen(QPen(QColor(T.ACCENT_BRIGHT), 2.0))
        painter.drawPath(path)

        painter.setPen(QPen(QColor(T.SURFACE_SUNKEN), 1.5))
        for index in range(len(self._values)):
            colour = T.TEXT if index == self._hover_index else T.ACCENT_BRIGHT
            painter.setBrush(QColor(colour))
            radius = HANDLE_RADIUS + (1.0 if index == self._hover_index else 0.0)
            painter.drawEllipse(self._point(index), radius, radius)

        font = QFont(painter.font())
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor(T.TEXT_FAINT))
        painter.drawText(QRectF(0, plot.top() - 5, MARGIN_LEFT - 8, 20), Qt.AlignRight, "Lowered")
        painter.drawText(
            QRectF(0, plot.bottom() - 15, MARGIN_LEFT - 8, 20), Qt.AlignRight, "Raised"
        )
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 5, plot.width(), MARGIN_BOTTOM - 5),
            Qt.AlignCenter,
            "Transition time",
        )

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._drag_index = self._nearest_handle(event.position())
        if self._drag_index is not None:
            self._apply_drag(event.position().y())

    def mouseMoveEvent(self, event) -> None:
        if self._drag_index is not None:
            self._apply_drag(event.position().y())
            return
        hover = self._nearest_handle(event.position())
        if hover != self._hover_index:
            self._hover_index = hover
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_index = None

    def leaveEvent(self, _event) -> None:
        if self._hover_index is not None:
            self._hover_index = None
            self.update()

    def _apply_drag(self, y: float) -> None:
        if self._drag_index is None:
            return
        plot = self._plot()
        value = (plot.bottom() - y) / max(1.0, plot.height())
        self._values[self._drag_index] = max(0.0, min(1.0, value))
        self.update()
        self.changed.emit(self.values())
