"""Versioned .nlog persistence, capture helpers and offline run analysis."""

from __future__ import annotations

import json
import os
from itertools import pairwise

from neptune.core import paths
from neptune.core.models import (
    SCHEMA_VERSION,
    LogEvent,
    LogMetadata,
    LogSample,
    NeptuneLog,
    RunAnalysis,
    finite_float,
    finite_int,
)

LOG_EXTENSION = ".nlog"

MAP_COLUMNS = 24
"""Boost Map grid width, for projecting logged samples onto map cells.

Must equal `neptune.ui.widgets.boostmap.COLUMNS` or a log's overlay lands on the wrong
cells. Duplicated rather than imported so this module stays free of any UI import;
`test_boost_map_grid_matches_log_projection` holds the two together.
"""


def map_context(log: NeptuneLog) -> tuple[float, int]:
    """(max rpm, map rows) used to project a log's samples onto the Boost Map."""
    config = log.metadata.test_config
    max_rpm = (
        finite_float(config.get("redline_rpm"))
        or max((sample.rpm or 0.0 for sample in log.samples), default=0.0)
        or 8000.0
    )
    rows = max(1, finite_int(config.get("boost_map_rows"), 1) or 1)
    return max_rpm, rows


def boost_map_cell(
    sample: LogSample, max_rpm: float, rows: int = 1, columns: int = MAP_COLUMNS
) -> tuple[int, int] | None:
    """Return the map cell represented by one sample, when its RPM is usable."""
    if sample.rpm is None or max_rpm <= 0.0 or rows <= 0 or columns <= 0:
        return None
    column = int(max(0.0, min(0.999, sample.rpm / max_rpm)) * columns)
    load = 1.0 - sample.throttle if sample.throttle is not None else 0.0
    row = int(max(0.0, min(0.999, load)) * rows)
    return row, column


def boost_map_path(
    samples: list[LogSample], max_rpm: float, rows: int = 1, columns: int = MAP_COLUMNS
) -> tuple[dict[tuple[int, int], int], list[tuple[int, int]]]:
    """Project logged RPM/throttle samples into Boost Map cells."""
    hits: dict[tuple[int, int], int] = {}
    path: list[tuple[int, int]] = []
    for sample in samples:
        cell = boost_map_cell(sample, max_rpm, rows, columns)
        if cell is None:
            continue
        hits[cell] = hits.get(cell, 0) + 1
        if not path or path[-1] != cell:
            path.append(cell)
    return hits, path


def log_dir() -> str:
    directory = os.path.join(paths.data_dir(), "logs")
    os.makedirs(directory, exist_ok=True)
    return directory


def save_log(log: NeptuneLog, path: str | None = None) -> tuple[bool, str, str | None]:
    if not isinstance(log, NeptuneLog) or not log.samples:
        return False, "There is no captured run to save.", None
    if path:
        target = path
    else:
        stem = log.metadata.created_at.replace(":", "-")
        target = os.path.join(log_dir(), f"{stem}{LOG_EXTENSION}")
        suffix = 2
        while os.path.exists(target):
            target = os.path.join(log_dir(), f"{stem}-{suffix}{LOG_EXTENSION}")
            suffix += 1
    if not paths.write_json(target, log.to_dict(), compact=True):
        return False, "Could not write the log file.", None
    return True, "", target


def load_log(path: str) -> tuple[NeptuneLog | None, str]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None, "That file is not a readable Neptune log."
    if not isinstance(data, dict) or not data.get("metadata"):
        return None, "That file is not a valid Neptune log."
    schema = finite_int(data.get("schema"), 1) or 1
    if schema > SCHEMA_VERSION:
        return None, f"This log uses a newer schema (v{schema}). Update Neptune first."
    try:
        log = NeptuneLog.from_dict(data)
    except (TypeError, ValueError, OverflowError):
        return None, "That Neptune log contains invalid data."
    if not log.samples:
        return None, "That Neptune log has no samples."
    if not log.analysis.events:
        log.analysis = analyze_log(log.metadata, log.samples)
    return log, ""


def list_logs() -> list[str]:
    try:
        return sorted(
            os.path.join(log_dir(), name)
            for name in os.listdir(log_dir())
            if name.lower().endswith(LOG_EXTENSION)
        )
    except OSError:
        return []


