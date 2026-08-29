"""Windows process attachment and typed memory access."""

from __future__ import annotations

import contextlib
import ctypes
import struct
from ctypes import wintypes

import neptune.memory.k32 as k32

_NAME_HINTS = (b"forzahorizon6", b"fh6")

kernel32 = k32

PROCESS_ALL_ACCESS = 0x1F0FFF
PROCESS_RUNTIME_ACCESS = 0x0010043A
PAGE_EXECUTE_READWRITE = 0x40
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPTHREAD = 0x00000004
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
THREAD_SUSPEND_RESUME = 0x0002
STILL_ACTIVE = 259

MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
WRITABLE_PROTECTIONS = (0x04, 0x08, 0x40, 0x80)
MODULE_SPACE = 0x7FF000000000


class ProcessError(Exception):
    """Attachment or access failure with a message suitable for display."""


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def game_is_running(exe_name: str = "forzahorizon6.exe") -> bool:
    return _find_pid(exe_name) is not None


class Process:
    """An attached game process."""

    def __init__(self, pid: int, handle: int, base: int, name: str):
        self.pid = pid
        self.handle = handle
        self.base = base
        self.name = name

    @classmethod
    def attach(cls, exe_name: str) -> Process:
        pid = _find_pid(exe_name)
        if not pid:
            raise ProcessError("Forza Horizon 6 is not running.")





        try:
            kernel32.enable_debug_privileges()
        except OSError:
            pass
        handle = _open_process(PROCESS_RUNTIME_ACCESS, pid) or _open_process(
            PROCESS_ALL_ACCESS, pid
        )
        if not handle:
            if not is_admin():
                raise ProcessError(
                    "Could not open the game. Run Neptune as administrator."
                )
            raise ProcessError("Could not open the game process.")

        base = _module_base(pid, exe_name, handle)
        if not base:
            _close(handle)
            raise ProcessError(
                "Found the game but could not read it. "
                "Run Neptune as administrator and start the game first."
            )
        return cls(pid, handle, base, exe_name)

    def close(self) -> None:



        if self.handle:
            _close(self.handle)
            self.handle = 0

    @property
    def alive(self) -> bool:
        if not self.handle:
            return False
        try:
            code = kernel32.GetExitCodeProcess(self.handle)
        except OSError:
            return False
        return code == STILL_ACTIVE

    @property
    def executable_path(self) -> str | None:
        if not self.handle:
            return None
        try:
            return k32.QueryFullProcessImageNameW(ctypes.c_void_p(self.handle))
        except OSError:
            return None

    def read(self, address: int, size: int) -> bytes | None:
        if not address or size <= 0:
            return None
        buffer = (ctypes.c_char * size)()
        try:
            read = kernel32.ReadProcessMemory(
                self.handle, ctypes.c_void_p(address), buffer, size
            )
        except OSError:
            return None
        if read != size:
            return None
        return bytes(buffer)

    def write(self, address: int, data: bytes) -> bool:
        if not address or not data:
            return False
        buffer = (ctypes.c_char * len(data))(*data)
        try:
            written = kernel32.WriteProcessMemory(
                self.handle, ctypes.c_void_p(address), buffer, len(data)
            )
        except OSError:
            return False
        return written == len(data)

    def write_protected(self, address: int, data: bytes) -> bool:
        if not address or not data:
            return False
        previous = None
        try:
            previous = kernel32.VirtualProtectEx(
                self.handle, ctypes.c_void_p(address), len(data), PAGE_EXECUTE_READWRITE
            )
        except OSError:
            pass
        try:
            return self.write(address, data)
        finally:
            if previous is not None:
                try:
                    kernel32.VirtualProtectEx(
                        self.handle, ctypes.c_void_p(address), len(data), previous
                    )
                except OSError:
                    pass

    def writable_regions(self, limit: int = 0x8000000) -> list[tuple[int, int]]:
        """Committed, writable heap regions as (base, size).

        Module and system space is skipped: nothing the tool searches for lives there,
        and walking it makes a scan several times slower for no result.
        """
        regions: list[tuple[int, int]] = []
        address = 0x10000
        while address < MODULE_SPACE:
            try:
                info = kernel32.VirtualQueryEx(self.handle, address)
            except OSError:
                address += 0x1000
                continue
            base = info.BaseAddress or address
            size = info.RegionSize
            if (
                info.State == MEM_COMMIT
                and not info.Protect & PAGE_GUARD
                and info.Protect in WRITABLE_PROTECTIONS
            ):
                regions.append((base, min(size, limit)))
            address = base + size
        return regions

    def thread_ids(self) -> list[int]:
        out: list[int] = []
        try:
            snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        except OSError:
            return out
        if snapshot == -1:
            return out
        try:
            entry = kernel32.THREADENTRY32()
            entry.dwSize = ctypes.sizeof(kernel32.THREADENTRY32)
            if not kernel32.Thread32First(snapshot, entry):
                return out
            while True:
                if entry.th32OwnerProcessID == self.pid:
                    out.append(entry.th32ThreadID)
                if not kernel32.Thread32Next(snapshot, entry):
                    break
        finally:
            _close(snapshot)
        return out

    @contextlib.contextmanager
    def threads_suspended(self):
        handles = []
        try:
            for tid in self.thread_ids():
                try:
                    thread = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, tid)
                except OSError:
                    continue
                if not thread:
                    continue
                try:
                    kernel32.SuspendThread(thread)
                except OSError:
                    _close(thread)
                    continue
                handles.append(thread)
            yield len(handles)
        finally:



            for thread in handles:
                try:
                    kernel32.ResumeThread(thread)
                except OSError:
                    pass
                finally:
                    _close(thread)

    def write_suspended(self, address: int, data: bytes) -> bool:
        with self.threads_suspended():
            return self.write_protected(address, data)

    def f32(self, address: int) -> float | None:
        data = self.read(address, 4)
        return struct.unpack("<f", data)[0] if data else None

    def i32(self, address: int) -> int | None:
        data = self.read(address, 4)
        return struct.unpack("<i", data)[0] if data else None

    def u32(self, address: int) -> int | None:
        data = self.read(address, 4)
        return struct.unpack("<I", data)[0] if data else None

    def pointer(self, address: int) -> int | None:
        data = self.read(address, 8)
        if not data:
            return None
        return struct.unpack("<Q", data)[0] or None

    def f32_array(self, address: int, count: int) -> list[float]:
        data = self.read(address, count * 4)
        return list(struct.unpack(f"<{count}f", data)) if data else []

    def set_f32(self, address: int, value: float) -> bool:
        return self.write(address, struct.pack("<f", float(value)))

    def set_i32(self, address: int, value: int) -> bool:
        return self.write(address, struct.pack("<i", int(value)))

    def set_f32_array(self, address: int, values) -> bool:
        payload = b"".join(struct.pack("<f", float(v)) for v in values)
        return self.write(address, payload)

    def chain(self, *offsets: int) -> int | None:
        address = self.base + offsets[0]
        for offset in offsets[1:]:
            resolved = self.pointer(address)
            if not resolved:
                return None
            address = resolved + offset
        return address


