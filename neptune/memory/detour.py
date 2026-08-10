"""Short-lived code hooks used to capture live object pointers."""
from __future__ import annotations

import ctypes
import struct
import threading
from ctypes import wintypes

from neptune.memory.process import Process

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                    wintypes.DWORD, wintypes.DWORD]
kernel32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                   wintypes.DWORD]
kernel32.VirtualProtectEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                      wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]

MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_EXECUTE_READWRITE = 0x40

JUMP_SIZE = 5
NOP = 0x90
REL32_RANGE = 0x7FFF0000


class DetourError(Exception):
    """A hook could not be installed or removed."""


def allocate_near(process: Process, target: int, size: int) -> int:
    step = 0x10000
    for delta in range(step, REL32_RANGE, step):
        for candidate in (target + delta, target - delta):
            if candidate <= 0x10000:
                continue
            address = kernel32.VirtualAllocEx(process.handle,
                                              ctypes.c_void_p(candidate & ~0xFFF), size,
                                              MEM_COMMIT | MEM_RESERVE,
                                              PAGE_EXECUTE_READWRITE)
            if address:
                return int(address)
    raise DetourError('Could not reserve memory near the target.')


class Detour:
    """One installed hook."""

    def __init__(self, process: Process, target: int, original: bytes,
                 stub: int, stub_size: int):
        self.process = process
        self.target = target
        self.original = original
        self.stub = stub
        self.stub_size = stub_size
        self.installed = True

    def read_slot(self, offset: int, size: int) -> bytes | None:
        if not self.installed or offset + size > self.stub_size:
            return None
        return self.process.read(self.stub + offset, size)

    def write_slot(self, offset: int, data: bytes) -> bool:
        if not self.installed or offset + len(data) > self.stub_size:
            return False
        return self.process.write(self.stub + offset, data)

    def uninstall(self) -> None:
        if not self.installed:
            return
        self.process.write_protected(self.target, self.original)
        kernel32.VirtualFreeEx(self.process.handle, ctypes.c_void_p(self.stub), 0, MEM_RELEASE)
        self.installed = False


class DetourManager:
    """Installs hooks and guarantees they are removed."""

    def __init__(self, process: Process):
        self.process = process
        self._detours: dict[str, Detour] = {}
        self._lock = threading.RLock()

    def install(self, key: str, target: int, code: bytes, hook_size: int,
                expected_original: bytes | None = None, stub_size: int = 0) -> Detour:
        with self._lock:
            existing = self._detours.get(key)
            if existing is not None:
                return existing

            if hook_size < JUMP_SIZE:
                raise DetourError('Hook site is too small.')

            original = self.process.read(target, hook_size)
            if not original or len(original) != hook_size:
                raise DetourError('Could not read the hook site.')
            if expected_original and not original.startswith(expected_original):
                raise DetourError('This game build is not supported.')

            total = max(len(code) + JUMP_SIZE, int(stub_size))
            stub = allocate_near(self.process, target, max(total, 0x40))

            body = bytearray(code)
            return_to = target + hook_size
            back = return_to - (stub + len(code) + JUMP_SIZE)
            if not -0x80000000 <= back <= 0x7FFFFFFF:
                kernel32.VirtualFreeEx(self.process.handle, ctypes.c_void_p(stub), 0, MEM_RELEASE)
                raise DetourError('Could not reach the return address.')
            body += b'\xE9' + struct.pack('<i', back)
            if len(body) < total:
                body += b'\x00' * (total - len(body))

            if not self.process.write(stub, bytes(body)):
                kernel32.VirtualFreeEx(self.process.handle, ctypes.c_void_p(stub), 0, MEM_RELEASE)
                raise DetourError('Could not write the hook.')

            forward = stub - (target + JUMP_SIZE)
            if not -0x80000000 <= forward <= 0x7FFFFFFF:
                kernel32.VirtualFreeEx(self.process.handle, ctypes.c_void_p(stub), 0, MEM_RELEASE)
                raise DetourError('Could not reach the hook.')
            patch = b'\xE9' + struct.pack('<i', forward) + bytes([NOP]) * (hook_size - JUMP_SIZE)

            if not self.process.write_protected(target, patch):
                kernel32.VirtualFreeEx(self.process.handle, ctypes.c_void_p(stub), 0, MEM_RELEASE)
                raise DetourError('Could not install the hook.')

            detour = Detour(self.process, target, original, stub, total)
            self._detours[key] = detour
            return detour

    def uninstall(self, key: str) -> None:
        with self._lock:
            detour = self._detours.pop(key, None)
        if detour is not None:
            detour.uninstall()

    def uninstall_all(self) -> None:
        with self._lock:
            items = list(self._detours.values())
            self._detours.clear()
        for detour in reversed(items):
            try:
                detour.uninstall()
            except Exception:
                pass

    def __len__(self) -> int:
        return len(self._detours)
