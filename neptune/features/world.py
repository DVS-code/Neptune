"""World: time of day."""
from __future__ import annotations

import struct
import threading
import time

from neptune.core.module import FeatureModule
from neptune.memory import offsets as O
from neptune.ui import theme as T
from neptune.ui.widgets.card import Banner, FieldRow, StatStrip, ToggleRow
from neptune.ui.widgets.sliderrow import SliderRow

HOOK_KEY = 'world.environment'
CAPTURE_SAMPLES = 60
CAPTURE_INTERVAL = 0.02
CAPTURE_RETRY_SECONDS = 2.0
POINTER_SLOT_SIZE = 8
STUB_SIZE = 0x40

PRESETS = [
    ('Sunrise', 5.5),
    ('Morning', 8.0),
    ('Midday', 11.0),
    ('Afternoon', 14.0),
    ('Sunset', 16.5),
    ('Dusk', 18.0),
    ('Night', 20.5),
    ('Midnight', 0.0),
]

HINT_TIME = 'Pick a time to change it straight away.'
HINT_HOLD = 'Stops the clock so the time you picked stays put.'
NOTE_WAITING = 'Load into the world, then pick a time.'


STORE_RCX_LENGTH = 7


def _stub_code(slot_offset: int) -> bytes:
    """Store the object pointer, then run the instruction that was replaced."""
    displacement = slot_offset - STORE_RCX_LENGTH
    return (b'\x48\x89\x0D' + struct.pack('<i', displacement)
            + O.Environment.GETTER_ORIGINAL)


def format_hour(hour: float) -> str:
    hour = hour % 24.0
    hours = int(hour)
    minutes = int(round((hour - hours) * 60.0))
    if minutes == 60:
        hours = (hours + 1) % 24
        minutes = 0
    return f'{hours:02d}:{minutes:02d}'


