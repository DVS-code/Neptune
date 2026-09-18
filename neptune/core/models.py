"""Shared domain objects for tunes, transmissions and Neptune logs.

The live memory layer remains deliberately separate from these objects.  A model can be
loaded and analysed when Forza is closed, while a runtime feature can choose which verified
fields it is safe to apply to the current car.
"""

from __future__ import annotations

import math
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1

# `Vehicle.gears()` reads eight forward-gear slots. A ninth write would land in whatever config
# field follows the ratio array.
MAX_FORWARD_GEARS = 8
MAX_LOG_SAMPLES = 250_000


def finite_float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def finite_int(value: Any, default: int | None = None) -> int | None:
    number = finite_float(value)
    if number is None:
        return default
    return int(number)


def _list_of_numbers(values: Any, limit: int = 4096) -> list[float]:
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    for value in values[:limit]:
        number = finite_float(value)
        if number is not None:
            result.append(number)
    return result


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class CarIdentity:
    """Who the log belongs to.

    `fingerprint` is the runtime identity (media name and engine config), used to compare two
    logs. `tune_key` is the Tunes store key the car had when the run was captured: the store key
    samples the idle and curve-count fields the cam and rev limiter write, so it cannot be
    recomputed from a later reading and is recorded as-is.
    """

    car_id: int | None = None
    media_name: str = ""
    friendly_name: str = ""
    fingerprint: list[Any] = field(default_factory=list)
    tune_key: str = ""

    @classmethod
    def from_vehicle(cls, vehicle, friendly_name: str = "", tune_key: str = "") -> CarIdentity:
        media_name = str(getattr(vehicle, "media_name", None) or "")
        car_id = None
        with suppress(Exception):
            car_id = finite_int(getattr(vehicle, "car_id", None))
        fingerprint = []
        with suppress(Exception):
            fingerprint = list(vehicle.identity_fingerprint() or ())
        display_name = friendly_name or media_name
        if not display_name and car_id is not None:
            display_name = f"Car #{car_id}"
        return cls(car_id, media_name, display_name, fingerprint, tune_key or "")

    def to_dict(self) -> dict:
        return {
            "car_id": self.car_id,
            "media_name": self.media_name,
            "friendly_name": self.friendly_name,
            "fingerprint": list(self.fingerprint),
            "tune_key": self.tune_key,
        }

    @classmethod
    def from_dict(cls, data: Any) -> CarIdentity:
        data = data if isinstance(data, dict) else {}
        fingerprint = data.get("fingerprint")
        return cls(
            car_id=finite_int(data.get("car_id")),
            media_name=str(data.get("media_name") or ""),
            friendly_name=str(data.get("friendly_name") or data.get("media_name") or ""),
            fingerprint=list(fingerprint)[:32] if isinstance(fingerprint, (list, tuple)) else [],
            tune_key=str(data.get("tune_key") or ""),
        )


