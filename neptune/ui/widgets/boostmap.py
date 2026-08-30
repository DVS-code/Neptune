"""An editable boost table.

A grid of cells with heat-map colouring, a live cursor on the cell the engine is currently in, and
the bulk operations a table needs to be usable: scale a selection, interpolate between its ends,
smooth, flatten.

Axes are RPM across, and optionally load down. `set_rows()` switches between the one-row RPM table
and the full grid.

Selection: click a cell, drag to extend, shift-click to extend from the anchor. Arrow keys move,
+/- nudge, and typing a number sets the selection directly.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from neptune.ui import theme as T

COLUMNS = 10
MAX_ROWS = 6
MIN_MULT = 0.0
MAX_MULT = 4.0
DEFAULT_MULT = 1.0
NUDGE = 0.05

LABEL_LEFT = 52
LABEL_BOTTOM = 22
CELL_MIN_HEIGHT = 26
MIN_HEIGHT = 120


COLD = QColor(58, 104, 178)
NEUTRAL = QColor(46, 50, 58)
WARM = QColor(198, 132, 48)
HOT = QColor(206, 62, 62)


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


def heat(value: float) -> QColor:
    """Colour for a multiplier: blue below 1.0, orange to red above."""
    if value < DEFAULT_MULT:
        return _blend(COLD, NEUTRAL, value / DEFAULT_MULT if DEFAULT_MULT else 1.0)
    span = MAX_MULT - DEFAULT_MULT
    t = (value - DEFAULT_MULT) / span if span else 0.0
    return _blend(NEUTRAL, WARM, t * 2) if t < 0.5 else _blend(WARM, HOT, (t - 0.5) * 2)


class BoostMap(QWidget):
    """An editable boost multiplier table."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(MIN_HEIGHT)

        self._rows = 1
        self._cells = [[DEFAULT_MULT] * COLUMNS]
        self._max_rpm = 8000.0
        self._live_rpm: float | None = None
        self._live_load: float | None = None

        self._anchor: tuple[int, int] | None = None
        self._cursor: tuple[int, int] | None = None
        self._dragging = False
        self._typed = ""

    def rows(self) -> int:
        return self._rows

    def set_rows(self, rows: int) -> None:
        """Switch between a 1D RPM table and a 2D RPM x load grid."""
        rows = max(1, min(MAX_ROWS, int(rows)))
        if rows == self._rows:
            return
        old = self._cells

        self._cells = [
            list(old[min(index, len(old) - 1)]) if index < len(old) else list(old[-1])
            for index in range(rows)
        ]
        self._rows = rows
        self._cursor = None
        self._anchor = None
        self.updateGeometry()
        self.update()

    def values(self) -> list[list[float]]:
        return [list(row) for row in self._cells]

    def flat(self) -> list[float]:
        """The table as one list, row-major — what gets persisted."""
        return [value for row in self._cells for value in row]

    def set_flat(self, values, rows: int | None = None) -> None:
        if rows:
            self._rows = max(1, min(MAX_ROWS, int(rows)))
        table = []
        for row in range(self._rows):
            line = []
            for column in range(COLUMNS):
                index = row * COLUMNS + column
                try:
                    value = float(values[index])
                except (TypeError, ValueError, IndexError):
                    value = DEFAULT_MULT
                if value != value:
                    value = DEFAULT_MULT
                line.append(max(MIN_MULT, min(MAX_MULT, value)))
            table.append(line)
        self._cells = table
        self.update()

    def set_max_rpm(self, rpm: float) -> None:
        if rpm and rpm > 500:
            self._max_rpm = float(rpm)
            self.update()

    def set_live(self, rpm: float | None, load: float | None = None) -> None:
        self._live_rpm = rpm
        self._live_load = load
        self.update()

    def multiplier_at(self, rpm: float, load: float | None = None) -> float:
        return multiplier_at(self.flat(), self._rows, rpm, self._max_rpm, load)

    def _selection(self) -> list[tuple[int, int]]:
        if self._cursor is None:
            return []
        anchor = self._anchor or self._cursor
        r0, r1 = sorted((anchor[0], self._cursor[0]))
        c0, c1 = sorted((anchor[1], self._cursor[1]))
        return [(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]

    def _apply_to_selection(self, function) -> None:
        cells = self._selection() or [(r, c) for r in range(self._rows) for c in range(COLUMNS)]
        for row, column in cells:
            value = function(self._cells[row][column])
            self._cells[row][column] = max(MIN_MULT, min(MAX_MULT, value))
        self.update()
        self.changed.emit()

    def scale_selection(self, factor: float) -> None:
        self._apply_to_selection(lambda v: v * factor)

    def add_selection(self, delta: float) -> None:
        self._apply_to_selection(lambda v: v + delta)

    def set_selection(self, value: float) -> None:
        self._apply_to_selection(lambda _v: value)

    def interpolate_selection(self) -> None:
        """Straight-line fill between the ends of the selection, per row.

        The operation that makes a table quick to build: set two cells, select across, interpolate.
        """
        cells = self._selection()
        if len(cells) < 3:
            return
        rows = sorted({r for r, _ in cells})
        columns = sorted({c for _, c in cells})
        if len(columns) >= 3:
            for row in rows:
                first, last = columns[0], columns[-1]
                start, end = self._cells[row][first], self._cells[row][last]
                span = last - first
                for column in columns:
                    t = (column - first) / span
                    self._cells[row][column] = start + (end - start) * t
        elif len(rows) >= 3:
            for column in columns:
                first, last = rows[0], rows[-1]
                start, end = self._cells[first][column], self._cells[last][column]
                span = last - first
                for row in rows:
                    t = (row - first) / span
                    self._cells[row][column] = start + (end - start) * t
        self.update()
        self.changed.emit()

    def smooth_selection(self) -> None:
        """Three-point average along each row, so a spiky table settles."""
        cells = self._selection() or [(r, c) for r in range(self._rows) for c in range(COLUMNS)]
        rows = sorted({r for r, _ in cells})
        columns = sorted({c for _, c in cells})
        if len(columns) < 3:
            return
        for row in rows:
            source = list(self._cells[row])
            for column in columns[1:-1]:
                self._cells[row][column] = (
                    source[column - 1] + source[column] * 2 + source[column + 1]
                ) / 4.0
        self.update()
        self.changed.emit()

    def flatten(self) -> None:
        self._cells = [[DEFAULT_MULT] * COLUMNS for _ in range(self._rows)]
        self.update()
        self.changed.emit()

    def sizeHint(self):
        from PySide6.QtCore import QSize

        return QSize(520, LABEL_BOTTOM + CELL_MIN_HEIGHT * self._rows + 8)

    def minimumSizeHint(self):
        return self.sizeHint()

    def _grid(self) -> QRectF:
        return QRectF(
            LABEL_LEFT,
            0,
            max(1, self.width() - LABEL_LEFT - 4),
            max(1, self.height() - LABEL_BOTTOM),
        )

    def _cell_rect(self, row: int, column: int) -> QRectF:
        grid = self._grid()
        width = grid.width() / COLUMNS
        height = grid.height() / self._rows
        return QRectF(grid.left() + column * width, grid.top() + row * height, width, height)

    def _cell_at(self, position) -> tuple[int, int] | None:
        grid = self._grid()
        if not grid.contains(position):
            return None
        column = int((position.x() - grid.left()) / (grid.width() / COLUMNS))
        row = int((position.y() - grid.top()) / (grid.height() / self._rows))
        if 0 <= row < self._rows and 0 <= column < COLUMNS:
            return (row, column)
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        cell = self._cell_at(event.position())
        if cell is None:
            return
        self.setFocus()
        self._typed = ""
        if event.modifiers() & Qt.ShiftModifier and self._anchor is not None:
            self._cursor = cell
        else:
            self._anchor = cell
            self._cursor = cell
        self._dragging = True
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging:
            return
        cell = self._cell_at(event.position())
        if cell is not None and cell != self._cursor:
            self._cursor = cell
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False

    def wheelEvent(self, event) -> None:
        if self._cursor is None:
            return
        steps = event.angleDelta().y() / 120.0
        self.add_selection(NUDGE * steps)
        event.accept()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if self._cursor is None:
            self._cursor = self._anchor = (0, 0)

        moves = {
            Qt.Key_Left: (0, -1),
            Qt.Key_Right: (0, 1),
            Qt.Key_Up: (-1, 0),
            Qt.Key_Down: (1, 0),
        }
        if key in moves:
            dr, dc = moves[key]
            row = max(0, min(self._rows - 1, self._cursor[0] + dr))
            column = max(0, min(COLUMNS - 1, self._cursor[1] + dc))
            self._cursor = (row, column)
            if not (event.modifiers() & Qt.ShiftModifier):
                self._anchor = self._cursor
            self._typed = ""
            self.update()
            return

        if key in (Qt.Key_Plus, Qt.Key_Equal):
            self.add_selection(NUDGE)
            return
        if key in (Qt.Key_Minus, Qt.Key_Underscore):
            self.add_selection(-NUDGE)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self._typed:
                try:
                    self.set_selection(float(self._typed))
                except ValueError:
                    pass
                self._typed = ""
            return
        if key == Qt.Key_Backspace:
            self._typed = self._typed[:-1]
            self.update()
            return

        text = event.text()
        if text and (text.isdigit() or text == "."):
            self._typed += text
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        grid = self._grid()
        selection = set(self._selection())

        live_cell = None
        if self._live_rpm is not None and self._max_rpm > 0:
            column = int(max(0.0, min(0.999, self._live_rpm / self._max_rpm)) * COLUMNS)
            row = 0
            if self._rows > 1 and self._live_load is not None:
                row = int(max(0.0, min(0.999, self._live_load)) * self._rows)
            live_cell = (row, column)

        small = QFont("Segoe UI", 8)
        cell_font = QFont("Segoe UI", 9)

        for row in range(self._rows):
            for column in range(COLUMNS):
                rect = self._cell_rect(row, column)
                value = self._cells[row][column]
                painter.fillRect(rect, heat(value))

                if (row, column) in selection:
                    painter.fillRect(rect, QColor(255, 255, 255, 34))

                painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
                painter.drawRect(rect)

                painter.setFont(cell_font)
                painter.setPen(QColor(240, 242, 246))
                painter.drawText(rect, Qt.AlignCenter, f"{value:.2f}")

        if live_cell is not None and live_cell[0] < self._rows and live_cell[1] < COLUMNS:
            painter.setPen(QPen(QColor(T.ACCENT_BRIGHT), 2))
            painter.drawRect(self._cell_rect(*live_cell).adjusted(1, 1, -1, -1))

        if selection:
            rows = sorted({r for r, _ in selection})
            columns = sorted({c for _, c in selection})
            top_left = self._cell_rect(rows[0], columns[0])
            bottom_right = self._cell_rect(rows[-1], columns[-1])
            painter.setPen(QPen(QColor(255, 255, 255, 190), 2))
            painter.drawRect(
                QRectF(
                    top_left.left(),
                    top_left.top(),
                    bottom_right.right() - top_left.left(),
                    bottom_right.bottom() - top_left.top(),
                ).adjusted(1, 1, -1, -1)
            )

        painter.setFont(small)
        painter.setPen(QColor(T.TEXT_FAINT))
        for column in range(COLUMNS):
            rect = self._cell_rect(0, column)
            rpm = self._max_rpm * (column + 0.5) / COLUMNS
            painter.drawText(
                QRectF(rect.left(), grid.bottom() + 2, rect.width(), LABEL_BOTTOM),
                Qt.AlignCenter,
                f"{rpm / 1000:.1f}k",
            )
        if self._rows > 1:
            for row in range(self._rows):
                rect = self._cell_rect(row, 0)
                share = 100 - int(100 * row / self._rows)
                painter.drawText(
                    QRectF(0, rect.top(), LABEL_LEFT - 6, rect.height()),
                    Qt.AlignRight | Qt.AlignVCenter,
                    f"{share}%",
                )
        else:
            painter.drawText(
                QRectF(0, grid.top(), LABEL_LEFT - 6, grid.height()),
                Qt.AlignRight | Qt.AlignVCenter,
                "boost",
            )

        if self._typed:
            painter.setPen(QColor(T.ACCENT_BRIGHT))
            painter.drawText(
                QRectF(grid.left(), grid.top(), grid.width(), 18),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"  typing {self._typed} — Enter to set",
            )


def multiplier_at(flat, rows: int, rpm: float, max_rpm: float, load: float | None = None) -> float:
    """Look the table up at an RPM (and load), interpolating along the RPM axis.

    Module-level so the feature can evaluate the table without touching the widget.
    """
    if not flat or not max_rpm or max_rpm <= 0:
        return DEFAULT_MULT
    rows = max(1, rows)
    row = 0
    if rows > 1 and load is not None:
        row = int(max(0.0, min(0.999, load)) * rows)
    base = row * COLUMNS
    line = flat[base : base + COLUMNS]
    if len(line) < COLUMNS:
        return DEFAULT_MULT

    position = max(0.0, min(1.0, rpm / max_rpm)) * COLUMNS - 0.5
    if position <= 0:
        return line[0]
    if position >= COLUMNS - 1:
        return line[-1]
    low = int(position)
    weight = position - low
    return line[low] * (1.0 - weight) + line[low + 1] * weight