def _close(handle) -> None:
    """Close a handle, ignoring failure.

    The ntdll wrappers raise where the old kernel32 calls returned a status code, and
    every close here sits in cleanup — a `finally`, or the error path of a failed
    attach. An exception thrown there would mask the real failure or abandon the rest
    of the cleanup, so a close that cannot succeed is simply let go.
    """
    try:
        kernel32.CloseHandle(handle)
    except OSError:
        pass


def _open_process(access: int, pid: int) -> int | None:
    try:
        handle = kernel32.OpenProcess(access, False, pid)
    except OSError:
        return None
    return handle.value if handle else None


def _find_pid(exe_name: str) -> int | None:
    try:
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    except OSError:
        return None
    if snapshot == -1:
        return None
    try:
        entry = kernel32.PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(kernel32.PROCESSENTRY32)
        wanted = exe_name.lower().rsplit(".", 1)[0].encode()
        if not kernel32.Process32First(snapshot, entry):
            return None
        exact = None
        loose = None
        while True:
            flat = entry.szExeFile.lower().replace(b" ", b"")
            stem = flat.rsplit(b".", 1)[0]
            if stem == wanted or flat == wanted:
                exact = entry.th32ProcessID
                break
            if loose is None and any(hint in flat for hint in _NAME_HINTS):
                loose = entry.th32ProcessID
            if not kernel32.Process32Next(snapshot, entry):
                break
        return exact or loose
    finally:
        _close(snapshot)


def _module_base(pid: int, exe_name: str, handle: int = 0) -> int | None:
    wanted = exe_name.lower().rsplit(".", 1)[0].encode()
    try:
        snapshot = kernel32.CreateToolhelp32Snapshot(
            TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
        )
    except OSError:
        snapshot = -1
    if snapshot != -1:
        try:
            entry = kernel32.MODULEENTRY32()
            entry.dwSize = ctypes.sizeof(kernel32.MODULEENTRY32)
            first = None
            if kernel32.Module32First(snapshot, entry):
                while True:
                    name = entry.szModule.lower().replace(b" ", b"")
                    address = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value
                    if first is None:
                        first = address
                    if wanted in name:
                        return address
                    if not kernel32.Module32Next(snapshot, entry):
                        break
            if first:
                return first
        finally:
            _close(snapshot)

    return _module_base_psapi(pid, exe_name, handle)


def _module_base_psapi(pid: int, exe_name: str, handle: int = 0) -> int | None:
    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        borrowed = bool(handle)
        proc = (
            handle
            or _open_process(PROCESS_RUNTIME_ACCESS, pid)
            or _open_process(0x0410, pid)
        )
        if not proc:
            return None
        try:
            modules = (ctypes.c_void_p * 1024)()
            needed = wintypes.DWORD(0)
            psapi.EnumProcessModulesEx.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(ctypes.c_void_p),
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                wintypes.DWORD,
            ]
            if not psapi.EnumProcessModulesEx(
                proc, modules, ctypes.sizeof(modules), ctypes.byref(needed), 0x03
            ):
                return None
            count = min(needed.value // ctypes.sizeof(ctypes.c_void_p), 1024)
            wanted = exe_name.lower().rsplit(".", 1)[0]
            name_buffer = ctypes.create_unicode_buffer(260)
            psapi.GetModuleBaseNameW.argtypes = [
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.LPWSTR,
                wintypes.DWORD,
            ]
            for index in range(count):
                if psapi.GetModuleBaseNameW(proc, modules[index], name_buffer, 260):
                    if wanted in name_buffer.value.lower().replace(" ", ""):
                        return modules[index]
            return modules[0] if count else None
        finally:
            if not borrowed:
                _close(proc)
    except Exception:
        return None