@dataclass
class TransmissionTune:
    final_drive: float | None = None
    ratios: list[float] = field(default_factory=list)
    gear_count: int | None = None
    gear_count_editable: bool = False

    @classmethod
    def from_vehicle(cls, vehicle) -> TransmissionTune:
        """Read this car's gearing, stopping at the first slot that is not a real gear.

        The game's ratio array is a fixed 8 slots whatever the car has, so a 6-speed leaves
        two slots of whatever was there before. Accepting anything above 0.05 let that
        trailing junk through as "gear 7", and a junk value outside 0.05-10.0 then failed
        `validate()`, which made the whole page report "Transmission data is unavailable for
        this car" for an ordinary car that reads perfectly well.

        Forward ratios also descend (4.23, 2.52, 1.66 ...), so a value that climbs is the end
        of the real gears rather than another one.
        """
        try:
            raw_ratios = list(vehicle.gears() or [])
        except Exception:
            raw_ratios = []
        ratios: list[float] = []
        for raw in raw_ratios:
            value = finite_float(raw)
            if value is None or not 0.05 <= value <= 10.0:
                break
            if ratios and value >= ratios[-1]:
                break
            ratios.append(value)

        count = finite_int(getattr(vehicle, "gear_count", None))
        # `gear_count` counts reverse and/or neutral too, so it can exceed the forward
        # ratios that were actually read. Only ever let it shorten the list.
        if count is None or count <= 0 or count > len(ratios):
            count = len(ratios) or None
        if count is not None:
            ratios = ratios[:count]
        return cls(finite_float(getattr(vehicle, "final_drive", None)), ratios, count, False)

    def validate(self) -> list[str]:
        errors = []
        if self.final_drive is None or not 0.1 <= self.final_drive <= 15.0:
            errors.append("Final drive must be between 0.1 and 15.0.")
        if not self.ratios:
            errors.append("At least one forward gear ratio is required.")
        if len(self.ratios) > MAX_FORWARD_GEARS:
            errors.append(f"A maximum of {MAX_FORWARD_GEARS} forward gears is supported by the current reader.")
        for index, ratio in enumerate(self.ratios, 1):
            if not 0.05 <= ratio <= 10.0:
                errors.append(f"Gear {index} must be between 0.05 and 10.0.")
        if self.gear_count is not None and self.gear_count != len(self.ratios):
            errors.append("Gear count must match the number of ratios.")
        return errors

    def to_dict(self) -> dict:
        return {
            "final_drive": self.final_drive,
            "ratios": list(self.ratios),
            "gear_count": self.gear_count,
            "gear_count_editable": bool(self.gear_count_editable),
        }

    @classmethod
    def from_dict(cls, data: Any) -> TransmissionTune:
        data = data if isinstance(data, dict) else {}
        ratios = _list_of_numbers(data.get("ratios"), MAX_FORWARD_GEARS)
        ratios = [max(0.05, min(10.0, value)) for value in ratios]
        count = finite_int(data.get("gear_count"))
        if count is not None:
            count = max(1, min(MAX_FORWARD_GEARS, count))
            ratios = ratios[:count]
        return cls(
            final_drive=finite_float(data.get("final_drive")),
            ratios=ratios,
            gear_count=count if count is None or count == len(ratios) else len(ratios),
            gear_count_editable=bool(data.get("gear_count_editable", False)),
        )


@dataclass
class LogMetadata:
    neptune_version: str = ""
    game_build: str = ""
    created_at: str = field(default_factory=utc_now)
    test_type: str = "Full acceleration pull"
    units: dict[str, str] = field(
        default_factory=lambda: {
            "speed": "km/h",
            "pressure": "psi",
            "power": "hp",
            "torque": "Nm",
        }
    )
    sample_rate_hz: float = 0.0
    car: CarIdentity = field(default_factory=CarIdentity)
    tune_id: str = ""
    tune_name: str = ""
    tune_revision: int | None = None
    transmission: TransmissionTune = field(default_factory=TransmissionTune)
    test_config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "neptune_version": self.neptune_version,
            "game_build": self.game_build,
            "created_at": self.created_at,
            "test_type": self.test_type,
            "units": dict(self.units),
            "sample_rate_hz": self.sample_rate_hz,
            "car": self.car.to_dict(),
            "tune_id": self.tune_id,
            "tune_name": self.tune_name,
            "tune_revision": self.tune_revision,
            "transmission": self.transmission.to_dict(),
            "test_config": dict(self.test_config),
        }

    @classmethod
    def from_dict(cls, data: Any) -> LogMetadata:
        data = data if isinstance(data, dict) else {}
        units = data.get("units") if isinstance(data.get("units"), dict) else {}
        return cls(
            neptune_version=str(data.get("neptune_version") or ""),
            game_build=str(data.get("game_build") or ""),
            created_at=str(data.get("created_at") or ""),
            test_type=str(data.get("test_type") or "Full acceleration pull"),
            units={
                "speed": str(units.get("speed") or "km/h"),
                "pressure": str(units.get("pressure") or "psi"),
                "power": str(units.get("power") or "hp"),
                "torque": str(units.get("torque") or "Nm"),
            },
            sample_rate_hz=finite_float(data.get("sample_rate_hz"), 0.0) or 0.0,
            car=CarIdentity.from_dict(data.get("car")),
            tune_id=str(data.get("tune_id") or ""),
            tune_name=str(data.get("tune_name") or ""),
            tune_revision=finite_int(data.get("tune_revision")),
            transmission=TransmissionTune.from_dict(data.get("transmission")),
            test_config=data.get("test_config") if isinstance(data.get("test_config"), dict) else {},
        )


