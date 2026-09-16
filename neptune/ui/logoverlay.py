"""A small capture-status panel that follows the game window."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from neptune.ui.gamewindow import GameWindowTracker

FRAME_MS = 120
SYNC_MS = 250
WIDTH = 360
HEIGHT = 132
DEFAULT_POSITION = (0.04, 0.72)
RADIUS = 12

BACKDROP = QColor(18, 20, 24, 232)
BORDER = QColor(255, 255, 255, 34)
TEXT = QColor(238, 240, 245)
MUTED = QColor(150, 156, 168)
LIVE = QColor(120, 214, 255)
OK = QColor(120, 224, 162)


class LogOverlay(QWidget):
    """A draggable, click-through-by-default log capture status overlay."""

    def __init__(self, parent=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.resize(WIDTH, HEIGHT)

        self._tracker: GameWindowTracker | None = None
        self._position = DEFAULT_POSITION
        self._locked = True
        self._drag_origin: QPoint | None = None
        self._on_moved = None
        self._test = "No log armed"
        self._state = "idle"
        self._samples = 0
        self._units = "hp / Nm | km/h | psi"
        self._status = "Choose Generate Log in Neptune"

        self._frames = QTimer(self)
        self._frames.setInterval(FRAME_MS)
        self._frames.timeout.connect(self.update)
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(SYNC_MS)
        self._sync_timer.timeout.connect(self._sync)

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

    def update_capture(
        self,
        test_type: str,
        state: str,
        samples: int,
        units: str,
        status: str,
    ) -> None:
        self._test = test_type or "No log armed"
        self._state = state or "idle"
        self._samples = max(0, int(samples or 0))
        self._units = units or self._units
        self._status = status or "Waiting for capture"
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

    def _reposition(self) -> None:
        if self._tracker is None:
            return
        placed = self._tracker.anchor(self._position[0], self._position[1], WIDTH, HEIGHT)
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
        span_x = max(1, bounds.width - WIDTH)
        span_y = max(1, bounds.height - HEIGHT)
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
        path.addRoundedRect(QRectF(0, 0, WIDTH, HEIGHT), RADIUS, RADIUS)
        painter.fillPath(path, BACKDROP)
        painter.setPen(QPen(BORDER, 1))
        painter.drawPath(path)

        painter.setPen(TEXT)
        painter.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
        painter.drawText(16, 23, "NEPTUNE LOG")

        state_colour = OK if self._state in ("armed", "running", "done") else MUTED
        painter.setPen(state_colour)
        painter.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
        painter.drawText(WIDTH - 96, 23, self._state.upper())

        painter.setPen(LIVE)
        painter.setFont(QFont("Segoe UI", 11, QFont.DemiBold))
        painter.drawText(16, 50, self._test[:42])
        painter.setPen(TEXT)
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(16, 72, f"{self._samples:,} samples  ·  {self._units}")
        painter.setPen(MUTED)
        painter.drawText(16, 96, self._status[:55])
        painter.drawText(16, 117, "Use Generate Log in Neptune to arm or finish")
