"""Suspension: ride height and air-ride drop."""
from __future__ import annotations

import threading
import time

from neptune.core import input as inp
from neptune.core.module import FeatureModule
from neptune.ui import theme as T
from neptune.ui.widgets.card import FieldRow, StatStrip
from neptune.ui.widgets.controls import BindButton, Segmented
from neptune.ui.widgets.sliderrow import SliderRow

RAMP_HZ = 60
DEFAULT_RAMP_SECONDS = 2.5
SETTLE_SECONDS = 0.5

MAX_DROP_M = 0.25
LOWER_RANGE_M = 0.20
RAISE_RANGE_M = 0.05

DEFAULT_FLOOR_M = 0.05
HARD_FLOOR_M = 0.01
FLOOR_CEILING_M = 0.30

AXLE_OVERLAP = 0.7
FRONT_WHEELS = (0, 1)
WHEEL_COUNT = 4

SEQUENCE_LABELS = {'together': 'Together', 'front': 'Front first', 'rear': 'Rear first'}

HINT_DROP = 'How far the car drops when air ride is triggered.'
HINT_FLOOR = 'The lowest the car will ever sit. Raise this if the car starts bouncing.'
HINT_RAMP = 'How long the car takes to move between heights.'
HINT_SEQUENCE = 'The order the axles move in. The raise plays the same order in reverse.'


def _axle_text(settings, first: float | None, second: float | None) -> str:
    if first is None or second is None:
        return '--'
    if abs(first - second) < 5e-4:
        return settings.format_height(first)
    return f'{settings.format_height(first)} / {settings.format_height(second)}'


