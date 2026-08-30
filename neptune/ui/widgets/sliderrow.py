"""A labelled slider with an editable numeric field."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import LineEdit

from neptune.ui.widgets.helpmark import HelpMark
from neptune.ui.widgets.slider import Slider

LABEL_WIDTH = 148
FIELD_WIDTH = 84
UNIT_WIDTH = 34


class SliderRow(QWidget):
    """One setting: name, slider, typed value and unit."""

    changed = Signal(float)

    def __init__(
        self,
        label: str,
        minimum: float,
        maximum: float,
        value: float,
        step: float = 0.01,
        decimals: int = 2,
        unit: str = "",
        hint: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._min = float(minimum)
        self._max = float(maximum)
        self._step = float(step) if step else 0.0
        self._decimals = int(decimals)
        self._value = self._clamp(float(value))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        name = QWidget()
        name.setFixedWidth(LABEL_WIDTH)
        name_row = QHBoxLayout(name)
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(6)

        self._label = QLabel(label)
        self._label.setObjectName("RowLabel")
        name_row.addWidget(self._label)

        self._help = HelpMark(hint)
        name_row.addWidget(self._help)
        name_row.addStretch(1)
        row.addWidget(name)

        self._slider = Slider(self._to_position(self._value))
        self._slider.moved.connect(self._on_slider)
        row.addWidget(self._slider, 1)

        self._field = LineEdit()
        self._field.setText(self._format(self._value))
        self._field.setObjectName("ValueEdit")
        self._field.setFixedWidth(FIELD_WIDTH)
        self._field.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._field.editingFinished.connect(self._on_field)
        row.addWidget(self._field)

        self._unit = QLabel(unit)
        self._unit.setObjectName("StatUnit")
        self._unit.setFixedWidth(UNIT_WIDTH)
        row.addWidget(self._unit)

        outer.addLayout(row)

    def _clamp(self, value: float) -> float:
        value = max(self._min, min(self._max, float(value)))
        if self._step > 0:
            steps = round((value - self._min) / self._step)
            value = max(self._min, min(self._max, self._min + steps * self._step))
        return value

    def _to_position(self, value: float) -> float:
        span = self._max - self._min
        return (value - self._min) / span if span > 0 else 0.0

    def _from_position(self, position: float) -> float:
        return self._min + position * (self._max - self._min)

    def _format(self, value: float) -> str:
        return f"{value:.{self._decimals}f}"

    def _on_slider(self, position: float) -> None:
        value = self._clamp(self._from_position(position))
        self._field.setText(self._format(value))
        if value == self._value:
            return
        self._value = value
        self.changed.emit(value)

    def _on_field(self) -> None:
        try:
            parsed = float(self._field.text().strip())
        except ValueError:
            self._field.setText(self._format(self._value))
            return
        value = self._clamp(parsed)
        self._field.setText(self._format(value))
        self._slider.set_position(self._to_position(value))
        if value == self._value:
            return
        self._value = value
        self.changed.emit(value)

    def value(self) -> float:
        return self._value

    def set_value(self, value: float, notify: bool = False) -> None:
        value = self._clamp(value)
        self._value = value
        self._slider.set_position(self._to_position(value))
        self._field.setText(self._format(value))
        if notify:
            self.changed.emit(value)

    def set_range(self, minimum: float, maximum: float) -> None:
        self._min = float(minimum)
        self._max = float(maximum)
        self.set_value(self._value)

    def set_unit(self, unit: str) -> None:
        self._unit.setText(unit)

    def set_enabled(self, enabled: bool) -> None:
        self._slider.setEnabled(enabled)
        self._field.setEnabled(enabled)
        self._label.setEnabled(enabled)
