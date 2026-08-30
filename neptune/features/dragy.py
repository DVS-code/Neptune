"""Dragy: times a run between two speeds.

0-60 mph, 100-200 km/h, or anything the user types. Arm it, drive, and the clock starts when the car
reaches the start speed and stops at the target.

Threshold crossings are interpolated between samples rather than snapped to whichever tick noticed
them, so a result does not vary with the tick rate.
"""

from __future__ import annotations

import time

from PySide6.QtWidgets import QLabel

from neptune.core.module import FeatureModule
from neptune.ui import theme as T
from neptune.ui.widgets.buttons import Button, PrimaryButton
from neptune.ui.widgets.card import Banner, FieldRow, StatStrip, ToggleRow
from neptune.ui.widgets.controls import Segmented
from neptune.ui.widgets.sliderrow import SliderRow

MS_TO_KPH = 3.6
MS_TO_MPH = 2.2369363


PRESETS = [
    ("0-60 mph", 0.0, 60.0, "mph"),
    ("0-100 mph", 0.0, 100.0, "mph"),
    ("60-130 mph", 60.0, 130.0, "mph"),
    ("100-200 mph", 100.0, 200.0, "mph"),
    ("150-250 mph", 150.0, 250.0, "mph"),
    ("0-100 km/h", 0.0, 100.0, "km/h"),
    ("0-200 km/h", 0.0, 200.0, "km/h"),
    ("100-200 km/h", 100.0, 200.0, "km/h"),
]
PRESET_BY_LABEL = {p[0]: p for p in PRESETS}
DEFAULT_PRESET = "0-60 mph"


METRES_PER_MILE = 1609.344
DISTANCE_PRESETS = [
    ("60 ft", METRES_PER_MILE / 88.0),
    ("1/8 mile", METRES_PER_MILE / 8.0),
    ("1/4 mile", METRES_PER_MILE / 4.0),
]
DISTANCE_BY_LABEL = {label: metres for label, metres in DISTANCE_PRESETS}

CUSTOM = "Custom"
DISTANCE = "Distance"
MODES = ("Preset", DISTANCE, CUSTOM)


RUN_TIMEOUT_SECONDS = 120.0

STANDSTILL_MS = 0.3

IDLE = "idle"
ARMED = "armed"
RUNNING = "running"
DONE = "done"

STATE_TEXT = {
    IDLE: "Not armed",
    ARMED: "Armed — waiting for the start speed",
    RUNNING: "Timing",
    DONE: "Finished",
}

HINT_OVERLAY = "Shows the timer on top of the game while you drive."
HINT_START = "The speed the clock starts at."
HINT_TARGET = "The speed the clock stops at."
HINT_DISTANCE = (
    "Timed from a standing start. Come to a stop, arm, then launch. "
    "Distance is measured from the car's own speed."
)
NOTE_ARMED_ROLLING = (
    "Slow below the start speed before the run — the clock starts when you cross it going up."
)


HISTORY_LENGTH = 5


def to_ms(value: float, unit: str) -> float:
    return value / (MS_TO_MPH if unit == "mph" else MS_TO_KPH)


def from_ms(value: float, unit: str) -> float:
    return value * (MS_TO_MPH if unit == "mph" else MS_TO_KPH)


