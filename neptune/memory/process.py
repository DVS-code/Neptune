"""Windows process attachment and typed memory access."""
from __future__ import annotations

import contextlib
import ctypes
import struct
from ctypes import wintypes

PROCESS_ALL_ACCESS = 0x1F0FFF
PROCESS_RUNTIME_ACCESS = 0x0010043A
PAGE_EXECUTE_READWRITE = 0x40
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPTHREAD = 0x00000004
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
THREAD_SUSPEND_RESUME = 0x0002
STILL_ACTIVE = 259

_NAME_HINTS = (b'forzahorizon6', b'fh6')

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)

kernel32.OpenThread.restype = wintypes.HANDLE
kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.SuspendThread.argtypes = [wintypes.HANDLE]
kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
kernel32.VirtualProtectEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                      wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD),
                ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
                ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_char * 260)]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('th32ModuleID', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD), ('GlblcntUsage', wintypes.DWORD),
                ('ProccntUsage', wintypes.DWORD), ('modBaseAddr', ctypes.POINTER(ctypes.c_byte)),
                ('modBaseSize', wintypes.DWORD), ('hModule', wintypes.HMODULE),
                ('szModule', ctypes.c_char * 256), ('szExePath', ctypes.c_char * 260)]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ThreadID', wintypes.DWORD), ('th32OwnerProcessID', wintypes.DWORD),
                ('tpBasePri', ctypes.c_long), ('tpDeltaPri', ctypes.c_long),
                ('dwFlags', wintypes.DWORD)]


class _LUID(ctypes.Structure):
    _fields_ = [('LowPart', wintypes.DWORD), ('HighPart', ctypes.c_long)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Luid', _LUID), ('Attributes', wintypes.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [('PrivilegeCount', wintypes.DWORD),
                ('Privileges', _LUID_AND_ATTRIBUTES * 1)]


class ProcessError(Exception):
    """Attachment or access failure with a message suitable for display."""


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def enable_debug_privilege() -> bool:
    token_adjust = 0x20
    token_query = 0x8
    privilege_enabled = 0x2
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.HANDLE)]
    advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                               ctypes.POINTER(_LUID)]
    advapi32.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL,
                                               ctypes.POINTER(_TOKEN_PRIVILEGES),
                                               wintypes.DWORD, ctypes.c_void_p,
                                               ctypes.c_void_p]
    token = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                         token_adjust | token_query, ctypes.byref(token)):
            return False
        luid = _LUID()
        if not advapi32.LookupPrivilegeValueW(None, 'SeDebugPrivilege', ctypes.byref(luid)):
            return False
        privileges = _TOKEN_PRIVILEGES()
        privileges.PrivilegeCount = 1
        privileges.Privileges[0].Luid = luid
        privileges.Privileges[0].Attributes = privilege_enabled
        return bool(advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(privileges),
                                                   0, None, None))
    except Exception:
        return False
    finally:
        if token:
            kernel32.CloseHandle(token)