def sample_from_vehicle(
    vehicle, timestamp: float, boost_multiplier: float | None = None, **states
) -> LogSample:
    """Read the channels the Vehicle abstraction exposes; `states` carries Neptune's own values.

    Channels are best-effort: one unreadable field should not discard an otherwise useful sample.
    """

    def read(name):
        try:
            return getattr(vehicle, name)
        except Exception:
            return None

    return LogSample(
        timestamp=float(timestamp),
        rpm=finite_float(read("rpm")),
        speed_ms=finite_float(read("speed_ms")),
        throttle=finite_float(read("throttle")),
        brake=finite_float(read("brake")),
        handbrake=finite_float(read("handbrake")),
        gear=finite_int(read("gear")),
        boost=finite_float(read("boost_gauge")),
        power=finite_float(states.get("power")),
        torque=finite_float(states.get("torque")),
        boost_cell=list(states.get("boost_cell") or []) or None,
        boost_multiplier=finite_float(boost_multiplier),
        anti_lag=bool(states.get("anti_lag", False)),
        scramble=bool(states.get("scramble", False)),
        launch_control=bool(states.get("launch_control", False)),
    )


def _crossed(previous, current, threshold) -> bool:
    previous = finite_float(previous)
    current = finite_float(current)
    threshold = finite_float(threshold)
    return (
        previous is not None
        and current is not None
        and threshold is not None
        and previous < threshold <= current
    )


def detect_events(metadata: LogMetadata, samples: list[LogSample]) -> list[LogEvent]:
    if not samples:
        return []
    events: list[LogEvent] = []
    launch = None
    if (samples[0].speed_ms or 0.0) <= 0.5:
        launch = next(
            (
                current
                for previous, current in pairwise(samples)
                if (previous.speed_ms or 0.0) <= 0.5 < (current.speed_ms or 0.0)
            ),
            None,
        )
        if launch is not None:
            events.append(
                LogEvent("launch", launch.timestamp, "Launch", details={"speed_after": launch.speed_ms})
            )

    peak_boost = max((sample.boost for sample in samples if sample.boost is not None), default=None)
    spool_recorded = False
    flags = (
        ("anti_lag", "Anti-Lag activation"),
        ("scramble", "Scramble activation"),
        ("launch_control", "Launch Control activation"),
    )
    for previous, current in pairwise(samples):
        if previous.gear is not None and current.gear is not None and previous.gear != current.gear:
            both_rpm = previous.rpm is not None and current.rpm is not None
            both_boost = previous.boost is not None and current.boost is not None
            events.append(
                LogEvent(
                    "shift",
                    current.timestamp,
                    f"{previous.gear} → {current.gear} Shift",
                    details={
                        "from_gear": previous.gear,
                        "to_gear": current.gear,
                        "rpm_before": previous.rpm,
                        "rpm_after": current.rpm,
                        "boost_before": previous.boost,
                        "boost_after": current.boost,
                        "speed_before": previous.speed_ms,
                        "speed_after": current.speed_ms,
                        "power_before": previous.power,
                        "power_after": current.power,
                        "torque_before": previous.torque,
                        "torque_after": current.torque,
                        "rpm_drop": previous.rpm - current.rpm if both_rpm else None,
                        "boost_drop": previous.boost - current.boost if both_boost else None,
                    },
                )
            )
        if (previous.throttle or 0.0) >= 0.9 and (current.throttle or 0.0) < 0.75:
            events.append(LogEvent("throttle_lift", current.timestamp, "Throttle lift"))
        for name, label in flags:
            if not getattr(previous, name) and getattr(current, name):
                events.append(LogEvent(name, current.timestamp, label))
        if (
            not spool_recorded
            and peak_boost is not None
            and peak_boost > 0.0
            and current.boost is not None
            and current.boost >= peak_boost * 0.9
        ):
            events.append(
                LogEvent(
                    "spool",
                    current.timestamp,
                    "Turbo spool",
                    details={"boost": current.boost, "peak_boost": peak_boost},
                )
            )
            spool_recorded = True

    for field, label, kind in (
        ("boost", "Peak boost", "peak_boost"),
        ("power", "Peak power", "peak_power"),
        ("torque", "Peak torque", "peak_torque"),
    ):
        values = [(getattr(sample, field), sample) for sample in samples if getattr(sample, field) is not None]
        if values:
            value, sample = max(values, key=lambda item: item[0])
            events.append(LogEvent(kind, sample.timestamp, label, details={"value": value}))

    config = metadata.test_config
    redline = finite_float(config.get("redline_rpm"))
    if redline:
        limiter = next((sample for sample in samples if (sample.rpm or 0.0) >= redline * 0.99), None)
        if limiter is not None:
            events.append(LogEvent("limiter", limiter.timestamp, "Rev limiter contact"))

    start_ms = finite_float(config.get("start_ms"))
    end_ms = finite_float(config.get("end_ms"))
    rolling = bool(start_ms and end_ms and end_ms > start_ms)
    start = (
        next((cur for prev, cur in pairwise(samples) if _crossed(prev.speed_ms, cur.speed_ms, start_ms)), None)
        if rolling
        else launch
    )
    if start is not None:
        events.append(LogEvent("test_start", start.timestamp, "Test start", details={"speed_ms": start.speed_ms}))

    end = None
    if end_ms and end_ms > (start_ms or 0.0):
        end = next((cur for prev, cur in pairwise(samples) if _crossed(prev.speed_ms, cur.speed_ms, end_ms)), None)
    if end is not None:
        events.append(LogEvent("test_end", end.timestamp, "Test end", details={"speed_ms": end.speed_ms}))
    else:
        lift = next((event for event in reversed(events) if event.kind == "throttle_lift"), None)
        if lift is not None:
            events.append(LogEvent("test_end", lift.timestamp, "Test end", details={"reason": lift.kind}))
        elif len(samples) >= 2 and not end_ms:
            events.append(
                LogEvent("test_end", samples[-1].timestamp, "Test end", details={"reason": "capture_finished"})
            )
    events.sort(key=lambda event: event.timestamp)
    return events


