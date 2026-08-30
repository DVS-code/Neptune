"""Recreation of some kernel32 functions using ntdll functions."""

import ctypes
import os
from ctypes import wintypes

ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

NTSTATUS = wintypes.LONG


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


PCUNICODE_STRING = ctypes.POINTER(_UNICODE_STRING)


class OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", PCUNICODE_STRING),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", wintypes.LPVOID),
        ("SecurityQualityOfService", wintypes.LPVOID),
    ]


class CLIENT_ID(ctypes.Structure):
    _fields_ = [
        ("UniqueProcess", wintypes.HANDLE),
        ("UniqueThread", wintypes.HANDLE),
    ]


PCOBJECT_ATTRIBUTES = ctypes.POINTER(OBJECT_ATTRIBUTES)
PCLIENT_ID = ctypes.POINTER(CLIENT_ID)


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("ExitStatus", NTSTATUS),
        ("PebBaseAddress", wintypes.LPVOID),
        ("AffinityMask", ctypes.c_size_t),
        ("BasePriority", wintypes.LONG),
        ("UniqueProcessId", ctypes.c_size_t),
        ("InheritedFromUniqueProcessId", ctypes.c_size_t),
    ]


ProcessBasicInformation = 0


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", wintypes.LPVOID),
        ("AllocationBase", wintypes.LPVOID),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


MemoryBasicInformation = 0


class _UNICODE_STRING_REMOTE(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPVOID),
    ]


class LIST_ENTRY(ctypes.Structure):
    _fields_ = [
        ("Flink", wintypes.LPVOID),
        ("Blink", wintypes.LPVOID),
    ]


class _PEB(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_byte * 2),
        ("BeingDebugged", ctypes.c_byte),
        ("Reserved2", ctypes.c_byte * 1),
        ("Reserved3", wintypes.LPVOID * 2),
        ("Ldr", wintypes.LPVOID),
    ]


class _PEB_LDR_DATA(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("Initialized", ctypes.c_byte),
        ("SsHandle", wintypes.LPVOID),
        ("InLoadOrderModuleList", LIST_ENTRY),
        ("InMemoryOrderModuleList", LIST_ENTRY),
        ("InInitializationOrderModuleList", LIST_ENTRY),
    ]


class _LDR_DATA_TABLE_ENTRY(ctypes.Structure):
    _fields_ = [
        ("InLoadOrderLinks", LIST_ENTRY),
        ("InMemoryOrderLinks", LIST_ENTRY),
        ("InInitializationOrderLinks", LIST_ENTRY),
        ("DllBase", wintypes.LPVOID),
        ("EntryPoint", wintypes.LPVOID),
        ("SizeOfImage", wintypes.ULONG),
        ("FullDllName", _UNICODE_STRING_REMOTE),
        ("BaseDllName", _UNICODE_STRING_REMOTE),
    ]


class _SYSTEM_THREAD_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("KernelTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("CreateTime", ctypes.c_longlong),
        ("WaitTime", wintypes.ULONG),
        ("StartAddress", wintypes.LPVOID),
        ("ClientId", CLIENT_ID),
        ("Priority", wintypes.LONG),
        ("BasePriority", wintypes.LONG),
        ("ContextSwitches", wintypes.ULONG),
        ("ThreadState", wintypes.ULONG),
        ("WaitReason", wintypes.ULONG),
    ]


class _SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", wintypes.ULONG),
        ("NumberOfThreads", wintypes.ULONG),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", wintypes.ULONG),
        ("NumberOfThreadsHighWatermark", wintypes.ULONG),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UNICODE_STRING),
        ("BasePriority", wintypes.LONG),
        ("UniqueProcessId", wintypes.LPVOID),
        ("InheritedFromUniqueProcessId", wintypes.LPVOID),
        ("HandleCount", wintypes.ULONG),
        ("SessionId", wintypes.ULONG),
        ("UniqueProcessKey", ctypes.c_size_t),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", wintypes.ULONG),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivatePageCount", ctypes.c_size_t),
        ("ReadOperationCount", ctypes.c_longlong),
        ("WriteOperationCount", ctypes.c_longlong),
        ("OtherOperationCount", ctypes.c_longlong),
        ("ReadTransferCount", ctypes.c_longlong),
        ("WriteTransferCount", ctypes.c_longlong),
        ("OtherTransferCount", ctypes.c_longlong),
        ("Threads", _SYSTEM_THREAD_INFORMATION * 0),
    ]


