"""Suspension: ride height and air-ride drop."""

from __future__ import annotations

import threading
import time

from PySide6.QtWidgets import QVBoxLayout, QWidget

from neptune.core import input as inp
from neptune.core.module import FeatureModule
from neptune.ui import theme as T
from neptune.ui.widgets.card import FieldRow, StatStrip, ToggleRow
from neptune.ui.widgets.controls import BindButton, Segmented
from neptune.ui.widgets.sliderrow import SliderRow

RAMP_HZ = 60
DEFAULT_RAMP_SECONDS = 2.5
SETTLE_SECONDS = 0.5

LOWER_PERCENT_MAX = 90.0
RAISE_PERCENT_MAX = 50.0
DROP_PERCENT_DEFAULT = 60.0
DEFAULT_FLOOR_PERCENT = 15.0


BOUNCE_MIN_M = 0.02
BOUNCE_MAX_M = 0.60
BOUNCE_DEFAULT_LOW = 0.08
BOUNCE_DEFAULT_HIGH = 0.30
BOUNCE_SPEED_MIN = 0.2
BOUNCE_SPEED_MAX = 4.0
BOUNCE_DEFAULT_SPEED = 1.0
BOUNCE_HZ = 60


HARD_FLOOR_M = 0.01

AXLE_OVERLAP = 0.7
FRONT_WHEELS = (0, 1)
WHEEL_COUNT = 4

SEQUENCE_LABELS = {"together": "Together", "front": "Front first", "rear": "Rear first"}

CAMBER_MIN_DEG = -20.0
CAMBER_MAX_DEG = 20.0

TRACK_MIN_MM = -150.0
TRACK_MAX_MM = 150.0

HINT_AXLE = "Raises or lowers this axle, as a share of normal ride height."
HINT_CAMBER = "Static camber, per wheel — each corner is independent."
HINT_CAMBER_MIRROR = "One value per axle instead of four — the right wheel mirrors the left."
HINT_TRACK = "Track width, per wheel — how far outward each corner sits."
HINT_TRACK_MIRROR = "One value per axle instead of four — both wheels move together."
HINT_DROP = "How far air ride drops the car."
HINT_FLOOR = "The lowest the car may ever sit. Raise it if the car scrapes or bounces."
HINT_RAMP = "How long the car takes to move between heights."
HINT_SEQUENCE = "The order the axles move in. The raise plays the same order in reverse."
HINT_BOUNCE = "Rocks the car up and down between the two heights, over and over."
HINT_BOUNCE_RANGE = "The lowest and highest the car sits while bouncing."
HINT_BOUNCE_SPEED = "How quickly the car cycles between the two heights."
HINT_BOUNCE_AUDIO = "Plays a track on loop while the bounce is running."


def _axle_text(settings, first: float | None, second: float | None) -> str:
    if first is None or second is None:
        return "--"
    if abs(first - second) < 5e-4:
        return settings.format_height(first)
    return f"{settings.format_height(first)} / {settings.format_height(second)}"


