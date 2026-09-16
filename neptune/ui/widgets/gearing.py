"""Compact gearing visualization for the Transmission and DYNO pages."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from neptune.ui import theme as T


class GearingChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(230)
        self._rows: list[dict] = []
        self._redline = 8000.0
        self._live_gear: int | None = None

    def set_data(self, rows: list[dict], redline: float, live_gear: int | None = None) -> None:
        self._rows = list(rows)
        self._redline = max(1.0, float(redline or 8000.0))
        self._live_gear = live_gear
        self.update()

    def sizeHint(self):
        return QSize(650, 250)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        plot = QRectF(56, 18, max(1.0, self.width() - 72), max(1.0, self.height() - 48))
        painter.fillRect(plot, QColor(T.SURFACE_SUNKEN))

        # Estimated powerband, 55-95% of the limiter, on the rpm axis the markers use.
        band_left = plot.left() + plot.width() * 0.55
        band_right = plot.left() + plot.width() * 0.95
        painter.fillRect(QRectF(band_left, plot.top(), band_right - band_left, plot.height()), QColor(74, 222, 128, 18))

        painter.setPen(QPen(QColor(T.GRID), 1))
        for index in range(6):
            rpm = self._redline * index / 5
            x = plot.left() + plot.width() * index / 5
            painter.drawLine(x, plot.top(), x, plot.bottom())
            painter.setPen(QColor(T.TEXT_FAINT))
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(QRectF(x - 24, plot.bottom() + 5, 48, 18), Qt.AlignCenter, f"{rpm / 1000:.1f}k")
            painter.setPen(QPen(QColor(T.GRID), 1))

        if not self._rows:
            painter.setPen(QColor(T.TEXT_FAINT))
            painter.drawText(plot, Qt.AlignCenter, "Transmission data unavailable")
            return

        row_height = min(30.0, plot.height() / max(1, len(self._rows)))
        # Bars are speeds, so they scale against the fastest gear. Dividing km/h by the rpm
        # limiter drew every bar at a few percent of the width.
        fastest = max((float(row.get("top_speed_kph") or 0.0) for row in self._rows), default=0.0) or 1.0
        for index, row in enumerate(self._rows):
            y = plot.top() + index * row_height + 3
            width = plot.width() * float(row.get("top_speed_kph") or 0.0) / fastest
            bar = QRectF(plot.left(), y, max(2.0, width), row_height - 7)
            active = row.get("gear") == self._live_gear
            painter.fillRect(bar, QColor(T.ACCENT if active else T.INFO))
            painter.setPen(QColor(T.TEXT))
            painter.setFont(QFont("Segoe UI", 9, 650))
            painter.drawText(QRectF(4, y, 45, row_height - 7), Qt.AlignRight | Qt.AlignVCenter, f"G{row.get('gear')}")
            painter.setPen(QColor(T.TEXT))
            painter.drawText(bar.adjusted(8, 0, -8, 0), Qt.AlignLeft | Qt.AlignVCenter, f"{row.get('top_speed_kph', 0):.0f} km/h")
            landed = float(row.get("shift_landing_rpm") or 0.0)
            if landed > 0.0:
                marker_x = plot.left() + plot.width() * min(self._redline, landed) / self._redline
                painter.setPen(QPen(QColor(T.WARN), 2))
                painter.drawLine(marker_x, y, marker_x, y + row_height - 7)
            optimal = float(row.get("optimal_shift_rpm") or 0.0)
            if optimal > 0.0:
                marker_x = plot.left() + plot.width() * min(self._redline, optimal) / self._redline
                painter.setPen(QPen(QColor(T.ACCENT_BRIGHT), 1))
                painter.drawLine(marker_x, y + 2, marker_x, y + row_height - 9)
