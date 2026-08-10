"""The player's vehicle: discovery, identity and typed live state."""
from __future__ import annotations

import math
import struct

from neptune.memory import offsets as O
from neptune.memory.process import Process

MIN_CURVE_POINTS = 2
MAX_CURVE_POINTS = 4096
ROLLING_RADIUS_M = 0.34
NM_RPM_TO_HP = 7127.0


class Vehicle:
    """A live car. Holds addresses only; every value is read on demand."""

    def __init__(self, process: Process, entity: int, car: int):
        self.process = process
        self.entity = entity
        self.car = car
        self.engine = car + O.Car.ENGINE_MODEL
        self.config = entity + O.Config.ENTITY_TO_CONFIG

    def fingerprint(self) -> tuple | None:
        """Identity key for this car.

        Samples only fields that no Neptune control ever writes, so applying a
        tune can never change the identity of the car it was applied to.
        """
        try:
            count = self.curve_count
            if not count or not (MIN_CURVE_POINTS <= count <= MAX_CURVE_POINTS):
                return None
            idle = self.idle_rpm
            scale = self.rpm_per_index
            aspiration = self.aspiration
            torque = self.torque_scale
            if idle is None or scale is None:
                return None
            return (
                int(count),
                int(round(idle)),
                int(round(scale)),
                int(aspiration) if aspiration is not None else -1,
                int(round(torque)) if torque is not None else -1,
            )
        except Exception:
            return None

    def is_car(self) -> bool:
        """True when this object reads as a real, loaded car."""
        rpm = self.rpm
        if rpm is None or not (-50.0 <= rpm <= 60000.0):
            return False
        redline = self.max_rpm
        if redline is not None and not (0.0 <= redline <= 60000.0):
            return False
        return True

    def can_tune_curve(self) -> bool:
        """True when the torque curve is safe to write."""
        count = self.curve_count
        if not count or not (MIN_CURVE_POINTS <= count <= MAX_CURVE_POINTS):
            return False
        scale = self.rpm_per_index
        if scale is None or abs(scale - 100.0) > 5.0:
            return False
        engine_rpm = self.engine_speed_rpm
        car_rpm = self.rpm
        if engine_rpm is None or car_rpm is None or abs(engine_rpm - car_rpm) > 1.0:
            return False
        return True

    @property
    def rpm(self) -> float | None:
        value = self.process.f32(self.car + O.Car.ENGINE_MODEL)
        return value * O.RAD_TO_RPM if value is not None else None

    @property
    def engine_speed_rpm(self) -> float | None:
        value = self.process.f32(self.engine + O.EngineModel.SPEED)
        return value * O.RAD_TO_RPM if value is not None else None

    @property
    def max_rpm(self) -> float | None:
        value = self.process.f32(self.car + O.Car.MAX_SPEED)
        return value * O.RAD_TO_RPM if value is not None else None

    @property
    def idle_rpm(self) -> float | None:
        value = self.process.f32(self.car + O.Car.IDLE_SPEED)
        return value * O.RAD_TO_RPM if value is not None else None

    @property
    def aspiration(self) -> int | None:
        return self.process.i32(self.engine + O.EngineModel.ASPIRATION)

    @property
    def curve_count(self) -> int | None:
        return self.process.i32(self.engine + O.EngineModel.CURVE_COUNT)

    @property
    def rpm_per_index(self) -> float | None:
        value = self.process.f32(self.engine + O.EngineModel.X_NORM)
        return value * O.RAD_TO_RPM if value is not None else None

    def _engine_rpm_field(self, offset: int) -> float | None:
        value = self.process.f32(self.engine + offset)
        return value * O.RAD_TO_RPM if value is not None else None

    def _set_engine_rpm_field(self, offset: int, rpm: float) -> bool:
        return self.process.set_f32(self.engine + offset, float(rpm) / O.RAD_TO_RPM)

    @property
    def shift_threshold(self) -> float | None:
        return self._engine_rpm_field(O.EngineModel.THRESH)

    def set_shift_threshold(self, rpm: float) -> bool:
        return self._set_engine_rpm_field(O.EngineModel.THRESH, rpm)

    @property
    def rev_ceiling(self) -> float | None:
        """The rpm the car shifts and limits at."""
        return self._engine_rpm_field(O.EngineModel.MAX_CLAMP)

    def set_rev_ceiling(self, rpm: float) -> bool:
        return self._set_engine_rpm_field(O.EngineModel.MAX_CLAMP, rpm)

    def curve(self) -> list[float]:
        count = self.curve_count
        if not count or not (MIN_CURVE_POINTS <= count <= MAX_CURVE_POINTS):
            return []
        return self.process.f32_array(self.engine + O.EngineModel.CURVE, count)

    def set_curve(self, values) -> bool:
        return self.process.set_f32_array(self.engine + O.EngineModel.CURVE, values)

    def set_curve_from(self, index: int, values) -> bool:
        return self.process.set_f32_array(
            self.engine + O.EngineModel.CURVE + index * 4, values)

    @property
    def boost_raw(self) -> float | None:
        return self.process.f32(self.car + O.Car.LIVE_BOOST)

    @property
    def boost_raw_blower(self) -> float | None:
        return self.process.f32(self.car + O.Car.LIVE_BOOST_SC)

    @property
    def blower_ceiling(self) -> float | None:
        values = [self.process.f32(self.car + offset)
                  for offset in O.Supercharger.CEILINGS]
        values = [value for value in values if value is not None]
        return max(values) if values else None

    def set_blower_ceiling(self, value: float) -> bool:
        ok = True
        for offset in O.Supercharger.CEILINGS:
            ok = self.process.set_f32(self.car + offset, float(value)) and ok
        return ok

    @property
    def boost_absolute(self) -> float | None:
        values = [value for value in (self.boost_raw, self.boost_raw_blower)
                  if value is not None]
        return max(values) if values else None

    @property
    def boost_gauge(self) -> float | None:
        """Boost as the in-game gauge shows it."""
        value = self.boost_absolute
        return O.boost_to_gauge(value) if value is not None else None

    @property
    def turbine(self) -> float | None:
        return self.process.f32(self.car + O.Car.LIVE_TURBINE)

    def set_turbine(self, value: float) -> bool:
        return self.process.set_f32(self.car + O.Car.LIVE_TURBINE, value)

    def turbo_get(self, field: str) -> float | None:
        offset = O.Turbo.offset(field)
        return self.process.f32(self.car + offset) if offset is not None else None

    def turbo_set(self, field: str, value: float) -> bool:
        offset = O.Turbo.offset(field)
        return self.process.set_f32(self.car + offset, value) if offset is not None else False

    @property
    def speed_ms(self) -> float | None:
        return self.process.f32(self.car + O.Car.SPEED)

    @property
    def gear(self) -> int | None:
        return self.process.i32(self.car + O.Car.GEAR)

    def wheel_read(self, field: int) -> list[float] | None:
        values = []
        for index in range(O.Wheels.COUNT):
            value = self.process.f32(O.Wheels.addr(self.car, index, field))
            if value is None:
                return None
            values.append(value)
        return values

    def wheel_write(self, field: int, values) -> bool:
        ok = True
        for index, value in enumerate(values[:O.Wheels.COUNT]):
            ok = self.process.set_f32(
                O.Wheels.addr(self.car, index, field), float(value)) and ok
        return ok

    @property
    def ride_height(self) -> list[float] | None:
        return self.wheel_read(O.Wheels.RIDE_HEIGHT)

    def set_ride_height(self, values) -> bool:
        return self.wheel_write(O.Wheels.RIDE_HEIGHT, values)

    @property
    def redline(self) -> float | None:
        return self.process.f32(self.config + O.Config.REDLINE)

    @property
    def torque_scale(self) -> float | None:
        return self.process.f32(self.config + O.Config.TORQUE_SCALE)

    @property
    def final_drive(self) -> float | None:
        return self.process.f32(self.config + O.Config.FINAL_DRIVE)

    def set_final_drive(self, value: float) -> bool:
        return self.process.set_f32(self.config + O.Config.FINAL_DRIVE, value)

    @property
    def reverse_ratio(self) -> float | None:
        return self.process.f32(self.config + O.Config.REVERSE)

    def set_reverse_ratio(self, value: float) -> bool:
        return self.process.set_f32(self.config + O.Config.REVERSE, value)

    def gears(self) -> list[float]:
        return self.process.f32_array(self.config + O.Config.GEARS, 8)

    def set_gear_ratio(self, index: int, value: float) -> bool:
        return self.process.set_f32(self.config + O.Config.GEARS + index * 4, value)

    @property
    def gear_count(self) -> int | None:
        return self.process.i32(self.config + O.Config.NUM_GEARS)

    def peak_torque(self) -> tuple[float, int]:
        """(Nm, rpm) at the current curve's peak."""
        curve = self.curve()
        scale = self.torque_scale
        if len(curve) < 2 or not scale:
            return (0.0, 0)
        body = curve[:-1]
        peak = max(body)
        reference = max(abs(value) for value in body) or 1.0
        per_index = self.rpm_per_index or 100.0
        return (peak / reference * scale, int(body.index(peak) * per_index))

    def peak_power(self) -> tuple[float, int]:
        """(hp, rpm) at the current curve's peak power."""
        curve = self.curve()
        scale = self.torque_scale
        if len(curve) < 2 or not scale:
            return (0.0, 0)
        body = curve[:-1]
        reference = max(abs(value) for value in body) or 1.0
        per_index = self.rpm_per_index or 100.0
        best_hp = 0.0
        best_rpm = 0
        for index, value in enumerate(body):
            rpm = index * per_index
            hp = (value / reference * scale) * rpm / NM_RPM_TO_HP
            if hp > best_hp:
                best_hp = hp
                best_rpm = int(rpm)
        return (best_hp, best_rpm)

    def speed_at_ceiling(self, gear_index: int) -> float:
        """Road speed in km/h at the rev ceiling in the given gear."""
        ratios = self.gears()
        final = self.final_drive
        ceiling = self.rev_ceiling
        if not ratios or gear_index >= len(ratios) or not final or not ceiling:
            return 0.0
        ratio = ratios[gear_index]
        if not ratio:
            return 0.0
        wheel_rpm = ceiling / (ratio * final)
        return wheel_rpm * 2 * math.pi * ROLLING_RADIUS_M * 60 / 1000.0