class SuspensionModule(FeatureModule):
    name = "suspension"
    title = "Suspension"
    subtitle = "Ride height and air ride."
    icon = "spring.png"
    group = "Vehicle"
    order = 30

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

        self.stock: list[float] | None = None
        self._radii: list[float] | None = None
        self._front_percent = 0.0
        self._rear_percent = 0.0

        self.stock_camber: list[float] | None = None
        self._camber: list[float | None] = [None, None, None, None]  # FL, FR, RR, RL
        self._camber_mirror = True

        self.stock_track: list[list[float] | None] = [None, None, None, None]  # 30-pt curve/wheel
        self._track: list[float | None] = [None, None, None, None]  # held delta, mm, FL/FR/RR/RL
        self._track_mirror = True
        self._drop_percent = DROP_PERCENT_DEFAULT
        self._floor_percent = DEFAULT_FLOOR_PERCENT
        self._ramp_seconds = DEFAULT_RAMP_SECONDS
        self._sequence = "together"
        self._lowered = False

        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._edge = inp.EdgeDetector()
        self._controls_dirty = False
        self._widgets: dict = {}

        self._bounce = False
        self._bounce_low = BOUNCE_DEFAULT_LOW
        self._bounce_high = BOUNCE_DEFAULT_HIGH
        self._bounce_speed = BOUNCE_DEFAULT_SPEED
        self._bounce_audio = True
        self._bounce_thread: threading.Thread | None = None
        self._bounce_cancel = threading.Event()

        from neptune.core.audio import Loop, OneShot

        self._slam = OneShot("sfx/slam.wav")
        self._maybach = Loop("sfx/maybach.mp3")
        self._slam.set_volume(self.settings.get("airride_volume"))
        self._maybach.set_volume(self.settings.get("maybach_volume"))
        self.settings.subscribe(self._on_settings_changed)

    def _on_settings_changed(self, key: str) -> None:
        """Live-apply the volume sliders that live in Settings.

        ⚠️ `Settings._notify` passes only the KEY, not the value — read it back rather than
        expecting a second argument, or every notification raises and is swallowed.
        """
        if key == "airride_volume":
            self._slam.set_volume(self.settings.get("airride_volume"))
        elif key == "maybach_volume":
            self._maybach.set_volume(self.settings.get("maybach_volume"))

    def _sync_controls(self) -> None:
        stock = self.stock_camber
        camber_keys = ("camber_fl", "camber_fr", "camber_rr", "camber_rl")
        camber_values = [
            self._camber[i] if self._camber[i] is not None else (stock[i] if stock else None)
            for i in range(4)
        ]
        track_keys = ("track_fl", "track_fr", "track_rr", "track_rl")
        track_values = [value if value is not None else 0.0 for value in self._track]
        for key, value in (
            ("front", self._front_percent),
            ("rear", self._rear_percent),
            ("drop", self._drop_percent),
            ("floor", self._floor_percent),
            ("ramp", self._ramp_seconds),
            ("bounce_low", self._bounce_low),
            ("bounce_high", self._bounce_high),
            ("bounce_speed", self._bounce_speed),
            *zip(camber_keys, camber_values, strict=True),
            ("camber_axle_front", camber_values[0]),
            ("camber_axle_rear", camber_values[3]),
            *zip(track_keys, track_values, strict=True),
            ("track_axle_front", track_values[0]),
            ("track_axle_rear", track_values[3]),
        ):
            slider = self._widgets.get(key)
            if slider is not None and value is not None:
                slider.set_value(value)
        for key, state in (
            ("bounce", self._bounce),
            ("bounce_audio", self._bounce_audio),
            ("camber_mirror", self._camber_mirror),
            ("track_mirror", self._track_mirror),
        ):
            row = self._widgets.get(key)
            if row is not None:
                try:
                    row.toggle.set_value(bool(state))
                except Exception:
                    pass
        wheels_panel = self._widgets.get("camber_wheels_panel")
        if wheels_panel is not None:
            wheels_panel.setVisible(not self._camber_mirror)
        axle_panel = self._widgets.get("camber_axle_panel")
        if axle_panel is not None:
            axle_panel.setVisible(self._camber_mirror)
        track_wheels_panel = self._widgets.get("track_wheels_panel")
        if track_wheels_panel is not None:
            track_wheels_panel.setVisible(not self._track_mirror)
        track_axle_panel = self._widgets.get("track_axle_panel")
        if track_axle_panel is not None:
            track_axle_panel.setVisible(self._track_mirror)
        selector = self._widgets.get("sequence")
        if selector is not None:
            selector.set_value(SEQUENCE_LABELS[self._sequence])

    def binding(self) -> dict | None:
        return self.settings.binding("suspension.airride")

    def bindings(self) -> list[dict]:
        return [
            {
                "key": "suspension.airride",
                "label": "Air ride up and down",
                "description": "Drops the car, or lifts it back to your set height.",
            }
        ]

    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle
        self._capture_stock(vehicle)
        self._capture_camber_stock(vehicle)
        self._capture_track_stock(vehicle)
        self._controls_dirty = True

    def _capture_stock(self, vehicle) -> None:
        if self._lowered or self._ramping or self._front_percent or self._rear_percent:
            return
        values = vehicle.ride_height if vehicle else None
        if not values or any(value is None for value in values):
            return
        if not all(0.01 < value < 2.0 for value in values):
            return
        self.stock = list(values)

        radii = vehicle.wheel_radius
        if radii and all(r is not None and 0.05 < r < 1.0 for r in radii):
            self._radii = list(radii)
        else:
            self._radii = None

    def _capture_camber_stock(self, vehicle) -> None:
        if any(value is not None for value in self._camber):
            return
        values = vehicle.camber if vehicle else None
        if not values or any(value is None for value in values):
            return
        self.stock_camber = list(values)

    def _capture_track_stock(self, vehicle) -> None:
        if any(value is not None for value in self._track):
            return
        if not vehicle:
            return
        curves = [vehicle.track_curve(wheel) for wheel in range(WHEEL_COUNT)]
        if any(curve is None for curve in curves):
            return
        self.stock_track = curves

    def on_car_changed(self, vehicle) -> None:
        self._cancel_ramp()
        self._lowered = False
        self._front_percent = 0.0
        self._rear_percent = 0.0
        self.stock = None
        self._radii = None
        self._camber = [None, None, None, None]
        self.stock_camber = None
        self._track = [None, None, None, None]
        self.stock_track = [None, None, None, None]
        self._controls_dirty = True
        self.vehicle = vehicle
        self._capture_stock(vehicle)
        self._capture_camber_stock(vehicle)
        self._capture_track_stock(vehicle)

    def on_car_reloaded(self, vehicle) -> None:
        self.vehicle = vehicle
        if self.stock and (self._lowered or self._front_percent or self._rear_percent):
            self._write(self._target(self._lowered))
        if any(value is not None for value in self._camber):
            self._write_camber()
        if any(value is not None for value in self._track):
            self._write_track()

    def on_detach(self) -> None:

        self._bounce = False
        self._bounce_cancel.set()
        self._maybach.stop()
        self._cancel_ramp()
        self.vehicle = None

    def restore(self) -> None:
        self._bounce = False
        self._bounce_cancel.set()
        self._maybach.stop()
        bounce_thread = self._bounce_thread
        if bounce_thread is not None and bounce_thread.is_alive():
            bounce_thread.join(timeout=1.0)
        self._bounce_thread = None
        self._cancel_ramp()
        vehicle = self.vehicle
        if vehicle is not None and self.stock:
            try:
                vehicle.set_ride_height(self.stock)
            except Exception:
                pass
        if vehicle is not None and self.stock_camber:
            try:
                vehicle.set_camber(self.stock_camber)
            except Exception:
                pass
        if vehicle is not None and all(curve is not None for curve in self.stock_track):
            try:
                for wheel in range(WHEEL_COUNT):
                    vehicle.set_track_width(wheel, self.stock_track[wheel], 0.0)
            except Exception:
                pass
        self._lowered = False
        self._front_percent = 0.0
        self._rear_percent = 0.0
        self._camber = [None, None, None, None]
        self._track = [None, None, None, None]
        self._controls_dirty = True

    def reset_controls(self) -> None:
        if self._bounce:
            self.set_bounce(False)
        self._lowered = False
        self._front_percent = 0.0
        self._rear_percent = 0.0
        self._camber = [None, None, None, None]
        self._track = [None, None, None, None]
        self._controls_dirty = True

    def tick(self, vehicle) -> None:
        self.vehicle = vehicle
        if self.stock is None:
            self._capture_stock(vehicle)
        if self.stock_camber is None:
            self._capture_camber_stock(vehicle)
        if any(curve is None for curve in self.stock_track):
            self._capture_track_stock(vehicle)
        if self._edge.pressed(self.binding()):
            self.toggle()
        self._reapply_if_rebaked(vehicle)
        self._reapply_camber_if_rebaked(vehicle)
        self._reapply_track_if_rebaked(vehicle)

    def _reapply_if_rebaked(self, vehicle) -> None:
        """Re-apply ride height when the game has quietly put stock height back."""
        if not self.stock or self._ramping or self._bounce:
            return
        if not (self._lowered or self._front_percent or self._rear_percent):
            return
        expected = self._target(self._lowered)
        current = vehicle.ride_height if vehicle else None
        if not current or len(current) != len(expected):
            return
        if any(abs(a - b) > 1e-3 for a, b in zip(current, expected, strict=True)):
            self._write(expected)

    def _reapply_camber_if_rebaked(self, vehicle) -> None:
        """Re-apply camber when the game has quietly rebaked the axle's table."""
        if not any(value is not None for value in self._camber):
            return
        current = vehicle.camber if vehicle else None
        if not current or len(current) != len(self._camber):
            return
        if any(
            target is not None and abs(actual - target) > 0.05
            for target, actual in zip(self._camber, current, strict=True)
        ):
            self._write_camber()

    def _reapply_track_if_rebaked(self, vehicle) -> None:
        """Re-apply track width when the game has quietly rebaked the axle's table."""
        if vehicle is None or not any(value is not None for value in self._track):
            return
        for wheel, delta_mm in enumerate(self._track):
            if delta_mm is None:
                continue
            baseline = self.stock_track[wheel]
            if baseline is None:
                continue
            if vehicle.track_width_ok(wheel, baseline, delta_mm / 1000.0) is False:
                self._write_track()
                return

    @property
    def _ramping(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _clamp(self, value: float, wheel: int = 0) -> float:
        """Keep a wheel from dropping below the floor.

        The floor is a share of this wheel's own clearance, so it means the same thing on any car.
        """
        percent = min(max(self._floor_percent, 0.0), 100.0)
        radius = self._radius_for(wheel)

        floor = max(self._stock_clearance(wheel) * (percent / 100.0), HARD_FLOOR_M)
        return max(floor + radius, value)

    def _radius_for(self, wheel: int) -> float:
        """Wheel radius, so clearance and hub height can be converted either way.

        Falls back to 0.0 when unknown, which degrades to the old hub-height behaviour rather than
        throwing the car at the ground.
        """
        radii = self._radii
        if not radii or wheel >= len(radii):
            return 0.0
        value = radii[wheel]
        return float(value) if value else 0.0

    def _stock_clearance(self, wheel: int) -> float:
        """This wheel's stock clearance above the hub — the quantity percentages scale."""
        stock = self.stock or []
        if wheel >= len(stock):
            return 0.0
        return max(0.0, stock[wheel] - self._radius_for(wheel))

    def _baseline(self) -> list[float]:
        """Stock height with the user's per-axle percentage applied."""
        stock = self.stock or []
        if len(stock) < WHEEL_COUNT:
            return list(stock)
        percents = (
            self._front_percent,
            self._front_percent,
            self._rear_percent,
            self._rear_percent,
        )
        out = []
        for wheel, (value, percent) in enumerate(zip(stock[:WHEEL_COUNT], percents, strict=True)):
            shift = self._stock_clearance(wheel) * (percent / 100.0)
            out.append(self._clamp(value + shift, wheel))
        return out

    def _target(self, lowered: bool) -> list[float]:
        baseline = self._baseline()
        if not lowered:
            return baseline
        out = []
        for wheel, value in enumerate(baseline):
            drop = self._stock_clearance(wheel) * (self._drop_percent / 100.0)
            out.append(self._clamp(value - drop, wheel))
        return out

    def _write(self, values) -> bool:
        vehicle = self.vehicle
        if vehicle is None or not values or any(value is None for value in values):
            return False
        return bool(vehicle.set_ride_height(values))

    def _write_camber(self) -> bool:
        """Write held per-wheel camber. O.Wheels.ORDER is (FL, FR, RR, RL);
        vehicle.set_camber() takes one verbatim value per wheel, so each
        corner is independent here, no axle mirroring.
        """
        vehicle = self.vehicle
        if vehicle is None:
            return False
        return bool(vehicle.set_camber(self._camber))

    def _set_camber(self, wheel: int, value: float) -> None:
        self._camber[wheel] = float(value)
        self._write_camber()

    def _set_camber_axle(self, axle: str, value: float) -> None:
        """Mirror-mode entry point: one value per axle, right wheel negated."""
        value = float(value)
        if axle == "front":
            self._camber[0], self._camber[1] = value, -value
        else:
            self._camber[3], self._camber[2] = value, -value
        self._write_camber()

    def _set_camber_mirror(self, enabled: bool) -> None:
        self._camber_mirror = bool(enabled)
        # Also resyncs every slider (not just panel visibility) so the axle sliders
        # don't show a stale value left over from before an asymmetric per-wheel edit.
        self._sync_controls()

    def _reset_camber(self) -> None:
        stock = self.stock_camber
        self._camber = list(stock) if stock else [None, None, None, None]
        for key, value in zip(
            ("camber_fl", "camber_fr", "camber_rr", "camber_rl"), self._camber, strict=True
        ):
            slider = self._widgets.get(key)
            if slider is not None and value is not None:
                slider.set_value(value)
        for key, value in (
            ("camber_axle_front", self._camber[0]),
            ("camber_axle_rear", self._camber[3]),
        ):
            slider = self._widgets.get(key)
            if slider is not None and value is not None:
                slider.set_value(value)
        self._write_camber()
        self._camber = [None, None, None, None]

    def _write_track(self) -> bool:
        """Write held per-wheel track-width delta. vehicle.set_track_width() takes a
        delta in metres against each wheel's stock curve, so a None entry leaves
        that wheel untouched and repeating the same call doesn't shift it further.
        """
        vehicle = self.vehicle
        if vehicle is None:
            return False
        ok = True
        wrote = False
        for wheel, delta_mm in enumerate(self._track):
            if delta_mm is None:
                continue
            baseline = self.stock_track[wheel]
            if baseline is None:
                continue
            wrote = True
            ok = vehicle.set_track_width(wheel, baseline, delta_mm / 1000.0) and ok
        return wrote and ok

    def _set_track(self, wheel: int, value_mm: float) -> None:
        self._track[wheel] = float(value_mm)
        self._write_track()

    def _set_track_axle(self, axle: str, value_mm: float) -> None:
        """Mirror-mode entry point: one value per axle, both wheels move together."""
        value_mm = float(value_mm)
        if axle == "front":
            self._track[0] = self._track[1] = value_mm
        else:
            self._track[3] = self._track[2] = value_mm
        self._write_track()

    def _set_track_mirror(self, enabled: bool) -> None:
        self._track_mirror = bool(enabled)
        self._sync_controls()

    def _reset_track(self) -> None:
        vehicle = self.vehicle
        if vehicle is not None and all(curve is not None for curve in self.stock_track):
            for wheel, curve in enumerate(self.stock_track):
                vehicle.set_track_width(wheel, curve, 0.0)
        for key in ("track_fl", "track_fr", "track_rr", "track_rl",
                    "track_axle_front", "track_axle_rear"):
            slider = self._widgets.get(key)
            if slider is not None:
                slider.set_value(0.0)
        self._track = [None, None, None, None]

    def toggle(self) -> None:
        if not self.stock or self.vehicle is None:
            return
        if self._bounce:
            return
        self._cancel_ramp()
        self._lowered = not self._lowered

        if self._lowered:
            self._slam.play()
        start = self.vehicle.ride_height or self._target(not self._lowered)
        self._start_ramp(
            start, self._target(self._lowered), self._ramp_seconds, reverse=not self._lowered
        )

    def set_bounce(self, enabled: bool) -> None:
        """Start or stop the continuous up/down cycle."""
        enabled = bool(enabled)
        if enabled == self._bounce:
            return
        self._bounce = enabled
        if enabled:
            if not self.stock or self.vehicle is None:
                self._bounce = False
                self._controls_dirty = True
                return
            self._cancel_ramp()
            self._bounce_cancel.clear()
            self._bounce_thread = threading.Thread(
                target=self._bounce_loop, daemon=True, name="neptune-maybach"
            )
            self._bounce_thread.start()
            if self._bounce_audio:
                self._maybach.start()
        else:
            self._stop_bounce()

    def _stop_bounce(self) -> None:
        self._maybach.stop()
        self._bounce_cancel.set()
        thread = self._bounce_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._bounce_thread = None

        if self.stock and self.vehicle is not None:
            current = self.vehicle.ride_height
            self._start_ramp(
                current or self._baseline(), self._target(self._lowered), SETTLE_SECONDS
            )

    def _set_bounce_audio(self, enabled: bool) -> None:
        self._bounce_audio = bool(enabled)

        if self._bounce and self._bounce_audio:
            self._maybach.start()
        else:
            self._maybach.stop()

    def _bounce_loop(self) -> None:
        """Ride the car between the two heights until switched off.

        A cosine gives a smooth turnaround at both ends; a linear ramp would snap direction and
        make the solver jolt — the same "ramp, never step" rule the air ride follows.
        """
        import math

        interval = 1.0 / BOUNCE_HZ
        phase = 0.0
        try:
            while not self._bounce_cancel.is_set():
                if self.vehicle is None or not self.stock:
                    return
                low, high = sorted((self._bounce_low, self._bounce_high))

                phase += 2.0 * math.pi * self._bounce_speed * interval
                if phase > 2.0 * math.pi:
                    phase -= 2.0 * math.pi
                fraction = (1.0 - math.cos(phase)) * 0.5
                target = low + (high - low) * fraction

                heights = [self._clamp(target, wheel) for wheel in range(WHEEL_COUNT)]
                if not self._write(heights):
                    return
                time.sleep(interval)
        except Exception:
            return

    def _start_ramp(self, start, end, seconds: float, reverse: bool = False) -> None:
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._ramp,
            daemon=True,
            name="neptune-airride",
            args=(start, end, seconds, reverse),
        )
        self._thread.start()

    def _cancel_ramp(self) -> None:
        self._cancel.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    def _axle_phase(self, wheel: int, reverse: bool) -> tuple[float, float]:
        if self._sequence == "together":
            return 0.0, 1.0
        is_front = wheel in FRONT_WHEELS
        front_leads = (self._sequence == "front") != reverse
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
        if axle == "front":
            self._front_percent = float(value)
        else:
            self._rear_percent = float(value)
        if not self.stock or self.vehicle is None:
            return
        self._cancel_ramp()
        start = self.vehicle.ride_height or self._target(self._lowered)
        self._start_ramp(start, self._target(self._lowered), SETTLE_SECONDS)

    def _set_floor(self, value: float) -> None:
        self._floor_percent = float(value)
        if not self.stock or self.vehicle is None:
            return
        target = self._target(self._lowered)
        current = self.vehicle.ride_height
        if (
            current
            and len(current) == len(target)
            and all(abs(a - b) < 1e-4 for a, b in zip(current, target, strict=True))
        ):
            return
        self._cancel_ramp()
        self._start_ramp(current or target, target, SETTLE_SECONDS)

    def _set_sequence(self, label: str) -> None:
        for key, text in SEQUENCE_LABELS.items():
            if text == label:
                self._sequence = key
                return

    def _reset_height(self) -> None:
        self._front_percent = 0.0
        self._rear_percent = 0.0
        for key in ("front", "rear"):
            slider = self._widgets.get(key)
            if slider is not None:
                slider.set_value(0.0)
        if self.stock and self.vehicle is not None:
            self._cancel_ramp()
            start = self.vehicle.ride_height or self._target(self._lowered)
            self._start_ramp(start, self._target(self._lowered), SETTLE_SECONDS)

    def build_page(self, page) -> None:
        from neptune.ui.widgets.buttons import Button, PrimaryButton

        height_card = page.add_card("Ride height", "Moves the height the car normally sits at.")

        front = SliderRow(
            "Front",
            -LOWER_PERCENT_MAX,
            RAISE_PERCENT_MAX,
            0.0,
            step=1,
            decimals=0,
            unit="%",
            hint=HINT_AXLE,
        )
        front.changed.connect(lambda value: self._set_offset("front", value))
        self._widgets["front"] = front
        height_card.add(front)

        rear = SliderRow(
            "Rear",
            -LOWER_PERCENT_MAX,
            RAISE_PERCENT_MAX,
            0.0,
            step=1,
            decimals=0,
            unit="%",
            hint=HINT_AXLE,
        )
        rear.changed.connect(lambda value: self._set_offset("rear", value))
        self._widgets["rear"] = rear
        height_card.add(rear)

        reset_button = Button("Reset to stock height")
        reset_button.clicked.connect(self._reset_height)
        height_card.add(reset_button)

        camber_card = page.add_card("Camber", HINT_CAMBER)

        camber_mirror = ToggleRow("Mirror axles", self._camber_mirror, hint=HINT_CAMBER_MIRROR)
        camber_mirror.toggle.toggled_value.connect(self._set_camber_mirror)
        self._widgets["camber_mirror"] = camber_mirror
        camber_card.add(camber_mirror)

        stock_camber = self.stock_camber

        wheels_panel = QWidget()
        wheels_panel.setVisible(not self._camber_mirror)
        wheels_layout = QVBoxLayout(wheels_panel)
        wheels_layout.setContentsMargins(0, 0, 0, 0)
        wheels_layout.setSpacing(12)
        for wheel, (key, label) in enumerate(
            (
                ("camber_fl", "Front Left"),
                ("camber_fr", "Front Right"),
                ("camber_rr", "Rear Right"),
                ("camber_rl", "Rear Left"),
            )
        ):
            camber_slider = SliderRow(
                label,
                CAMBER_MIN_DEG,
                CAMBER_MAX_DEG,
                stock_camber[wheel] if stock_camber else 0.0,
                step=0.1,
                decimals=1,
                unit="°",
            )
            camber_slider.changed.connect(lambda value, w=wheel: self._set_camber(w, value))
            self._widgets[key] = camber_slider
            wheels_layout.addWidget(camber_slider)
        self._widgets["camber_wheels_panel"] = wheels_panel
        camber_card.add(wheels_panel)

        axle_panel = QWidget()
        axle_panel.setVisible(self._camber_mirror)
        axle_layout = QVBoxLayout(axle_panel)
        axle_layout.setContentsMargins(0, 0, 0, 0)
        axle_layout.setSpacing(12)
        for key, axle, label in (
            ("camber_axle_front", "front", "Front"),
            ("camber_axle_rear", "rear", "Rear"),
        ):
            axle_slider = SliderRow(
                label,
                CAMBER_MIN_DEG,
                CAMBER_MAX_DEG,
                stock_camber[0 if axle == "front" else 3] if stock_camber else 0.0,
                step=0.1,
                decimals=1,
                unit="°",
            )
            axle_slider.changed.connect(lambda value, a=axle: self._set_camber_axle(a, value))
            self._widgets[key] = axle_slider
            axle_layout.addWidget(axle_slider)
        self._widgets["camber_axle_panel"] = axle_panel
        camber_card.add(axle_panel)

        camber_reset_button = Button("Reset to stock camber")
        camber_reset_button.clicked.connect(self._reset_camber)
        camber_card.add(camber_reset_button)

        track_card = page.add_card("Track Width", HINT_TRACK)

        track_mirror = ToggleRow("Mirror axles", self._track_mirror, hint=HINT_TRACK_MIRROR)
        track_mirror.toggle.toggled_value.connect(self._set_track_mirror)
        self._widgets["track_mirror"] = track_mirror
        track_card.add(track_mirror)

        track_wheels_panel = QWidget()
        track_wheels_panel.setVisible(not self._track_mirror)
        track_wheels_layout = QVBoxLayout(track_wheels_panel)
        track_wheels_layout.setContentsMargins(0, 0, 0, 0)
        track_wheels_layout.setSpacing(12)
        for wheel, (key, label) in enumerate(
            (
                ("track_fl", "Front Left"),
                ("track_fr", "Front Right"),
                ("track_rr", "Rear Right"),
                ("track_rl", "Rear Left"),
            )
        ):
            track_slider = SliderRow(
                label,
                TRACK_MIN_MM,
                TRACK_MAX_MM,
                0.0,
                step=1,
                decimals=0,
                unit="mm",
            )
            track_slider.changed.connect(lambda value, w=wheel: self._set_track(w, value))
            self._widgets[key] = track_slider
            track_wheels_layout.addWidget(track_slider)
        self._widgets["track_wheels_panel"] = track_wheels_panel
        track_card.add(track_wheels_panel)

        track_axle_panel = QWidget()
        track_axle_panel.setVisible(self._track_mirror)
        track_axle_layout = QVBoxLayout(track_axle_panel)
        track_axle_layout.setContentsMargins(0, 0, 0, 0)
        track_axle_layout.setSpacing(12)
        for key, axle, label in (
            ("track_axle_front", "front", "Front"),
            ("track_axle_rear", "rear", "Rear"),
        ):
            track_axle_slider = SliderRow(
                label,
                TRACK_MIN_MM,
                TRACK_MAX_MM,
                0.0,
                step=1,
                decimals=0,
                unit="mm",
            )
            track_axle_slider.changed.connect(lambda value, a=axle: self._set_track_axle(a, value))
            self._widgets[key] = track_axle_slider
            track_axle_layout.addWidget(track_axle_slider)
        self._widgets["track_axle_panel"] = track_axle_panel
        track_card.add(track_axle_panel)

        track_reset_button = Button("Reset to stock track width")
        track_reset_button.clicked.connect(self._reset_track)
        track_card.add(track_reset_button)

        air_card = page.add_card("Air ride", "Drops the car on a key press.")

        toggle_button = PrimaryButton("Drop or lift now")
        toggle_button.clicked.connect(self.toggle)
        air_card.add(toggle_button)

        bind_button = BindButton(self.binding(), settings=self.settings, key="suspension.airride")
        bind_button.bound.connect(
            lambda binding: self.settings.set_binding("suspension.airride", binding)
        )
        self._widgets["bind"] = bind_button
        air_card.add(FieldRow("Control", bind_button))

        air_card.add_divider()

        drop = SliderRow(
            "Drop by",
            0.0,
            LOWER_PERCENT_MAX,
            DROP_PERCENT_DEFAULT,
            step=1,
            decimals=0,
            unit="%",
            hint=HINT_DROP,
        )
        drop.changed.connect(lambda value: setattr(self, "_drop_percent", float(value)))
        self._widgets["drop"] = drop
        air_card.add(drop)

        floor = SliderRow(
            "Lowest allowed",
            0.0,
            100.0,
            DEFAULT_FLOOR_PERCENT,
            step=1,
            decimals=0,
            unit="%",
            hint=HINT_FLOOR,
        )
        floor.changed.connect(self._set_floor)
        self._widgets["floor"] = floor
        air_card.add(floor)

        ramp = SliderRow(
            "Movement time",
            0.3,
            6.0,
            DEFAULT_RAMP_SECONDS,
            step=0.1,
            decimals=1,
            unit="s",
            hint=HINT_RAMP,
        )
        ramp.changed.connect(lambda value: setattr(self, "_ramp_seconds", float(value)))
        self._widgets["ramp"] = ramp
        air_card.add(ramp)

        sequence = Segmented(list(SEQUENCE_LABELS.values()), SEQUENCE_LABELS[self._sequence])
        sequence.changed.connect(self._set_sequence)
        self._widgets["sequence"] = sequence
        air_card.add(FieldRow("Order", sequence, hint=HINT_SEQUENCE))

        bounce_card = page.add_card("Maybach bounce", "Rocks the car up and down on repeat.")

        bounce_toggle = ToggleRow("Bounce", self._bounce, hint=HINT_BOUNCE)
        bounce_toggle.toggle.toggled_value.connect(self.set_bounce)
        self._widgets["bounce"] = bounce_toggle
        bounce_card.add(bounce_toggle)

        low = SliderRow(
            "Lowest",
            BOUNCE_MIN_M,
            BOUNCE_MAX_M,
            self._bounce_low,
            step=0.005,
            decimals=3,
            unit="m",
            hint=HINT_BOUNCE_RANGE,
        )
        low.changed.connect(lambda value: setattr(self, "_bounce_low", float(value)))
        self._widgets["bounce_low"] = low
        bounce_card.add(low)

        high = SliderRow(
            "Highest",
            BOUNCE_MIN_M,
            BOUNCE_MAX_M,
            self._bounce_high,
            step=0.005,
            decimals=3,
            unit="m",
        )
        high.changed.connect(lambda value: setattr(self, "_bounce_high", float(value)))
        self._widgets["bounce_high"] = high
        bounce_card.add(high)

        speed = SliderRow(
            "Speed",
            BOUNCE_SPEED_MIN,
            BOUNCE_SPEED_MAX,
            self._bounce_speed,
            step=0.1,
            decimals=1,
            unit="Hz",
            hint=HINT_BOUNCE_SPEED,
        )
        speed.changed.connect(lambda value: setattr(self, "_bounce_speed", float(value)))
        self._widgets["bounce_speed"] = speed
        bounce_card.add(speed)

        audio_toggle = ToggleRow("Play sound", self._bounce_audio, hint=HINT_BOUNCE_AUDIO)
        audio_toggle.toggle.toggled_value.connect(self._set_bounce_audio)
        self._widgets["bounce_audio"] = audio_toggle
        bounce_card.add(audio_toggle)

        live_card = page.add_card("Live")
        stats = StatStrip()
        stats.add("state", "State", "Stock")
        stats.add("front", "Front", "--")
        stats.add("rear", "Rear", "--")
        stats.add("camber_fl_live", "Camber Front Left", "--")
        stats.add("camber_fr_live", "Camber Front Right", "--")
        stats.add("camber_rr_live", "Camber Rear Right", "--")
        stats.add("camber_rl_live", "Camber Rear Left", "--")
        self._widgets["stats"] = stats
        live_card.add(stats)

    def refresh(self, vehicle) -> None:
        stats = self._widgets.get("stats")
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
            stats.set("state", "Moving", T.WARN)
        elif self._lowered:
            stats.set("state", "Lowered", T.ACCENT_BRIGHT)
        else:
            stats.set("state", "Stock", T.TEXT)

        stats.set("front", _axle_text(self.settings, current[0], current[1]), unit="")
        stats.set("rear", _axle_text(self.settings, current[2], current[3]), unit="")

        camber = vehicle.camber
        if camber:
            for key, value in zip(
                ("camber_fl_live", "camber_fr_live", "camber_rr_live", "camber_rl_live"),
                camber,
                strict=True,
            ):
                stats.set(key, f"{value:.2f}", unit="°")

    def save_state(self) -> dict:
        return {
            "units": "percent",
            "front": self._front_percent,
            "rear": self._rear_percent,
            "drop": self._drop_percent,
            "floor": self._floor_percent,
            "ramp_seconds": self._ramp_seconds,
            "sequence": self._sequence,
            "bounce_low": self._bounce_low,
            "bounce_high": self._bounce_high,
            "bounce_speed": self._bounce_speed,
            "bounce_audio": self._bounce_audio,
            "camber_fl": self._camber[0],
            "camber_fr": self._camber[1],
            "camber_rr": self._camber[2],
            "camber_rl": self._camber[3],
            "camber_mirror": self._camber_mirror,
            "track_fl": self._track[0],
            "track_fr": self._track[1],
            "track_rr": self._track[2],
            "track_rl": self._track[3],
            "track_mirror": self._track_mirror,
        }

    def load_state(self, data: dict) -> None:
        data = data or {}

        def _number(key, fallback, low=None, high=None):
            """A preset is a file on disk: treat every field as untrusted.

            ⚠️ A bare `float(...)` here raised on a hand-edited or corrupt preset (a string, None,
            or NaN), which aborted the whole load and left the module half-populated. Bad fields
            now fall back to the default instead.
            """
            try:
                value = float(data.get(key, fallback))
            except (TypeError, ValueError):
                return fallback
            if value != value:
                return fallback
            if low is not None:
                value = max(low, value)
            if high is not None:
                value = min(high, value)
            return value

        legacy = data.get("units") != "percent"
        typical = 0.15

        def _percent(key: str, fallback: float, low: float, high: float) -> float:
            if legacy and key in data:
                metres = _number(key, 0.0, -1.0, 1.0)
                return max(low, min(high, metres / typical * 100.0))
            return _number(key, fallback, low, high)

        self._front_percent = _percent("front", 0.0, -LOWER_PERCENT_MAX, RAISE_PERCENT_MAX)
        self._rear_percent = _percent("rear", 0.0, -LOWER_PERCENT_MAX, RAISE_PERCENT_MAX)
        self._drop_percent = _percent("drop", DROP_PERCENT_DEFAULT, 0.0, LOWER_PERCENT_MAX)
        self._floor_percent = _percent("floor", DEFAULT_FLOOR_PERCENT, 0.0, 100.0)
        self._ramp_seconds = _number("ramp_seconds", DEFAULT_RAMP_SECONDS, 0.3, 6.0)

        sequence = data.get("sequence")
        if sequence in SEQUENCE_LABELS:
            self._sequence = sequence

        self._bounce_low = _number("bounce_low", BOUNCE_DEFAULT_LOW, BOUNCE_MIN_M, BOUNCE_MAX_M)
        self._bounce_high = _number("bounce_high", BOUNCE_DEFAULT_HIGH, BOUNCE_MIN_M, BOUNCE_MAX_M)
        self._bounce_speed = _number(
            "bounce_speed", BOUNCE_DEFAULT_SPEED, BOUNCE_SPEED_MIN, BOUNCE_SPEED_MAX
        )
        self._bounce_audio = bool(data.get("bounce_audio", True))

        self._camber = [
            (
                _number(key, 0.0, CAMBER_MIN_DEG, CAMBER_MAX_DEG)
                if data.get(key) is not None
                else None
            )
            for key in ("camber_fl", "camber_fr", "camber_rr", "camber_rl")
        ]
        self._camber_mirror = bool(data.get("camber_mirror", True))
        if any(value is not None for value in self._camber):
            self._write_camber()

        self._track = [
            (
                _number(key, 0.0, TRACK_MIN_MM, TRACK_MAX_MM)
                if data.get(key) is not None
                else None
            )
            for key in ("track_fl", "track_fr", "track_rr", "track_rl")
        ]
        self._track_mirror = bool(data.get("track_mirror", True))
        if any(value is not None for value in self._track):
            self._write_track()

        self._controls_dirty = True