SystemProcessInformation = 5
STATUS_INFO_LENGTH_MISMATCH = -1073741820

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

TH32CS_SNAPHEAPLIST = 0x00000001
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPTHREAD = 0x00000004
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
TH32CS_INHERIT = 0x80000000
TH32CS_SNAPALL = TH32CS_SNAPHEAPLIST | TH32CS_SNAPPROCESS | TH32CS_SNAPTHREAD | TH32CS_SNAPMODULE

MAX_PATH = 260
MAX_MODULE_NAME32 = 255

SE_DEBUG_PRIVILEGE = 20
SE_PRIVILEGE_ENABLED = 0x00000002

TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008

STATUS_NOT_ALL_ASSIGNED = 0x00000106
ERROR_NOT_ALL_ASSIGNED = 1300
ERROR_INVALID_HANDLE = 6

PROCESS_NAME_NATIVE = 0x00000001

ProcessImageFileName = 27
ProcessImageFileNameWin32 = 43

NtCurrentProcess = wintypes.HANDLE(-1)


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * MAX_PATH),
    ]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", wintypes.LPVOID),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", ctypes.c_char * (MAX_MODULE_NAME32 + 1)),
        ("szExePath", ctypes.c_char * MAX_PATH),
    ]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


class LUID(ctypes.Structure):
    _fields_ = [
        ("LowPart", wintypes.DWORD),
        ("HighPart", wintypes.LONG),
    ]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Luid", LUID),
        ("Attributes", wintypes.DWORD),
    ]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", LUID_AND_ATTRIBUTES * 1),
    ]


ntdll.NtReadVirtualMemory.restype = NTSTATUS
ntdll.NtReadVirtualMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPVOID,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]

ntdll.NtWriteVirtualMemory.restype = NTSTATUS
ntdll.NtWriteVirtualMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCVOID,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]

ntdll.NtOpenProcess.restype = NTSTATUS
ntdll.NtOpenProcess.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    PCOBJECT_ATTRIBUTES,
    PCLIENT_ID,
]

ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
ntdll.RtlNtStatusToDosError.argtypes = [NTSTATUS]


ntdll.NtProtectVirtualMemory.restype = NTSTATUS
ntdll.NtProtectVirtualMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.LPVOID),
    ctypes.POINTER(ctypes.c_size_t),
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]

ntdll.NtQueryVirtualMemory.restype = NTSTATUS
ntdll.NtQueryVirtualMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]


ntdll.NtQuerySystemInformation.restype = NTSTATUS
ntdll.NtQuerySystemInformation.argtypes = [
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]

ntdll.NtQueryInformationProcess.restype = NTSTATUS
ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]

ntdll.NtClose.restype = NTSTATUS
ntdll.NtClose.argtypes = [wintypes.HANDLE]

ntdll.NtOpenThread.restype = NTSTATUS
ntdll.NtOpenThread.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    PCOBJECT_ATTRIBUTES,
    PCLIENT_ID,
]

ntdll.NtSuspendThread.restype = NTSTATUS
ntdll.NtSuspendThread.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]

ntdll.NtResumeThread.restype = NTSTATUS
ntdll.NtResumeThread.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]

ntdll.NtOpenProcessToken.restype = NTSTATUS
ntdll.NtOpenProcessToken.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
]