_context_cache: dict[int, int] = {}


def resolve_context(process: Process) -> int:
    cached = _context_cache.get(process.pid)
    if cached:
        return cached

    resolved = O.Chain.CTX
    try:
        from neptune.memory.scanner import ModuleImage
        image = ModuleImage.capture(process)
        hits = image.find_all(O.Chain.CTX_SIG, limit=4)
        if len(hits) == 1:
            instruction = hits[0] + O.Chain.CTX_SIG_INSN
            displacement = process.read(hits[0] + O.Chain.CTX_SIG_DISP, 4)
            if displacement:
                target = instruction + O.Chain.CTX_SIG_INSN_LEN + int.from_bytes(
                    displacement, 'little', signed=True)
                candidate = target - process.base
                if 0 < candidate < 0x20000000 and process.pointer(process.base + candidate):
                    resolved = candidate
    except Exception:
        pass

    _context_cache[process.pid] = resolved
    return resolved


def find_vehicles(process: Process) -> tuple[list[Vehicle], str | None]:
    """Every valid car currently loaded. Returns (vehicles, error)."""
    chain = O.Chain
    context = resolve_context(process)
    list_slot = process.chain(context, chain.CTX_TO_A, chain.A_TO_MGR, chain.MGR_TO_LIST)
    entity_list = process.pointer(list_slot) if list_slot else None
    if not entity_list:
        return [], 'No car found. Drive out into the world first.'

    begin = process.pointer(entity_list + chain.LIST_VEC_BEGIN)
    end_data = process.read(entity_list + chain.LIST_VEC_END, 8)
    if not begin or not end_data:
        return [], 'No car found. Drive out into the world first.'

    end = struct.unpack('<Q', end_data)[0]
    if end < begin or (end - begin) % 8 or (end - begin) > 0x100000:
        return [], 'No car found. Drive out into the world first.'

    vehicles = []
    rejected = 0
    for address in range(begin, end, 8):
        entity = process.pointer(address)
        if not entity:
            continue
        car = process.pointer(entity + chain.ENTITY_TO_CAR)
        if not car:
            continue
        vehicle = Vehicle(process, entity, car)
        if vehicle.is_car():
            vehicles.append(vehicle)
        else:
            rejected += 1

    if not vehicles and rejected:
        return [], 'A car was found but could not be read on this game build.'
    return vehicles, None
