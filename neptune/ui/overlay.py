"""A floating gauge that sits over the game."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QVBoxLayout, QWidget

from neptune.memory import offsets as O
from neptune.ui.gamewindow import GameWindowTracker
from neptune.ui.widgets.gaugeface import GaugeFace

FRAME_MS = 16
SYNC_MS = 250
CEILING_FRAMES = 30

DEFAULT_SIZE = 210
MIN_SIZE = 120
MAX_SIZE = 420
DEFAULT_POSITION = (0.86, 0.78)

WIDE_MODES = ("Bar",)
WIDE_RATIO = 0.40
DIGITAL_RATIO = 0.62

SCALE_LADDER = (15.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0, 250.0, 400.0, 600.0, 1000.0)


def dimensions_for(mode: str, size: int) -> tuple[int, int]:
    """Get the width and height of the gauge for a given mode and height."""
    if mode in WIDE_MODES:
        return size, max(60, int(size * WIDE_RATIO))
    if mode == "Digital":
        return size, int(size * DIGITAL_RATIO)
    return size, size


def scale_for(value: float, floor: float) -> float:
    """The smallest dial scale that comfortably contains `value`."""
    wanted = max(float(value) * 1.15, float(floor))
    for step in SCALE_LADDER:
        if wanted <= step:
            return step
    return SCALE_LADDER[-1]


class GaugeOverlay(QWidget):
    """A frameless window holding the gauge, driven by its own frame timer."""

    def __init__(self, parent=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.face = GaugeFace()
        layout.addWidget(self.face)

        self._tracker: GameWindowTracker | None = None
        self._vehicle = None
        self._settings = None
        self._pid: int | None = None

        self._size = DEFAULT_SIZE
        self._position = DEFAULT_POSITION
        self._locked = True
        self._auto_range = True
        self._ceiling: float | None = None
        self._blower_peak = -999.0
        self._ceiling_countdown = 0

        self._drag_origin: QPoint | None = None
        self._on_moved = None

        self._frames = QTimer(self)
        self._frames.setInterval(FRAME_MS)
        self._frames.timeout.connect(self._frame)

        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(SYNC_MS)
        self._sync_timer.timeout.connect(self._sync)

        self.resize(*self._dimensions())

    def _dimensions(self) -> tuple[int, int]:
        return dimensions_for(self.face.mode, self._size)

    def set_settings(self, settings) -> None:
        self._settings = settings

    def vehicle(self):
        return self._vehicle

    def set_vehicle(self, vehicle) -> None:
        """Hand the overlay the car so it can read boost at its own frame rate."""
        self._vehicle = vehicle
        if vehicle is None:
            self._ceiling = None
            self._blower_peak = -999.0

    def set_moved_callback(self, callback) -> None:
        self._on_moved = callback

    def set_size(self, size: int) -> None:
        self._size = max(MIN_SIZE, min(MAX_SIZE, int(size)))
        self.resize(*self._dimensions())
        self._reposition()

    def set_position(self, relative_x: float, relative_y: float) -> None:
        self._position = (max(0.0, min(1.0, relative_x)), max(0.0, min(1.0, relative_y)))
        self._reposition()

    def position(self) -> tuple[float, float]:
        return self._position

    def set_mode(self, mode: str) -> None:
        self.face.mode = mode
        self.resize(*self._dimensions())
        self._reposition()
        self.face.update()

    def set_auto_range(self, enabled: bool) -> None:
        self._auto_range = bool(enabled)
        if not self._auto_range:
            self.face.set_range(0.0, SCALE_LADDER[0])

    def set_locked(self, locked: bool) -> None:
        """Locked means clicks pass through to the game; unlocked means draggable."""
        self._locked = bool(locked)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, self._locked)
        if self._tracker is not None:
            self._tracker.set_click_through(self._locked)
        self.face.show_handle = not self._locked
        self.face.update()

    def start(self, pid: int | None) -> None:
        self._pid = pid
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

    def _frame(self) -> None:
        """Read the car and repaint. Runs at the gauge's own frame rate."""
        vehicle = self._vehicle
        if vehicle is None:
            return
        try:
            boost = vehicle.boost_gauge
            if boost is not None:
                value = boost
                if self._settings is not None:
                    value, _unit = self._settings.pressure(boost)
                self.face.set_value(
                    max(0.0, value), gear=vehicle.gear if self.face.show_gear else None
                )

            self._ceiling_countdown -= 1
            if self._ceiling_countdown <= 0:
                self._ceiling_countdown = CEILING_FRAMES
                self._refresh_ceiling(vehicle)
        except Exception:
            return

    def _refresh_ceiling(self, vehicle) -> None:
        """Range the dial off the car's boost ceiling."""
        if not self._auto_range:
            return
        try:
            blower = vehicle.boost_raw_blower
            if blower is not None and blower > self._blower_peak:
                self._blower_peak = blower

            ceiling = None
            if O.is_supercharged(self._blower_peak, vehicle.blower_ceiling):
                ceiling = O.boost_to_gauge(max(self._blower_peak, vehicle.blower_ceiling or 0.0))
            else:
                turbo = vehicle.turbo_block()
                raw = turbo.get("max_boost")
                if raw and not O.is_naturally_aspirated(raw, turbo.get("turbine_limit")):
                    ceiling = O.boost_to_gauge(raw)

            if ceiling is None or ceiling <= 0:
                return
            if self._settings is not None:
                ceiling, _unit = self._settings.pressure(ceiling)

            self._ceiling = ceiling
            top = scale_for(ceiling, SCALE_LADDER[0])
            peak = self.face.peak()
            if peak > ceiling:
                top = scale_for(peak, top)
            self.face.set_range(0.0, top)
        except Exception:
            return

    def _sync(self) -> None:
        if self._tracker is None:
            return
        if self._drag_origin is not None:
            return
        if not self._tracker.game_hwnd:
            self._tracker.attach()
        bounds = self._tracker.sync()
        if bounds.valid:
            self._reposition()

    def _reposition(self) -> None:
        if self._tracker is None:
            return
        width, height = self._dimensions()
        placed = self._tracker.anchor(self._position[0], self._position[1], width, height)
        if placed is None:
            return
        self.move(placed[0], placed[1])

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
        """Remember where the gauge sits as a fraction of the game's picture."""
        if self._tracker is None:
            return
        bounds = self._tracker.rect
        if not bounds.valid:
            return
        width, height = self._dimensions()
        span_x = max(1, bounds.width - width)
        span_y = max(1, bounds.height - height)
        self._position = (
            max(0.0, min(1.0, (self.x() - bounds.x) / span_x)),
            max(0.0, min(1.0, (self.y() - bounds.y) / span_y)),
        )
        if self._on_moved is not None:
            self._on_moved(self._position)
