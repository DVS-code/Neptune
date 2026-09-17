"""Lightweight multi-channel log graph with one synchronized cursor."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget

from neptune.core.models import NeptuneLog
from neptune.memory import offsets as O
from neptune.ui import theme as T

GROUPS = {
    "Power": ("power", "torque", "rpm"),
    "Turbo": ("boost", "boost_multiplier", "throttle"),
    "Acceleration": ("speed_ms", "gear"),
    "Transmission": ("gear", "rpm", "speed_ms"),
}

COLOURS = (T.ACCENT_BRIGHT, T.INFO, T.OK, T.WARN)


class LogGraph(QWidget):
    cursor_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(280)
        self._log: NeptuneLog | None = None
        self._group = next(iter(GROUPS))
        self._cursor = 0
        self._units = {"speed": "km/h", "pressure": "psi", "power": "hp", "torque": "Nm"}
        # A log holds up to 250k samples. Converting and decimating them on every paint made each
        # cursor move rework the whole run, so both are cached: series per field, traces per size.
        self._series: dict[str, list[float]] = {}
        self._traces: tuple | None = None  # ((group, width, height, pixel ratio), QPixmap)

    def set_units(self, units: dict | None) -> None:
        units = units if isinstance(units, dict) else {}
        self._units = {
            "speed": units.get("speed") if units.get("speed") in ("km/h", "mph") else "km/h",
            "pressure": units.get("pressure") if units.get("pressure") in ("psi", "bar") else "psi",
            "power": units.get("power") if units.get("power") in ("hp", "kW") else "hp",
            "torque": units.get("torque") if units.get("torque") in ("Nm", "lb-ft") else "Nm",
        }
        self._series.clear()
        self._traces = None
        self.update()

    def set_log(self, log: NeptuneLog | None) -> None:
        self._log = log
        self._cursor = 0
        self._series.clear()
        self._traces = None
        self.update()

    def set_group(self, group: str) -> None:
        if group in GROUPS:
            self._group = group
            self._traces = None
            self.update()

    def set_cursor(self, index: int, emit: bool = False) -> None:
        count = len(self._log.samples) if self._log else 0
        self._cursor = max(0, min(max(0, count - 1), int(index)))
        self.update()
        if emit:
            self.cursor_changed.emit(self._cursor)

    def _factor(self, field: str) -> float:
        if field == "speed_ms":
            return O.MS_TO_MPH if self._units["speed"] == "mph" else O.MS_TO_KPH
        if field == "boost" and self._units["pressure"] == "bar":
            return O.PSI_TO_BAR
        if field == "power" and self._units["power"] == "kW":
            return O.HP_TO_KW
        if field == "torque" and self._units["torque"] == "lb-ft":
            return O.NM_TO_LBFT
        return 1.0

    def _values(self, field: str) -> list[float]:
        values = self._series.get(field)
        if values is None:
            factor = self._factor(field)
            values = [
                float(value) * factor if value is not None else 0.0
                for value in (getattr(sample, field) for sample in self._log.samples)
            ]
            self._series[field] = values
        return values

    def _plot(self) -> QRectF:
        return QRectF(48, 18, max(1, self.width() - 62), max(1, self.height() - 42))

    def mouseMoveEvent(self, event) -> None:
        if self._log is None or len(self._log.samples) < 2:
            return
        plot = self._plot()
        fraction = max(0.0, min(1.0, (event.position().x() - plot.left()) / plot.width()))
        self.set_cursor(round(fraction * (len(self._log.samples) - 1)), True)

    def _trace_pixmap(self, plot: QRectF) -> QPixmap:
        """The traces, drawn once per view: one min/max pair per pixel column per field.

        Drawing the polylines is the expensive part (~15 ms each), so a cursor move only blits this.
        """
        ratio = self.devicePixelRatioF()
        key = (self._group, plot.width(), plot.height(), ratio)
        if self._traces is not None and self._traces[0] == key:
            return self._traces[1]
        pixmap = QPixmap(max(1, int(plot.width() * ratio)), max(1, int(plot.height() * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        count = len(self._log.samples)
        per_column = max(1, -(-count // max(1, int(plot.width()))))
        for series_index, field in enumerate(GROUPS.get(self._group, ())):
            values = self._values(field)
            minimum, maximum = min(values), max(values)
            span = maximum - minimum or 1.0
            polygon = QPolygonF()
            for start in range(0, count, per_column):
                chunk = values[start : start + per_column]
                x = plot.width() * start / (count - 1)
                for value in (min(chunk), max(chunk)) if per_column > 1 else chunk:
                    polygon.append(QPointF(x, plot.height() * (1.0 - (value - minimum) / span)))
            # 1 px: a wider pen sends the min/max zigzag through Qt's stroker, ~0.5 s per trace here.
            painter.setPen(QPen(QColor(COLOURS[series_index % len(COLOURS)]), 1))
            painter.drawPolyline(polygon)
        painter.end()
        self._traces = (key, pixmap)
        return pixmap

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        plot = self._plot()
        painter.fillRect(plot, QColor(T.SURFACE_SUNKEN))
        painter.setPen(QPen(QColor(T.GRID), 1))
        for step in range(5):
            y = plot.top() + plot.height() * step / 4
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        if self._log is None or len(self._log.samples) < 2:
            painter.setPen(QColor(T.TEXT_FAINT))
            painter.drawText(plot, Qt.AlignCenter, "Open a .nlog file to inspect the run")
            return

        count = len(self._log.samples)
        painter.drawPixmap(plot.topLeft(), self._trace_pixmap(plot))
        for series_index, field in enumerate(GROUPS.get(self._group, ())):
            painter.setPen(QColor(COLOURS[series_index % len(COLOURS)]))
            painter.drawText(
                QRectF(plot.left() + 8, plot.top() + 6 + series_index * 17, 190, 16),
                Qt.AlignLeft,
                f"{field}: {self._values(field)[self._cursor]:.2f}",
            )

        cursor_x = plot.left() + plot.width() * self._cursor / (count - 1)
        painter.setPen(QPen(QColor(T.TEXT), 1))
        painter.drawLine(QPointF(cursor_x, plot.top()), QPointF(cursor_x, plot.bottom()))
        first = self._log.samples[0].timestamp
        span_s = max(0.001, self._log.samples[-1].timestamp - first)
        marker = QColor(T.WARN)
        marker.setAlpha(100)
        painter.setPen(QPen(marker, 1))
        for event in self._log.analysis.events:
            x = plot.left() + plot.width() * max(0.0, min(1.0, (event.timestamp - first) / span_s))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
