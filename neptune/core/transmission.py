"""Pure transmission calculations used by the Transmission page, DYNO and log analysis."""

from __future__ import annotations

import math

from neptune.core.models import TransmissionTune, finite_float
from neptune.vehicle.vehicle import ROLLING_RADIUS_M


def speed_at_rpm(rpm: float, ratio: float, final_drive: float, radius_m: float = ROLLING_RADIUS_M) -> float:
    rpm = finite_float(rpm, 0.0) or 0.0
    ratio = finite_float(ratio, 0.0) or 0.0
    final_drive = finite_float(final_drive, 0.0) or 0.0
    if rpm <= 0.0 or ratio <= 0.0 or final_drive <= 0.0 or radius_m <= 0.0:
        return 0.0
    wheel_rpm = rpm / (ratio * final_drive)
    return wheel_rpm * 2.0 * math.pi * radius_m * 60.0 / 1000.0


def rpm_after_shift(rpm: float, current_ratio: float, next_ratio: float) -> float:
    if rpm <= 0.0 or current_ratio <= 0.0 or next_ratio <= 0.0:
        return 0.0
    return rpm * next_ratio / current_ratio


def gear_rows(
    tune: TransmissionTune,
    redline: float,
    torque_curve: list[float] | None = None,
    rpm_step: float | None = None,
) -> list[dict]:
    """One row per forward gear: top speed, where the next gear lands, and a shift estimate."""
    rows = []
    for index, ratio in enumerate(tune.ratios):
        following = tune.ratios[index + 1] if index + 1 < len(tune.ratios) else None
        rows.append(
            {
                "gear": index + 1,
                "top_speed_kph": speed_at_rpm(redline, ratio, tune.final_drive or 0.0),
                "shift_landing_rpm": rpm_after_shift(redline, ratio, following) if following else 0.0,
                "optimal_shift_rpm": optimal_shift_rpm(tune.ratios, index, redline, torque_curve, rpm_step)
                if torque_curve and rpm_step
                else None,
            }
        )
    return rows


def optimal_shift_rpm(
    ratios: list[float], gear_index: int, redline: float, torque_curve: list[float], rpm_step: float
) -> float | None:
    """Estimate the upshift point from wheel-torque equality.

    Shift at the first rpm, from the torque peak up, where the next gear at the rpm it lands on
    puts at least as much torque to the wheels as the current gear; the limiter when that never
    happens. This is an estimate only: drivetrain losses, boost transients and shift time are not
    represented. Missing or malformed inputs return None instead of a confident guess.

    The next-gear torque must be read at the rpm each candidate shift lands on. Reading it at
    the landing rpm of a shift at the limiter compared every candidate against the same point.
    """
    if not 0 <= gear_index + 1 < len(ratios) or redline <= 0.0 or rpm_step <= 0.0:
        return None
    current, following = ratios[gear_index], ratios[gear_index + 1]
    body = torque_curve[:-1]  # the last sample is the limiter tail, not torque
    if current <= 0.0 or following <= 0.0 or len(body) < 2:
        return None

    def torque(rpm: float) -> float:
        index = max(0, min(len(body) - 1, round(rpm / rpm_step)))
        return max(0.0, float(body[index]))

    rpm = max(range(len(body)), key=body.__getitem__) * rpm_step
    while rpm <= redline:
        if torque(rpm) * current <= torque(rpm * following / current) * following:
            return rpm
        rpm += rpm_step
    return redline