def game_is_running(exe_name: str = 'forzahorizon6.exe') -> bool:
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
            raise ProcessError('Forza Horizon 6 is not running.')

        enable_debug_privilege()
        handle = (kernel32.OpenProcess(PROCESS_RUNTIME_ACCESS, False, pid)
                  or kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid))
        if not handle:
            if not is_admin():
                raise ProcessError('Could not open the game. Run Neptune as administrator.')
            raise ProcessError('Could not open the game process.')

        base = _module_base(pid, exe_name, handle)
        if not base:
            kernel32.CloseHandle(handle)
            raise ProcessError('Found the game but could not read it. '
                               'Run Neptune as administrator and start the game first.')
        return cls(pid, handle, base, exe_name)

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = 0

    @property
    def alive(self) -> bool:
        if not self.handle:
            return False
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE

    def read(self, address: int, size: int) -> bytes | None:
        if not address or size <= 0:
            return None
        buffer = (ctypes.c_char * size)()
        read = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(self.handle, ctypes.c_void_p(address),
                                        buffer, size, ctypes.byref(read))
        if not ok or read.value != size:
            return None
        return bytes(buffer)

    def write(self, address: int, data: bytes) -> bool:
        if not address or not data:
            return False
        buffer = (ctypes.c_char * len(data))(*data)
        written = ctypes.c_size_t(0)
        ok = kernel32.WriteProcessMemory(self.handle, ctypes.c_void_p(address),
                                         buffer, len(data), ctypes.byref(written))
        return bool(ok) and written.value == len(data)

    def write_protected(self, address: int, data: bytes) -> bool:
        if not address or not data:
            return False
        previous = wintypes.DWORD()
        changed = kernel32.VirtualProtectEx(self.handle, ctypes.c_void_p(address), len(data),
                                            PAGE_EXECUTE_READWRITE, ctypes.byref(previous))
        try:
            return self.write(address, data)
        finally:
            if changed:
                scratch = wintypes.DWORD()
                kernel32.VirtualProtectEx(self.handle, ctypes.c_void_p(address), len(data),
                                          previous.value, ctypes.byref(scratch))

    def thread_ids(self) -> list[int]:
        out: list[int] = []
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snapshot == -1:
            return out
        try:
            entry = THREADENTRY32()
            entry.dwSize = ctypes.sizeof(THREADENTRY32)
            if not kernel32.Thread32First(snapshot, ctypes.byref(entry)):
                return out
            while True:
                if entry.th32OwnerProcessID == self.pid:
                    out.append(entry.th32ThreadID)
                if not kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                    break
        finally:
            kernel32.CloseHandle(snapshot)
        return out

    @contextlib.contextmanager
    def threads_suspended(self):
        handles = []
        try:
            for tid in self.thread_ids():
                thread = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, tid)
                if not thread:
                    continue
                if kernel32.SuspendThread(thread) == 0xFFFFFFFF:
                    kernel32.CloseHandle(thread)
                    continue
                handles.append(thread)
            yield len(handles)
        finally:
            for thread in handles:
                try:
                    kernel32.ResumeThread(thread)
                finally:
                    kernel32.CloseHandle(thread)

    def write_suspended(self, address: int, data: bytes) -> bool:
        with self.threads_suspended():
            return self.write_protected(address, data)

    def f32(self, address: int) -> float | None:
        data = self.read(address, 4)
        return struct.unpack('<f', data)[0] if data else None

    def i32(self, address: int) -> int | None:
        data = self.read(address, 4)
        return struct.unpack('<i', data)[0] if data else None

    def u32(self, address: int) -> int | None:
        data = self.read(address, 4)
        return struct.unpack('<I', data)[0] if data else None

    def pointer(self, address: int) -> int | None:
        data = self.read(address, 8)
        if not data:
            return None
        return struct.unpack('<Q', data)[0] or None

    def f32_array(self, address: int, count: int) -> list[float]:
        data = self.read(address, count * 4)
        return list(struct.unpack(f'<{count}f', data)) if data else []

    def set_f32(self, address: int, value: float) -> bool:
        return self.write(address, struct.pack('<f', float(value)))

    def set_i32(self, address: int, value: int) -> bool:
        return self.write(address, struct.pack('<i', int(value)))

    def set_f32_array(self, address: int, values) -> bool:
        payload = b''.join(struct.pack('<f', float(v)) for v in values)
        return self.write(address, payload)

    def chain(self, *offsets: int) -> int | None:
        address = self.base + offsets[0]
        for offset in offsets[1:]:
            resolved = self.pointer(address)
            if not resolved:
                return None
            address = resolved + offset
        return address


def _find_pid(exe_name: str) -> int | None:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return None
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        wanted = exe_name.lower().rsplit('.', 1)[0].encode()
        if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
            return None
        exact = None
        loose = None
        while True:
            flat = entry.szExeFile.lower().replace(b' ', b'')
            stem = flat.rsplit(b'.', 1)[0]
            if stem == wanted or flat == wanted:
                exact = entry.th32ProcessID
                break
            if loose is None and any(hint in flat for hint in _NAME_HINTS):
                loose = entry.th32ProcessID
            if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                break
        return exact or loose
    finally:
        kernel32.CloseHandle(snapshot)


def _module_base(pid: int, exe_name: str, handle: int = 0) -> int | None:
    wanted = exe_name.lower().rsplit('.', 1)[0].encode()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snapshot != -1:
        try:
            entry = MODULEENTRY32()
            entry.dwSize = ctypes.sizeof(MODULEENTRY32)
            first = None
            if kernel32.Module32First(snapshot, ctypes.byref(entry)):
                while True:
                    name = entry.szModule.lower().replace(b' ', b'')
                    address = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value
                    if first is None:
                        first = address
                    if wanted in name:
                        return address
                    if not kernel32.Module32Next(snapshot, ctypes.byref(entry)):
                        break
            if first:
                return first
        finally:
            kernel32.CloseHandle(snapshot)
    return _module_base_psapi(pid, exe_name, handle)


def _module_base_psapi(pid: int, exe_name: str, handle: int = 0) -> int | None:
    try:
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        borrowed = bool(handle)
        proc = handle or kernel32.OpenProcess(PROCESS_RUNTIME_ACCESS, False, pid) \
            or kernel32.OpenProcess(0x0410, False, pid)
        if not proc:
            return None
        try:
            modules = (ctypes.c_void_p * 1024)()
            needed = wintypes.DWORD(0)
            psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE,
                                                   ctypes.POINTER(ctypes.c_void_p),
                                                   wintypes.DWORD,
                                                   ctypes.POINTER(wintypes.DWORD),
                                                   wintypes.DWORD]
            if not psapi.EnumProcessModulesEx(proc, modules, ctypes.sizeof(modules),
                                              ctypes.byref(needed), 0x03):
                return None
            count = min(needed.value // ctypes.sizeof(ctypes.c_void_p), 1024)
            wanted = exe_name.lower().rsplit('.', 1)[0]
            name_buffer = ctypes.create_unicode_buffer(260)
            psapi.GetModuleBaseNameW.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                                 wintypes.LPWSTR, wintypes.DWORD]
            for index in range(count):
                if psapi.GetModuleBaseNameW(proc, modules[index], name_buffer, 260):
                    if wanted in name_buffer.value.lower().replace(' ', ''):
                        return modules[index]
            return modules[0] if count else None
        finally:
            if not borrowed:
                kernel32.CloseHandle(proc)
    except Exception:
        return None
