"""Camshaft behaviour model used by the Engine module."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

CAM_MODE_RPM = "RPM"
CAM_MODE_THROTTLE = "Throttle"
CAM_MODES = (CAM_MODE_RPM, CAM_MODE_THROTTLE)

THROTTLE_FADE_END = 0.25
DEFAULT_FADE_SECONDS = 0.75
MIN_FADE_SECONDS = 0.10
MAX_FADE_SECONDS = 2.00
CAM_IDLE_LOPE_MIN_HZ = 1.0
CAM_IDLE_LOPE_MAX_HZ = 8.0
CAM_IDLE_LOPE_DEFAULT_HZ = 3.6
CAM_IDLE_SWING = 0.40
CAM_TORQUE_DIP = 0.72
CAM_TORQUE_RECOVERY = 1.05
CAM_TORQUE_MIN = 0.55
CAM_TORQUE_MAX = 1.15
CAM_WAVE_SHARPNESS = 5.5
CAM_SECOND_HARMONIC = 0.42
CAM_TUNING_MIN = 0.0
CAM_TUNING_MAX = 2.0
CAM_TUNING_DEFAULT = 1.0
CAM_SHARPNESS_MIN = 1.0
CAM_SHARPNESS_MAX = 12.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _smoothstep(value: float) -> float:
    value = _clamp(value)
    return value * value * (3.0 - 2.0 * value)


def _range(value: float, start: float, end: float) -> float:
    if end <= start:
        return 1.0 if value >= end else 0.0
    return _smoothstep((value - start) / (end - start))


@dataclass(frozen=True)
class CamFrame:
    """One controller result, expressed as normalized values."""

    intensity: float
    throttle_gate: float
    rpm_gate: float
    throttle_rate: float
    torque_pulse: float = 1.0
    idle_target_rpm: float | None = None


class CamController:
    """Stateful cam gate with a real pedal-rate-dependent fade.

    ``intensity`` is 0..1.  A fast pedal application increases the fade rate,
    while lifting the pedal lets the cam return more gently.  RPM mode uses
    the low-speed portion of the engine range as the cam window; Throttle mode
    leaves RPM out of the source signal and follows the pedal gate directly.
    """

    def __init__(self) -> None:
        self.enabled = False
        self.mode = CAM_MODE_RPM
        self.aggressiveness = 0.50
        self.frequency_hz = CAM_IDLE_LOPE_DEFAULT_HZ
        self.depth = CAM_TUNING_DEFAULT
        self.torque_dip = CAM_TUNING_DEFAULT
        self.recovery_pulse = CAM_TUNING_DEFAULT
        self.sharpness = CAM_WAVE_SHARPNESS
        self.fade_seconds = DEFAULT_FADE_SECONDS
        self.release_rpm = 3500.0

        self._throttle_gate = 1.0
        self._previous_throttle: float | None = None
        self._last_time: float | None = None
        self._lope_phase = 0.0

    def reset(self) -> None:
        self._throttle_gate = 1.0
        self._previous_throttle = None
        self._last_time = None
        self._lope_phase = 0.0

    def configure(
        self,
        *,
        enabled: bool | None = None,
        mode: str | None = None,
        aggressiveness: float | None = None,
        frequency_hz: float | None = None,
        depth: float | None = None,
        torque_dip: float | None = None,
        recovery_pulse: float | None = None,
        sharpness: float | None = None,
        fade_seconds: float | None = None,
        release_rpm: float | None = None,
    ) -> None:
        if enabled is not None:
            self.enabled = bool(enabled)
        if mode in CAM_MODES:
            self.mode = mode
        if aggressiveness is not None:
            self.aggressiveness = _clamp(aggressiveness)
        if frequency_hz is not None:
            self.frequency_hz = _clamp(
                frequency_hz,
                CAM_IDLE_LOPE_MIN_HZ,
                CAM_IDLE_LOPE_MAX_HZ,
            )
        if depth is not None:
            self.depth = _clamp(depth, CAM_TUNING_MIN, CAM_TUNING_MAX)
        if torque_dip is not None:
            self.torque_dip = _clamp(torque_dip, CAM_TUNING_MIN, CAM_TUNING_MAX)
        if recovery_pulse is not None:
            self.recovery_pulse = _clamp(recovery_pulse, CAM_TUNING_MIN, CAM_TUNING_MAX)
        if sharpness is not None:
            self.sharpness = _clamp(sharpness, CAM_SHARPNESS_MIN, CAM_SHARPNESS_MAX)
        if fade_seconds is not None:
            self.fade_seconds = _clamp(fade_seconds, MIN_FADE_SECONDS, MAX_FADE_SECONDS)
        if release_rpm is not None:
            self.release_rpm = max(500.0, float(release_rpm))

    def update(
        self,
        rpm: float | None,
        throttle: float | None,
        now: float | None = None,
        idle_rpm: float | None = None,
    ) -> CamFrame:
        now = time.monotonic() if now is None else float(now)
        if now != now or not math.isfinite(now):
            now = time.monotonic()

        throttle = _clamp(throttle if throttle is not None else 0.0)
        rpm = max(0.0, float(rpm or 0.0))

        dt = 0.01 if self._last_time is None else max(0.001, min(0.25, now - self._last_time))
        throttle_rate = 0.0
        if self._previous_throttle is not None:
            throttle_rate = (throttle - self._previous_throttle) / dt
        self._previous_throttle = throttle
        self._last_time = now

        target_gate = 1.0 - _range(throttle, 0.0, THROTTLE_FADE_END)
        if target_gate < self._throttle_gate:
            # The faster the pedal moves, the faster overlap disappears.
            speed = (1.0 / self.fade_seconds) * (1.0 + _clamp(throttle_rate, 0.0, 6.0) * 0.35)
        else:
            # Cam overlap returns more gradually when the driver lifts.
            speed = 0.70 / self.fade_seconds
        self._throttle_gate += max(-speed * dt, min(speed * dt, target_gate - self._throttle_gate))
        self._throttle_gate = _clamp(self._throttle_gate)

        rpm_gate = 1.0 - _range(rpm, self.release_rpm * 0.35, self.release_rpm)
        # RPM mode adds the low-speed cam window; throttle mode uses only the
        # pedal gate. Both modes still obey the same rate-dependent throttle
        # fade so the cam disappears as the pedal crosses 25 percent.
        source = rpm_gate if self.mode == CAM_MODE_RPM else 1.0
        intensity = self.aggressiveness * source * self._throttle_gate if self.enabled else 0.0

        idle_target_rpm = None
        if self.enabled:
            # This frequency-controlled fundamental plus second harmonic is
            # the pattern that sounded right when compared in game. The
            # sharper wave at high aggression makes the idle target itself
            # snap between the cam's drop and recovery instead of merely
            # wobbling around stock.
            lope_gate = _clamp(rpm_gate)
            self._lope_phase = (self._lope_phase + dt * self.frequency_hz * math.tau) % math.tau
            wave = 0.5 + 0.5 * math.tanh(
                self.sharpness
                * (
                    math.sin(self._lope_phase)
                    + CAM_SECOND_HARMONIC * math.sin(2.0 * self._lope_phase - 0.8)
                )
            )
            if idle_rpm is not None:
                base_idle = max(1.0, float(idle_rpm))
                idle_gate = self.aggressiveness * self._throttle_gate * lope_gate
                idle_target_rpm = base_idle * (
                    1.0
                    + (
                        -CAM_IDLE_SWING * self.depth
                        + 2.0 * CAM_IDLE_SWING * self.depth * wave
                    )
                    * idle_gate
                )
            # The low point is a deliberate torque dip and the high point is
            # a strong recovery pulse. The wider idle swing is what makes a
            # 100% setting sound/feel like a very large, choppy cam.
            torque_pulse = 1.0 + intensity * lope_gate * (
                -CAM_TORQUE_DIP * self.torque_dip
                + CAM_TORQUE_RECOVERY * self.recovery_pulse * wave
            )
        else:
            torque_pulse = 1.0
        return CamFrame(
            intensity=_clamp(intensity),
            throttle_gate=self._throttle_gate,
            rpm_gate=_clamp(rpm_gate),
            throttle_rate=throttle_rate,
            torque_pulse=_clamp(torque_pulse, CAM_TORQUE_MIN, CAM_TORQUE_MAX),
            idle_target_rpm=idle_target_rpm,
        )


def apply_cam_shape(
    curve: list[float],
    rpm_per_index: float,
    redline: float,
    intensity: float,
    torque_pulse: float = 1.0,
) -> list[float]:
    """Return a cam-like curve without mutating the caller's curve.

    More overlap softens the low-speed portion and lets the high-speed side
    breathe slightly better.  The final curve point is left untouched because
    Neptune uses it as the engine's limiter tail.
    """

    if len(curve) < 2 or rpm_per_index <= 0.0 or redline <= 0.0:
        return list(curve)
    intensity = _clamp(intensity)
    torque_pulse = _clamp(torque_pulse, CAM_TORQUE_MIN, CAM_TORQUE_MAX)
    if intensity <= 1e-6:
        return list(curve)

    low_end = max(1800.0, redline * 0.50)
    high_start = low_end
    high_end = max(high_start + 1.0, redline * 0.92)
    pulse_end = max(2200.0, min(low_end, redline * 0.46))
    shaped = list(curve)
    for index, value in enumerate(curve[:-1]):
        rpm = index * rpm_per_index
        low_overlap = 1.0 - _range(rpm, 0.0, low_end)
        high_breathing = _range(rpm, high_start, high_end)
        factor = 1.0 - 0.24 * intensity * low_overlap + 0.10 * intensity * high_breathing
        pulse_envelope = 1.0 - _range(rpm, 0.0, pulse_end)
        factor *= 1.0 + (torque_pulse - 1.0) * pulse_envelope
        shaped[index] = value * factor
    return shaped
