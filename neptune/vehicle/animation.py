"""The player's car animations: panel events, driver visibility and wipers.

Panel events (doors, hood, pop-ups...) are fired through the game's animation setter, which
creates the event on first use, so they need `neptune.memory.remote`. Driver visibility and the
wipers are plain memory: one byte on the render model, and the wiper simulation's own state.
Everything here is address logic only; the Animations feature owns state and restore.
"""

from __future__ import annotations

import struct

from neptune.memory import offsets as O
from neptune.memory import remote
from neptune.memory.process import Process
from neptune.memory.scanner import compile_pattern

A, D, W = O.Animation, O.Driver, O.Wipers
HEAP_MIN = 0x10000000
HEAP_MAX = 0x7FF000000000


class AnimationError(Exception):
    """Animations are unavailable right now; the message says why."""


def _heap(value) -> bool:
    return value is not None and HEAP_MIN <= value < HEAP_MAX and value % 8 == 0


def fnv1a(name: str) -> int:
    value = 0x811C9DC5
    for byte in name.encode("ascii"):
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


# --------------------------------------------------------------------------- panel events

_setter_cache: dict[int, int] = {}


def trigger_setter(process: Process) -> int:
    """The animation bool/trigger setter, by signature. Cached per process."""
    cached = _setter_cache.get(process.pid)
    if cached:
        return cached
    from neptune.memory.scanner import ModuleImage

    hits = ModuleImage.capture(process).find_all(A.TRIGGER_SIG, limit=2)
    if len(hits) != 1:
        raise AnimationError("This game build's animations are not supported yet.")
    _setter_cache[process.pid] = hits[0]
    return hits[0]


def component(process: Process, entity: int) -> int:
    """The car's animation component, once its graph has loaded."""
    comp = process.pointer(entity + A.ENTITY_TO_COMPONENT)
    runtime = process.pointer(comp + A.COMPONENT_TO_RUNTIME) if _heap(comp) else None
    if not _heap(runtime):
        raise AnimationError("The car's animations are not loaded yet.")
    ready = process.read(runtime + A.RUNTIME_READY, 1)
    graphs = process.u32(runtime + A.RUNTIME_GRAPHS)
    if not ready or not ready[0] or not graphs:
        raise AnimationError("The car's animations are not loaded yet.")
    return comp


def fire(process: Process, entity: int, events: list[str], value: bool) -> None:
    """Set each named event to `value` in one call. Unknown events are harmless no-ops."""
    if not events:
        return
    setter = trigger_setter(process)
    comp = component(process, entity)
    data = b"".join(struct.pack("<I", fnv1a(name)) for name in events)
    calls = [
        (setter, (comp, remote.data_ref(index * 4), 1 if value else 0))
        for index in range(len(events))
    ]
    remote.call_many(process, calls, data=data)


def presentation(process: Process, entity: int) -> int | None:
    data = process.read(entity + A.PRESENTATION, 1)
    return data[0] if data and data[0] <= 1 else None


def set_presentation(process: Process, entity: int, value: int) -> bool:
    return process.write(entity + A.PRESENTATION, bytes([1 if value else 0]))


# --------------------------------------------------------------------------- driver


def driver_field(process: Process, entity: int) -> int:
    """Address of the driver-visibility byte, proven against the model's own getter and setter."""
    proxy = process.pointer(entity + O.Render.ENTITY_TO_PROXY)
    comp = process.pointer(entity + A.ENTITY_TO_COMPONENT)
    if not _heap(proxy) or not _heap(comp) or process.pointer(comp + A.COMPONENT_TO_PROXY) != proxy:
        raise AnimationError("The driver model could not be found.")
    model = process.pointer(proxy + O.Render.PROXY_TO_MODEL)
    vtable = process.pointer(model) if _heap(model) else None
    if not vtable or not (process.base <= vtable < process.base + 0x20000000):
        raise AnimationError("The driver model could not be found.")
    getter = process.read(process.pointer(vtable + D.GETTER_SLOT) or 0, 8)
    setter = process.read(process.pointer(vtable + D.SETTER_SLOT) or 0, 7)
    if not getter or not setter:
        raise AnimationError("The driver model could not be found.")
    if not compile_pattern(D.GETTER_SIG).match(getter) or not compile_pattern(D.SETTER_SIG).match(
        setter
    ):
        raise AnimationError("This game build's driver visibility is not supported yet.")
    offset = struct.unpack_from("<I", getter, D.FIELD_AT)[0]
    if offset != struct.unpack_from("<I", setter, D.FIELD_AT - 1)[0]:
        raise AnimationError("This game build's driver visibility is not supported yet.")
    return model + offset


def driver_visible(process: Process, address: int) -> bool | None:
    data = process.read(address, 1)
    return None if not data or data[0] > 1 else data[0] == 1


def set_driver_visible(process: Process, address: int, visible: bool) -> bool:
    return process.write(address, bytes([1 if visible else 0]))


# --------------------------------------------------------------------------- wipers


def wiper_state(process: Process, entity: int) -> dict | None:
    data = process.read(entity + W.PHASE, 23)
    if not data:
        return None
    phase, duration = struct.unpack_from("<ff", data, 0)
    state = data[W.STATE - W.PHASE]
    if state > 3 or not (-0.1 <= phase <= 1.1) or not (0.0 <= duration <= 10.0):
        return None
    return {"phase": phase, "duration": duration, "state": state, "flag": data[W.FLAG - W.PHASE]}


def request_sweep(process: Process, entity: int, duration: float | None = None) -> bool:
    """Start one sweep. With `duration`, start it at that length (the game times the rest)."""
    if duration is None:
        return process.write(entity + W.STATE, bytes([W.REQUEST]))
    current = wiper_state(process, entity)
    if current is None or current["state"] >= W.REQUEST:
        return False
    ok = process.set_f32(entity + W.DURATION, max(0.1, min(10.0, float(duration))))
    # timer 0, keep the flag, enter the sweeping state directly with our duration
    payload = struct.pack("<f", 0.0) + bytes([current["flag"], W.SWEEPING])
    return process.write(entity + W.TIMER, payload) and ok


def set_wiper_duration(process: Process, entity: int, duration: float) -> bool:
    return process.set_f32(entity + W.DURATION, float(duration))