ntdll.NtAdjustPrivilegesToken.restype = NTSTATUS
ntdll.NtAdjustPrivilegesToken.argtypes = [
    wintypes.HANDLE,
    wintypes.BOOLEAN,
    ctypes.POINTER(TOKEN_PRIVILEGES),
    wintypes.ULONG,
    ctypes.c_void_p,
    ctypes.c_void_p,
]

ntdll.RtlAdjustPrivilege.restype = NTSTATUS
ntdll.RtlAdjustPrivilege.argtypes = [
    wintypes.ULONG,
    wintypes.BOOLEAN,
    wintypes.BOOLEAN,
    ctypes.POINTER(wintypes.BOOLEAN),
]


def _nt_check(status):

    if status < 0:
        raise ctypes.WinError(ntdll.RtlNtStatusToDosError(status))


def ReadProcessMemory(hProcess, lpBaseAddress, lpBuffer, nSize):
    bytes_read = wintypes.ULONG(0)
    status = ntdll.NtReadVirtualMemory(
        hProcess, lpBaseAddress, lpBuffer, nSize, ctypes.byref(bytes_read)
    )
    _nt_check(status)
    return bytes_read.value


def WriteProcessMemory(hProcess, lpBaseAddress, lpBuffer, nSize):
    bytes_written = wintypes.ULONG(0)
    status = ntdll.NtWriteVirtualMemory(
        hProcess, lpBaseAddress, lpBuffer, nSize, ctypes.byref(bytes_written)
    )
    _nt_check(status)
    return bytes_written.value


def OpenProcess(dwDesiredAccess, bInheritHandle, dwProcessId):
    hProcess = wintypes.HANDLE()
    object_attributes = OBJECT_ATTRIBUTES()
    object_attributes.Length = ctypes.sizeof(OBJECT_ATTRIBUTES)
    if bInheritHandle:
        object_attributes.Attributes = 0x2
    client_id = CLIENT_ID()
    client_id.UniqueProcess = wintypes.HANDLE(dwProcessId)
    status = ntdll.NtOpenProcess(
        ctypes.byref(hProcess),
        dwDesiredAccess,
        ctypes.byref(object_attributes),
        ctypes.byref(client_id),
    )
    _nt_check(status)
    return hProcess


def _read_struct(hProcess, address, struct_type):
    buf = ctypes.create_string_buffer(ctypes.sizeof(struct_type))
    ReadProcessMemory(hProcess, address, buf, ctypes.sizeof(struct_type))
    return struct_type.from_buffer_copy(buf)


def _read_remote_wstring(hProcess, unicode_string):
    if not unicode_string.Buffer or not unicode_string.Length:
        return ""
    buf = ctypes.create_string_buffer(unicode_string.Length)
    ReadProcessMemory(hProcess, unicode_string.Buffer, buf, unicode_string.Length)
    return buf.raw.decode("utf-16-le", errors="replace")


def _fetch_system_process_information():
    size = 1 << 16
    buf = ctypes.create_string_buffer(size)
    while True:
        return_length = wintypes.ULONG(0)
        status = ntdll.NtQuerySystemInformation(
            SystemProcessInformation, buf, size, ctypes.byref(return_length)
        )
        if status == STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, return_length.value)
            buf = ctypes.create_string_buffer(size)
            continue
        _nt_check(status)
        return buf