class SuspensionModule(FeatureModule):
    name = 'suspension'
    title = 'Suspension'
    subtitle = 'Ride height and air ride.'
    icon = '▬'
    group = 'Vehicle'
    order = 30

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

        self.stock: list[float] | None = None
        self._front_offset = 0.0
        self._rear_offset = 0.0
        self._drop = 0.12
        self._floor = DEFAULT_FLOOR_M
        self._ramp_seconds = DEFAULT_RAMP_SECONDS
        self._sequence = 'together'
        self._lowered = False

        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._edge = inp.EdgeDetector()
        self._controls_dirty = False
        self._widgets: dict = {}

    def _sync_controls(self) -> None:
        for key, value in (('front', self._front_offset),
                           ('rear', self._rear_offset),
                           ('drop', self._drop),
                           ('floor', self._floor),
                           ('ramp', self._ramp_seconds)):
            slider = self._widgets.get(key)
            if slider is not None:
                slider.set_value(value)
        selector = self._widgets.get('sequence')
        if selector is not None:
            selector.set_value(SEQUENCE_LABELS[self._sequence])

    def binding(self) -> dict | None:
        return self.settings.binding('suspension.airride')

    def bindings(self) -> list[dict]:
        return [{
            'key': 'suspension.airride',
            'label': 'Air ride up and down',
            'description': 'Drops the car, or lifts it back to your set height.',
        }]

    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle
        self._capture_stock(vehicle)

    def _capture_stock(self, vehicle) -> None:
        if self._lowered or self._ramping or self._front_offset or self._rear_offset:
            return
        values = vehicle.ride_height if vehicle else None
        if not values or any(value is None for value in values):
            return
        if not all(0.01 < value < 2.0 for value in values):
            return
        self.stock = list(values)

    def on_car_changed(self, vehicle) -> None:
        self._cancel_ramp()
        self._lowered = False
        self.stock = None
        self.vehicle = vehicle
        self._capture_stock(vehicle)

    def on_car_reloaded(self, vehicle) -> None:
        self.vehicle = vehicle
        if self.stock and (self._lowered or self._front_offset or self._rear_offset):
            self._write(self._target(self._lowered))

    def on_detach(self) -> None:
        self._cancel_ramp()
        self.vehicle = None

    def restore(self) -> None:
        self._cancel_ramp()
        vehicle = self.vehicle
        if vehicle is not None and self.stock:
            try:
                vehicle.set_ride_height(self.stock)
            except Exception:
                pass
        self._lowered = False
        self._front_offset = 0.0
        self._rear_offset = 0.0

    def reset_controls(self) -> None:
        self._lowered = False
        self._front_offset = 0.0
        self._rear_offset = 0.0
        self._controls_dirty = True

    def tick(self, vehicle) -> None:
        self.vehicle = vehicle
        if self.stock is None:
            self._capture_stock(vehicle)
        if self._edge.pressed(self.binding()):
            self.toggle()

    @property
    def _ramping(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _clamp(self, value: float) -> float:
        floor = min(max(self._floor, HARD_FLOOR_M), FLOOR_CEILING_M)
        return max(floor, value)

    def _baseline(self) -> list[float]:
        stock = self.stock or []
        if len(stock) < WHEEL_COUNT:
            return list(stock)
        offsets = (self._front_offset, self._front_offset,
                   self._rear_offset, self._rear_offset)
        return [self._clamp(value + offset)
                for value, offset in zip(stock[:WHEEL_COUNT], offsets, strict=True)]

    def _target(self, lowered: bool) -> list[float]:
        baseline = self._baseline()
        if not lowered:
            return baseline
        drop = min(self._drop, MAX_DROP_M)
        return [self._clamp(value - drop) for value in baseline]

    def _write(self, values) -> bool:
        vehicle = self.vehicle
        if vehicle is None or not values or any(value is None for value in values):
            return False
        return bool(vehicle.set_ride_height(values))

    def toggle(self) -> None:
        if not self.stock or self.vehicle is None:
            return
        self._cancel_ramp()
        self._lowered = not self._lowered
        start = self.vehicle.ride_height or self._target(not self._lowered)
        self._start_ramp(start, self._target(self._lowered),
                         self._ramp_seconds, reverse=not self._lowered)

    def _start_ramp(self, start, end, seconds: float, reverse: bool = False) -> None:
        self._cancel.clear()
        self._thread = threading.Thread(target=self._ramp, daemon=True,
                                        name='neptune-airride',
                                        args=(start, end, seconds, reverse))
        self._thread.start()

    def _cancel_ramp(self) -> None:
        self._cancel.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    def _axle_phase(self, wheel: int, reverse: bool) -> tuple[float, float]:
        if self._sequence == 'together':
            return 0.0, 1.0
        is_front = wheel in FRONT_WHEELS
        front_leads = (self._sequence == 'front') != reverse
        leads = is_front == front_leads
        lead = 0.0 if leads else (1.0 - AXLE_OVERLAP)
        return lead, AXLE_OVERLAP

    def _ramp(self, start, end, seconds: float, reverse: bool) -> None:
        if not start or not end or len(start) != len(end):
            return
        if any(value is None for value in start) or any(value is None for value in end):
            return

        steps = max(1, int(seconds * RAMP_HZ))
        interval = 1.0 / RAMP_HZ
        phases = [self._axle_phase(index, reverse) for index in range(len(start))]

        try:
            for step in range(steps + 1):
                if self._cancel.is_set() or self.vehicle is None:
                    return
                progress = step / steps
                values = []
                for index, (origin, destination) in enumerate(zip(start, end, strict=True)):
                    lead, span = phases[index]
                    fraction = 0.0 if span <= 0 else (progress - lead) / span
                    fraction = min(1.0, max(0.0, fraction))
                    eased = fraction * fraction * (3.0 - 2.0 * fraction)
                    values.append(origin + (destination - origin) * eased)
                if not self._write(values):
                    return
                time.sleep(interval)
        except Exception:
            return

    def _set_offset(self, axle: str, value: float) -> None:
        if axle == 'front':
            self._front_offset = float(value)
        else:
            self._rear_offset = float(value)
        if not self.stock or self.vehicle is None:
            return
        self._cancel_ramp()
        start = self.vehicle.ride_height or self._target(self._lowered)
        self._start_ramp(start, self._target(self._lowered), SETTLE_SECONDS)

    def _set_floor(self, value: float) -> None:
        self._floor = float(value)
        if not self.stock or self.vehicle is None:
            return
        target = self._target(self._lowered)
        current = self.vehicle.ride_height
        if current and len(current) == len(target) and all(
                abs(a - b) < 1e-4 for a, b in zip(current, target, strict=True)):
            return
        self._cancel_ramp()
        self._start_ramp(current or target, target, SETTLE_SECONDS)

    def _set_sequence(self, label: str) -> None:
        for key, text in SEQUENCE_LABELS.items():
            if text == label:
                self._sequence = key
                return

    def _reset_height(self) -> None:
        self._front_offset = 0.0
        self._rear_offset = 0.0
        for key in ('front', 'rear'):
            slider = self._widgets.get(key)
            if slider is not None:
                slider.set_value(0.0)
        if self.stock and self.vehicle is not None:
            self._cancel_ramp()
            start = self.vehicle.ride_height or self._target(self._lowered)
            self._start_ramp(start, self._target(self._lowered), SETTLE_SECONDS)

    def build_page(self, page) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QPushButton

        height_card = page.add_card(
            'Ride height', 'Moves the height the car normally sits at.')

        front = SliderRow('Front', -LOWER_RANGE_M, RAISE_RANGE_M, 0.0,
                          step=0.005, decimals=3, unit='m')
        front.changed.connect(lambda value: self._set_offset('front', value))
        self._widgets['front'] = front
        height_card.add(front)

        rear = SliderRow('Rear', -LOWER_RANGE_M, RAISE_RANGE_M, 0.0,
                         step=0.005, decimals=3, unit='m')
        rear.changed.connect(lambda value: self._set_offset('rear', value))
        self._widgets['rear'] = rear
        height_card.add(rear)

        reset_button = QPushButton('Reset to stock height')
        reset_button.setCursor(Qt.PointingHandCursor)
        reset_button.clicked.connect(self._reset_height)
        height_card.add(reset_button)

        air_card = page.add_card('Air ride', 'Drops the car on a key press.')

        toggle_button = QPushButton('Drop or lift now')
        toggle_button.setObjectName('Primary')
        toggle_button.setCursor(Qt.PointingHandCursor)
        toggle_button.clicked.connect(self.toggle)
        air_card.add(toggle_button)

        bind_button = BindButton(self.binding())
        bind_button.bound.connect(
            lambda binding: self.settings.set_binding('suspension.airride', binding))
        self._widgets['bind'] = bind_button
        air_card.add(FieldRow('Control', bind_button))

        air_card.add_divider()

        drop = SliderRow('Drop by', 0.0, MAX_DROP_M, 0.12, step=0.005,
                         decimals=3, unit='m', hint=HINT_DROP)
        drop.changed.connect(lambda value: setattr(self, '_drop', float(value)))
        self._widgets['drop'] = drop
        air_card.add(drop)

        floor = SliderRow('Lowest allowed', HARD_FLOOR_M, FLOOR_CEILING_M,
                          DEFAULT_FLOOR_M, step=0.005, decimals=3, unit='m',
                          hint=HINT_FLOOR)
        floor.changed.connect(self._set_floor)
        self._widgets['floor'] = floor
        air_card.add(floor)

        ramp = SliderRow('Movement time', 0.3, 6.0, DEFAULT_RAMP_SECONDS,
                         step=0.1, decimals=1, unit='s', hint=HINT_RAMP)
        ramp.changed.connect(lambda value: setattr(self, '_ramp_seconds', float(value)))
        self._widgets['ramp'] = ramp
        air_card.add(ramp)

        sequence = Segmented(list(SEQUENCE_LABELS.values()),
                             SEQUENCE_LABELS[self._sequence])
        sequence.changed.connect(self._set_sequence)
        self._widgets['sequence'] = sequence
        air_card.add(FieldRow('Order', sequence, hint=HINT_SEQUENCE))

        live_card = page.add_card('Live')
        stats = StatStrip()
        stats.add('state', 'State', 'Stock')
        stats.add('front', 'Front', '--')
        stats.add('rear', 'Rear', '--')
        self._widgets['stats'] = stats
        live_card.add(stats)

    def refresh(self, vehicle) -> None:
        stats = self._widgets.get('stats')
        if stats is None:
            return

        if self._controls_dirty:
            self._controls_dirty = False
            self._sync_controls()

        if vehicle is None or not self.stock:
            stats.reset()
            return

        current = vehicle.ride_height
        if not current:
            return

        if self._ramping:
            stats.set('state', 'Moving', T.WARN)
        elif self._lowered:
            stats.set('state', 'Lowered', T.ACCENT_BRIGHT)
        else:
            stats.set('state', 'Stock', T.TEXT)

        stats.set('front', _axle_text(self.settings, current[0], current[1]), unit='')
        stats.set('rear', _axle_text(self.settings, current[2], current[3]), unit='')

    def save_state(self) -> dict:
        return {
            'front': self._front_offset,
            'rear': self._rear_offset,
            'drop': self._drop,
            'floor': self._floor,
            'ramp_seconds': self._ramp_seconds,
            'sequence': self._sequence,
        }

    def load_state(self, data: dict) -> None:
        data = data or {}
        self._front_offset = float(data.get('front', 0.0))
        self._rear_offset = float(data.get('rear', 0.0))
        self._drop = float(data.get('drop', 0.12))
        self._floor = min(max(float(data.get('floor', DEFAULT_FLOOR_M)), HARD_FLOOR_M),
                          FLOOR_CEILING_M)
        self._ramp_seconds = float(data.get('ramp_seconds', DEFAULT_RAMP_SECONDS))

        sequence = data.get('sequence')
        if sequence in SEQUENCE_LABELS:
            self._sequence = sequence

        self._controls_dirty = True
