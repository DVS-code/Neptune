"""The Dragy timer, floating over the game.

Deliberately plain: a big elapsed time, the run being timed, and the live speed. It is read at a
glance while accelerating, so there is nothing else on it.

Follows the same window-anchoring approach as the boost gauge overlay — position is stored as a
fraction of the game's picture so it stays put when the game is moved or resized.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from neptune.ui.gamewindow import GameWindowTracker

FRAME_MS = 16
SYNC_MS = 250

WIDTH = 260
HEIGHT = 214
DEFAULT_POSITION = (0.5, 0.12)
RADIUS = 10


BUTTON_HEIGHT = 30
BUTTON_GAP = 8
BUTTON_MARGIN = 14
BUTTON_TOP = HEIGHT - BUTTON_HEIGHT - 12
BUTTON_WIDTH = (WIDTH - BUTTON_MARGIN * 2 - BUTTON_GAP) // 2

BACKDROP = QColor(18, 20, 24, 214)
BORDER = QColor(255, 255, 255, 28)
TEXT = QColor(238, 240, 245)
MUTED = QColor(150, 156, 168)
LIVE = QColor(120, 214, 255)
FINISHED = QColor(150, 245, 170)

BUTTON_FILL = QColor(44, 48, 56, 235)
BUTTON_HOVER = QColor(62, 68, 78, 245)
BUTTON_ARMED = QColor(150, 60, 60, 235)
BUTTON_BORDER = QColor(255, 255, 255, 38)

MS_TO_KPH = 3.6
MS_TO_MPH = 2.2369363


class DragyOverlay(QWidget):
    """Frameless always-on-top timer readout."""

    def __init__(self, parent=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool
                         | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.resize(WIDTH, HEIGHT)

        self._settings = None
        self._tracker: GameWindowTracker | None = None
        self._position = DEFAULT_POSITION
        self._locked = False
        self._drag_origin: QPoint | None = None

        self._run = ''
        self._state = 'idle'
        self._result: float | None = None
        self._started: float | None = None
        self._speed: float | None = None
        self._unit = 'mph'
        self._history: list = []

        self._on_arm = None
        self._on_reset = None
        self._hover: str | None = None
        self._pressed: str | None = None

        self.setMouseTracking(True)

        self._frames = QTimer(self)
        self._frames.setInterval(FRAME_MS)
        self._frames.timeout.connect(self.update)

        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(SYNC_MS)
        self._sync_timer.timeout.connect(self._sync)

    def set_settings(self, settings) -> None:
        self._settings = settings

    def update_run(self, run: str, state: str, result, started, speed, unit,
                   history=None) -> None:
        self._run = run
        self._state = state
        self._result = result
        self._started = started
        self._speed = speed
        self._unit = unit
        if history is not None:
            self._history = list(history)

    def start(self, pid: int | None) -> None:
        """Show the overlay and bind it to the game window."""
        self._pid = pid
        self.show()
        handle = int(self.winId())
        self._tracker = GameWindowTracker(handle, pid)

        self._tracker.make_overlay(click_through=False)
        self._tracker.attach()
        self._sync()
        self._frames.start()
        self._sync_timer.start()

    def stop(self) -> None:
        self._frames.stop()
        self._sync_timer.stop()
        if self._tracker is not None:
            self._tracker.detach()
            self._tracker = None
        self.hide()

    def _sync(self) -> None:
        if self._tracker is None or self._drag_origin is not None:
            return
        if not self._tracker.game_hwnd:
            self._tracker.attach()
        bounds = self._tracker.sync()
        if bounds.valid:
            placed = self._tracker.anchor(self._position[0], self._position[1],
                                          WIDTH, HEIGHT)
            if placed is not None:
                self.move(placed[0], placed[1])


    def set_callbacks(self, on_arm, on_reset) -> None:
        self._on_arm = on_arm
        self._on_reset = on_reset

    @staticmethod
    def _arm_rect() -> QRect:
        return QRect(BUTTON_MARGIN, BUTTON_TOP, BUTTON_WIDTH, BUTTON_HEIGHT)

    @staticmethod
    def _reset_rect() -> QRect:
        return QRect(BUTTON_MARGIN + BUTTON_WIDTH + BUTTON_GAP, BUTTON_TOP,
                     BUTTON_WIDTH, BUTTON_HEIGHT)


    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        point = event.position().toPoint()
        if self._arm_rect().contains(point):
            self._pressed = 'arm'
            self.update()
            return
        if self._reset_rect().contains(point):
            self._pressed = 'reset'
            self.update()
            return
        self._drag_origin = event.globalPosition().toPoint() - self.pos()
        self.setCursor(QCursor(Qt.ClosedHandCursor))

    def mouseMoveEvent(self, event) -> None:
        point = event.position().toPoint()
        hover = None
        if self._arm_rect().contains(point):
            hover = 'arm'
        elif self._reset_rect().contains(point):
            hover = 'reset'
        if hover != self._hover:
            self._hover = hover
            self.update()
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)

    def leaveEvent(self, event) -> None:
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        pressed, self._pressed = self._pressed, None
        if pressed is not None:
            point = event.position().toPoint()
            rect = self._arm_rect() if pressed == 'arm' else self._reset_rect()
            if rect.contains(point):
                callback = self._on_arm if pressed == 'arm' else self._on_reset
                if callback is not None:
                    callback()
            self.update()
            return
        self._drag_origin = None
        self.setCursor(QCursor(Qt.OpenHandCursor))
        if self._tracker is not None:
            bounds = self._tracker.sync()
            if bounds.valid and bounds.width and bounds.height:




                origin = self.pos()
                span_x = max(1, bounds.width - self.width())
                span_y = max(1, bounds.height - self.height())
                self._position = (
                    max(0.0, min(1.0, (origin.x() - bounds.x) / span_x)),
                    max(0.0, min(1.0, (origin.y() - bounds.y) / span_y)))

    def _display_time(self) -> tuple[str, QColor]:
        if self._result is not None:
            return f'{self._result:.2f}s', FINISHED
        if self._state == 'running' and self._started is not None:
            return f'{time.perf_counter() - self._started:.2f}s', LIVE
        if self._state == 'armed':
            return 'READY', MUTED
        return '--', MUTED

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(0, 0, WIDTH, HEIGHT, RADIUS, RADIUS)
        painter.fillPath(path, BACKDROP)
        painter.setPen(BORDER)
        painter.drawPath(path)


        painter.setPen(MUTED)
        painter.setFont(QFont('Segoe UI', 9))
        painter.drawText(14, 24, self._run or 'Dragy')


        text, colour = self._display_time()
        painter.setPen(colour)
        font = QFont('Segoe UI', 34)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.drawText(14, 78, text)


        painter.setPen(MUTED)
        painter.setFont(QFont('Segoe UI', 10))
        if self._speed is not None:
            factor = MS_TO_MPH if self._unit == 'mph' else MS_TO_KPH
            painter.drawText(14, 108, f'{self._speed * factor:.0f} {self._unit}')

        state = {'armed': 'Armed', 'running': 'Timing',
                 'done': 'Finished'}.get(self._state, '')
        if state:
            painter.setPen(LIVE if self._state == 'running' else MUTED)
            painter.drawText(WIDTH - 90, 108, state)


        painter.setFont(QFont('Segoe UI', 9))
        top = 126
        if self._history:
            best = min(t for _, t in self._history)
            for index, (label, seconds) in enumerate(self._history[:3]):
                painter.setPen(FINISHED if seconds <= best else MUTED)
                painter.drawText(14, top + index * 15, f'{seconds:.2f}s')
                painter.setPen(MUTED)
                painter.drawText(70, top + index * 15, label)
        else:
            painter.setPen(MUTED)
            painter.drawText(14, top, 'No runs yet')

        armed = self._state in ('armed', 'running')
        self._draw_button(painter, self._arm_rect(),
                          'Disarm' if armed else 'Arm', 'arm', armed)
        self._draw_button(painter, self._reset_rect(), 'Reset', 'reset', False)

    def _draw_button(self, painter: QPainter, rect: QRect, label: str,
                     key: str, active: bool) -> None:
        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()),
                            float(rect.width()), float(rect.height()), 7, 7)
        if active:
            fill = BUTTON_ARMED
        elif self._pressed == key or self._hover == key:
            fill = BUTTON_HOVER
        else:
            fill = BUTTON_FILL
        painter.fillPath(path, fill)
        painter.setPen(BUTTON_BORDER)
        painter.drawPath(path)

        painter.setPen(TEXT)
        font = QFont('Segoe UI', 10)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, label)