class DragyModule(FeatureModule):
    name = "dragy"
    title = "Dragy"
    subtitle = "Times how long the car takes between two speeds."
    icon = "dragy.png"
    group = "Vehicle"
    order = 35

    always_refresh = True

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

        self._mode = "Preset"
        self._preset = DEFAULT_PRESET
        self._preset_unit = "mph"
        self._custom_start = 0.0
        self._custom_target = 60.0
        self._custom_unit = "mph"
        self._distance = "1/4 mile"
        self._travelled = 0.0
        self._trap_speed: float | None = None

        self._state = IDLE
        self._start_time: float | None = None
        self._result: float | None = None
        self._armed_at = 0.0

        self._last_speed: float | None = None
        self._last_stamp: float | None = None
        self._below_start = False

        self._history: list[tuple[str, float]] = []
        self._history_dirty = True

        self._overlay_enabled = False
        self._overlay = None
        self._process = None
        self._controls_dirty = False
        self._widgets: dict = {}

    def _definition(self) -> tuple[float, float, str]:
        """(start, target, unit) for the currently selected run."""
        if self._mode == CUSTOM:
            return self._custom_start, self._custom_target, self._custom_unit
        _, start, target, unit = PRESET_BY_LABEL.get(self._preset, PRESET_BY_LABEL[DEFAULT_PRESET])
        return start, target, unit

    def _is_distance(self) -> bool:
        return self._mode == DISTANCE

    def _distance_metres(self) -> float:
        return DISTANCE_BY_LABEL.get(self._distance, DISTANCE_BY_LABEL["1/4 mile"])

    def _label(self) -> str:
        if self._is_distance():
            return self._distance
        start, target, unit = self._definition()
        return f"{start:g}-{target:g} {unit}"

    def _valid(self) -> bool:
        if self._is_distance():
            return self._distance_metres() > 0.0
        start, target, _ = self._definition()
        return target > start >= 0.0

    def tick_process(self, process) -> None:
        """Dispatched even with no car, which is how the overlay learns the game's pid."""
        self._process = process

    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle
        if vehicle is not None:
            self._process = vehicle.process
        self._reset_run()

        if self._overlay_enabled:
            self._ensure_overlay()

    def on_car_changed(self, vehicle) -> None:
        self.vehicle = vehicle
        self.disarm()

    def on_detach(self) -> None:
        self.vehicle = None
        self.disarm()

    def shutdown(self) -> None:
        if self._overlay is not None:
            self._overlay.stop()
            self._overlay.deleteLater()
            self._overlay = None

    def _reset_run(self) -> None:
        self._start_time = None
        self._last_speed = None
        self._last_stamp = None
        self._below_start = False
        self._travelled = 0.0
        self._trap_speed = None

    def arm(self) -> None:
        if not self._valid():
            return
        self._state = ARMED
        self._result = None
        self._armed_at = time.perf_counter()
        self._reset_run()
        self._controls_dirty = True

    def disarm(self) -> None:
        self._state = IDLE
        self._reset_run()
        self._controls_dirty = True

    def toggle_arm(self) -> None:
        if self._state in (ARMED, RUNNING):
            self.disarm()
        else:
            self.arm()

    def reset(self) -> None:
        """Clear the last result and stand down, ready to arm again."""
        self._state = IDLE
        self._result = None
        self._reset_run()
        self._controls_dirty = True

    def tick(self, vehicle) -> None:
        self.vehicle = vehicle
        if vehicle is None or self._state not in (ARMED, RUNNING):
            return

        speed = vehicle.speed_ms
        now = time.perf_counter()
        if speed is None or speed != speed:
            return

        if self._is_distance():
            self._tick_distance(speed, now)
            return

        start, target, unit = self._definition()
        start_ms = to_ms(start, unit)
        target_ms = to_ms(target, unit)

        standing = start_ms <= 0.0

        previous, stamp = self._last_speed, self._last_stamp
        self._last_speed, self._last_stamp = speed, now
        if previous is None or stamp is None:
            self._below_start = True if standing else speed <= start_ms
            return

        if self._state == ARMED:
            if standing:
                self._below_start = self._below_start or speed <= STANDSTILL_MS
            if not self._below_start:
                if speed <= start_ms:
                    self._below_start = True
                return
            if start_ms <= 0.0:
                if speed > STANDSTILL_MS:
                    self._start_time = self._crossing(
                        previous, speed, stamp, now, 0.0, allow_before=True
                    )
                    self._state = RUNNING
                    self._controls_dirty = True
                return
            if speed > start_ms >= previous:
                self._start_time = self._crossing(previous, speed, stamp, now, start_ms)
                self._state = RUNNING
                self._controls_dirty = True
            return

        if now - (self._start_time or now) > RUN_TIMEOUT_SECONDS:
            self.disarm()
            return
        if speed >= target_ms > previous:
            crossed = self._crossing(previous, speed, stamp, now, target_ms)
            self._result = max(0.0, crossed - (self._start_time or crossed))
            self._state = DONE
            self._record(self._label(), self._result)
            self._controls_dirty = True

    def _tick_distance(self, speed: float, now: float) -> None:
        """Time a standing-start run over a fixed distance.

        There is no position to read — Data Out publishes none on this build — so the
        distance is integrated from speed. Speed is sampled at ~184 Hz and is continuous,
        which is what makes this accurate enough to time with.
        """
        previous, stamp = self._last_speed, self._last_stamp
        self._last_speed, self._last_stamp = speed, now
        if previous is None or stamp is None:
            self._below_start = True
            return

        if self._state == ARMED:
            self._below_start = self._below_start or speed <= STANDSTILL_MS
            if not self._below_start:
                if speed <= STANDSTILL_MS:
                    self._below_start = True
                return
            if speed > STANDSTILL_MS:
                step = now - stamp
                rate = (speed - previous) / step if step > 0.0 else 0.0
                started = now - (speed / rate) if rate > 0.0 else now
                started = min(started, now)
                self._start_time = started

                tail = max(0.0, now - started)
                self._travelled = speed * 0.5 * tail
                self._trap_speed = None
                self._state = RUNNING
                self._controls_dirty = True
            return

        if now - (self._start_time or now) > RUN_TIMEOUT_SECONDS:
            self.disarm()
            return

        step = now - stamp
        if step <= 0.0:
            return

        gained = (previous + speed) * 0.5 * step
        target = self._distance_metres()
        if self._travelled + gained < target:
            self._travelled += gained
            return

        remaining = target - self._travelled
        fraction = remaining / gained if gained > 0.0 else 0.0
        crossed = stamp + step * max(0.0, min(1.0, fraction))
        self._travelled = target
        self._trap_speed = previous + (speed - previous) * max(0.0, min(1.0, fraction))
        self._result = max(0.0, crossed - (self._start_time or crossed))
        self._state = DONE
        self._record(self._label(), self._result)
        self._controls_dirty = True

    def _record(self, label: str, seconds: float) -> None:
        """Keep the newest runs, most recent first."""
        self._history.insert(0, (label, float(seconds)))
        del self._history[HISTORY_LENGTH:]
        self._history_dirty = True

    def _clear_history(self) -> None:
        self._history.clear()
        self._history_dirty = True

    def _paint_history(self) -> None:
        rows = self._widgets.get("history_rows")
        if not rows:
            return
        best = min((t for _, t in self._history), default=None)
        for index, row in enumerate(rows):
            if index >= len(self._history):
                row.setText("No runs yet" if index == 0 else "")
                row.setStyleSheet("")
                continue
            label, seconds = self._history[index]
            mark = "   best" if best is not None and seconds <= best else ""
            row.setText(f"{seconds:.2f}s    {label}{mark}")

            row.setStyleSheet(f"color: {T.ACCENT_BRIGHT};" if mark else "")

    @staticmethod
    def _crossing(
        previous: float,
        current: float,
        then: float,
        now: float,
        threshold: float,
        allow_before: bool = False,
    ) -> float:
        """The moment the speed actually crossed `threshold`, between two samples.

        Linear interpolation. At 100 Hz a car doing 0-60 moves ~0.3 mph per tick, so taking the
        sample's own timestamp would quantise the result to the tick; interpolating removes that.

        `allow_before` lets the result land BEFORE `then`, by extrapolating back along the same
        slope. Needed only for a standing start, where the launch happened somewhere between the
        last stationary reading and the first moving one.
        """
        span = current - previous
        if span <= 0.0:
            return now
        fraction = (threshold - previous) / span
        low = -8.0 if allow_before else 0.0
        fraction = max(low, min(1.0, fraction))
        return then + (now - then) * fraction

    def set_overlay(self, enabled: bool) -> None:
        self._overlay_enabled = bool(enabled)
        if not enabled:
            if self._overlay is not None:
                self._overlay.stop()
            return
        self._ensure_overlay()

    def _ensure_overlay(self):
        """Create the overlay if needed and bind it to the game window."""
        if self._overlay is None:
            try:
                from neptune.ui.dragyoverlay import DragyOverlay
            except Exception:
                return None
            self._overlay = DragyOverlay()
            self._overlay.set_settings(self.settings)
            self._overlay.set_callbacks(self.toggle_arm, self.reset)

        pid = self._process.pid if self._process is not None else None
        self._overlay.start(pid)
        return self._overlay

    def build_page(self, page) -> None:
        run_card = page.add_card("Run", "Pick the two speeds to time between.")

        mode = Segmented(list(MODES), self._mode)
        mode.changed.connect(self._set_mode)
        self._widgets["mode"] = mode
        run_card.add(FieldRow("Mode", mode))

        units = Segmented(["mph", "km/h"], self._preset_unit)
        units.changed.connect(self._set_preset_unit)
        self._widgets["preset_unit"] = units
        self._widgets["preset_unit_row"] = FieldRow("Units", units)
        run_card.add(self._widgets["preset_unit_row"])

        imperial = Segmented([p[0] for p in PRESETS if p[3] == "mph"], self._preset)
        imperial.changed.connect(self._set_preset)
        self._widgets["preset"] = imperial
        self._widgets["preset_row"] = FieldRow("Run", imperial)
        run_card.add(self._widgets["preset_row"])

        metric = Segmented([p[0] for p in PRESETS if p[3] == "km/h"], None)
        metric.changed.connect(self._set_preset)
        self._widgets["metric"] = metric
        self._widgets["metric_row"] = FieldRow("Run", metric)
        run_card.add(self._widgets["metric_row"])

        distance = Segmented([label for label, _ in DISTANCE_PRESETS], self._distance)
        distance.changed.connect(self._set_distance)
        self._widgets["distance"] = distance
        self._widgets["distance_row"] = FieldRow("Distance", distance, hint=HINT_DISTANCE)
        run_card.add(self._widgets["distance_row"])

        unit = Segmented(["mph", "km/h"], self._custom_unit)
        unit.changed.connect(self._set_custom_unit)
        self._widgets["unit"] = unit
        self._widgets["unit_row"] = FieldRow("Custom unit", unit)
        run_card.add(self._widgets["unit_row"])

        start = SliderRow(
            "From",
            0,
            300,
            self._custom_start,
            step=5,
            decimals=0,
            unit=self._custom_unit,
            hint=HINT_START,
        )
        start.changed.connect(lambda v: self._set_custom("start", v))
        self._widgets["start"] = start
        run_card.add(start)

        target = SliderRow(
            "To",
            5,
            400,
            self._custom_target,
            step=5,
            decimals=0,
            unit=self._custom_unit,
            hint=HINT_TARGET,
        )
        target.changed.connect(lambda v: self._set_custom("target", v))
        self._widgets["target"] = target
        run_card.add(target)

        arm_button = PrimaryButton("Arm")
        arm_button.clicked.connect(self.toggle_arm)
        self._widgets["arm"] = arm_button
        run_card.add(arm_button)

        reset_button = Button("Reset")
        reset_button.clicked.connect(self.reset)
        run_card.add(reset_button)

        result_card = page.add_card("Result")
        stats = StatStrip()
        stats.add("time", "Time", "--")
        stats.add("run", "Run", self._label())
        stats.add("state", "State", STATE_TEXT[IDLE])
        stats.add("speed", "Speed", "--")
        self._widgets["stats"] = stats
        result_card.add(stats)

        banner = Banner("", "info")
        banner.setVisible(False)
        self._widgets["banner"] = banner
        result_card.add(banner)

        history_card = page.add_card("Recent runs", "Your last five finished runs.")
        rows = []
        for index in range(HISTORY_LENGTH):
            row = QLabel("")
            row.setObjectName("RowHint" if index else "StatValue")
            rows.append(row)
            history_card.add(row)
        self._widgets["history_rows"] = rows

        clear_button = Button("Clear runs")
        clear_button.clicked.connect(self._clear_history)
        history_card.add(clear_button)

        overlay_card = page.add_card("Overlay")
        overlay = ToggleRow("Show on top of the game", False, hint=HINT_OVERLAY)
        overlay.toggle.toggled_value.connect(self.set_overlay)
        self._widgets["overlay"] = overlay
        overlay_card.add(overlay)

        self._update_custom_visibility()

    def _set_mode(self, value: str) -> None:
        self._mode = value
        self._update_custom_visibility()
        self.disarm()

    def _set_distance(self, label: str) -> None:
        if label in DISTANCE_BY_LABEL:
            self._distance = label
        self.disarm()

    def _set_preset(self, label: str) -> None:
        if label in PRESET_BY_LABEL:
            self._preset = label
            self._mode = "Preset"
            self.disarm()

    def _set_custom_unit(self, unit: str) -> None:
        self._custom_unit = unit
        self._sync_custom_units()
        self.disarm()

    def _sync_custom_units(self) -> None:
        """Label the From/To sliders with the unit they are actually in.

        Without this they read as bare numbers while the unit selector sits above them,
        so "From 60" gives no way to tell whether it means mph or km/h.
        """
        for key in ("start", "target"):
            slider = self._widgets.get(key)
            if slider is not None:
                slider.set_unit(self._custom_unit)

    def _set_custom(self, which: str, value: float) -> None:
        if which == "start":
            self._custom_start = float(value)
        else:
            self._custom_target = float(value)
        self.disarm()

    def _update_custom_visibility(self) -> None:
        """Show only the controls that belong to the current mode and unit.

        Hides whole FieldRows, not just the inner widget — leaving the row visible left an orphaned
        'Custom unit' label on screen while in Preset mode.
        """
        custom = self._mode == CUSTOM
        distance = self._mode == DISTANCE
        speeds = not custom and not distance
        for key in ("unit_row", "start", "target"):
            widget = self._widgets.get(key)
            if widget is not None:
                widget.setVisible(custom)
        row = self._widgets.get("distance_row")
        if row is not None:
            row.setVisible(distance)
        row = self._widgets.get("preset_unit_row")
        if row is not None:
            row.setVisible(speeds)
        imperial = self._widgets.get("preset_row")
        if imperial is not None:
            imperial.setVisible(speeds and self._preset_unit == "mph")
        metric = self._widgets.get("metric_row")
        if metric is not None:
            metric.setVisible(speeds and self._preset_unit == "km/h")

    def _set_preset_unit(self, unit: str) -> None:
        """Switch the preset list between mph and km/h, keeping one run selected."""
        self._preset_unit = unit
        matching = [p for p in PRESETS if p[3] == unit]
        if matching and PRESET_BY_LABEL.get(self._preset, ("", 0, 0, ""))[3] != unit:
            self._preset = matching[0][0]
            widget = self._widgets.get("preset" if unit == "mph" else "metric")
            if widget is not None:
                widget.set_value(self._preset)
        self._update_custom_visibility()
        self.disarm()

    def refresh(self, vehicle) -> None:
        speed = vehicle.speed_ms if vehicle is not None else None

        if self._overlay_enabled and self._overlay is not None:
            self._overlay.update_run(
                self._label(),
                self._state,
                self._result,
                self._start_time,
                speed,
                self._definition()[2],
                self._history,
            )

        stats = self._widgets.get("stats")
        if stats is None:
            return

        if self._controls_dirty:
            self._controls_dirty = False
            button = self._widgets.get("arm")
            if button is not None:
                button.setText("Disarm" if self._state in (ARMED, RUNNING) else "Arm")

        if self._history_dirty:
            self._history_dirty = False
            self._paint_history()

        stats.set("run", self._label(), unit="")
        stats.set(
            "state",
            STATE_TEXT.get(self._state, ""),
            T.ACCENT_BRIGHT if self._state == RUNNING else None,
            unit="",
        )

        if self._result is not None:
            stats.set("time", f"{self._result:.2f}", T.ACCENT_BRIGHT, unit="s")
        elif self._state == RUNNING and self._start_time is not None:
            stats.set("time", f"{time.perf_counter() - self._start_time:.2f}", unit="s")
        else:
            stats.set("time", "--", T.TEXT_FAINT, unit="")

        if speed is None:
            stats.set("speed", "--", T.TEXT_FAINT, unit="")
        else:
            _, _, unit = self._definition()
            stats.set("speed", f"{from_ms(speed, unit):.0f}", unit=f" {unit}")

        banner = self._widgets.get("banner")
        if banner is not None:
            start, _, _ = self._definition()
            if not self._valid():
                banner.set("The finish speed must be higher than the start speed.", "warn")
            elif self._state == ARMED and start > 0 and not self._below_start:
                banner.set(NOTE_ARMED_ROLLING, "info")
            else:
                banner.setVisible(False)

    def save_state(self) -> dict:
        return {
            "mode": self._mode,
            "preset": self._preset,
            "distance": self._distance,
            "custom_start": self._custom_start,
            "custom_target": self._custom_target,
            "custom_unit": self._custom_unit,
            "overlay": self._overlay_enabled,
        }

    def load_state(self, data: dict) -> None:
        data = data or {}

        def _number(key, fallback, low, high):
            try:
                value = float(data.get(key, fallback))
            except (TypeError, ValueError):
                return fallback
            if value != value:
                return fallback
            return max(low, min(high, value))

        self._mode = data.get("mode") if data.get("mode") in MODES else "Preset"
        preset = data.get("preset")
        self._preset = preset if preset in PRESET_BY_LABEL else DEFAULT_PRESET
        distance = data.get("distance")
        self._distance = distance if distance in DISTANCE_BY_LABEL else "1/4 mile"
        self._custom_start = _number("custom_start", 0.0, 0.0, 300.0)
        self._custom_target = _number("custom_target", 60.0, 5.0, 400.0)
        self._custom_unit = (
            data.get("custom_unit") if data.get("custom_unit") in ("mph", "km/h") else "mph"
        )

        self.set_overlay(bool(data.get("overlay", False)))
        toggle = self._widgets.get("overlay")
        if toggle is not None:
            toggle.toggle.set_value(self._overlay_enabled)

        for key, value in (
            ("mode", self._mode),
            ("distance", self._distance),
            ("preset_unit", self._preset_unit),
            ("unit", self._custom_unit),
        ):
            widget = self._widgets.get(key)
            if widget is not None:
                widget.set_value(value)
        widget = self._widgets.get("preset" if self._preset_unit == "mph" else "metric")
        if widget is not None:
            widget.set_value(self._preset)
        for key, value in (("start", self._custom_start), ("target", self._custom_target)):
            slider = self._widgets.get(key)
            if slider is not None:
                slider.set_value(value)
        self._sync_custom_units()
        self._update_custom_visibility()

        self._state = IDLE
        self._controls_dirty = True
