"""Run one short function call inside the game.

Some game behaviour has no memory lever: an animation trigger, for example, only exists once the
game's own setter has created it. For those, Neptune runs the game's function on a short-lived
thread of its own:

    sub rsp, 0x28
    mov rcx, a0 ; mov rdx, a1 ; mov r8, a2 ; mov r9, a3
    mov rax, function
    call rax                 ; repeated for each call in a batch
    add rsp, 0x28
    ret                      ; eax becomes the thread's exit code

The stub and any data it points at share one fresh page. The page is made execute/read before
the thread starts, so nothing in the game can write into it while it runs. Calls are serialised,
bounded by a timeout, and the page is freed only once the thread has provably finished: freeing
code a thread might still be executing would crash the game, so a call that times out
deliberately leaks its page instead.

Only call functions whose behaviour is understood and that are safe to enter from another
thread (the animation setter takes its own lock and queues work while the graph is busy).
"""

from __future__ import annotations

import struct
import threading

from neptune.memory import k32
from neptune.memory.process import Process

PAGE_SIZE = 0x1000
DATA_OFFSET = 0x800
DEFAULT_TIMEOUT_MS = 2000

SUB_RSP = bytes.fromhex("4883EC28")
ADD_RSP = bytes.fromhex("4883C428")
RET = bytes.fromhex("C3")
MOV_RAX = bytes.fromhex("48B8")
CALL_RAX = bytes.fromhex("FFD0")
ARG_REGISTERS = (
    bytes.fromhex("48B9"),
    bytes.fromhex("48BA"),
    bytes.fromhex("49B8"),
    bytes.fromhex("49B9"),
)


class DataRef:
    """An argument that becomes the address of `offset` within the call's data block."""

    __slots__ = ("offset",)

    def __init__(self, offset: int = 0):
        self.offset = int(offset)


def data_ref(offset: int = 0) -> DataRef:
    return DataRef(offset)


DATA = DataRef(0)  # the start of the data block

_lock = threading.Lock()
_leaked: list[int] = []


class RemoteCallError(Exception):
    """A call could not be made, or did not finish in time."""


def build_stub(calls: list[tuple[int, list[int]]]) -> bytes:
    """x64 stub making each (function, args) call in order; eax of the last becomes the result."""
    code = bytearray(SUB_RSP)  # shadow space, and keeps rsp 16-byte aligned at each call
    for function, args in calls:
        if not function:
            raise RemoteCallError("No function to call.")
        if len(args) > len(ARG_REGISTERS):
            raise RemoteCallError("At most four arguments are supported.")
        for opcode, value in zip(ARG_REGISTERS, args, strict=False):
            code += opcode + struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF)
        code += MOV_RAX + struct.pack("<Q", function) + CALL_RAX
    code += ADD_RSP + RET
    if len(code) > DATA_OFFSET:
        raise RemoteCallError("Too many calls for one stub.")
    return bytes(code)


def call(
    process: Process, function: int, *args, data: bytes = b"", timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> int:
    """Call `function(*args)` in the game and return its 32-bit result."""
    return call_many(process, [(function, args)], data=data, timeout_ms=timeout_ms)


def call_many(
    process: Process, calls, data: bytes = b"", timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> int:
    """Make several calls on one thread, in order. Returns the last call's 32-bit result."""
    calls = list(calls)
    if not calls:
        return 0
    if len(data) > PAGE_SIZE - DATA_OFFSET:
        raise RemoteCallError("Call data is too large.")
    with _lock:
        handle = process.handle
        try:
            page = k32.VirtualAllocEx(handle, PAGE_SIZE, k32.PAGE_READWRITE)
        except OSError as error:
            raise RemoteCallError(f"Could not allocate call memory: {error}") from error
        started = finished = False
        try:
            resolved = [
                (
                    int(function),
                    [
                        page + DATA_OFFSET + arg.offset if isinstance(arg, DataRef) else int(arg)
                        for arg in args
                    ],
                )
                for function, args in calls
            ]
            stub = build_stub(resolved)
            if not process.write(page, stub) or (
                data and not process.write(page + DATA_OFFSET, data)
            ):
                raise RemoteCallError("Could not write the call stub.")
            k32.VirtualProtectEx(handle, page, PAGE_SIZE, k32.PAGE_EXECUTE_READ)
            k32.FlushInstructionCache(handle, page, len(stub))
            thread = k32.CreateRemoteThread(handle, page)
            started = True
            try:
                finished = k32.WaitForSingleObject(thread, timeout_ms)
                if not finished:
                    raise RemoteCallError("The game did not finish the call in time.")
                return k32.GetExitCodeThread(thread)
            finally:
                k32.CloseHandle(thread)
        except OSError as error:
            raise RemoteCallError(str(error)) from error
        finally:
            if finished or not started:
                try:
                    k32.VirtualFreeEx(handle, page)
                except OSError:
                    _leaked.append(page)
            else:
                _leaked.append(page)  # never free code a thread may still be running
