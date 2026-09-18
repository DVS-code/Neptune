"""Engine: torque curve, torque multiplier, rev limit and anti-lag."""

from __future__ import annotations

import math
import time

from PySide6.QtWidgets import QHBoxLayout

from neptune.core import input as inp
from neptune.core.module import FeatureModule
from neptune.features.cam import (
    CAM_IDLE_LOPE_DEFAULT_HZ,
    CAM_IDLE_LOPE_MAX_HZ,
    CAM_IDLE_LOPE_MIN_HZ,
    CAM_MODE_RPM,
    CAM_MODES,
    CAM_SHARPNESS_MAX,
    CAM_SHARPNESS_MIN,
    CAM_TUNING_DEFAULT,
    CAM_TUNING_MAX,
    CAM_TUNING_MIN,
    CAM_WAVE_SHARPNESS,
    DEFAULT_FADE_SECONDS,
    MAX_FADE_SECONDS,
    MIN_FADE_SECONDS,
    CamController,
    apply_cam_shape,
)
from neptune.features.launchcontrol import (
    LC_DEFAULT_MAX_NORM,
    LC_DEFAULT_MIN_NORM,
    LaunchControlController,
)
from neptune.ui import theme as T
from neptune.ui.widgets.buttons import Button, PrimaryButton
from neptune.ui.widgets.card import Banner, FieldRow, StatStrip, ToggleRow, bind_progressive
from neptune.ui.widgets.controls import BindButton, Segmented
from neptune.ui.widgets.sliderrow import SliderRow
from neptune.ui.widgets.torquegraph import TorqueGraph
from neptune.vehicle.vehicle import MAX_CURVE_POINTS

SPEED_CAP_HYSTERESIS_KPH = 3.0
REAPPLY_INTERVAL = 1.0
MIN_CURVE_POINTS = 8

TORQUE_MIN = 0.25
TORQUE_MAX = 4.0
REV_MIN = 3000
REV_MAX = 12000
HOLD_MIN = 1500
HOLD_MAX = 9000

CAM_AGGRESSIVENESS_MIN = 0.0
CAM_AGGRESSIVENESS_MAX = 1.0
CAM_RELEASE_RPM_MIN = 1500
CAM_RELEASE_RPM_MAX = 8000
CAM_WRITE_INTERVAL = 0.03
CAM_IDLE_MATCH_RPM = 0.5

LC_UI_MIN = 0.0
LC_UI_MAX = 100.0

BOUNCE_BAND = 350
CUT_MS = 30
LIFT_MS = 30

LAUNCH_HANDOFF_SECONDS = 0.65
LAUNCH_HANDOFF_MIN_RPM = 2200.0
LAUNCH_HANDOFF_MAX_SPEED_MS = 4.0
LAUNCH_HANDOFF_MIN_THROTTLE = 0.50
LAUNCH_HANDOFF_MAX_ASSIST = 0.65

HINT_TORQUE = "Multiplies engine torque across the whole rev range."
HINT_REV = (
    "Changes the rev limiter. For automatic transmission the shift points will stay the "
    "same, so manual shifting is recommended. To apply the rev limiter change you must "
    "reset car position."
)
HINT_ANTILAG = "Hold the bound control to sit on the limiter and build boost. Release to launch."
HINT_SPEED_CAP = "Anti-lag releases past this speed, like a launch control."
HINT_LAUNCH = (
    "Builds boost while the game's own launch control is active, so you "
    "launch with more boost than the game gives you. Bind this to the same "
    "control you use for launch control \u2014 your e-brake. Release starts a "
    "short torque handoff to prevent the launch RPM from falling away."
)
HINT_CAM = (
    "Adds a real uneven low-rpm cam lope through the live idle target and engine curve. It is "
    "strongest at low rpm and fades from the moment the throttle moves, reaching zero at 25% "
    "throttle. Higher aggressiveness also makes the idle swing and recovery pulse sharper."
)
HINT_CAM_AGGRESSIVENESS = (
    "How much low-rpm overlap, torque pulsing and high-rpm breathing the cam adds. "
    "At 100% it uses the super-choppy large-cam profile."
)
HINT_CAM_FREQUENCY = (
    f"How often the idle lope repeats. Adjustable from {CAM_IDLE_LOPE_MIN_HZ:.1f} to "
    f"{CAM_IDLE_LOPE_MAX_HZ:.1f} Hz; {CAM_IDLE_LOPE_DEFAULT_HZ:.1f} Hz is the tested default."
)
HINT_CAM_DEPTH = "Scales how far the live idle target falls and rises. 100% is the super-choppy profile."
HINT_CAM_TORQUE_DIP = "Scales the torque cut at the bottom of each cam pulse."
HINT_CAM_RECOVERY = "Scales the torque recovery after each cam pulse."
HINT_CAM_SHARPNESS = "Controls how abruptly the cam snaps between its drop and recovery."
HINT_CAM_MODE = "RPM mode follows engine speed. Throttle mode follows the pedal only."
HINT_CAM_RELEASE = "The rpm where the cam's low-speed overlap has mostly faded in RPM mode."
HINT_CAM_FADE = "How long the cam takes to return after the pedal passes the 25% gate."
HINT_LC = (
    "The game stores launch control as a normalized RPM window. The sliders are percentages "
    "of the engine band; 30% start and 80% limit are the stock values found in this build."
)
HINT_LAUNCH_ARM = (
    "Arms the hold control. Turning it off releases the launch immediately and stops "
    "Neptune building boost during it."
)
NOTE_LAUNCH_GAME_LC = (
    "Leave the game's launch control ON (Settings > Difficulty) \u2014 "
    "this builds boost while it holds the revs."
)


def _contiguous_runs(pending: dict[int, float]) -> list[tuple[int, list[float]]]:
    runs: list[tuple[int, list[float]]] = []
    for index in sorted(pending):
        if runs and index == runs[-1][0] + len(runs[-1][1]):
            runs[-1][1].append(pending[index])
        else:
            runs.append((index, [pending[index]]))
    return runs


def _smoothstep01(value: float) -> float:
    value = max(0.0, min(1.0, float(value)))
    return value * value * (3.0 - 2.0 * value)


def apply_launch_handoff(
    curve: list[float],
    rpm_per_index: float,
    current_rpm: float,
    target_rpm: float,
    max_assist: float = LAUNCH_HANDOFF_MAX_ASSIST,
) -> list[float]:
    """Add short-lived low/mid-RPM torque support during launch handoff.

    Releasing the game's launch input transfers the engine from its launch
    limiter to the drivetrain in one frame.  The live telemetry showed that
    transfer pulling the engine from about 5.7k to 2.4k RPM even while boost
    stayed high.  This helper only adds torque below the captured launch RPM,
    leaves the limiter tail untouched, and returns to the user's curve as soon
    as the engine catches the target.
    """
    if (
        len(curve) < 2
        or rpm_per_index <= 0.0
        or target_rpm <= 0.0
        or current_rpm >= target_rpm
        or max_assist <= 0.0
    ):
        return list(curve)

    gap = (target_rpm - max(0.0, current_rpm)) / max(500.0, target_rpm * 0.45)
    gap = max(0.0, min(1.0, gap))
    gap = _smoothstep01(gap)
    band_start = max(500.0, target_rpm * 0.20)
    band_end = max(band_start + 1.0, target_rpm * 0.95)

    shaped = list(curve)
    for index, value in enumerate(curve[:-1]):
        rpm = index * rpm_per_index
        envelope = 1.0 - _smoothstep01((rpm - band_start) / (band_end - band_start))
        shaped[index] = value * (1.0 + max_assist * gap * envelope)
    return shaped


