"""The boost gauge face."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QConicalGradient, QFont, QPainter, QPainterPath,
                           QPen, QRadialGradient)
from PySide6.QtWidgets import QWidget

ACCENTS = {
    'blue': '#3b82f6',
    'purple': '#a855f7',
    'cyan': '#22d3ee',
    'green': '#4ade80',
    'amber': '#fbbf24',
    'red': '#f87171',
    'white': '#e5e7eb',
}
ACCENT_ORDER = ('blue', 'purple', 'cyan', 'green', 'amber', 'red', 'white')

MODES = ('Dial', 'Digital', 'Bar')

BEZEL = '#0a0b0d'
FACE = '#0e1014'
TICK = '#4b5563'
TEXT_DIM = '#9ba1ad'

START_ANGLE = 216.0
SWEEP_ANGLE = 252.0
MAJOR_TICKS = 8
MINOR_PER_MAJOR = 2
NEEDLE_SMOOTHING = 0.28


class GaugeFace(QWidget):
    """Draws live boost as a dial, a digital readout or a bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._value = 0.0
        self._shown = 0.0
        self._peak = 0.0
        self._minimum = -15.0
        self._maximum = 30.0
        self._unit = 'psi'
        self._label = 'BOOST'
        self._gear = None
        self._rpm = None

        self.mode = 'Dial'
        self.accent = 'blue'
        self.glow = True
        self.show_peak = True
        self.show_gear = False
        self.show_handle = False

    def peak(self) -> float:
        return self._peak

    def set_range(self, minimum: float, maximum: float) -> None:
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self.update()

    def set_unit(self, unit: str) -> None:
        self._unit = unit
        self.update()

    def set_value(self, value: float | None, gear=None, rpm=None) -> None:
        target = 0.0 if value is None else float(value)
        self._value = target
        self._shown += (target - self._shown) * NEEDLE_SMOOTHING
        if target > self._peak:
            self._peak = target
        self._gear = gear
        self._rpm = rpm
        self.update()

    def reset_peak(self) -> None:
        self._peak = self._value
        self.update()

    def _colour(self) -> QColor:
        return QColor(ACCENTS.get(self.accent, ACCENTS['blue']))

    def _fraction(self, value: float) -> float:
        span = self._maximum - self._minimum
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (value - self._minimum) / span))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        if self.mode == 'Digital':
            self._paint_digital(painter)
        elif self.mode == 'Bar':
            self._paint_bar(painter)
        else:
            self._paint_dial(painter)

        if self.show_handle:
            self._paint_handle(painter)

    def _paint_handle(self, painter: QPainter) -> None:
        """A dashed outline shown while the gauge can be dragged."""
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        pen = QPen(self._colour().lighter(140), 1.6, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        radius = min(rect.width(), rect.height()) * (
            0.5 if self.mode == 'Dial' else 0.12)
        painter.drawRoundedRect(rect, radius, radius)

    def _paint_dial(self, painter: QPainter) -> None:
        side = min(self.width(), self.height())
        if side < 40:
            return
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = side / 2.0 - 2.0
        accent = self._colour()

        bezel = QRadialGradient(centre.x(), centre.y() - radius * 0.3, radius * 1.5)
        bezel.setColorAt(0.0, QColor('#22262e'))
        bezel.setColorAt(0.55, QColor('#14171c'))
        bezel.setColorAt(1.0, QColor('#050608'))
        painter.setPen(Qt.NoPen)
        painter.setBrush(bezel)
        painter.drawEllipse(centre, radius, radius)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor('#2c313a'), max(1.0, radius * 0.012)))
        painter.drawEllipse(centre, radius * 0.985, radius * 0.985)

        inner = radius * 0.88
        face = QRadialGradient(centre.x(), centre.y() - inner * 0.25, inner * 1.4)
        face.setColorAt(0.0, QColor('#15181e'))
        face.setColorAt(1.0, QColor('#080a0d'))
        painter.setPen(Qt.NoPen)
        painter.setBrush(face)
        painter.drawEllipse(centre, inner, inner)

        track_radius = radius * 0.66
        track = QRectF(centre.x() - track_radius, centre.y() - track_radius,
                       track_radius * 2, track_radius * 2)
        thickness = max(2.5, radius * 0.055)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor('#242832'), thickness, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(track, int(-START_ANGLE * 16), int(SWEEP_ANGLE * 16))

        filled = self._fraction(self._shown) * SWEEP_ANGLE
        if filled > 0.5:
            if self.glow:
                for spread, alpha in ((2.6, 26), (1.7, 46)):
                    halo = QColor(accent)
                    halo.setAlpha(alpha)
                    painter.setPen(QPen(halo, thickness * spread,
                                        Qt.SolidLine, Qt.RoundCap))
                    painter.drawArc(track, int(-START_ANGLE * 16), int(-filled * 16))

            sweep = QConicalGradient(centre, START_ANGLE)
            sweep.setColorAt(0.0, accent.lighter(145))
            sweep.setColorAt(0.35, accent)
            sweep.setColorAt(0.7, accent.darker(130))
            painter.setPen(QPen(sweep, thickness, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(track, int(-START_ANGLE * 16), int(-filled * 16))

        self._paint_ticks(painter, centre, radius)
        self._paint_needle(painter, centre, radius, accent)

        painter.setPen(QColor(accent))
        value_font = QFont(painter.font())
        value_font.setPointSizeF(max(9.0, radius * 0.30))
        value_font.setWeight(QFont.Bold)
        painter.setFont(value_font)
        painter.drawText(QRectF(centre.x() - radius, centre.y() - radius * 0.30,
                                radius * 2, radius * 0.46),
                         Qt.AlignCenter, f'{self._value:.1f}')

        painter.setPen(QColor(TEXT_DIM))
        small = QFont(painter.font())
        small.setPointSizeF(max(5.5, radius * 0.105))
        small.setWeight(QFont.DemiBold)
        painter.setFont(small)
        painter.drawText(QRectF(centre.x() - radius, centre.y() + radius * 0.16,
                                radius * 2, radius * 0.22),
                         Qt.AlignCenter, self._unit.upper())

        self._paint_extras(painter, centre, radius, accent)

    def _paint_ticks(self, painter: QPainter, centre: QPointF, radius: float) -> None:
        total = MAJOR_TICKS * MINOR_PER_MAJOR
        number_font = QFont(painter.font())
        number_font.setPointSizeF(max(5.0, radius * 0.105))
        number_font.setWeight(QFont.DemiBold)

        for index in range(total + 1):
            fraction = index / total
            angle = math.radians(START_ANGLE - fraction * SWEEP_ANGLE)
            major = index % MINOR_PER_MAJOR == 0
            cosine = math.cos(angle)
            sine = math.sin(angle)

            outer = radius * 0.735
            inner = radius * (0.695 if major else 0.715)
            painter.setPen(QPen(QColor('#5b6472' if major else '#343b46'),
                                max(1.0, radius * (0.016 if major else 0.010))))
            painter.drawLine(QPointF(centre.x() + cosine * inner,
                                     centre.y() - sine * inner),
                             QPointF(centre.x() + cosine * outer,
                                     centre.y() - sine * outer))

            if not major or radius < 58:
                continue
            label = self._minimum + fraction * (self._maximum - self._minimum)
            painter.setFont(number_font)
            painter.setPen(QColor('#79828f'))
            spot = radius * 0.845
            box = radius * 0.17
            painter.drawText(QRectF(centre.x() + cosine * spot - box,
                                    centre.y() - sine * spot - box * 0.6,
                                    box * 2, box * 1.2),
                             Qt.AlignCenter, f'{label:.0f}')

    def _paint_needle(self, painter: QPainter, centre: QPointF, radius: float,
                      accent: QColor) -> None:
        angle = math.radians(START_ANGLE - self._fraction(self._shown) * SWEEP_ANGLE)
        inner = radius * 0.44
        outer = radius * 0.635
        span = math.radians(4.5)

        path = QPainterPath(QPointF(centre.x() + math.cos(angle) * outer,
                                    centre.y() - math.sin(angle) * outer))
        path.lineTo(QPointF(centre.x() + math.cos(angle + span) * inner,
                            centre.y() - math.sin(angle + span) * inner))
        path.lineTo(QPointF(centre.x() + math.cos(angle - span) * inner,
                            centre.y() - math.sin(angle - span) * inner))
        path.closeSubpath()

        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawPath(path)

        if self.show_peak and self._peak > self._minimum:
            peak_angle = math.radians(
                START_ANGLE - self._fraction(self._peak) * SWEEP_ANGLE)
            painter.setPen(QPen(QColor(accent).lighter(170),
                                max(1.6, radius * 0.022)))
            painter.drawLine(
                QPointF(centre.x() + math.cos(peak_angle) * radius * 0.60,
                        centre.y() - math.sin(peak_angle) * radius * 0.60),
                QPointF(centre.x() + math.cos(peak_angle) * radius * 0.715,
                        centre.y() - math.sin(peak_angle) * radius * 0.715))

    def _paint_extras(self, painter: QPainter, centre: QPointF, radius: float,
                      accent: QColor) -> None:
        caption = QFont(painter.font())
        caption.setPointSizeF(max(5.0, radius * 0.095))
        caption.setWeight(QFont.DemiBold)
        painter.setFont(caption)
        painter.setPen(QColor('#69727f'))
        painter.drawText(QRectF(centre.x() - radius, centre.y() - radius * 0.56,
                                radius * 2, radius * 0.22),
                         Qt.AlignCenter, self._label)

        if not self.show_gear or self._gear is None:
            return
        if not 0 < int(self._gear) < 11:
            return

        painter.setPen(QColor(accent))
        gear_font = QFont(painter.font())
        gear_font.setPointSizeF(max(7.0, radius * 0.17))
        gear_font.setWeight(QFont.Bold)
        painter.setFont(gear_font)
        painter.drawText(QRectF(centre.x() - radius, centre.y() + radius * 0.40,
                                radius * 2, radius * 0.26),
                         Qt.AlignCenter, str(int(self._gear)))

    def _paint_digital(self, painter: QPainter) -> None:
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        accent = self._colour()
        radius = min(rect.width(), rect.height()) * 0.12

        painter.setPen(QPen(QColor(BEZEL).lighter(160), 1.0))
        painter.setBrush(QColor(FACE))
        painter.drawRoundedRect(rect, radius, radius)

        painter.setPen(QColor(TEXT_DIM))
        label_font = QFont(painter.font())
        label_font.setPointSizeF(max(6.0, rect.height() * 0.13))
        label_font.setWeight(QFont.DemiBold)
        painter.setFont(label_font)
        painter.drawText(rect.adjusted(0, rect.height() * 0.10, 0, 0),
                         Qt.AlignHCenter | Qt.AlignTop, self._label)

        if self.glow:
            halo = QColor(accent)
            halo.setAlpha(60)
            painter.setPen(halo)
            glow_font = QFont(painter.font())
            glow_font.setPointSizeF(max(14.0, rect.height() * 0.40))
            glow_font.setWeight(QFont.Bold)
            painter.setFont(glow_font)
            painter.drawText(rect.adjusted(1.5, 1.5, 1.5, 1.5), Qt.AlignCenter,
                             f'{self._value:.1f}')

        painter.setPen(accent)
        value_font = QFont(painter.font())
        value_font.setPointSizeF(max(14.0, rect.height() * 0.40))
        value_font.setWeight(QFont.Bold)
        painter.setFont(value_font)
        painter.drawText(rect, Qt.AlignCenter, f'{self._value:.1f}')

        parts = [self._unit.upper()]
        if self.show_peak:
            parts.append(f'PEAK {self._peak:.1f}')
        if self.show_gear and self._gear is not None and 0 < int(self._gear) < 11:
            parts.append(f'GEAR {int(self._gear)}')
        footer = '   '.join(parts)

        available = rect.width() * 0.88
        footer_font = QFont(label_font)
        size = label_font.pointSizeF()
        while size > 5.0:
            footer_font.setPointSizeF(size)
            painter.setFont(footer_font)
            if painter.fontMetrics().horizontalAdvance(footer) <= available:
                break
            size -= 0.5

        painter.setPen(QColor(TEXT_DIM))
        painter.setFont(footer_font)
        painter.drawText(rect.adjusted(0, 0, 0, -rect.height() * 0.08),
                         Qt.AlignHCenter | Qt.AlignBottom, footer)

    def _paint_bar(self, painter: QPainter) -> None:
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        accent = self._colour()
        radius = min(rect.width(), rect.height()) * 0.10

        painter.setPen(QPen(QColor(BEZEL).lighter(160), 1.0))
        painter.setBrush(QColor(FACE))
        painter.drawRoundedRect(rect, radius, radius)

        painter.setPen(QColor(TEXT_DIM))
        label_font = QFont(painter.font())
        label_font.setPointSizeF(max(6.0, rect.height() * 0.15))
        label_font.setWeight(QFont.DemiBold)
        painter.setFont(label_font)
        painter.drawText(rect.adjusted(rect.width() * 0.06, 0, 0,
                                       -rect.height() * 0.10),
                         Qt.AlignLeft | Qt.AlignVCenter, self._label)

        painter.setPen(accent)
        value_font = QFont(painter.font())
        value_font.setPointSizeF(max(9.0, rect.height() * 0.26))
        value_font.setWeight(QFont.Bold)
        painter.setFont(value_font)
        painter.drawText(rect.adjusted(0, 0, -rect.width() * 0.06,
                                       -rect.height() * 0.10),
                         Qt.AlignRight | Qt.AlignVCenter,
                         f'{self._value:.1f} {self._unit}')

        track = QRectF(rect.left() + rect.width() * 0.06,
                       rect.bottom() - rect.height() * 0.26,
                       rect.width() * 0.88, rect.height() * 0.10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(TICK).darker(180))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)

        filled = QRectF(track)
        filled.setWidth(track.width() * self._fraction(self._shown))
        if filled.width() > 1:
            painter.setBrush(accent)
            painter.drawRoundedRect(filled, filled.height() / 2, filled.height() / 2)
