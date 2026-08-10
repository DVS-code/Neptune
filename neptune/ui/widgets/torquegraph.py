"""Torque curve plot with draggable points."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from neptune.ui import theme as T

MARGIN_LEFT = 12
MARGIN_RIGHT = 16
MARGIN_TOP = 14
MARGIN_BOTTOM = 26
GRAPH_HEIGHT = 220
EMPTY_HEIGHT = 96
HIT_RADIUS = 9
GRID_LINES = 4


class TorqueGraph(QWidget):
    """Draws the stock and live torque curves and lets points be dragged."""

    point_changed = Signal(int, float)
    edit_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(EMPTY_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

        self.stock: list[float] = []
        self.live: list[float] = []
        self.rpm_per_index = 100.0
        self.rpm = 0.0
        self.ceiling: float | None = None
        self.editable = False

        self._selection: set[int] = set()
        self._drag_index: int | None = None
        self._hover_index: int | None = None
        self._band_origin: QPointF | None = None
        self._band: QRectF | None = None

    def set_data(self, stock, live, rpm_per_index=100.0, rpm=0.0,
                 ceiling=None, editable=False) -> None:
        self.stock = list(stock or [])
        self.live = list(live or [])
        self.rpm_per_index = rpm_per_index or 100.0
        self.rpm = rpm or 0.0
        self.ceiling = ceiling
        self.editable = editable
        if self._selection:
            limit = max(0, len(self.live) - 1)
            self._selection = {index for index in self._selection if index < limit}

        wanted = GRAPH_HEIGHT if (self.live or self.stock) else EMPTY_HEIGHT
        if self.height() != wanted:
            self.setFixedHeight(wanted)
        self.update()

    def selection(self) -> set[int]:
        return set(self._selection)

    def clear_selection(self) -> None:
        self._selection.clear()
        self.update()

    def _points(self) -> int:
        return max(len(self.live) - 1, len(self.stock) - 1, 1)

    def _plot(self) -> QRectF:
        return QRectF(MARGIN_LEFT, MARGIN_TOP,
                      max(1.0, self.width() - MARGIN_LEFT - MARGIN_RIGHT),
                      max(1.0, self.height() - MARGIN_TOP - MARGIN_BOTTOM))

    def _value_range(self) -> tuple[float, float]:
        values = [value for value in (self.live[:-1] + self.stock[:-1])
                  if value is not None]
        if not values:
            return 0.0, 1.0
        low = min(values)
        high = max(values)
        if high - low < 1e-6:
            high = low + 1.0
        padding = (high - low) * 0.14
        return low - padding, high + padding

    def _x_for(self, index: int) -> float:
        plot = self._plot()
        span = max(1, self._points() - 1)
        return plot.left() + plot.width() * index / span

    def _y_for(self, value: float) -> float:
        plot = self._plot()
        low, high = self._value_range()
        return plot.bottom() - plot.height() * (value - low) / (high - low)

    def _index_at(self, x: float) -> int:
        plot = self._plot()
        if plot.width() <= 0:
            return 0
        fraction = (x - plot.left()) / plot.width()
        span = max(1, self._points() - 1)
        return max(0, min(span, int(round(fraction * span))))

    def _value_at(self, y: float) -> float:
        plot = self._plot()
        low, high = self._value_range()
        fraction = (plot.bottom() - y) / max(1.0, plot.height())
        return low + fraction * (high - low)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        plot = self._plot()

        if not self.live and not self.stock:
            painter.setPen(QColor(T.TEXT_FAINT))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             'Attach to the game and load a car')
            return

        painter.setPen(QPen(QColor(T.GRID), 1))
        for step in range(GRID_LINES + 1):
            y = plot.top() + plot.height() * step / GRID_LINES
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        self._draw_axis(painter, plot)

        if self.stock and len(self.stock) > 2:
            self._draw_curve(painter, self.stock[:-1], QColor(T.CURVE_STOCK),
                             dashed=True, fill=False)

        if self.live and len(self.live) > 2:
            self._draw_curve(painter, self.live[:-1], QColor(T.CURVE_LIVE),
                             dashed=False, fill=True)
            self._draw_points(painter)

        self._draw_needle(painter, plot)

        if self._band is not None:
            painter.setPen(QPen(QColor(T.ACCENT), 1, Qt.DashLine))
            painter.setBrush(QColor(168, 85, 247, 30))
            painter.drawRect(self._band)

    def _draw_axis(self, painter: QPainter, plot: QRectF) -> None:
        font = QFont(painter.font())
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor(T.TEXT_FAINT))

        span = max(1, self._points() - 1)
        top_rpm = span * self.rpm_per_index
        if top_rpm <= 0:
            return

        step = self._axis_step(top_rpm)
        rpm = 0.0
        while rpm <= top_rpm + 1e-6:
            x = plot.left() + plot.width() * (rpm / top_rpm)
            painter.drawText(QRectF(x - 28, plot.bottom() + 5, 56, 16),
                             Qt.AlignCenter, f'{rpm:.0f}')
            rpm += step

    @staticmethod
    def _axis_step(top_rpm: float) -> float:
        for candidate in (1000.0, 2000.0, 2500.0, 5000.0):
            if top_rpm / candidate <= 8:
                return candidate
        return 10000.0

    def _draw_curve(self, painter: QPainter, values: list[float], colour: QColor,
                    dashed: bool, fill: bool) -> None:
        if len(values) < 2:
            return
        plot = self._plot()
        path = QPainterPath()
        for index, value in enumerate(values):
            point = QPointF(self._x_for(index), self._y_for(value))
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)

        if fill:
            area = QPolygonF()
            area.append(QPointF(self._x_for(0), plot.bottom()))
            for index, value in enumerate(values):
                area.append(QPointF(self._x_for(index), self._y_for(value)))
            area.append(QPointF(self._x_for(len(values) - 1), plot.bottom()))

            gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
            gradient.setColorAt(0.0, QColor(168, 85, 247, 56))
            gradient.setColorAt(1.0, QColor(168, 85, 247, 4))
            painter.setPen(Qt.NoPen)
            painter.setBrush(gradient)
            painter.drawPolygon(area)

        pen = QPen(colour, 2.0)
        if dashed:
            pen.setStyle(Qt.DashLine)
            pen.setWidthF(1.4)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def _draw_points(self, painter: QPainter) -> None:
        if not self.editable:
            return
        values = self.live[:-1]
        step = max(1, len(values) // 60)
        painter.setPen(Qt.NoPen)
        for index in range(0, len(values), step):
            selected = index in self._selection
            hovered = index == self._hover_index
            if not (selected or hovered):
                continue
            painter.setBrush(QColor(T.ACCENT_BRIGHT) if selected else QColor(T.TEXT))
            radius = 4.0 if selected else 3.0
            painter.drawEllipse(QPointF(self._x_for(index), self._y_for(values[index])),
                                radius, radius)

    def _draw_needle(self, painter: QPainter, plot: QRectF) -> None:
        if self.rpm <= 0 or self.rpm_per_index <= 0:
            return
        index = self.rpm / self.rpm_per_index
        span = max(1, self._points() - 1)
        if index > span:
            return
        x = plot.left() + plot.width() * index / span
        painter.setPen(QPen(QColor(T.ACCENT_BRIGHT), 1.4))
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

    def mousePressEvent(self, event) -> None:
        if not self.editable or not self.live:
            return
        position = event.position()

        if event.button() == Qt.RightButton:
            self._band_origin = position
            self._band = QRectF(position, position)
            self.update()
            return

        if event.button() != Qt.LeftButton:
            return

        index = self._index_at(position.x())
        if index >= len(self.live) - 1:
            return

        if event.modifiers() & Qt.ShiftModifier:
            self._selection.symmetric_difference_update({index})
            self.update()
            return

        distance = abs(self._y_for(self.live[index]) - position.y())
        if distance > HIT_RADIUS and index not in self._selection:
            self._selection.clear()

        self._drag_index = index
        self._apply_drag(position)

    def mouseMoveEvent(self, event) -> None:
        position = event.position()

        if self._band_origin is not None:
            self._band = QRectF(self._band_origin, position).normalized()
            self.update()
            return

        if self._drag_index is not None:
            self._apply_drag(position)
            return

        if self.editable and self.live:
            index = self._index_at(position.x())
            hover = index if index < len(self.live) - 1 else None
            if hover != self._hover_index:
                self._hover_index = hover
                self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.RightButton and self._band_origin is not None:
            self._commit_band()
            self._band_origin = None
            self._band = None
            self.update()
            return

        if self._drag_index is not None:
            self._drag_index = None
            self.edit_finished.emit()

    def leaveEvent(self, _event) -> None:
        if self._hover_index is not None:
            self._hover_index = None
            self.update()

    def _commit_band(self) -> None:
        if self._band is None or not self.live:
            return
        self._selection.clear()
        limit = len(self.live) - 1
        for index in range(limit):
            x = self._x_for(index)
            y = self._y_for(self.live[index])
            if self._band.contains(QPointF(x, y)):
                self._selection.add(index)

    def _apply_drag(self, position) -> None:
        if self._drag_index is None or not self.live:
            return
        limit = len(self.live) - 1
        index = max(0, min(limit - 1, self._drag_index))
        target = self._value_at(position.y())
        delta = target - self.live[index]

        indices = self._selection if index in self._selection else {index}
        for point in sorted(indices):
            if point >= limit:
                continue
            value = self.live[point] + delta
            self.live[point] = value
            self.point_changed.emit(point, value)
        self.update()

    def scale_selection(self, factor: float) -> None:
        """Multiply the selected points, or the whole curve when nothing is selected."""
        if not self.live:
            return
        limit = len(self.live) - 1
        indices = sorted(self._selection) if self._selection else range(limit)
        for point in indices:
            if point >= limit:
                continue
            value = self.live[point] * factor
            self.live[point] = value
            self.point_changed.emit(point, value)
        self.update()
        self.edit_finished.emit()