def analyze_log(metadata: LogMetadata, samples: list[LogSample]) -> RunAnalysis:
    samples = sorted(
        (sample for sample in samples if isinstance(sample, LogSample)), key=lambda sample: sample.timestamp
    )
    if len(samples) < 2:
        return RunAnalysis(
            "Invalid", ["The run does not contain enough samples."], events=detect_events(metadata, samples)
        )

    duration = max(0.0, samples[-1].timestamp - samples[0].timestamp)
    reasons: list[str] = []
    expected = 1.0 / metadata.sample_rate_hz if metadata.sample_rate_hz > 0 else 0.05
    max_gap = max(b.timestamp - a.timestamp for a, b in pairwise(samples))
    if max_gap > max(0.15, expected * 3.0):
        reasons.append(f"Moderate sample gap of {max_gap:.2f} s.")
    if max_gap > max(0.5, expected * 8.0):
        reasons.append(f"Large data gap of {max_gap:.2f} s.")

    config = metadata.test_config
    start_ms = finite_float(config.get("start_ms"))
    end_ms = finite_float(config.get("end_ms"))
    speeds = [sample.speed_ms for sample in samples if sample.speed_ms is not None]
    if start_ms is not None and speeds and min(speeds) > start_ms + 2.0:
        reasons.append("Test started above the expected starting condition.")
    if end_ms is not None and (not speeds or max(speeds) < end_ms):
        reasons.append("Target speed was never reached.")

    throttle = [sample.throttle for sample in samples if sample.throttle is not None]
    brakes = [sample.brake for sample in samples if sample.brake is not None]
    if config.get("full_throttle", True) and throttle and min(throttle) < 0.85:
        reasons.append(f"Throttle dropped to {min(throttle) * 100:.0f}%.")
    if brakes and max(brakes) > 0.10:
        reasons.append("Brake input interrupted the run.")

    metrics: dict[str, float] = {"duration": duration}
    for field, key in (("rpm", "peak_rpm"), ("boost", "peak_boost"), ("power", "peak_power"), ("torque", "peak_torque")):
        values = [getattr(sample, field) for sample in samples if getattr(sample, field) is not None]
        if values:
            metrics[key] = max(values)

    if not speeds or duration <= 0.0:
        quality = "Invalid"
        reasons.insert(0, "No usable speed timeline was captured.")
    elif any(reason.startswith(("Target speed was never reached", "Large data gap")) for reason in reasons):
        quality = "Invalid"
    elif not reasons:
        quality = "Excellent"
    elif all(reason.startswith("Moderate sample gap") for reason in reasons):
        quality = "Good"
    else:
        quality = "Poor"
    return RunAnalysis(quality, reasons, duration, metrics, detect_events(metadata, samples))


def compare_logs(left: NeptuneLog, right: NeptuneLog) -> dict:
    """Return evidence-only differences; callers must not turn these into causal claims."""
    reasons = []
    if left.metadata.test_type != right.metadata.test_type:
        reasons.append("The test types do not match.")
    left_car, right_car = left.metadata.car, right.metadata.car
    if left_car.fingerprint and right_car.fingerprint and left_car.fingerprint != right_car.fingerprint:
        reasons.append("The car identities do not match.")
    elif left_car.car_id is not None and right_car.car_id is not None and left_car.car_id != right_car.car_id:
        reasons.append("The car IDs do not match.")
    left_range = (left.metadata.test_config.get("start_ms"), left.metadata.test_config.get("end_ms"))
    right_range = (right.metadata.test_config.get("start_ms"), right.metadata.test_config.get("end_ms"))
    if left_range != right_range:
        reasons.append("The target speed ranges do not match.")
    metrics = {
        key: {"left": a, "right": b, "difference": b - a}
        for key in sorted(set(left.analysis.metrics) | set(right.analysis.metrics))
        if (a := left.analysis.metrics.get(key)) is not None and (b := right.analysis.metrics.get(key)) is not None
    }
    return {
        "compatible": not reasons,
        "reasons": reasons,
        "metrics": metrics,
        "quality": {"left": left.analysis.quality, "right": right.analysis.quality},
    }
