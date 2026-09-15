"""The DYNO panel floating over the game."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from neptune.ui.gamewindow import GameWindowTracker
from neptune.ui.widgets.dynograph import DynoGraph

FRAME_MS = 90
SYNC_MS = 250

WIDTH = 500
GRAPH_HEIGHT = 286
HEIGHT_GRAPH = 372
HEIGHT_NUMBERS = 146
DEFAULT_POSITION = (0.04, 0.10)
RADIUS = 12

BACKDROP = QColor(18, 20, 24, 228)
BORDER = QColor(255, 255, 255, 34)
TEXT = QColor(238, 240, 245)
MUTED = QColor(150, 156, 168)
LIVE = QColor(120, 214, 255)
POWER = QColor(190, 135, 255)


class DynoOverlay(QWidget):
    """A draggable, click-through-by-default dyno graph and number readout."""

    def __init__(self, parent=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)

        self._tracker: GameWindowTracker | None = None
        self._vehicle = None
        self._position = DEFAULT_POSITION
        self._mode = "Graph"
        self._locked = True
        self._drag_origin: QPoint | None = None
        self._on_moved = None

        self._graph = DynoGraph(self)
        self._graph.setFixedHeight(GRAPH_HEIGHT)
        self._rpm = "--"
        self._torque = "--"
        self._power = "--"
        self._boost = "--"
        self._gear = "--"
        self._status = "Attach to the game and load a car"

        self._frames = QTimer(self)
        self._frames.setInterval(FRAME_MS)
        self._frames.timeout.connect(self.update)

        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(SYNC_MS)
        self._sync_timer.timeout.connect(self._sync)

        self._resize_for_mode()

    def set_vehicle(self, vehicle) -> None:
        self._vehicle = vehicle

    def set_mode(self, mode: str) -> None:
        self._mode = mode if mode in ("Graph", "Numbers") else "Graph"
        self._resize_for_mode()
        self._reposition()
        self.update()

    def set_position(self, relative_x: float, relative_y: float) -> None:
        self._position = (
            max(0.0, min(1.0, float(relative_x))),
            max(0.0, min(1.0, float(relative_y))),
        )
        self._reposition()

    def position(self) -> tuple[float, float]:
        return self._position

    def set_moved_callback(self, callback) -> None:
        self._on_moved = callback

    def set_locked(self, locked: bool) -> None:
        self._locked = bool(locked)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, self._locked)
        if self._tracker is not None:
            self._tracker.set_click_through(self._locked)
        self.setCursor(QCursor(Qt.ArrowCursor if self._locked else Qt.OpenHandCursor))

    def update_values(
        self,
        torque,
        power,
        rpm_per_index,
        rpm,
        redline,
        show_torque,
        show_power,
        peak_torque,
        peak_power,
        boost,
        gear,
        status,
        torque_unit="Nm",
        power_unit="hp",
    ) -> None:
        self._graph.set_data(
            torque,
            power,
            rpm_per_index,
            rpm,
            redline,
            show_torque,
            show_power,
            torque_unit=torque_unit,
            power_unit=power_unit,
        )
        self._graph.setFixedHeight(GRAPH_HEIGHT)
        self._rpm = f"{float(rpm):.0f}" if rpm is not None else "--"
        self._torque = peak_torque or "--"
        self._power = peak_power or "--"
        self._boost = boost or "--"
        self._gear = gear or "--"
        self._status = status or "Waiting for dyno data"
        self.update()

    def start(self, pid: int | None) -> None:
        self.show()
        handle = int(self.winId())
        self._tracker = GameWindowTracker(handle, pid)
        self._tracker.make_overlay(click_through=self._locked)
        self._tracker.attach()
        self._reposition()
        self._frames.start()
        self._sync_timer.start()

    def stop(self) -> None:
        self._frames.stop()
        self._sync_timer.stop()
        if self._tracker is not None:
            self._tracker.detach()
            self._tracker = None
        self.hide()

    def _resize_for_mode(self) -> None:
        self.resize(WIDTH, HEIGHT_GRAPH if self._mode == "Graph" else HEIGHT_NUMBERS)
        self._graph.setVisible(self._mode == "Graph")
        if self._mode == "Graph":
            self._graph.setGeometry(10, 36, WIDTH - 20, GRAPH_HEIGHT)

    def _reposition(self) -> None:
        if self._tracker is None:
            return
        placed = self._tracker.anchor(
            self._position[0], self._position[1], self.width(), self.height()
        )
        if placed is not None:
            self.move(placed[0], placed[1])

    def _sync(self) -> None:
        if self._tracker is None or self._drag_origin is not None:
            return
        if not self._tracker.game_hwnd:
            self._tracker.attach()
        bounds = self._tracker.sync()
        if bounds.valid:
            self._reposition()

    def mousePressEvent(self, event) -> None:
        if self._locked or event.button() != Qt.LeftButton:
            return
        self._drag_origin = event.globalPosition().toPoint() - self.pos()
        self.setCursor(QCursor(Qt.ClosedHandCursor))

    def mouseMoveEvent(self, event) -> None:
        if self._locked or self._drag_origin is None:
            return
        self.move(event.globalPosition().toPoint() - self._drag_origin)

    def mouseReleaseEvent(self, event) -> None:
        if self._locked or event.button() != Qt.LeftButton:
            return
        self._drag_origin = None
        self.setCursor(QCursor(Qt.OpenHandCursor))
        self._store_position()

    def _store_position(self) -> None:
        if self._tracker is None:
            return
        bounds = self._tracker.rect
        if not bounds.valid:
            return
        span_x = max(1, bounds.width - self.width())
        span_y = max(1, bounds.height - self.height())
        self._position = (
            max(0.0, min(1.0, (self.x() - bounds.x) / span_x)),
            max(0.0, min(1.0, (self.y() - bounds.y) / span_y)),
        )
        if self._on_moved is not None:
            self._on_moved(self._position)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, WIDTH, self.height()), RADIUS, RADIUS)
        painter.fillPath(path, BACKDROP)
        painter.setPen(BORDER)
        painter.drawPath(path)

        painter.setPen(TEXT)
        painter.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
        painter.drawText(16, 23, "DYNO")
        if self._mode == "Numbers":
            self._draw_numbers(painter)
            return

        painter.setPen(MUTED)
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(16, 345, f"RPM {self._rpm}")
        painter.setPen(LIVE)
        painter.drawText(118, 345, f"Torque {self._torque}")
        painter.setPen(POWER)
        painter.drawText(270, 345, f"Power {self._power}")
        painter.setPen(MUTED)
        painter.drawText(16, 362, f"Boost {self._boost}    Gear {self._gear}")
        painter.drawText(220, 362, self._status[:34])

    def _draw_numbers(self, painter: QPainter) -> None:
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(MUTED)
        painter.drawText(18, 56, "RPM")
        painter.drawText(174, 56, "PEAK TORQUE")
        painter.drawText(348, 56, "PEAK POWER")
        painter.setFont(QFont("Segoe UI", 22, QFont.DemiBold))
        painter.setPen(TEXT)
        painter.drawText(18, 84, self._rpm)
        painter.setPen(LIVE)
        painter.drawText(174, 84, self._torque)
        painter.setPen(POWER)
        painter.drawText(348, 84, self._power)
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(MUTED)
        painter.drawText(18, 117, f"Boost {self._boost}    Gear {self._gear}")
        painter.drawText(18, 135, self._status[:68])