class WorldModule(FeatureModule):
    name = 'world'
    title = 'World'
    subtitle = 'Time of day.'
    icon = '◑'
    group = 'World'
    order = 40

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

        self._process = None
        self._environment = 0
        self._hour = 12.0
        self._hold = False
        self._requested = False
        self._last_capture = 0.0
        self._capturing = False
        self._lock = threading.RLock()
        self._controls_dirty = False
        self._widgets: dict = {}

    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle
        if vehicle is not None:
            self._process = vehicle.process

    def on_detach(self) -> None:
        """A car went away. The world clock is not owned by the car, so the
        captured pointer is kept and only dropped if it stops reading."""
        self.vehicle = None

    def restore(self) -> None:
        self._hold = False
        self._requested = False

    def reset_controls(self) -> None:
        self._hold = False
        self._requested = False
        self._controls_dirty = True

    def tick_process(self, process) -> None:
        if process is None or not process.alive:
            return
        self._process = process

        if not (self._hold or self._requested):
            return

        if not self._pointer_valid():
            self._environment = 0
            self._start_capture()
            return

        if self._write_hour(self._hour) and not self._hold:
            self._requested = False

    def _start_capture(self) -> None:
        """Look for the world clock on a worker thread.

        The search watches the game for about a second, so it must never run on
        the runtime thread where it would stall every other feature.
        """
        if self._capturing:
            return
        now = time.monotonic()
        if now - self._last_capture < CAPTURE_RETRY_SECONDS:
            return
        self._last_capture = now
        self._capturing = True
        threading.Thread(target=self._capture, daemon=True,
                         name='neptune-worldclock').start()

    def _pointer_valid(self) -> bool:
        process = self._process
        if not process or not self._environment:
            return False
        data = process.read(self._environment + O.Environment.TIME, 4)
        if not data:
            return False
        seconds = struct.unpack('<f', data)[0]
        return seconds == seconds and 0.0 <= seconds <= O.Environment.SECONDS_PER_DAY

    def _capture(self) -> None:
        """Briefly hook the time reader to learn which object holds the clock.

        Several objects use the same reader, so every pointer seen is collected
        and then scored: the real clock holds a valid seconds-of-day value and
        moves forward as the game runs.
        """
        process = self._process
        if process is None or not process.alive:
            self._capturing = False
            return

        with self._lock:
            manager = None
            try:
                from neptune.memory.detour import DetourManager
                from neptune.memory.scanner import ModuleImage

                image = ModuleImage.capture(process)
                site = image.find_optional(O.Environment.GETTER_SIG)
                if not site:
                    return

                slot = STUB_SIZE - POINTER_SLOT_SIZE
                manager = DetourManager(process)
                detour = manager.install(HOOK_KEY, site, _stub_code(slot),
                                         len(O.Environment.GETTER_ORIGINAL),
                                         expected_original=O.Environment.GETTER_ORIGINAL,
                                         stub_size=STUB_SIZE)

                seen: dict[int, list[float]] = {}
                for _ in range(CAPTURE_SAMPLES):
                    detour.write_slot(slot, b'\x00' * POINTER_SLOT_SIZE)
                    time.sleep(CAPTURE_INTERVAL)
                    raw = detour.read_slot(slot, POINTER_SLOT_SIZE)
                    if not raw or len(raw) != POINTER_SLOT_SIZE:
                        continue
                    pointer = struct.unpack('<Q', raw)[0]
                    if not (0x10000 < pointer < 0x7FFFFFFFFFFF):
                        continue
                    seconds = self._read_seconds(process, pointer)
                    if seconds is None:
                        continue
                    seen.setdefault(pointer, []).append(seconds)

                self._environment = self._best_candidate(seen)
            except Exception:
                self._environment = 0
            finally:
                if manager is not None:
                    try:
                        manager.uninstall_all()
                    except Exception:
                        pass
                self._capturing = False

    @staticmethod
    def _read_seconds(process, pointer: int) -> float | None:
        """Seconds-of-day held by a candidate object, or None if it is not one."""
        data = process.read(pointer + O.Environment.TIME, 4)
        if not data or len(data) != 4:
            return None
        seconds = struct.unpack('<f', data)[0]
        if seconds != seconds:
            return None
        if not 0.0 <= seconds <= O.Environment.SECONDS_PER_DAY:
            return None
        return seconds

    @staticmethod
    def _best_candidate(seen: dict[int, list[float]]) -> int:
        """Pick the object whose value behaves like a running clock."""
        if not seen:
            return 0

        moving = []
        for pointer, samples in seen.items():
            if len(samples) < 2:
                continue
            spread = max(samples) - min(samples)
            if spread > 0.0:
                moving.append((spread, pointer))
        if moving:
            moving.sort()
            return moving[-1][1]

        return max(seen.items(), key=lambda item: max(item[1]))[0]

    def _write_hour(self, hour: float) -> bool:
        process = self._process
        if process is None or not self._environment:
            return False
        seconds = (hour % 24.0) * O.Environment.SECONDS_PER_HOUR
        packed = struct.pack('<f', seconds)
        written = process.write(self._environment + O.Environment.TIME, packed)
        process.write(self._environment + O.Environment.TIME_MIRROR, packed)
        process.write(self._environment + O.Environment.DIRTY_A, b'\x01')
        process.write(self._environment + O.Environment.DIRTY_B, b'\x01')
        return bool(written)

    def _current_hour(self) -> float | None:
        process = self._process
        if process is None or not self._environment:
            return None
        data = process.read(self._environment + O.Environment.TIME, 4)
        if not data:
            return None
        seconds = struct.unpack('<f', data)[0]
        if seconds != seconds or not 0.0 <= seconds <= O.Environment.SECONDS_PER_DAY:
            return None
        return seconds / O.Environment.SECONDS_PER_HOUR

    def _apply_hour(self, hour: float) -> None:
        """Ask for a time change.

        The write is attempted here so a pick lands instantly, and is also left
        pending so the runtime thread retries if the clock was not reachable.
        """
        self._hour = float(hour) % 24.0
        self._requested = True
        self._last_capture = 0.0
        if self._pointer_valid() and self._write_hour(self._hour):
            if not self._hold:
                self._requested = False

    def _on_slider(self, value: float) -> None:
        self._apply_hour(value)
        label = self._widgets.get('preview')
        if label is not None:
            label.setText(format_hour(self._hour))

    def _on_preset(self, hour: float) -> None:
        slider = self._widgets.get('time')
        if slider is not None:
            slider.set_value(hour)
        self._apply_hour(hour)
        label = self._widgets.get('preview')
        if label is not None:
            label.setText(format_hour(self._hour))

    def _on_hold(self, enabled: bool) -> None:
        self._hold = bool(enabled)
        if self._hold:
            self._requested = True
            self._last_capture = 0.0

    def build_page(self, page) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton

        time_card = page.add_card('Time of day', HINT_TIME)

        slider = SliderRow('Time', 0.0, 23.75, 12.0, step=0.25, decimals=2, unit='h')
        slider.changed.connect(self._on_slider)
        self._widgets['time'] = slider
        time_card.add(slider)

        preview = QLabel(format_hour(self._hour))
        preview.setObjectName('StatValue')
        self._widgets['preview'] = preview
        time_card.add(FieldRow('Selected', preview))

        time_card.add_divider()

        grid = QGridLayout()
        grid.setSpacing(8)
        for index, (label, hour) in enumerate(PRESETS):
            button = QPushButton(label)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked, value=hour: self._on_preset(value))
            grid.addWidget(button, index // 4, index % 4)
        time_card.add_layout(grid)

        hold = ToggleRow('Hold this time', False, hint=HINT_HOLD)
        hold.toggle.toggled_value.connect(self._on_hold)
        self._widgets['hold'] = hold
        time_card.add(hold)

        live_card = page.add_card('Live')
        stats = StatStrip()
        stats.add('clock', 'In game', '--')
        stats.add('state', 'Clock', 'Running')
        self._widgets['stats'] = stats
        live_card.add(stats)

        banner = Banner(NOTE_WAITING, 'info')
        self._widgets['banner'] = banner
        live_card.add(banner)

    def refresh(self, _vehicle) -> None:
        stats = self._widgets.get('stats')
        banner = self._widgets.get('banner')
        if stats is None:
            return

        if self._controls_dirty:
            self._controls_dirty = False
            hold = self._widgets.get('hold')
            if hold is not None:
                hold.set_value(self._hold)
            slider = self._widgets.get('time')
            if slider is not None:
                slider.set_value(self._hour)
            preview = self._widgets.get('preview')
            if preview is not None:
                preview.setText(format_hour(self._hour))

        hour = self._current_hour()
        if hour is None:
            stats.set('clock', '--', T.TEXT_FAINT, unit='')
        else:
            stats.set('clock', format_hour(hour), unit='')

        if self._hold:
            stats.set('state', 'Held', T.ACCENT_BRIGHT, unit='')
        else:
            stats.set('state', 'Running', T.TEXT, unit='')

        if banner is not None:
            if self._environment:
                banner.setVisible(False)
            elif self._requested or self._hold:
                banner.set('Finding the world clock. This takes a moment.', 'info')
            else:
                banner.set(NOTE_WAITING, 'info')

    def save_state(self) -> dict:
        return {'hour': self._hour}

    def load_state(self, data: dict) -> None:
        data = data or {}
        try:
            self._hour = float(data.get('hour', 12.0)) % 24.0
        except (TypeError, ValueError):
            self._hour = 12.0
        self._controls_dirty = True
