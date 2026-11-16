"""Engine on/off for the player's car, through the game's own ignition setter."""

from __future__ import annotations

import struct

from neptune.memory import offsets as O
from neptune.memory import remote
from neptune.memory.process import Process
from neptune.memory.scanner import compile_pattern

I = O.Ignition  # noqa: E741


class IgnitionError(Exception):
    """The engine cannot be switched on this car right now; the message says why."""


def engine_on(process: Process, entity: int) -> bool | None:
    data = process.read(entity + I.ENGINE_ON, 1)
    if not data or data[0] > 1:
        return None
    return data[0] == 1


def drive_power(process: Process, entity: int) -> float | None:
    value = process.f32(entity + I.DRIVE_POWER)
    return value if value is not None and 0.0 <= value <= 1.0 else None


def set_drive_power(process: Process, entity: int, value: float) -> bool:
    return process.set_f32(entity + I.DRIVE_POWER, max(0.0, min(1.0, float(value))))


def setter(process: Process, entity: int) -> int:
    """The ignition setter from this entity's live vtable, or raise if it is not the one we know."""
    vtable = process.pointer(entity)
    function = process.pointer(vtable + I.SETTER_SLOT) if vtable else None
    if not function or not (process.base <= vtable < process.base + 0x20000000):
        raise IgnitionError("The car's engine controls could not be found.")
    code = process.read(function, 32)
    if not code or not compile_pattern(I.SETTER_SIG).match(code):
        raise IgnitionError("This game build's engine switch is not supported yet.")
    field = struct.unpack_from("<I", code, I.SETTER_FIELD_AT)[0]
    if field != I.ENGINE_ON:
        raise IgnitionError("This game build's engine switch is not supported yet.")
    return function


def check_car(process: Process, entity: int) -> None:
    if process.u32(entity + I.CAR_MODE) != 0:
        raise IgnitionError("The engine switch only works on your own car.")
    flag = process.read(entity + I.MODE_FLAG, 1)
    if not flag or flag[0] > 1:
        raise IgnitionError("The car is in a mode where the engine cannot be switched.")
    if engine_on(process, entity) is None:
        raise IgnitionError("The car's engine state could not be read.")


def set_engine(process: Process, entity: int, on: bool) -> bool:
    """Switch the engine through the game's setter. True when it now reads as requested."""
    check_car(process, entity)
    function = setter(process, entity)
    remote.call(process, function, entity, 1 if on else 0)
    return engine_on(process, entity) == bool(on)
