"""Dedicated Boost Map 2.0 editor window."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from neptune.ui import theme as T
from neptune.ui.widgets.boostmap import COLUMNS, THROTTLE_ROWS, BoostMap
from neptune.ui.widgets.buttons import Button, PrimaryButton
from neptune.ui.widgets.card import StatStrip
from neptune.ui.widgets.controls import Segmented

AXIS_RPM = "RPM only"
AXIS_THROTTLE = "RPM x throttle"


class BoostMapWorkspace(QWidget):
    """The Boost Map in its own window.

    Editable when opened from Turbo, where `changed` feeds the live map. Read-only when opened
    over a log: that map is a copy nothing saves, so editing it would silently go nowhere.
    """

    changed = Signal()

    def __init__(self, values=None, rows: int = 1, max_rpm: float = 8000.0, parent=None, editable: bool = True):
        super().__init__(parent)
        rows = max(1, rows)
        self._stock = [1.0] * (COLUMNS * rows)
        self._compare = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 20)
        root.setSpacing(14)

        title = QLabel("Boost Map 2.0")
        title.setObjectName("StatValue")
        root.addWidget(title)
        caption = QLabel(
            "RPM × throttle control with live cell context and reversible edits"
            if editable
            else "Read-only view of the logged run. Green cells were hit by the log."
        )
        caption.setStyleSheet(f"color: {T.TEXT_MUTED};")
        root.addWidget(caption)

        self.map = BoostMap()
        self.map.set_rows(rows)
        self.map.set_flat(values or self._stock, rows)
        self.map.set_max_rpm(max_rpm)

        if not editable:
            self.map.setFocusPolicy(Qt.NoFocus)  # typed values edit the selection
            root.addWidget(self.map, 1)
            return

        axis_row = QHBoxLayout()
        axis_label = QLabel("Table")
        axis_label.setStyleSheet(f"color: {T.TEXT_MUTED};")
        axis_row.addWidget(axis_label)
        self._axis = Segmented([AXIS_RPM, AXIS_THROTTLE], AXIS_THROTTLE if rows > 1 else AXIS_RPM)
        self._axis.changed.connect(self._set_axis)
        axis_row.addWidget(self._axis)
        axis_row.addStretch(1)
        root.addLayout(axis_row)

        stats = StatStrip()
        for key, label in (
            ("rpm", "RPM"),
            ("throttle", "Throttle"),
            ("gear", "Gear"),
            ("boost", "Actual boost"),
            ("multiplier", "Map multiplier"),
        ):
            stats.add(key, label, "--")
        self._stats = stats
        root.insertWidget(2, stats)

        self.map.changed.connect(self.changed)
        root.addWidget(self.map, 1)

        tools = QHBoxLayout()
        tools.setSpacing(7)
        operations = (
            ("−5%", lambda: self.map.scale_selection(0.95)),
            ("+5%", lambda: self.map.scale_selection(1.05)),
            ("Interpolate", self.map.interpolate_selection),
            ("Smooth", self.map.smooth_selection),
            ("Flatten", self.map.flatten),
            ("Reset selected", self.map.reset_selected),
            ("Undo", self.map.undo),
            ("Redo", self.map.redo),
        )
        for label, handler in operations:
            button = PrimaryButton(label) if label in ("Undo", "Redo") else Button(label)
            button.clicked.connect(handler)
            tools.addWidget(button)
        tools.addStretch(1)
        root.addLayout(tools)

        compare = Button("Compare stock")
        compare.clicked.connect(self._toggle_stock)
        lower = QHBoxLayout()
        lower.addWidget(compare)
        lower.addStretch(1)
        root.addLayout(lower)

    def _set_axis(self, label: str) -> None:
        """Switch between the single RPM row and the full RPM x throttle grid."""
        rows = THROTTLE_ROWS if label == AXIS_THROTTLE else 1
        if rows == self.map.rows():
            return
        # set_rows copies the existing row down the new ones, so going 1 -> 8 keeps the
        # RPM curve the user already shaped instead of resetting it to flat.
        self.map.set_rows(rows)
        self._stock = [1.0] * (COLUMNS * rows)
        if self._compare:
            self.map.set_comparison(self._stock)
        self.changed.emit()

    def set_table(self, values, rows: int, max_rpm: float | None = None) -> None:
        """Adopt a table loaded elsewhere (a preset or a saved tune) without emitting.

        Silent by design: this is the owner pushing state in, so echoing `changed` back
        would have the owner write its own value over itself.
        """
        rows = max(1, int(rows))
        self.map.set_rows(rows)
        self.map.set_flat(values, rows)
        if max_rpm:
            self.map.set_max_rpm(max_rpm)
        self._stock = [1.0] * (COLUMNS * rows)
        if self._compare:
            self.map.set_comparison(self._stock)
        axis = getattr(self, "_axis", None)
        if axis is not None:
            axis.set_value(AXIS_THROTTLE if rows > 1 else AXIS_RPM)

    def _toggle_stock(self) -> None:
        self._compare = not self._compare
        self.map.set_comparison(self._stock if self._compare else None)

    def values(self) -> list[float]:
        return self.map.flat()

    def set_live(self, rpm=None, throttle=None, gear=None, boost=None, multiplier=None) -> None:
        values = {
            "rpm": f"{rpm:.0f}" if rpm is not None else "--",
            "throttle": f"{throttle * 100:.0f}%" if throttle is not None else "--",
            "gear": str(gear) if gear is not None else "--",
            "boost": f"{boost:.1f}" if boost is not None else "--",
            "multiplier": f"{multiplier:.2f}x" if multiplier is not None else "--",
        }
        for key, value in values.items():
            self._stats.set(key, value)
        self.map.set_live(rpm, None if throttle is None else 1.0 - throttle)