@dataclass(slots=True)  # a log holds up to MAX_LOG_SAMPLES of these: no per-sample __dict__
class LogSample:
    """One captured frame. Only channels `Vehicle` can actually read are recorded."""

    timestamp: float
    rpm: float | None = None
    speed_ms: float | None = None
    throttle: float | None = None
    brake: float | None = None
    handbrake: float | None = None
    gear: int | None = None
    boost: float | None = None
    power: float | None = None
    torque: float | None = None
    boost_cell: list[int] | None = None
    boost_multiplier: float | None = None
    anti_lag: bool = False
    scramble: bool = False
    launch_control: bool = False

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "rpm": self.rpm,
            "speed_ms": self.speed_ms,
            "throttle": self.throttle,
            "brake": self.brake,
            "handbrake": self.handbrake,
            "gear": self.gear,
            "boost": self.boost,
            "power": self.power,
            "torque": self.torque,
            "boost_cell": list(self.boost_cell) if self.boost_cell is not None else None,
            "boost_multiplier": self.boost_multiplier,
            "anti_lag": bool(self.anti_lag),
            "scramble": bool(self.scramble),
            "launch_control": bool(self.launch_control),
        }

    @classmethod
    def from_dict(cls, data: Any) -> LogSample:
        data = data if isinstance(data, dict) else {}
        cell = data.get("boost_cell")
        if isinstance(cell, (list, tuple)) and len(cell) >= 2:
            boost_cell = [finite_int(cell[0], 0) or 0, finite_int(cell[1], 0) or 0]
        else:
            boost_cell = None
        return cls(
            timestamp=finite_float(data.get("timestamp"), 0.0) or 0.0,
            rpm=finite_float(data.get("rpm")),
            speed_ms=finite_float(data.get("speed_ms")),
            throttle=finite_float(data.get("throttle")),
            brake=finite_float(data.get("brake")),
            handbrake=finite_float(data.get("handbrake")),
            gear=finite_int(data.get("gear")),
            boost=finite_float(data.get("boost")),
            power=finite_float(data.get("power")),
            torque=finite_float(data.get("torque")),
            boost_cell=boost_cell,
            boost_multiplier=finite_float(data.get("boost_multiplier")),
            anti_lag=bool(data.get("anti_lag", False)),
            scramble=bool(data.get("scramble", False)),
            launch_control=bool(data.get("launch_control", False)),
        )


@dataclass
class LogEvent:
    kind: str
    timestamp: float
    label: str = ""
    end_timestamp: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "timestamp": self.timestamp,
            "label": self.label,
            "end_timestamp": self.end_timestamp,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: Any) -> LogEvent:
        data = data if isinstance(data, dict) else {}
        return cls(
            kind=str(data.get("kind") or "event"),
            timestamp=finite_float(data.get("timestamp"), 0.0) or 0.0,
            label=str(data.get("label") or data.get("kind") or "Event"),
            end_timestamp=finite_float(data.get("end_timestamp")),
            details=data.get("details") if isinstance(data.get("details"), dict) else {},
        )


@dataclass
class RunAnalysis:
    quality: str = "Invalid"
    reasons: list[str] = field(default_factory=list)
    duration: float | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    events: list[LogEvent] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "quality": self.quality,
            "reasons": list(self.reasons),
            "duration": self.duration,
            "metrics": dict(self.metrics),
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, data: Any) -> RunAnalysis:
        data = data if isinstance(data, dict) else {}
        return cls(
            quality=str(data.get("quality") or "Invalid"),
            reasons=[str(item) for item in data.get("reasons", []) if item is not None][:32],
            duration=finite_float(data.get("duration")),
            metrics={
                str(key): number
                for key, value in (data.get("metrics") or {}).items()
                if (number := finite_float(value)) is not None
            },
            events=[LogEvent.from_dict(item) for item in data.get("events", [])][:4096],
        )


@dataclass
class NeptuneLog:
    metadata: LogMetadata
    samples: list[LogSample] = field(default_factory=list)
    analysis: RunAnalysis = field(default_factory=RunAnalysis)
    schema: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "schema": int(self.schema),
            "metadata": self.metadata.to_dict(),
            "samples": [sample.to_dict() for sample in self.samples],
            "analysis": self.analysis.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> NeptuneLog:
        data = data if isinstance(data, dict) else {}
        samples = data.get("samples")
        samples = samples[:MAX_LOG_SAMPLES] if isinstance(samples, list) else []
        return cls(
            metadata=LogMetadata.from_dict(data.get("metadata")),
            samples=[LogSample.from_dict(item) for item in samples],
            analysis=RunAnalysis.from_dict(data.get("analysis")),
            schema=finite_int(data.get("schema"), 1) or 1,
        )


@dataclass
class TuneChange:
    field: str
    setting: str
    current: Any
    proposed: Any


@dataclass
class TuneSuggestion:
    goal: str
    title: str
    summary: str
    evidence: str
    changes: list[TuneChange] = field(default_factory=list)
    event_timestamp: float | None = None
    proposal_patches: list[dict[str, Any]] = field(default_factory=list)
