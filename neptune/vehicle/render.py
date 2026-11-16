"""Max detail: ask the game to render the world in its full car-detail mode.

Pure memory. The render-mode controller holds a small request block (current, previous, option,
pending); the game's own RequestMode writes it in that order with pending last, and the game
then performs the transition on its next update. Neptune publishes the same request, only from
Free Roam, and only ever reverts a Detail mode it asked for itself.
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass

from neptune.memory import offsets as O
from neptune.memory.process import Process

M = O.MaxDetail
_global_cache: dict[int, int] = {}


class RenderError(Exception):
    """Max detail is unavailable right now; the message says why."""


@dataclass(frozen=True)
class RenderState:
    current: int
    previous: int
    pending: int
    option: int

    @property
    def settled(self) -> bool:
        return self.pending == 0


def _heap(value) -> bool:
    return value is not None and 0x10000 <= value < 0x7FF000000000 and value % 8 == 0


def _system(process: Process, global_address: int) -> int | None:
    root = process.pointer(global_address)
    system = process.pointer(root + M.GLOBAL_TO_SYSTEM) if _heap(root) else None
    return system if _heap(system) else None


def resolve_global(process: Process) -> int:
    """Absolute address of the render-system global: the one most code sites read."""
    cached = _global_cache.get(process.pid)
    if cached:
        return cached
    votes: Counter = Counter()
    try:
        from neptune.memory.scanner import ModuleImage

        image = ModuleImage.capture(process)
        for pattern in M.GLOBAL_SIGS:
            for hit in image.find_all(pattern, limit=512):
                displacement = struct.unpack_from("<i", image.data, hit - image.base + 3)[0]
                votes[hit + 7 + displacement] += 1
    except Exception:
        votes.clear()
    candidates = [address for address, _ in votes.most_common(3)]
    candidates.append(process.base + M.GLOBAL_FALLBACK)
    for address in candidates:
        if controller(process, address) is not None:
            _global_cache[process.pid] = address
            return address
    raise RenderError("This game build's max detail is not supported yet.")


def controller(process: Process, global_address: int) -> int | None:
    """The render-mode controller under the render system, or None if the layout does not fit."""
    system = _system(process, global_address)
    if system is None:
        return None
    wanted = process.u32(system + M.SYSTEM_TYPE)
    sentinel = process.pointer(system + M.SYSTEM_LIST)
    if wanted is None or not _heap(sentinel):
        return None
    node = process.pointer(sentinel)
    for _ in range(M.MAX_NODES):
        if not _heap(node) or node == sentinel:
            return None
        item = process.pointer(node + M.NODE_ITEM)
        if _heap(item):
            kind = process.read(item + M.ITEM_TYPE, 2)
            if kind and struct.unpack("<H", kind)[0] == (wanted & 0xFFFF):
                state = read_state(process, item)
                if state is not None:
                    return item
        node = process.pointer(node)
    return None


def read_state(process: Process, ctrl: int) -> RenderState | None:
    data = process.read(ctrl + M.CURRENT, 16)
    if not data:
        return None
    current, previous = struct.unpack_from("<ii", data, 0)
    pending = data[M.PENDING - M.CURRENT]
    option = struct.unpack_from("<i", data, M.OPTION - M.CURRENT)[0]
    if pending > 1 or not (0 <= current < 64) or not (-1 <= previous < 64):
        return None
    return RenderState(current, previous, pending, option)


def request(process: Process, ctrl: int, expected: RenderState, mode: int) -> bool:
    """Publish a mode change the way the game does: fields first, the pending flag last."""
    if read_state(process, ctrl) != expected or not expected.settled:
        return False
    ok = process.write(ctrl + M.OPTION, struct.pack("<i", 0))
    ok = process.write(ctrl + M.PREVIOUS, struct.pack("<i", expected.current)) and ok
    ok = process.write(ctrl + M.CURRENT, struct.pack("<i", mode)) and ok
    if not ok or read_state(process, ctrl) != RenderState(mode, expected.current, 0, 0):
        return False
    return process.write(ctrl + M.PENDING, b"\x01")
