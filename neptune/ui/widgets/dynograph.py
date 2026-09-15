"""Dyno graph for the live engine curve."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from neptune.ui import theme as T

MARGIN_LEFT = 48
MARGIN_RIGHT = 48
MARGIN_TOP = 24
MARGIN_BOTTOM = 30
GRAPH_HEIGHT = 286
EMPTY_HEIGHT = 110
GRID_LINES = 4


class DynoGraph(QWidget):
    """Plot torque and power against RPM without exposing any write controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(EMPTY_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.torque: list[float] = []
        self.power: list[float] = []
        self.rpm_per_index = 100.0
        self.rpm = 0.0
        self.redline: float | None = None
        self.show_torque = True
        self.show_power = True
        self.torque_unit = "Nm"
        self.power_unit = "hp"

    def set_data(
        self,
        torque,
        power,
        rpm_per_index=100.0,
        rpm=0.0,
        redline=None,
        show_torque=True,
        show_power=True,
        torque_unit="Nm",
        power_unit="hp",
    ) -> None:
        """Values arrive already converted to `torque_unit` and `power_unit`."""
        self.torque = [float(value) for value in (torque or [])]
        self.power = [float(value) for value in (power or [])]
        self.rpm_per_index = float(rpm_per_index or 100.0)
        self.rpm = max(0.0, float(rpm or 0.0))
        self.redline = float(redline) if redline and redline > 0 else None
        self.show_torque = bool(show_torque)
        self.show_power = bool(show_power)
        self.torque_unit = torque_unit
        self.power_unit = power_unit
        wanted = GRAPH_HEIGHT if (self.torque or self.power) else EMPTY_HEIGHT
        if self.height() != wanted:
            self.setFixedHeight(wanted)
        self.update()

    def _points(self) -> int:
        return max(len(self.torque), len(self.power), 1)

    def _plot(self) -> QRectF:
        return QRectF(
            MARGIN_LEFT,
            MARGIN_TOP,
            max(1.0, self.width() - MARGIN_LEFT - MARGIN_RIGHT),
            max(1.0, self.height() - MARGIN_TOP - MARGIN_BOTTOM),
        )

    def _max_rpm(self) -> float:
        return max(1.0, (self._points() - 1) * self.rpm_per_index)

    def _max_value(self) -> tuple[float, float]:
        torque = max(self.torque, default=0.0)
        power = max(self.power, default=0.0)
        return max(1.0, torque * 1.12), max(1.0, power * 1.12)

    def _x_for(self, index: int) -> float:
        plot = self._plot()
        span = max(1, self._points() - 1)
        return plot.left() + plot.width() * index / span

    def _y_for(self, value: float, maximum: float) -> float:
        plot = self._plot()
        return plot.bottom() - plot.height() * max(0.0, value) / maximum

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if not self.torque and not self.power:
            painter.setPen(QColor(T.TEXT_FAINT))
            painter.drawText(self.rect(), Qt.AlignCenter, "Attach to the game and load a car")
            return

        plot = self._plot()
        torque_max, power_max = self._max_value()
        painter.setPen(QPen(QColor(T.GRID), 1))
        for step in range(GRID_LINES + 1):
            y = plot.top() + plot.height() * step / GRID_LINES
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        self._draw_axis(painter, plot, torque_max, power_max)
        if self.show_torque and len(self.torque) > 1:
            self._draw_curve(painter, self.torque, torque_max, QColor(T.INFO))
        if self.show_power and len(self.power) > 1:
            self._draw_curve(painter, self.power, power_max, QColor(T.ACCENT_BRIGHT))

        if self.redline and self.redline < self._max_rpm():
            x = plot.left() + plot.width() * self.redline / self._max_rpm()
            painter.setPen(QPen(QColor(T.WARN), 1, Qt.DashLine))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.drawText(QRectF(x - 30, 3, 60, 18), Qt.AlignCenter, "REDLINE")

        if self.rpm > 0.0:
            x = plot.left() + plot.width() * min(1.0, self.rpm / self._max_rpm())
            painter.setPen(QPen(QColor(T.TEXT), 1))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

    def _draw_axis(self, painter: QPainter, plot: QRectF, torque_max: float, power_max: float) -> None:
        font = QFont(painter.font())
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor(T.TEXT_FAINT))

        for step in range(GRID_LINES + 1):
            fraction = 1.0 - step / GRID_LINES
            y = plot.top() + plot.height() * step / GRID_LINES
            torque = torque_max * fraction
            power = power_max * fraction
            painter.drawText(QRectF(0, y - 8, MARGIN_LEFT - 7, 16), Qt.AlignRight, f"{torque:.0f}")
            painter.drawText(
                QRectF(plot.right() + 7, y - 8, MARGIN_RIGHT - 7, 16),
                Qt.AlignLeft,
                f"{power:.0f}",
            )

        painter.drawText(QRectF(0, 2, MARGIN_LEFT, 16), Qt.AlignRight, self.torque_unit)
        painter.drawText(
            QRectF(plot.right() + 7, 2, MARGIN_RIGHT - 7, 16), Qt.AlignLeft, self.power_unit
        )
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 7, plot.width(), 18),
            Qt.AlignCenter,
            f"RPM  0 — {self._max_rpm():.0f}",
        )

    def _draw_curve(self, painter, values: list[float], maximum: float, colour: QColor) -> None:
        path = QPainterPath()
        for index, value in enumerate(values):
            point = QPointF(self._x_for(index), self._y_for(value, maximum))
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.setPen(QPen(colour, 2.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