def _query_system_processes():
    buf = _fetch_system_process_information()
    processes = []
    offset = 0
    base_address = ctypes.addressof(buf)
    while True:
        entry = _SYSTEM_PROCESS_INFORMATION.from_address(base_address + offset)
        name = ""
        if entry.ImageName.Buffer:
            name = ctypes.wstring_at(entry.ImageName.Buffer, entry.ImageName.Length // 2)
        processes.append(
            {
                "pid": entry.UniqueProcessId or 0,
                "ppid": entry.InheritedFromUniqueProcessId or 0,
                "threads": entry.NumberOfThreads,
                "name": name,
            }
        )
        if entry.NextEntryOffset == 0:
            break
        offset += entry.NextEntryOffset
    return processes


def _query_system_threads():
    buf = _fetch_system_process_information()
    threads = []
    offset = 0
    base_address = ctypes.addressof(buf)
    while True:
        entry = _SYSTEM_PROCESS_INFORMATION.from_address(base_address + offset)
        threads_address = base_address + offset + _SYSTEM_PROCESS_INFORMATION.Threads.offset
        for i in range(entry.NumberOfThreads):
            thread = _SYSTEM_THREAD_INFORMATION.from_address(
                threads_address + i * ctypes.sizeof(_SYSTEM_THREAD_INFORMATION)
            )
            threads.append(
                {
                    "tid": thread.ClientId.UniqueThread or 0,
                    "pid": thread.ClientId.UniqueProcess or 0,
                    "base_priority": thread.BasePriority,
                }
            )
        if entry.NextEntryOffset == 0:
            break
        offset += entry.NextEntryOffset
    return threads


def _query_process_modules(pid):
    hProcess = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        return_length = wintypes.ULONG(0)
        status = ntdll.NtQueryInformationProcess(
            hProcess,
            ProcessBasicInformation,
            ctypes.byref(pbi),
            ctypes.sizeof(pbi),
            ctypes.byref(return_length),
        )
        _nt_check(status)

        peb = _read_struct(hProcess, pbi.PebBaseAddress, _PEB)
        ldr = _read_struct(hProcess, peb.Ldr, _PEB_LDR_DATA)
        list_head = peb.Ldr + _PEB_LDR_DATA.InMemoryOrderModuleList.offset
        entry_link_offset = _LDR_DATA_TABLE_ENTRY.InMemoryOrderLinks.offset

        modules = []
        current = ldr.InMemoryOrderModuleList.Flink
        for _ in range(4096):
            if not current or current == list_head:
                break
            entry = _read_struct(hProcess, current - entry_link_offset, _LDR_DATA_TABLE_ENTRY)
            modules.append(
                {
                    "pid": pid,
                    "base": entry.DllBase or 0,
                    "size": entry.SizeOfImage,
                    "name": _read_remote_wstring(hProcess, entry.BaseDllName),
                    "path": _read_remote_wstring(hProcess, entry.FullDllName),
                }
            )
            current = entry.InMemoryOrderLinks.Flink
        return modules
    finally:
        CloseHandle(hProcess)


_snapshots = {}
_next_snapshot_handle = 0x7FFF0000


def CreateToolhelp32Snapshot(dwFlags, th32ProcessID):
    global _next_snapshot_handle

    state = {
        "processes": None,
        "proc_index": 0,
        "modules": None,
        "mod_index": 0,
        "threads": None,
        "thread_index": 0,
    }
    if dwFlags & TH32CS_SNAPPROCESS:
        state["processes"] = _query_system_processes()
    if dwFlags & (TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32):
        state["modules"] = _query_process_modules(th32ProcessID or os.getpid())
    if dwFlags & TH32CS_SNAPTHREAD:
        state["threads"] = _query_system_threads()

    handle = _next_snapshot_handle
    _next_snapshot_handle += 4
    _snapshots[handle] = state
    return handle


def _get_snapshot_state(hSnapshot):
    try:
        return _snapshots[hSnapshot]
    except KeyError:
        raise ctypes.WinError(ERROR_INVALID_HANDLE) from None


def _snapshot_advance(entries, index_key, state, entry_out, fill):
    entries = entries or []
    index = state[index_key]
    if index >= len(entries):
        return False
    if entry_out.dwSize < ctypes.sizeof(type(entry_out)):
        raise ctypes.WinError(122)
    fill(entry_out, entries[index])
    state[index_key] += 1
    return True


def _fill_process_entry(pe32, proc):
    pe32.cntUsage = 0
    pe32.th32ProcessID = proc["pid"]
    pe32.th32DefaultHeapID = 0
    pe32.th32ModuleID = 0
    pe32.cntThreads = proc["threads"]
    pe32.th32ParentProcessID = proc["ppid"]
    pe32.pcPriClassBase = 0
    pe32.dwFlags = 0
    pe32.szExeFile = proc["name"].encode("mbcs", "replace")[: MAX_PATH - 1]


def Process32First(hSnapshot, lppe):
    state = _get_snapshot_state(hSnapshot)
    state["proc_index"] = 0
    return _snapshot_advance(state["processes"], "proc_index", state, lppe, _fill_process_entry)


def Process32Next(hSnapshot, lppe):
    state = _get_snapshot_state(hSnapshot)
    return _snapshot_advance(state["processes"], "proc_index", state, lppe, _fill_process_entry)


def _fill_module_entry(me32, mod):
    me32.th32ModuleID = 1
    me32.th32ProcessID = mod["pid"]
    me32.GlblcntUsage = 0
    me32.ProccntUsage = 0
    me32.modBaseAddr = mod["base"]
    me32.modBaseSize = mod["size"]
    me32.hModule = mod["base"]
    me32.szModule = mod["name"].encode("mbcs", "replace")[:MAX_MODULE_NAME32]
    me32.szExePath = mod["path"].encode("mbcs", "replace")[: MAX_PATH - 1]


def Module32First(hSnapshot, lpme):
    state = _get_snapshot_state(hSnapshot)
    state["mod_index"] = 0
    return _snapshot_advance(state["modules"], "mod_index", state, lpme, _fill_module_entry)


def Module32Next(hSnapshot, lpme):
    state = _get_snapshot_state(hSnapshot)
    return _snapshot_advance(state["modules"], "mod_index", state, lpme, _fill_module_entry)


def _fill_thread_entry(te32, thread):
    te32.cntUsage = 0
    te32.th32ThreadID = thread["tid"]
    te32.th32OwnerProcessID = thread["pid"]
    te32.tpBasePri = thread["base_priority"]
    te32.tpDeltaPri = 0
    te32.dwFlags = 0


def Thread32First(hSnapshot, lpte):
    state = _get_snapshot_state(hSnapshot)
    state["thread_index"] = 0
    return _snapshot_advance(state["threads"], "thread_index", state, lpte, _fill_thread_entry)


def Thread32Next(hSnapshot, lpte):
    state = _get_snapshot_state(hSnapshot)
    return _snapshot_advance(state["threads"], "thread_index", state, lpte, _fill_thread_entry)


def CloseHandle(hObject):
    key = hObject.value if isinstance(hObject, ctypes.c_void_p) else hObject
    if key in _snapshots:
        del _snapshots[key]
        return True
    status = ntdll.NtClose(hObject)
    _nt_check(status)
    return True


def GetExitCodeProcess(hProcess):
    pbi = PROCESS_BASIC_INFORMATION()
    return_length = wintypes.ULONG(0)
    status = ntdll.NtQueryInformationProcess(
        hProcess,
        ProcessBasicInformation,
        ctypes.byref(pbi),
        ctypes.sizeof(pbi),
        ctypes.byref(return_length),
    )
    _nt_check(status)
    return pbi.ExitStatus


def VirtualProtectEx(hProcess, lpAddress, dwSize, flNewProtect):
    base, size = ctypes.c_void_p(lpAddress), ctypes.c_size_t(dwSize)
    old = wintypes.ULONG(0)
    status = ntdll.NtProtectVirtualMemory(
        hProcess,
        ctypes.byref(base),
        ctypes.byref(size),
        flNewProtect,
        ctypes.byref(old),
    )
    _nt_check(status)
    return old.value


def VirtualQueryEx(hProcess, lpAddress):
    mbi = _MEMORY_BASIC_INFORMATION()
    return_length = ctypes.c_size_t(0)
    status = ntdll.NtQueryVirtualMemory(
        hProcess,
        ctypes.c_void_p(lpAddress),
        MemoryBasicInformation,
        ctypes.byref(mbi),
        ctypes.sizeof(mbi),
        ctypes.byref(return_length),
    )
    _nt_check(status)
    return mbi


def OpenThread(dwDesiredAccess, bInheritHandle, dwThreadId):
    hThread = wintypes.HANDLE()
    object_attributes = OBJECT_ATTRIBUTES()
    object_attributes.Length = ctypes.sizeof(OBJECT_ATTRIBUTES)
    if bInheritHandle:
        object_attributes.Attributes = 0x2
    client_id = CLIENT_ID()
    client_id.UniqueThread = wintypes.HANDLE(dwThreadId)
    status = ntdll.NtOpenThread(
        ctypes.byref(hThread),
        dwDesiredAccess,
        ctypes.byref(object_attributes),
        ctypes.byref(client_id),
    )
    _nt_check(status)
    return hThread


def SuspendThread(hThread):
    previous_count = wintypes.ULONG(0)
    status = ntdll.NtSuspendThread(hThread, ctypes.byref(previous_count))
    _nt_check(status)
    return previous_count.value


def ResumeThread(hThread):
    previous_count = wintypes.ULONG(0)
    status = ntdll.NtResumeThread(hThread, ctypes.byref(previous_count))
    _nt_check(status)
    return previous_count.value


def enable_debug_privileges_ex():

    hToken = wintypes.HANDLE()
    status = ntdll.NtOpenProcessToken(
        NtCurrentProcess,
        TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
        ctypes.byref(hToken),
    )
    _nt_check(status)
    try:
        new_state = TOKEN_PRIVILEGES()
        new_state.PrivilegeCount = 1
        new_state.Privileges[0].Luid.LowPart = SE_DEBUG_PRIVILEGE
        new_state.Privileges[0].Luid.HighPart = 0
        new_state.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        status = ntdll.NtAdjustPrivilegesToken(
            hToken, False, ctypes.byref(new_state), 0, None, None
        )
        if status == STATUS_NOT_ALL_ASSIGNED:
            raise ctypes.WinError(ERROR_NOT_ALL_ASSIGNED)
        _nt_check(status)
    finally:
        CloseHandle(hToken)


def enable_debug_privileges():
    """
    Basically a short version of enable_debug_privileges_ex, uses RtlAdjustPrivilege that does
    NtOpenProcessToken + NtAdjustPrivilegesToken + NtClose in one call,
    in our case we can use it because we are enabling the privilege for the current process,
    """
    previously_enabled = wintypes.BOOLEAN(False)
    status = ntdll.RtlAdjustPrivilege(
        SE_DEBUG_PRIVILEGE, True, False, ctypes.byref(previously_enabled)
    )
    _nt_check(status)
    return bool(previously_enabled.value)


def QueryFullProcessImageNameW(hProcess, dwFlags=0):
    if dwFlags & ~PROCESS_NAME_NATIVE:
        raise ctypes.WinError(87)  # ERROR_INVALID_PARAMETER

    info_class = (
        ProcessImageFileName if (dwFlags & PROCESS_NAME_NATIVE) else ProcessImageFileNameWin32
    )
    size = 4096
    buf = ctypes.create_string_buffer(size)
    ret_len = wintypes.ULONG(0)
    status = ntdll.NtQueryInformationProcess(hProcess, info_class, buf, size, ctypes.byref(ret_len))
    if status == STATUS_INFO_LENGTH_MISMATCH:
        size = ret_len.value
        buf = ctypes.create_string_buffer(size)
        status = ntdll.NtQueryInformationProcess(
            hProcess, info_class, buf, size, ctypes.byref(ret_len)
        )
    _nt_check(status)
    us = _UNICODE_STRING.from_buffer_copy(buf)
    if not us.Buffer or us.Length == 0:
        return ""
    raw = ctypes.string_at(us.Buffer, us.Length)
    return raw.decode("utf-16-le")


def QueryFullProcessImageNameA(hProcess, dwFlags=0):
    return QueryFullProcessImageNameW(hProcess, dwFlags).encode("mbcs", "replace")
