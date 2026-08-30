"""The player's vehicle: discovery, identity and typed live state."""

from __future__ import annotations

import math
import struct

from neptune.memory import offsets as O
from neptune.memory.process import Process

MIN_CURVE_POINTS = 2


MAX_CURVE_POINTS = 246
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
        return self.process.set_f32_array(self.engine + O.EngineModel.CURVE + index * 4, values)

    @property
    def boost_raw(self) -> float | None:
        return self.process.f32(self.car + O.Car.LIVE_BOOST)

    @property
    def boost_raw_blower(self) -> float | None:
        return self.process.f32(self.car + O.Car.LIVE_BOOST_SC)

    @property
    def blower_ceiling(self) -> float | None:

        values = self.process.f32_array(self.car + O.Supercharger.CEILING_A, 2)
        if not values:
            values = [self.process.f32(self.car + offset) for offset in O.Supercharger.CEILINGS]
        values = [value for value in values if value is not None]
        return max(values) if values else None

    def set_blower_ceiling(self, value: float) -> bool:
        ok = True
        for offset in O.Supercharger.CEILINGS:
            ok = self.process.set_f32(self.car + offset, float(value)) and ok
        return ok

    @property
    def boost_absolute(self) -> float | None:
        values = [value for value in (self.boost_raw, self.boost_raw_blower) if value is not None]
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

    def turbo_block(self) -> dict[str, float | None]:
        """Every turbo field in one read.

        The six fields are contiguous, so a caller that wants more than one of them
        should use this rather than calling `turbo_get` per field. Falls back to
        per-field reads if the block read fails, so a partial result is still possible.
        """
        values = self.process.f32_array(self.car + O.Turbo.BLOCK, O.Turbo.BLOCK_COUNT)
        if len(values) == O.Turbo.BLOCK_COUNT:
            return dict(zip(O.Turbo.FIELDS, values, strict=True))
        return {field: self.turbo_get(field) for field in O.Turbo.FIELDS}

    def turbo_set(self, field: str, value: float) -> bool:
        offset = O.Turbo.offset(field)
        return self.process.set_f32(self.car + offset, value) if offset is not None else False

    @property
    def speed_ms(self) -> float | None:
        return self.process.f32(self.car + O.Car.SPEED)

    @property
    def gear(self) -> int | None:
        return self.process.i32(self.car + O.Car.GEAR)

    def _input(self, offset: int, low: float = 0.0, high: float = 1.0) -> float | None:
        value = self.process.f32(self.car + offset)
        if value is None or value != value:
            return None
        return max(low, min(high, value))

    @property
    def throttle(self) -> float | None:
        """Throttle position, 0..1."""
        return self._input(O.Car.THROTTLE)

    @property
    def brake(self) -> float | None:
        """Brake pedal, 0..1."""
        return self._input(O.Car.BRAKE)

    @property
    def steer(self) -> float | None:
        """Steering, -1 (full left) to +1 (full right)."""
        return self._input(O.Car.STEER, -1.0, 1.0)

    @property
    def handbrake(self) -> float | None:
        """Handbrake, 0..1."""
        return self._input(O.Car.HANDBRAKE)

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
        for index, value in enumerate(values[: O.Wheels.COUNT]):
            ok = self.process.set_f32(O.Wheels.addr(self.car, index, field), float(value)) and ok
        return ok

    @property
    def ride_height(self) -> list[float] | None:
        return self.wheel_read(O.Wheels.RIDE_HEIGHT)

    def set_ride_height(self, values) -> bool:
        return self.wheel_write(O.Wheels.RIDE_HEIGHT, values)

    @property
    def camber(self) -> list[float] | None:
        """Per-wheel static camber, in degrees. Stored as a half-angle (sin, cos) pair."""
        values = []
        for index in range(O.Wheels.COUNT):
            base = O.Wheels.addr(self.car, index, 0)
            pair = self.process.f32_array(base + O.Wheels.CAMBER_SIN, 2)
            if len(pair) != 2:
                return None
            sin, cos = pair
            values.append(2.0 * math.degrees(math.atan2(sin, cos)))
        return values

    def _camber_table(self, wheel: int) -> int | None:
        """This wheel's axle camber-kinematics table address (see offsets.CamberTable)."""
        T = O.CamberTable
        axle = self.process.pointer(O.Wheels.addr(self.car, wheel, 0) + T.PTR)
        return self.process.pointer(axle + T.SUB) if axle else None

    def set_camber(self, values) -> bool:
        """Bake per-wheel static camber, in degrees, into the axle's kinematics table."""
        T = O.CamberTable
        ok = True
        written: set[tuple[int, int]] = set()
        for wheel, degrees in enumerate(values[: O.Wheels.COUNT]):
            if degrees is None:
                continue
            table = self._camber_table(wheel)
            if not table:
                continue
            region = T.REGION[wheel]
            key = (table, region)
            if key in written:
                continue
            written.add(key)
            half = math.radians(float(degrees)) / 2.0
            pair = (math.sin(half), math.cos(half))
            for i in range(T.ENTRY_COUNT):
                entry = table + region + i * T.ENTRY_SIZE
                ok = self.process.set_f32_array(entry + T.CAMBER_SIN, pair) and ok
        return bool(written) and ok

    @property
    def wheel_radius(self) -> list[float] | None:
        """Per-wheel radius in metres. Constant for a given car and tyre fitment."""
        return self.wheel_read(O.Wheels.RADIUS)

    @property
    def clearance(self) -> list[float] | None:
        """Chassis clearance above the hub, per wheel — what a tuner calls ride height.

        `RIDE_HEIGHT` stores hub-centre-to-ground, so it carries the wheel radius with it. Two cars
        on the same clearance can differ by 5 cm of hub height purely from tyre size, which is why
        an absolute offset applied to `RIDE_HEIGHT` lands differently on every car.
        """
        heights = self.ride_height
        radii = self.wheel_radius
        if not heights or not radii or len(heights) != len(radii):
            return None
        return [height - radius for height, radius in zip(heights, radii, strict=True)]

    def _sso_read(self, address: int) -> str | None:
        """Read one small-string-optimised std::string."""
        raw = self.process.read(address, 16)
        if raw is None:
            return None
        length = self.process.read(address + O.CarConfig.STRING_LENGTH, 8)
        if length is None:
            return None
        count = struct.unpack("<Q", length)[0]
        if count > O.CarConfig.STRING_CAPACITY:
            return None
        try:
            return raw[:count].decode("ascii")
        except UnicodeDecodeError:
            return None

    @property
    def car_config(self) -> int | None:
        """The customization record: identity, livery, wheels and fitted parts."""
        return self.process.pointer(self.entity + O.CarConfig.ENTITY_TO_RECORD)

    @property
    def media_name(self) -> str | None:
        """The car's internal name, e.g. ALF_Giulia_16."""
        record = self.car_config
        if not record:
            return None
        return self._sso_read(record + O.CarConfig.MEDIA_NAME)

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

    def boost_multiplier(self) -> float:
        """How much the induction system multiplies engine torque, 1.0 when NA.

        `max_scale` (`car+0x0ABC`) is the one boost lever measured to reach physics, and
        power responds LINEARLY to it — 0.5x gave -50%, 3x gave +191%, on two cars with
        telemetry power as the oracle. So it is the forced-induction term the peak figures
        need: without it the estimate is the bare engine curve and reads far too low on any
        boosted car.

        ⚠️ Naturally aspirated cars must read exactly 1.0 rather than whatever happens to
        sit in the field, or a car with no turbo would have its estimate scaled by noise.
        """
        block = self.turbo_block()
        scale = block.get("max_scale")
        if scale is None or scale != scale or scale <= 0.0:
            return 1.0
        if O.is_naturally_aspirated(block.get("max_boost"), block.get("turbine_limit")):
            return 1.0
        return scale

    def peaks(self, curve=None) -> tuple[tuple[float, int], tuple[float, int]]:
        """((peak Nm, rpm), (peak hp, rpm)) from one pass over the curve.

        Pass a curve that has already been read to avoid re-reading it. The interface
        refreshes these several times a second alongside the graph, and reading the
        array once for all three is the difference between one memory read per refresh
        and three.

        ★ MEASURED against the game's own telemetry, not derived:

            torque_nm = curve[index] * max_scale * rpm_per_index

        The curve is **already in a physical unit** — its values run ~2.8-3.7 on a real
        car, not 0-1 — so normalising it by its own maximum (which the old code did) threw
        away the magnitude entirely and left `torque_scale` standing in for it. That is why
        a boosted car read ~3.8x too low.

        ⚠️ `torque_scale` (`config-0x0C`) is NOT part of this product. It reads 236.2 on
        the validation car where the true multiplier is 176.1; using it is what produced
        the wrong answer. It stays only in `fingerprint()`, where it identifies a car
        rather than scaling anything.

        Validation, 784 full-throttle telemetry samples on a turbo car:
        | rpm  | predicted | measured | ratio  |
        |------|-----------|----------|--------|
        | 6500 |     643.7 |    643.7 | 1.0000 |
        | 7000 |     638.1 |    637.6 | 1.0008 |
        | 8000 |     607.6 |    607.0 | 1.0009 |
        Median across the whole range 0.9965. The low bands read a little under because
        those samples were taken while still accelerating and the turbo had not fully
        spooled — the steady-state bands are exact.
        """
        if curve is None:
            curve = self.curve()
        if len(curve) < 2:
            return ((0.0, 0), (0.0, 0))

        per_index = self.rpm_per_index or 100.0

        scale = self.boost_multiplier() * per_index
        body = curve[:-1]

        best_nm = 0.0
        best_nm_rpm = 0
        best_hp = 0.0
        best_hp_rpm = 0
        for index, value in enumerate(body):
            rpm = index * per_index
            nm = value * scale
            if index == 0 or nm > best_nm:
                best_nm = nm
                best_nm_rpm = int(rpm)
            hp = nm * rpm / NM_RPM_TO_HP
            if hp > best_hp:
                best_hp = hp
                best_hp_rpm = int(rpm)
        return ((best_nm, best_nm_rpm), (best_hp, best_hp_rpm))

    def peak_torque(self, curve=None) -> tuple[float, int]:
        """(Nm, rpm) at the current curve's peak."""
        return self.peaks(curve)[0]

    def peak_power(self, curve=None) -> tuple[float, int]:
        """(hp, rpm) at the current curve's peak power."""
        return self.peaks(curve)[1]

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
                target = (
                    instruction
                    + O.Chain.CTX_SIG_INSN_LEN
                    + int.from_bytes(displacement, "little", signed=True)
                )
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
        return [], "No car found. Drive out into the world first."

    begin = process.pointer(entity_list + chain.LIST_VEC_BEGIN)
    end_data = process.read(entity_list + chain.LIST_VEC_END, 8)
    if not begin or not end_data:
        return [], "No car found. Drive out into the world first."

    end = struct.unpack("<Q", end_data)[0]
    if end < begin or (end - begin) % 8 or (end - begin) > 0x100000:
        return [], "No car found. Drive out into the world first."

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
        return [], "A car was found but could not be read on this game build."
    return vehicles, None