class EngineModule(FeatureModule):
    name = "engine"
    title = "Engine"
    subtitle = "Torque delivery, rev limit and launch behaviour."
    icon = "engine.png"
    group = "Vehicle"
    order = 10

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

        self.stock_curve: list[float] = []
        self.stock_ceiling: float | None = None
        self.stock_redline: float | None = None
        self.stock_thresh: float | None = None
        self.stock_neg_clamp: float | None = None

        self._torque_multiplier = 1.0
        self._rev_limit: float | None = None
        self._custom_curve: list[float] | None = None
        self._last_reapply = 0.0
        self._pending_edits: dict[int, float] = {}

        self._process = None
        self._launch_control = LaunchControlController()
        self._lc_enabled = False
        self._lc_min_norm = LC_DEFAULT_MIN_NORM
        self._lc_max_norm = LC_DEFAULT_MAX_NORM
        self._lc_user_configured = False

        self._cam = CamController()
        self._cam_enabled = False
        self._cam_mode = CAM_MODE_RPM
        self._cam_aggressiveness = 0.50
        self._cam_frequency_hz = CAM_IDLE_LOPE_DEFAULT_HZ
        self._cam_depth = CAM_TUNING_DEFAULT
        self._cam_torque_dip = CAM_TUNING_DEFAULT
        self._cam_recovery_pulse = CAM_TUNING_DEFAULT
        self._cam_sharpness = CAM_WAVE_SHARPNESS
        self._cam_release_rpm = 3500.0
        self._cam_fade_seconds = DEFAULT_FADE_SECONDS
        self._cam_last_write = 0.0
        self._cam_last_curve: list[float] | None = None
        self._cam_frame = None
        self._cam_idle_stock_rpm: float | None = None
        self._cam_idle_written: float | None = None

        self._armed = False
        self._engaged = False
        self._ready = False
        self._cut = False

        self._wall_tail_written = False
        self._phase_started = 0.0
        self._hold_rpm = 4000
        self._build_boost = True
        self._turbo = None

        self._launch_armed = False
        self._launch_engaged = False
        self._launch_handoff_until = 0.0
        self._launch_handoff_target_rpm: float | None = None

        self._speed_cap_enabled = False
        self._speed_cap_ms = 40.0 / 3.6
        self._over_cap = False

        self._controls_dirty = False
        self._widgets: dict = {}

    def bind_turbo(self, turbo) -> None:
        self._turbo = turbo

    @property
    def anti_lag_active(self) -> bool:
        return self._engaged

    @property
    def launch_active(self) -> bool:
        return self._launch_engaged or self._launch_handoff_active()

    def tick_process(self, process) -> None:
        """Resolve and maintain the global launch-control RPM window."""
        previous_pid = self._process.pid if self._process is not None else None
        self._process = process
        was_ready = self._launch_control.ready
        ready = self._launch_control.attach(process)

        if (
            ready
            and (previous_pid != process.pid or not was_ready)
            and not self._lc_user_configured
        ):
            stock = self._launch_control.stock_values
            if stock is not None:
                self._lc_min_norm, self._lc_max_norm = stock
                self._controls_dirty = True

        self._launch_control.set_targets(self._lc_min_norm, self._lc_max_norm)
        if self._lc_enabled and ready:
            if not self._launch_control.enabled:
                self._launch_control.set_enabled(True)
            else:
                self._launch_control.tick()

    def binding(self) -> dict | None:
        return self.settings.binding("engine.antilag")

    def launch_binding(self) -> dict | None:
        return self.settings.binding("engine.launch")

    def bindings(self) -> list[dict]:
        return [
            {
                "key": "engine.antilag",
                "label": "Hold for anti-lag",
                "description": "Hold to sit on the limiter and build boost.",
            },
            {
                "key": "engine.launch",
                "label": "Hold for enhanced launch control",
                "description": "Hold while the game holds the revs, to build boost for the launch.",
            },
        ]

    def on_attach(self, vehicle) -> None:
        """Capture stock values. Never touches widgets: this runs off the interface thread."""
        self.vehicle = vehicle
        self._cancel_launch_handoff()
        self._cam.reset()
        self._cam_last_write = 0.0
        self._cam_last_curve = None
        self._cam_frame = None
        self._cam_idle_stock_rpm = None
        self._cam_idle_written = None

        if vehicle is None:
            return
        self.stock_curve = vehicle.curve()
        # game mirrors it into the engine model field.
        self._cam_idle_stock_rpm = vehicle.idle_rpm
        if self._rev_limit is None:
            self.stock_ceiling = vehicle.rev_ceiling
            self.stock_redline = vehicle.redline
            # The limiter is three engine-model fields, not one: MAX_CLAMP bounds the curve,
            # THRESH is where it cuts/shifts, NEG_CLAMP is the hard cut. Capture all three so
            # the slider can raise them together and put them back.
            self.stock_thresh = vehicle.shift_threshold
            self.stock_neg_clamp = vehicle.neg_clamp
        self._controls_dirty = True

    def on_car_changed(self, vehicle) -> None:
        self._restore_cam_idle()
        self._engaged = False
        self._ready = False
        self._launch_engaged = False
        self._cancel_launch_handoff()
        # The torque multiplier is a RELATIVE factor, so it carries across cars: it
        # re-applies to the new car's own stock curve. Everything below it is absolute
        # and car-specific (an rpm, a hand-shaped curve), so it is dropped.
        self._rev_limit = None
        self._custom_curve = None
        self._pending_edits.clear()
        self._cam_enabled = False
        self._cam.reset()
        self.on_attach(vehicle)
        # on_attach has just captured the NEW car's stock curve, so applying now scales
        # that curve rather than carrying the old car's absolute values across.
        if abs(self._torque_multiplier - 1.0) > 1e-6:
            self._apply_curve()

    def on_car_reloaded(self, vehicle) -> None:
        self.vehicle = vehicle
        if self._cam_idle_stock_rpm is None:
            self._cam_idle_stock_rpm = vehicle.idle_rpm
        if self._curve_is_tuned():
            self._reapply_curve_state()

    def on_detach(self) -> None:
        # The idle stock and last cam write are kept: a reload is the same car, and the
        # restore guard needs both to tell whether the game rebuilt the idle field.
        self.vehicle = None
        self._engaged = False
        self._ready = False
        self._launch_engaged = False
        self._cancel_launch_handoff()
        self._cam.reset()
        self._cam_last_curve = None
        self._cam_frame = None
        # Launch control is process-wide, not per car: forget it only with the process,
        # or its stock values would be re-read while still holding the custom ones.
        process = self._launch_control.process
        if process is None or not process.alive:
            self._launch_control.clear()
            self._process = None

    def restore(self) -> None:
        vehicle = self.vehicle
        self._restore_cam_idle()
        self._engaged = False
        self._ready = False
        self._launch_engaged = False
        self._cancel_launch_handoff()
        self._torque_multiplier = 1.0
        self._rev_limit = None
        self._custom_curve = None
        self._pending_edits.clear()
        self._cam_enabled = False
        self._cam.reset()
        self._cam_last_curve = None
        self._cam_frame = None
        self._lc_enabled = False
        self._launch_control.set_enabled(False)
        self._controls_dirty = True
        if vehicle is None or not self.stock_curve:
            return
        self._write_curve(self.stock_curve)
        self._write_rev_limits(vehicle, None)

    def reset_controls(self) -> None:
        self._armed = False
        self._engaged = False
        self._ready = False
        self._launch_engaged = False
        self._cancel_launch_handoff()
        self._launch_armed = False
        launch_arm = self._widgets.get("launch_arm")
        if launch_arm is not None:
            launch_arm.set_value(False)
        self._torque_multiplier = 1.0
        self._rev_limit = None
        self._custom_curve = None
        self._cam_enabled = False
        self._cam.reset()
        self._cam_last_curve = None
        self._cam_frame = None
        self._restore_cam_idle()
        self._lc_enabled = False
        self._launch_control.set_enabled(False)
        self._lc_min_norm = LC_DEFAULT_MIN_NORM
        self._lc_max_norm = LC_DEFAULT_MAX_NORM
        self._lc_user_configured = False

        torque = self._widgets.get("torque")
        if torque is not None:
            torque.set_value(1.0)
        arm = self._widgets.get("arm")
        if arm is not None:
            arm.set_value(False)
        self._sync_rev_slider()
        cam_toggle = self._widgets.get("cam_enabled")
        if cam_toggle is not None:
            cam_toggle.set_value(False)
        lc_toggle = self._widgets.get("lc_enabled")
        if lc_toggle is not None:
            lc_toggle.set_value(False)
        lc_min = self._widgets.get("lc_min_norm")
        if lc_min is not None:
            lc_min.set_value(self._lc_min_norm * LC_UI_MAX)
        lc_max = self._widgets.get("lc_max_norm")
        if lc_max is not None:
            lc_max.set_value(self._lc_max_norm * LC_UI_MAX)

    def tick(self, vehicle) -> None:
        self.vehicle = vehicle

        if vehicle is None or not self.stock_curve:
            return

        if self._curve_is_tuned() and not self._engaged:
            now = time.monotonic()
            if now - self._last_reapply >= REAPPLY_INTERVAL:
                self._last_reapply = now
                cam_live = self._cam_last_curve is not None
                if cam_live and now - self._cam_last_write < REAPPLY_INTERVAL:
                    # The cam rewrites the whole curve every few frames already. Writing the
                    # plain curve under it here would drop the cam for up to one write interval.
                    self._apply_rev_ceiling()
                else:
                    self._reapply_curve_state()

        if self._pending_edits and not self._engaged:
            pending = self._pending_edits
            self._pending_edits = {}
            for start, values in _contiguous_runs(pending):
                vehicle.set_curve_from(start, values)
            self._custom_curve = self._curve_with_edits(pending)

        launch_owns_tick = self._launch_armed and self._tick_launch(vehicle)
        self._tick_cam(vehicle)

        if launch_owns_tick:
            return

        if not self._armed:
            if self._engaged:
                self._release()
            return

        wanted = inp.is_down(self.binding())
        if wanted and self._speed_cap_enabled and self._past_speed_cap(vehicle):
            wanted = False

        if wanted and not self._engaged:
            self._engage()
        elif not wanted and self._engaged:
            self._release()

        if self._engaged:
            self._bounce(vehicle)
            if self._build_boost and self._turbo is not None:
                self._turbo.ramp_turbine()

    def _tick_launch(self, vehicle) -> bool:
        """Spool the turbo while held and bridge the release into the launch."""
        if not inp.is_down(self.launch_binding()):
            if self._launch_engaged:
                self._launch_engaged = False
                self._start_launch_handoff(vehicle)
                if not self._launch_handoff_active() and self._turbo is not None:
                    self._turbo.reset_ramp()
            if self._launch_handoff_active():
                if self._turbo is not None:
                    self._turbo.ramp_turbine()
                return True
            self._cancel_launch_handoff()
            return False

        if self._launch_handoff_active():
            # Re-pressing the control takes ownership back immediately while
            # leaving the already-built turbine value intact.
            self._launch_handoff_until = 0.0
            self._launch_handoff_target_rpm = None
        if not self._launch_engaged:
            self._launch_engaged = True
        if self._turbo is not None:
            self._turbo.ramp_turbine()
        return True

    def _start_launch_handoff(self, vehicle) -> None:
        """Arm a guarded, short launch torque handoff after control-up."""
        rpm = float(vehicle.rpm or 0.0)
        throttle = vehicle.throttle
        speed = vehicle.speed_ms
        if (
            rpm < LAUNCH_HANDOFF_MIN_RPM
            or throttle is not None and throttle < LAUNCH_HANDOFF_MIN_THROTTLE
            or speed is not None and speed > LAUNCH_HANDOFF_MAX_SPEED_MS
        ):
            self._cancel_launch_handoff()
            return
        self._launch_handoff_target_rpm = rpm
        self._launch_handoff_until = time.monotonic() + LAUNCH_HANDOFF_SECONDS

    def _launch_handoff_active(self, now: float | None = None) -> bool:
        if self._launch_handoff_target_rpm is None:
            return False
        now = time.monotonic() if now is None else float(now)
        return now < self._launch_handoff_until

    def _cancel_launch_handoff(self) -> None:
        """End a pending handoff. A no-op otherwise.

        `_tick_launch` calls this on every tick the control is up. Resetting the turbine ramp
        unconditionally here re-zeroed anti-lag's own ramp each tick, so it never built boost.
        """
        if self._launch_handoff_target_rpm is None:
            return
        self._launch_handoff_until = 0.0
        self._launch_handoff_target_rpm = None
        if self._turbo is not None:
            self._turbo.reset_ramp()

    def _on_launch_armed(self, enabled: bool) -> None:
        self._launch_armed = bool(enabled)
        if not self._launch_armed:
            if self._launch_engaged and self._turbo is not None:
                self._turbo.reset_ramp()
            self._launch_engaged = False
            self._cancel_launch_handoff()

    def _on_lc_enabled(self, enabled: bool) -> None:
        self._lc_enabled = bool(enabled)
        self._launch_control.set_targets(self._lc_min_norm, self._lc_max_norm)
        if not self._lc_enabled:
            self._launch_control.set_enabled(False)
            return
        if self._launch_control.process is None:
            # The runtime will resolve the block on the next process tick.
            return
        if not self._launch_control.set_enabled(True):
            self._lc_enabled = False
            toggle = self._widgets.get("lc_enabled")
            if toggle is not None:
                toggle.set_value(False)

    def _on_lc_min_norm(self, value: float) -> None:
        self._lc_user_configured = True
        self._lc_min_norm = max(LC_UI_MIN, min(0.99, float(value) / LC_UI_MAX))
        if self._lc_max_norm <= self._lc_min_norm:
            self._lc_max_norm = min(1.0, self._lc_min_norm + 0.01)
            slider = self._widgets.get("lc_max_norm")
            if slider is not None:
                slider.set_value(self._lc_max_norm * LC_UI_MAX)
        self._launch_control.set_targets(self._lc_min_norm, self._lc_max_norm)
        if self._lc_enabled and self._launch_control.ready:
            self._launch_control.apply()

    def _on_lc_max_norm(self, value: float) -> None:
        self._lc_user_configured = True
        self._lc_max_norm = max(0.01, min(LC_UI_MAX, float(value) / LC_UI_MAX))
        if self._lc_max_norm <= self._lc_min_norm:
            self._lc_min_norm = max(LC_UI_MIN, self._lc_max_norm - 0.01)
            slider = self._widgets.get("lc_min_norm")
            if slider is not None:
                slider.set_value(self._lc_min_norm * LC_UI_MAX)
        self._launch_control.set_targets(self._lc_min_norm, self._lc_max_norm)
        if self._lc_enabled and self._launch_control.ready:
            self._launch_control.apply()

    def _past_speed_cap(self, vehicle) -> bool:
        speed = vehicle.speed_ms if vehicle is not None else None
        if speed is None:
            return False
        hysteresis = SPEED_CAP_HYSTERESIS_KPH / 3.6
        if self._over_cap:
            if speed <= self._speed_cap_ms - hysteresis:
                self._over_cap = False
        elif speed > self._speed_cap_ms:
            self._over_cap = True
        return self._over_cap

    def _limiter_value(self) -> float:
        terminal = self.stock_curve[-1]
        if terminal >= 0:
            peak = max(abs(value) for value in self.stock_curve[:-1]) or 1.0
            terminal = -0.2 * peak
        return terminal

    def _engage(self, force_ready: bool = False) -> None:
        vehicle = self.vehicle
        if vehicle is None or len(self.stock_curve) < MIN_CURVE_POINTS:
            return
        self._engaged = True
        self._cut = False
        rpm = vehicle.rpm or 0.0
        self._ready = force_ready or rpm <= self._hold_rpm
        if self._ready:
            self._set_wall(vehicle, True)
            self._phase_started = time.monotonic()

    def _release(self) -> None:
        self._engaged = False
        self._cut = False
        self._ready = False
        if self.vehicle is not None and self.stock_curve:
            self._apply_curve()
        if self._turbo is not None:
            self._turbo.reset_ramp()

    def _bounce(self, vehicle) -> None:
        rpm = vehicle.rpm or 0.0
        if not self._ready:
            if rpm <= self._hold_rpm:
                self._ready = True
                self._set_wall(vehicle, True)
                self._phase_started = time.monotonic()
            return

        now = time.monotonic()
        elapsed = (now - self._phase_started) * 1000.0
        if self._cut:
            if elapsed >= CUT_MS and rpm <= self._hold_rpm - BOUNCE_BAND:
                self._set_wall(vehicle, False)
                self._phase_started = now
        elif elapsed >= LIFT_MS or rpm >= self._hold_rpm:
            self._set_wall(vehicle, True)
            self._phase_started = now

    def _set_wall(self, vehicle, engaged: bool) -> None:
        curve = self._curve_without_cam()
        count = len(curve)
        per_index = vehicle.rpm_per_index or 100.0

        lifted = max(5, min(int(round((self._hold_rpm + BOUNCE_BAND) / per_index)), count - 2))
        base = max(4, min(int(round(self._hold_rpm / per_index)), lifted - 1))
        edge = base if engaged else lifted
        limiter = self._limiter_value()

        if not self._wall_tail_written:
            tail = [limiter] * (count - 1 - lifted)
            if tail:
                vehicle.set_curve_from(lifted, tail)
            self._wall_tail_written = True

        band = list(curve[base:edge]) + [limiter] * (lifted - edge)
        if band:
            vehicle.set_curve_from(base, band)
        self._cut = engaged

    def _apply_curve(self) -> None:
        vehicle = self.vehicle
        if vehicle is None or not self.stock_curve or self._engaged:
            return
        self._write_curve(self._curve_without_cam())

        self._wall_tail_written = False

    def _write_curve(self, values: list[float]) -> bool:
        """Write curve samples and publish the matching live sample count."""
        vehicle = self.vehicle
        values = list(values)
        if vehicle is None or not values:
            return False
        if not vehicle.set_curve(values):
            return False
        return vehicle.set_curve_count(len(values))

    def _extend_curve_for_rev_limit(self, curve: list[float]) -> list[float]:
        """Extend the positive torque samples when the limiter is raised.

        The final stock point is the game's negative limiter tail, not a
        torque sample. Move that tail to the new endpoint and fill the added
        high-rpm samples with a gently declining, non-negative continuation.
        This keeps the curve physically bounded while giving MAX_CLAMP real
        data to consume above the stock redline.
        """
        target = self._rev_limit
        vehicle = self.vehicle
        if (
            target is None
            or vehicle is None
            or len(curve) < MIN_CURVE_POINTS
            or target <= 0.0
        ):
            return list(curve)

        per_index = float(vehicle.rpm_per_index or 100.0)
        stock_endpoint = (len(curve) - 1) * per_index
        if target <= stock_endpoint + 1.0:
            return list(curve)

        target_index = max(len(curve) - 1, int(math.ceil(target / per_index)))
        target_count = min(MAX_CURVE_POINTS, target_index + 1)
        if target_count <= len(curve):
            return list(curve)

        body = list(curve[:-1])
        limiter_tail = curve[-1]
        lookback = min(8, len(body) - 1)
        slope = (body[-1] - body[-1 - lookback]) / lookback if lookback else 0.0
        # A raised limiter should not invent a power increase past the stock
        # curve. Keep any natural decline, but never extrapolate upward.
        slope = min(0.0, slope)
        last = body[-1]
        for step in range(1, target_count - len(curve) + 1):
            body.append(max(0.05, last + slope * step))
        return body + [limiter_tail]

    def _write_rev_limits(self, vehicle, rpm: float | None) -> None:
        """Set the three fields the limiter lives in, or restore the captured stock values.

        MAX_CLAMP on its own is not the limiter; it is the upper bound the torque curve is
        sampled to. THRESH is where the engine cuts, and NEG_CLAMP is the hard cut. Writing
        only MAX_CLAMP is why the rev-limit slider did nothing in game.
        """
        if rpm is None:
            if self.stock_ceiling:
                vehicle.set_rev_ceiling(self.stock_ceiling)
            if self.stock_thresh:
                vehicle.set_shift_threshold(self.stock_thresh)
            if self.stock_neg_clamp:
                vehicle.set_neg_clamp(self.stock_neg_clamp)
            return
        vehicle.set_rev_ceiling(rpm)
        vehicle.set_shift_threshold(rpm)
        vehicle.set_neg_clamp(rpm)

    def _apply_rev_ceiling(self) -> None:
        vehicle = self.vehicle
        if vehicle is None:
            return
        self._write_rev_limits(vehicle, self._rev_limit)

    def _apply_rev_limit(self) -> None:
        self._apply_curve()
        self._apply_rev_ceiling()

    def _apply_custom_curve(self) -> None:
        vehicle = self.vehicle
        if vehicle is None or not self._custom_curve or self._engaged:
            return
        self._write_curve(self._curve_without_cam())

    def _curve_without_cam(self) -> list[float]:
        """Build the current user curve before the live cam overlay."""
        if self._custom_curve is not None:
            base = list(self._custom_curve)
        elif not self.stock_curve:
            return []
        else:
            body = [value * self._torque_multiplier for value in self.stock_curve[:-1]]
            base = body + [self.stock_curve[-1]]
        return self._extend_curve_for_rev_limit(base)

    def _curve_with_edits(self, edits: dict[int, float]) -> list[float]:
        """The user's curve with graph point edits folded in.

        Built from `_curve_without_cam`, never from the graph's live samples: while the cam
        or the launch handoff is writing, those samples carry that frame's modulation, and
        saving them would bake it into the custom curve for good.
        """
        curve = self._curve_without_cam()
        limit = len(curve) - 1
        for index, value in edits.items():
            if 0 <= index < limit:
                curve[index] = value
        return curve

    def _restore_cam_idle(self) -> None:
        """Return the live car idle target to the value captured on attach.

        Only when the field still holds the cam's last write. Anything else means the game
        rebuilt it (a reload, or a different car in the same memory), and writing this car's
        stock idle there would hand it to whatever lives there now.
        """
        vehicle = self.vehicle
        written, self._cam_idle_written = self._cam_idle_written, None
        if vehicle is None or written is None or self._cam_idle_stock_rpm is None:
            return
        current = vehicle.idle_rpm
        if current is None or abs(current - written) > CAM_IDLE_MATCH_RPM:
            return
        vehicle.set_idle_rpm(self._cam_idle_stock_rpm)

    def _tick_cam(self, vehicle) -> None:
        """Apply the reversible cam overlay at a modest write rate."""
        handoff_active = self._launch_handoff_active()
        if (
            not self._cam_enabled
            and self._cam_last_curve is None
            and self._cam_idle_written is None
            and not handoff_active
        ):
            return
        if self._engaged:
            return

        rpm = vehicle.rpm
        throttle = vehicle.throttle
        if rpm is None or throttle is None:
            return

        self._cam.configure(
            enabled=self._cam_enabled,
            mode=self._cam_mode,
            aggressiveness=self._cam_aggressiveness,
            frequency_hz=self._cam_frequency_hz,
            depth=self._cam_depth,
            torque_dip=self._cam_torque_dip,
            recovery_pulse=self._cam_recovery_pulse,
            sharpness=self._cam_sharpness,
            fade_seconds=self._cam_fade_seconds,
            release_rpm=self._cam_release_rpm,
        )
        redline = vehicle.rev_ceiling or self.stock_redline or self._cam_release_rpm * 2.0
        self._cam_frame = self._cam.update(rpm, throttle, idle_rpm=self._cam_idle_stock_rpm)
        now = time.monotonic()

        target_idle = self._cam_frame.idle_target_rpm
        if self._cam_enabled and target_idle is not None:
            # Past 25% throttle the target sits still; skip rewriting an unchanged value.
            written = self._cam_idle_written
            changed = written is None or abs(target_idle - written) > CAM_IDLE_MATCH_RPM
            if changed and vehicle.set_idle_rpm(target_idle):
                self._cam_idle_written = target_idle
        elif not self._cam_enabled:
            self._restore_cam_idle()

        if not self._cam_enabled and not handoff_active:
            if self._cam_last_curve is not None:
                base = self._curve_without_cam()
                if base:
                    self._write_curve(base)
                self._cam_last_curve = None
            return

        if not vehicle.can_tune_curve():
            return

        if now - self._cam_last_write < CAM_WRITE_INTERVAL:
            return
        base = self._curve_without_cam()
        if len(base) < MIN_CURVE_POINTS:
            return
        per_index = vehicle.rpm_per_index or 100.0
        shaped = (
            apply_cam_shape(
                base,
                per_index,
                redline,
                self._cam_frame.intensity,
                self._cam_frame.torque_pulse,
            )
            if self._cam_enabled
            else base
        )
        if handoff_active and self._launch_handoff_target_rpm is not None:
            shaped = apply_launch_handoff(
                shaped,
                per_index,
                float(rpm),
                self._launch_handoff_target_rpm,
            )
        if self._write_curve(shaped):
            self._cam_last_write = now
            self._cam_last_curve = shaped

    def _curve_is_tuned(self) -> bool:
        return (
            self._rev_limit is not None
            or self._torque_multiplier != 1.0
            or self._custom_curve is not None
            or self._cam_enabled
        )

    def _reapply_curve_state(self) -> None:
        """Re-apply whatever curve state is active.
        (This is called on a timer while the car is held, to catch the game re-baking the curve
        back to stock on a teleport or fast travel.)
        """
        if self._custom_curve is not None:
            self._apply_custom_curve()
        else:
            self._apply_curve()
        self._apply_rev_ceiling()

    def _sync_rev_slider(self) -> None:
        slider = self._widgets.get("rev")
        if slider is None:
            return
        target = self._rev_limit or self.stock_ceiling or self.stock_redline or 7000.0
        slider.set_value(target)

    def _on_torque(self, value: float) -> None:
        self._torque_multiplier = float(value)
        self._custom_curve = None
        self._apply_curve()

    def _on_rev_limit(self, value: float) -> None:
        reference = self.stock_ceiling or self.stock_redline or 7000.0
        self._rev_limit = None if abs(value - reference) < 1.0 else float(value)
        self._custom_curve = None
        self._apply_rev_limit()

    def _on_hold_rpm(self, value: float) -> None:
        self._hold_rpm = int(value)

        self._wall_tail_written = False

    def _on_speed_cap(self, value: float) -> None:
        self._speed_cap_ms = self.settings.speed_to_ms(value)
        self._over_cap = False

    def _on_speed_cap_enabled(self, enabled: bool) -> None:
        self._speed_cap_enabled = bool(enabled)
        self._over_cap = False
        slider = self._widgets.get("speed_cap")
        if slider is not None:
            slider.set_enabled(bool(enabled))

    def _on_point_changed(self, index: int, value: float) -> None:
        if not self._engaged:
            self._pending_edits[index] = value

    def _on_cam_enabled(self, enabled: bool) -> None:
        self._cam_enabled = bool(enabled)
        self._cam.reset()
        self._cam_last_write = 0.0
        if not self._cam_enabled and self.vehicle is not None and not self._engaged:
            self._restore_cam_idle()
            base = self._curve_without_cam()
            if base:
                self._write_curve(base)
            self._cam_last_curve = None

    def _on_cam_mode(self, mode: str) -> None:
        if mode in CAM_MODES:
            self._cam_mode = mode
            self._cam.reset()

    def _on_cam_aggressiveness(self, value: float) -> None:
        self._cam_aggressiveness = max(
            CAM_AGGRESSIVENESS_MIN,
            min(CAM_AGGRESSIVENESS_MAX, float(value) / 100.0),
        )
        self._cam_last_write = 0.0

    def _on_cam_frequency(self, value: float) -> None:
        self._cam_frequency_hz = max(
            CAM_IDLE_LOPE_MIN_HZ,
            min(CAM_IDLE_LOPE_MAX_HZ, float(value)),
        )
        self._cam_last_write = 0.0

    def _on_cam_depth(self, value: float) -> None:
        self._cam_depth = max(
            CAM_TUNING_MIN,
            min(CAM_TUNING_MAX, float(value) / 100.0),
        )
        self._cam_last_write = 0.0

    def _on_cam_torque_dip(self, value: float) -> None:
        self._cam_torque_dip = max(
            CAM_TUNING_MIN,
            min(CAM_TUNING_MAX, float(value) / 100.0),
        )
        self._cam_last_write = 0.0

    def _on_cam_recovery_pulse(self, value: float) -> None:
        self._cam_recovery_pulse = max(
            CAM_TUNING_MIN,
            min(CAM_TUNING_MAX, float(value) / 100.0),
        )
        self._cam_last_write = 0.0

    def _on_cam_sharpness(self, value: float) -> None:
        self._cam_sharpness = max(
            CAM_SHARPNESS_MIN,
            min(CAM_SHARPNESS_MAX, float(value)),
        )
        self._cam_last_write = 0.0

    def _on_cam_release_rpm(self, value: float) -> None:
        self._cam_release_rpm = max(CAM_RELEASE_RPM_MIN, min(CAM_RELEASE_RPM_MAX, float(value)))
        self._cam_last_write = 0.0

    def _on_cam_fade_seconds(self, value: float) -> None:
        self._cam_fade_seconds = max(MIN_FADE_SECONDS, min(MAX_FADE_SECONDS, float(value)))
        self._cam.reset()

    def _commit_curve(self) -> None:
        vehicle = self.vehicle
        graph = self._widgets.get("graph")
        if vehicle is None or graph is None or not self.stock_curve or self._engaged:
            return
        pending = self._pending_edits
        self._pending_edits = {}
        self._custom_curve = self._curve_with_edits(pending)
        self._write_curve(self._curve_without_cam())

    def _reset_curve(self) -> None:
        vehicle = self.vehicle
        if vehicle is None or not self.stock_curve:
            return
        self._torque_multiplier = 1.0
        self._custom_curve = None
        self._pending_edits.clear()
        self._write_curve(self._curve_without_cam())
        slider = self._widgets.get("torque")
        if slider is not None:
            slider.set_value(1.0)
        graph = self._widgets.get("graph")
        if graph is not None:
            graph.clear_selection()

    def build_page(self, page) -> None:
        curve_card = page.add_card(
            "Torque curve",
            "Drag to reshape. Right-drag to select a range, then drag any selected point "
            "to move the whole group.",
        )

        graph = TorqueGraph()
        graph.point_changed.connect(self._on_point_changed)
        self._widgets["graph"] = graph
        curve_card.add(graph)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        apply_button = PrimaryButton("Apply curve")
        apply_button.clicked.connect(self._commit_curve)
        actions.addWidget(apply_button)

        double_button = Button("Double")
        double_button.clicked.connect(lambda: graph.scale_selection(2.0))
        actions.addWidget(double_button)

        halve_button = Button("Halve")
        halve_button.clicked.connect(lambda: graph.scale_selection(0.5))
        actions.addWidget(halve_button)

        reset_button = Button("Reset to stock")
        reset_button.clicked.connect(self._reset_curve)
        actions.addWidget(reset_button)
        actions.addStretch(1)
        curve_card.add_layout(actions)

        power_card = page.add_card("Power and limit")
        torque = SliderRow(
            "Torque", TORQUE_MIN, TORQUE_MAX, 1.0, step=0.05, decimals=2, unit="x", hint=HINT_TORQUE
        )
        torque.changed.connect(self._on_torque)
        self._widgets["torque"] = torque
        power_card.add(torque)

        rev = SliderRow(
            "Rev limit", REV_MIN, REV_MAX, 7000, step=50, decimals=0, unit="rpm", hint=HINT_REV
        )
        rev.changed.connect(self._on_rev_limit)
        self._widgets["rev"] = rev
        power_card.add(rev)

        cam_card = page.add_card("Cam mod", HINT_CAM)
        cam_toggle = ToggleRow("Enable cam mod", False)
        cam_toggle.toggle.toggled_value.connect(self._on_cam_enabled)
        self._widgets["cam_enabled"] = cam_toggle
        cam_card.add(cam_toggle)

        aggressiveness = SliderRow(
            "Aggressiveness",
            CAM_AGGRESSIVENESS_MIN * 100.0,
            CAM_AGGRESSIVENESS_MAX * 100.0,
            self._cam_aggressiveness * 100.0,
            step=1.0,
            decimals=0,
            unit="%",
            hint=HINT_CAM_AGGRESSIVENESS,
        )
        aggressiveness.changed.connect(self._on_cam_aggressiveness)
        self._widgets["cam_aggressiveness"] = aggressiveness
        cam_card.add(aggressiveness)

        frequency = SliderRow(
            "Frequency",
            CAM_IDLE_LOPE_MIN_HZ,
            CAM_IDLE_LOPE_MAX_HZ,
            self._cam_frequency_hz,
            step=0.1,
            decimals=1,
            unit="Hz",
            hint=HINT_CAM_FREQUENCY,
        )
        frequency.changed.connect(self._on_cam_frequency)
        self._widgets["cam_frequency_hz"] = frequency
        cam_card.add(frequency)

        depth = SliderRow(
            "Cam depth",
            CAM_TUNING_MIN * 100.0,
            CAM_TUNING_MAX * 100.0,
            self._cam_depth * 100.0,
            step=1.0,
            decimals=0,
            unit="%",
            hint=HINT_CAM_DEPTH,
        )
        depth.changed.connect(self._on_cam_depth)
        self._widgets["cam_depth"] = depth
        cam_card.add(depth)

        torque_dip = SliderRow(
            "Torque dip",
            CAM_TUNING_MIN * 100.0,
            CAM_TUNING_MAX * 100.0,
            self._cam_torque_dip * 100.0,
            step=1.0,
            decimals=0,
            unit="%",
            hint=HINT_CAM_TORQUE_DIP,
        )
        torque_dip.changed.connect(self._on_cam_torque_dip)
        self._widgets["cam_torque_dip"] = torque_dip
        cam_card.add(torque_dip)

        recovery = SliderRow(
            "Recovery pulse",
            CAM_TUNING_MIN * 100.0,
            CAM_TUNING_MAX * 100.0,
            self._cam_recovery_pulse * 100.0,
            step=1.0,
            decimals=0,
            unit="%",
            hint=HINT_CAM_RECOVERY,
        )
        recovery.changed.connect(self._on_cam_recovery_pulse)
        self._widgets["cam_recovery_pulse"] = recovery
        cam_card.add(recovery)

        sharpness = SliderRow(
            "Wave sharpness",
            CAM_SHARPNESS_MIN,
            CAM_SHARPNESS_MAX,
            self._cam_sharpness,
            step=0.1,
            decimals=1,
            unit="x",
            hint=HINT_CAM_SHARPNESS,
        )
        sharpness.changed.connect(self._on_cam_sharpness)
        self._widgets["cam_sharpness"] = sharpness
        cam_card.add(sharpness)

        mode = Segmented(list(CAM_MODES), self._cam_mode)
        mode.changed.connect(self._on_cam_mode)
        self._widgets["cam_mode"] = mode
        mode_row = FieldRow("Control mode", mode, hint=HINT_CAM_MODE)
        cam_card.add(mode_row)

        release_rpm = SliderRow(
            "RPM fade-out",
            CAM_RELEASE_RPM_MIN,
            CAM_RELEASE_RPM_MAX,
            self._cam_release_rpm,
            step=100,
            decimals=0,
            unit="rpm",
            hint=HINT_CAM_RELEASE,
        )
        release_rpm.changed.connect(self._on_cam_release_rpm)
        self._widgets["cam_release_rpm"] = release_rpm
        cam_card.add(release_rpm)

        fade = SliderRow(
            "Throttle response",
            MIN_FADE_SECONDS,
            MAX_FADE_SECONDS,
            self._cam_fade_seconds,
            step=0.05,
            decimals=2,
            unit="s",
            hint=HINT_CAM_FADE,
        )
        fade.changed.connect(self._on_cam_fade_seconds)
        self._widgets["cam_fade_seconds"] = fade
        cam_card.add(fade)

        cam_note = Banner(
            "Low-rpm lope is a real torque pulse, not just a static curve shape. Throttle gate: "
            "cam fades from 0% to 25% pedal. A faster pedal rise fades it faster; lifting "
            "brings it back smoothly.",
            "info",
        )
        cam_card.add(cam_note)
        bind_progressive(
            cam_toggle,
            aggressiveness,
            frequency,
            depth,
            torque_dip,
            recovery,
            sharpness,
            mode_row,
            release_rpm,
            fade,
            cam_note,
        )

        antilag_card = page.add_card("Anti-lag", HINT_ANTILAG)
        arm = ToggleRow("Enable anti-lag", False)
        arm.toggle.toggled_value.connect(lambda value: setattr(self, "_armed", value))
        self._widgets["arm"] = arm
        antilag_card.add(arm)

        bind_button = BindButton(self.binding(), settings=self.settings, key="engine.antilag")
        bind_button.bound.connect(
            lambda binding: self.settings.set_binding("engine.antilag", binding)
        )
        self._widgets["bind"] = bind_button

        bind_row = FieldRow("Hold control", bind_button)
        antilag_card.add(bind_row)

        hold = SliderRow("Hold at", HOLD_MIN, HOLD_MAX, 4000, step=100, decimals=0, unit="rpm")
        hold.changed.connect(self._on_hold_rpm)
        self._widgets["hold"] = hold
        antilag_card.add(hold)

        boost_toggle = ToggleRow("Build boost while holding", True)
        boost_toggle.toggle.toggled_value.connect(
            lambda value: setattr(self, "_build_boost", value)
        )
        self._widgets["build_boost"] = boost_toggle
        antilag_card.add(boost_toggle)

        antilag_card.add_divider()

        cap_toggle = ToggleRow("Release above a speed", False, hint=HINT_SPEED_CAP)
        cap_toggle.toggle.toggled_value.connect(self._on_speed_cap_enabled)
        self._widgets["speed_cap_toggle"] = cap_toggle
        antilag_card.add(cap_toggle)

        _, unit = self.settings.speed(0)
        cap = SliderRow("Release above", 5, 200, 40, step=5, decimals=0, unit=unit)
        cap.changed.connect(self._on_speed_cap)
        cap.set_enabled(False)
        self._widgets["speed_cap"] = cap
        antilag_card.add(cap)
        bind_progressive(arm, bind_row, hold, boost_toggle, cap_toggle, cap)

        launch_card = page.add_card("Enhanced launch control", HINT_LAUNCH)
        launch_note = Banner(NOTE_LAUNCH_GAME_LC, "warn")
        launch_card.add(launch_note)

        lc_toggle = ToggleRow("Enable custom launch RPM", self._lc_enabled, hint=HINT_LC)
        lc_toggle.toggle.toggled_value.connect(self._on_lc_enabled)
        self._widgets["lc_enabled"] = lc_toggle
        launch_card.add(lc_toggle)

        lc_min = SliderRow(
            "Launch RPM start",
            LC_UI_MIN,
            LC_UI_MAX,
            self._lc_min_norm * LC_UI_MAX,
            step=1.0,
            decimals=0,
            unit="%",
            hint="Normalized lower edge of the game's launch-control RPM window.",
        )
        lc_min.changed.connect(self._on_lc_min_norm)
        self._widgets["lc_min_norm"] = lc_min
        launch_card.add(lc_min)

        lc_max = SliderRow(
            "Launch RPM limit",
            LC_UI_MIN,
            LC_UI_MAX,
            self._lc_max_norm * LC_UI_MAX,
            step=1.0,
            decimals=0,
            unit="%",
            hint="Normalized upper edge of the game's launch-control RPM window.",
        )
        lc_max.changed.connect(self._on_lc_max_norm)
        self._widgets["lc_max_norm"] = lc_max
        launch_card.add(lc_max)

        lc_status = Banner("", "warn")
        lc_status.setVisible(False)
        self._widgets["lc_status"] = lc_status
        launch_card.add(lc_status)
        # The status banner shows and hides itself in refresh().
        bind_progressive(lc_toggle, lc_min, lc_max)

        launch_arm = ToggleRow("Enable enhanced launch control", False, hint=HINT_LAUNCH_ARM)
        launch_arm.toggle.toggled_value.connect(self._on_launch_armed)
        self._widgets["launch_arm"] = launch_arm
        launch_card.add(launch_arm)

        launch_bind = BindButton(self.launch_binding(), settings=self.settings, key="engine.launch")
        launch_bind.bound.connect(
            lambda binding: self.settings.set_binding("engine.launch", binding)
        )
        self._widgets["launch_bind"] = launch_bind
        launch_bind_row = FieldRow("Hold control", launch_bind)
        launch_card.add(launch_bind_row)
        bind_progressive(launch_arm, launch_bind_row)

        live_card = page.add_card("Live")
        stats = StatStrip()
        stats.add("state", "Anti-lag", "Off")
        stats.add("launch", "Launch", "Off")
        stats.add("cam", "Cam", "Off")
        stats.add("torque", "Peak torque", "--")
        stats.add("power", "Peak power", "--")
        self._widgets["stats"] = stats
        live_card.add(stats)

        banner = Banner("", "warn")
        banner.setVisible(False)
        self._widgets["banner"] = banner
        live_card.add(banner)

    def refresh(self, vehicle) -> None:
        graph = self._widgets.get("graph")
        stats = self._widgets.get("stats")
        banner = self._widgets.get("banner")
        if graph is None or stats is None:
            return

        if self._controls_dirty:
            self._controls_dirty = False
            self._sync_rev_slider()
            torque = self._widgets.get("torque")
            if torque is not None:
                torque.set_value(self._torque_multiplier)
            cam_toggle = self._widgets.get("cam_enabled")
            if cam_toggle is not None:
                cam_toggle.set_value(self._cam_enabled)
            cam_mode = self._widgets.get("cam_mode")
            if cam_mode is not None:
                cam_mode.set_value(self._cam_mode)
            cam_aggressiveness = self._widgets.get("cam_aggressiveness")
            if cam_aggressiveness is not None:
                cam_aggressiveness.set_value(self._cam_aggressiveness * 100.0)
            cam_frequency = self._widgets.get("cam_frequency_hz")
            if cam_frequency is not None:
                cam_frequency.set_value(self._cam_frequency_hz)
            cam_depth = self._widgets.get("cam_depth")
            if cam_depth is not None:
                cam_depth.set_value(self._cam_depth * 100.0)
            cam_torque_dip = self._widgets.get("cam_torque_dip")
            if cam_torque_dip is not None:
                cam_torque_dip.set_value(self._cam_torque_dip * 100.0)
            cam_recovery = self._widgets.get("cam_recovery_pulse")
            if cam_recovery is not None:
                cam_recovery.set_value(self._cam_recovery_pulse * 100.0)
            cam_sharpness = self._widgets.get("cam_sharpness")
            if cam_sharpness is not None:
                cam_sharpness.set_value(self._cam_sharpness)
            cam_release = self._widgets.get("cam_release_rpm")
            if cam_release is not None:
                cam_release.set_value(self._cam_release_rpm)
            cam_fade = self._widgets.get("cam_fade_seconds")
            if cam_fade is not None:
                cam_fade.set_value(self._cam_fade_seconds)
            lc_toggle = self._widgets.get("lc_enabled")
            if lc_toggle is not None:
                lc_toggle.set_value(self._lc_enabled)
            lc_min = self._widgets.get("lc_min_norm")
            if lc_min is not None:
                lc_min.set_value(self._lc_min_norm * LC_UI_MAX)
            lc_max = self._widgets.get("lc_max_norm")
            if lc_max is not None:
                lc_max.set_value(self._lc_max_norm * LC_UI_MAX)

        if vehicle is None:
            graph.set_data([], [], editable=False)
            stats.reset()
            if banner is not None:
                banner.setVisible(False)
            return

        live_curve = vehicle.curve()
        if self._cam_last_curve is not None:
            live_curve = self._curve_without_cam() or live_curve
        graph.set_data(
            self.stock_curve,
            live_curve,
            vehicle.rpm_per_index or 100.0,
            vehicle.rpm or 0.0,
            vehicle.rev_ceiling,
            editable=not self._engaged,
        )

        if self._engaged and not self._launch_engaged:
            stats.set("state", f"Holding {self._hold_rpm}", T.ACCENT_BRIGHT)
        elif self._armed:
            stats.set("state", "Armed", T.OK)
        else:
            stats.set("state", "Off", T.TEXT_FAINT)

        if self._launch_engaged:
            stats.set("launch", "Building boost", T.ACCENT_BRIGHT)
        elif self._launch_handoff_active():
            stats.set("launch", "Launch handoff", T.ACCENT_BRIGHT)
        elif self._launch_armed:
            stats.set("launch", "Armed", T.OK)
        else:
            stats.set("launch", "Off", T.TEXT_FAINT)

        lc_status = self._widgets.get("lc_status")
        if lc_status is not None:
            if self._lc_enabled and self._launch_control.error:
                lc_status.set(
                    f"Custom launch RPM unavailable: {self._launch_control.error}",
                    "warn",
                )
            elif self._lc_enabled and self._launch_control.ready:
                lc_status.set("Custom launch RPM active.", "info")
            elif self._lc_enabled:
                lc_status.set("Waiting for the game process.", "info")
            else:
                lc_status.setVisible(False)

        if self._cam_enabled and self._cam_frame is not None:
            cam_percent = self._cam_frame.intensity * 100.0
            stats.set("cam", f"{cam_percent:.0f}", T.ACCENT_BRIGHT, unit=self._cam_mode)
        elif self._cam_enabled:
            stats.set("cam", "Armed", T.OK, unit=self._cam_mode)
        else:
            stats.set("cam", "Off", T.TEXT_FAINT, unit="")

        (torque_nm, torque_rpm), (power_hp, power_rpm) = vehicle.peaks(live_curve)
        stats.set("torque", f"{torque_nm:.0f}", unit=f"Nm @ {torque_rpm}")
        stats.set("power", f"{power_hp:.0f}", unit=f"hp @ {power_rpm}")

        if banner is not None:
            if not vehicle.can_tune_curve():
                banner.set(
                    "This engine uses a layout Neptune cannot reshape. "
                    "Torque and rev limit still work.",
                    "warn",
                )
            else:
                banner.setVisible(False)

    def save_state(self) -> dict:
        return {
            "torque": self._torque_multiplier,
            "rev_limit": self._rev_limit,
            "hold_rpm": self._hold_rpm,
            "build_boost": self._build_boost,
            "launch_armed": self._launch_armed,
            "lc_enabled": self._lc_enabled,
            "lc_min_norm": self._lc_min_norm,
            "lc_max_norm": self._lc_max_norm,
            "speed_cap_enabled": self._speed_cap_enabled,
            "speed_cap_ms": self._speed_cap_ms,
            "cam_enabled": self._cam_enabled,
            "cam_mode": self._cam_mode,
            "cam_aggressiveness": self._cam_aggressiveness,
            "cam_frequency_hz": self._cam_frequency_hz,
            "cam_depth": self._cam_depth,
            "cam_torque_dip": self._cam_torque_dip,
            "cam_recovery_pulse": self._cam_recovery_pulse,
            "cam_sharpness": self._cam_sharpness,
            "cam_release_rpm": self._cam_release_rpm,
            "cam_fade_seconds": self._cam_fade_seconds,
        }

    def load_state(self, data: dict) -> None:
        data = data or {}
        self._custom_curve = None

        def _number(key, fallback, low=None, high=None):
            """Read a number defensively.

            A hand-edited or corrupt preset can hold a string, None, NaN or infinity. Bare
            `int()`/`float()` raise on those and abort the load half-way, leaving the module in a
            mixed state. Every field falls back to its default and clamps to its own rail instead.
            """
            try:
                value = float(data.get(key, fallback))
            except (TypeError, ValueError):
                return fallback
            if value != value or value in (float("inf"), float("-inf")):
                return fallback
            if low is not None:
                value = max(low, value)
            if high is not None:
                value = min(high, value)
            return value

        self._hold_rpm = int(_number("hold_rpm", 4000, HOLD_MIN, HOLD_MAX))
        hold = self._widgets.get("hold")
        if hold is not None:
            hold.set_value(self._hold_rpm)

        self._launch_engaged = False
        self._launch_armed = bool(data.get("launch_armed", False))
        launch_arm = self._widgets.get("launch_arm")
        if launch_arm is not None:
            launch_arm.set_value(self._launch_armed)

        self._lc_user_configured = "lc_min_norm" in data or "lc_max_norm" in data
        self._lc_min_norm = _number("lc_min_norm", LC_DEFAULT_MIN_NORM, 0.0, 0.99)
        self._lc_max_norm = _number("lc_max_norm", LC_DEFAULT_MAX_NORM, 0.01, 1.0)
        if self._lc_min_norm is None:
            self._lc_min_norm = LC_DEFAULT_MIN_NORM
        if self._lc_max_norm is None:
            self._lc_max_norm = LC_DEFAULT_MAX_NORM
        if self._lc_max_norm <= self._lc_min_norm:
            self._lc_max_norm = min(1.0, self._lc_min_norm + 0.01)
            self._lc_min_norm = min(self._lc_min_norm, self._lc_max_norm - 0.01)
        self._lc_enabled = bool(data.get("lc_enabled", False))
        self._launch_control.set_targets(self._lc_min_norm, self._lc_max_norm)
        lc_toggle = self._widgets.get("lc_enabled")
        if lc_toggle is not None:
            lc_toggle.set_value(self._lc_enabled)
        lc_min = self._widgets.get("lc_min_norm")
        if lc_min is not None:
            lc_min.set_value(self._lc_min_norm * LC_UI_MAX)
        lc_max = self._widgets.get("lc_max_norm")
        if lc_max is not None:
            lc_max.set_value(self._lc_max_norm * LC_UI_MAX)

        self._build_boost = bool(data.get("build_boost", True))
        boost_toggle = self._widgets.get("build_boost")
        if boost_toggle is not None:
            boost_toggle.set_value(self._build_boost)

        self._speed_cap_enabled = bool(data.get("speed_cap_enabled", False))
        self._speed_cap_ms = _number("speed_cap_ms", 40.0 / 3.6, 0.0, 200.0)
        self._over_cap = False
        cap_toggle = self._widgets.get("speed_cap_toggle")
        if cap_toggle is not None:
            cap_toggle.set_value(self._speed_cap_enabled)
        cap = self._widgets.get("speed_cap")
        if cap is not None:
            value, _unit = self.settings.speed(self._speed_cap_ms)
            cap.set_value(value)
            cap.set_enabled(self._speed_cap_enabled)

        mode = data.get("cam_mode", CAM_MODE_RPM)
        self._cam_mode = mode if mode in CAM_MODES else CAM_MODE_RPM
        self._cam_enabled = bool(data.get("cam_enabled", False))
        self._cam_aggressiveness = _number("cam_aggressiveness", 0.50, 0.0, 1.0)
        self._cam_frequency_hz = _number(
            "cam_frequency_hz",
            CAM_IDLE_LOPE_DEFAULT_HZ,
            CAM_IDLE_LOPE_MIN_HZ,
            CAM_IDLE_LOPE_MAX_HZ,
        )
        self._cam_depth = _number(
            "cam_depth",
            CAM_TUNING_DEFAULT,
            CAM_TUNING_MIN,
            CAM_TUNING_MAX,
        )
        self._cam_torque_dip = _number(
            "cam_torque_dip",
            CAM_TUNING_DEFAULT,
            CAM_TUNING_MIN,
            CAM_TUNING_MAX,
        )
        self._cam_recovery_pulse = _number(
            "cam_recovery_pulse",
            CAM_TUNING_DEFAULT,
            CAM_TUNING_MIN,
            CAM_TUNING_MAX,
        )
        self._cam_sharpness = _number(
            "cam_sharpness",
            CAM_WAVE_SHARPNESS,
            CAM_SHARPNESS_MIN,
            CAM_SHARPNESS_MAX,
        )
        self._cam_release_rpm = _number(
            "cam_release_rpm", 3500.0, CAM_RELEASE_RPM_MIN, CAM_RELEASE_RPM_MAX
        )
        self._cam_fade_seconds = _number(
            "cam_fade_seconds", DEFAULT_FADE_SECONDS, MIN_FADE_SECONDS, MAX_FADE_SECONDS
        )
        self._cam.configure(
            enabled=self._cam_enabled,
            mode=self._cam_mode,
            aggressiveness=self._cam_aggressiveness,
            frequency_hz=self._cam_frequency_hz,
            depth=self._cam_depth,
            torque_dip=self._cam_torque_dip,
            recovery_pulse=self._cam_recovery_pulse,
            sharpness=self._cam_sharpness,
            release_rpm=self._cam_release_rpm,
            fade_seconds=self._cam_fade_seconds,
        )
        self._cam.reset()
        cam_toggle = self._widgets.get("cam_enabled")
        if cam_toggle is not None:
            cam_toggle.set_value(self._cam_enabled)
        cam_mode = self._widgets.get("cam_mode")
        if cam_mode is not None:
            cam_mode.set_value(self._cam_mode)
        cam_aggressiveness = self._widgets.get("cam_aggressiveness")
        if cam_aggressiveness is not None:
            cam_aggressiveness.set_value(self._cam_aggressiveness * 100.0)
        cam_frequency = self._widgets.get("cam_frequency_hz")
        if cam_frequency is not None:
            cam_frequency.set_value(self._cam_frequency_hz)
        cam_depth = self._widgets.get("cam_depth")
        if cam_depth is not None:
            cam_depth.set_value(self._cam_depth * 100.0)
        cam_torque_dip = self._widgets.get("cam_torque_dip")
        if cam_torque_dip is not None:
            cam_torque_dip.set_value(self._cam_torque_dip * 100.0)
        cam_recovery = self._widgets.get("cam_recovery_pulse")
        if cam_recovery is not None:
            cam_recovery.set_value(self._cam_recovery_pulse * 100.0)
        cam_sharpness = self._widgets.get("cam_sharpness")
        if cam_sharpness is not None:
            cam_sharpness.set_value(self._cam_sharpness)
        cam_release = self._widgets.get("cam_release_rpm")
        if cam_release is not None:
            cam_release.set_value(self._cam_release_rpm)
        cam_fade = self._widgets.get("cam_fade_seconds")
        if cam_fade is not None:
            cam_fade.set_value(self._cam_fade_seconds)

        rev_limit = data.get("rev_limit")
        self._rev_limit = _number("rev_limit", None, REV_MIN, REV_MAX) if rev_limit else None
        self._sync_rev_slider()

        multiplier = _number("torque", 1.0, TORQUE_MIN, TORQUE_MAX)
        torque = self._widgets.get("torque")
        if torque is not None:
            torque.set_value(multiplier)
        self._torque_multiplier = multiplier
        self._apply_rev_limit() if self._rev_limit is not None else self._apply_curve()
