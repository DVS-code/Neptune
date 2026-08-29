"""An animated pill switch."""
from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractButton

from neptune.ui import theme as T

TRACK_W = 40
TRACK_H = 22
KNOB_INSET = 3


class Toggle(QAbstractButton):

    toggled_value = Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(QSize(TRACK_W, TRACK_H))
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b'knob_pos', self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def get_knob_pos(self) -> float:
        return self._pos

    def set_knob_pos(self, value: float) -> None:
        self._pos = value
        self.update()

    knob_pos = Property(float, get_knob_pos, set_knob_pos)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()
        self.toggled_value.emit(checked)

    def set_value(self, checked: bool, notify: bool = False) -> None:
        checked = bool(checked)
        if checked == self.isChecked():
            return
        if notify:
            self.setChecked(checked)
            return
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self._anim.stop()
        self.set_knob_pos(1.0 if checked else 0.0)

    def value(self) -> bool:
        return self.isChecked()

    def sizeHint(self) -> QSize:
        return QSize(TRACK_W, TRACK_H)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        off = QColor(T.BORDER_STRONG)
        on = QColor(T.ACCENT)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        if not self.isEnabled():
            track = QColor(T.BORDER)

        rect = QRectF(0, 0, TRACK_W, TRACK_H)
        path = QPainterPath()
        path.addRoundedRect(rect, TRACK_H / 2, TRACK_H / 2)
        painter.fillPath(path, track)

        radius = (TRACK_H - KNOB_INSET * 2) / 2
        travel = TRACK_W - TRACK_H
        cx = KNOB_INSET + radius + travel * self._pos
        cy = TRACK_H / 2
        knob = QColor(T.TEXT) if self.isEnabled() else QColor(T.TEXT_FAINT)
        painter.setBrush(knob)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))
