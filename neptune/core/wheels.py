"""Racing-wheel input over the legacy joystick API.

Wheels are DirectInput/HID devices, so XInput never sees them — `XInputGetState` only answers for
Xbox-protocol pads. That is why a G29 binds nothing through `core.input`'s pad path even though
Windows and the game both see the wheel perfectly.

`winmm`'s joystick API does see them, needs no third-party package, and reaches every wheel that
presents as a standard HID game controller. Verified against a Logitech G29 (VID 046D / PID C24F):
joystick id 1, 25 buttons, `joyGetPosEx` rc=0.

Buttons and the POV hat only. Axes are deliberately not bindable — pedals and steering rest at, or
sweep through, positions that would fire a hold-style binding while simply driving.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

winmm = ctypes.WinDLL("winmm")

MAX_DEVICES = 16
MAX_BUTTONS = 32


JOY_RETURNBUTTONS = 0x00000080
JOY_RETURNPOV = 0x00000040
POLL_FLAGS = JOY_RETURNBUTTONS | JOY_RETURNPOV

POV_CENTERED = 0xFFFF


POV_BASE = 0x1000
POV_DIRECTIONS = [
    ("D-Pad Up", 0),
    ("D-Pad Right", 9000),
    ("D-Pad Down", 18000),
    ("D-Pad Left", 27000),
]
POV_TOLERANCE = 4500


RESCAN_INTERVAL = 2.0


class JOYCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("szPname", ctypes.c_wchar * 32),
        ("wXmin", wintypes.UINT),
        ("wXmax", wintypes.UINT),
        ("wYmin", wintypes.UINT),
        ("wYmax", wintypes.UINT),
        ("wZmin", wintypes.UINT),
        ("wZmax", wintypes.UINT),
        ("wNumButtons", wintypes.UINT),
        ("wPeriodMin", wintypes.UINT),
        ("wPeriodMax", wintypes.UINT),
        ("wRmin", wintypes.UINT),
        ("wRmax", wintypes.UINT),
        ("wUmin", wintypes.UINT),
        ("wUmax", wintypes.UINT),
        ("wVmin", wintypes.UINT),
        ("wVmax", wintypes.UINT),
        ("wCaps", wintypes.UINT),
        ("wMaxAxes", wintypes.UINT),
        ("wNumAxes", wintypes.UINT),
        ("wMaxButtons", wintypes.UINT),
        ("szRegKey", ctypes.c_wchar * 32),
        ("szOEMVxD", ctypes.c_wchar * 260),
    ]


class JOYINFOEX(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("dwXpos", wintypes.DWORD),
        ("dwYpos", wintypes.DWORD),
        ("dwZpos", wintypes.DWORD),
        ("dwRpos", wintypes.DWORD),
        ("dwUpos", wintypes.DWORD),
        ("dwVpos", wintypes.DWORD),
        ("dwButtons", wintypes.DWORD),
        ("dwButtonNumber", wintypes.DWORD),
        ("dwPOV", wintypes.DWORD),
        ("dwReserved1", wintypes.DWORD),
        ("dwReserved2", wintypes.DWORD),
    ]


LOGITECH = 0x046D
THRUSTMASTER = 0x044F
FANATEC = 0x0EB7

_G29 = {
    0: "X",
    1: "Square",
    2: "Circle",
    3: "Triangle",
    4: "Right Paddle",
    5: "Left Paddle",
    6: "R2",
    7: "L2",
    8: "Share",
    9: "Options",
    10: "R3",
    11: "L3",
    12: "PlayStation",
    19: "Dial Right",
    20: "Dial Left",
    21: "Dial Press",
    22: "Minus",
    23: "Plus",
    24: "Enter",
}

_G920 = {
    0: "A",
    1: "B",
    2: "X",
    3: "Y",
    4: "Right Paddle",
    5: "Left Paddle",
    6: "RB",
    7: "LB",
    8: "View",
    9: "Menu",
    10: "R3",
    11: "L3",
}

KNOWN_WHEELS: dict[tuple[int, int], tuple[str, dict[int, str]]] = {
    (LOGITECH, 0xC24F): ("Logitech G29", _G29),
    (LOGITECH, 0xC260): ("Logitech G29", _G29),
    (LOGITECH, 0xC262): ("Logitech G920", _G920),
    (LOGITECH, 0xC266): ("Logitech G923", _G29),
    (LOGITECH, 0xC267): ("Logitech G923", _G920),
    (LOGITECH, 0xC24A): ("Logitech G27", _G29),
    (LOGITECH, 0xC294): ("Logitech Driving Force", {}),
    (LOGITECH, 0xC29B): ("Logitech G27", _G29),
    (LOGITECH, 0xC298): ("Logitech Driving Force Pro", {}),
    (LOGITECH, 0xC29A): ("Logitech Driving Force GT", {}),
    (THRUSTMASTER, 0xB65D): ("Thrustmaster T150", {}),
    (THRUSTMASTER, 0xB66E): ("Thrustmaster T300", {}),
    (THRUSTMASTER, 0xB689): ("Thrustmaster TS-PC", {}),
    (THRUSTMASTER, 0xB66D): ("Thrustmaster T500", {}),
    (FANATEC, 0x0005): ("Fanatec CSL Elite", {}),
    (FANATEC, 0x0006): ("Fanatec CSL Elite PS4", {}),
    (FANATEC, 0x0020): ("Fanatec DD1", {}),
    (FANATEC, 0x0E03): ("Fanatec CSL DD", {}),
}


MICROSOFT = 0x045E


class Device:
    """One connected joystick-class device."""

    __slots__ = ("index", "name", "vendor", "product", "buttons", "has_pov")

    def __init__(self, index: int, caps: JOYCAPSW):
        self.index = index
        self.vendor = int(caps.wMid)
        self.product = int(caps.wPid)
        self.buttons = min(int(caps.wNumButtons), MAX_BUTTONS)
        self.has_pov = bool(caps.wCaps & 0x0016)
        known = KNOWN_WHEELS.get((self.vendor, self.product))

        self.name = known[0] if known else self._fallback_name(caps)

    @staticmethod
    def _fallback_name(caps: JOYCAPSW) -> str:
        raw = (caps.szPname or "").strip()
        if raw and "PC-joystick" not in raw:
            return raw
        return f"Wheel {caps.wMid:04X}:{caps.wPid:04X}"

    def button_label(self, index: int) -> str:
        known = KNOWN_WHEELS.get((self.vendor, self.product))
        if known:
            label = known[1].get(index)
            if label:
                return label
        return f"Button {index + 1}"


_devices: list[Device] = []
_last_scan = 0.0


def _read_caps(index: int) -> JOYCAPSW | None:
    caps = JOYCAPSW()
    if winmm.joyGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps)) != 0:
        return None
    return caps


def _poll(index: int) -> JOYINFOEX | None:
    info = JOYINFOEX()
    info.dwSize = ctypes.sizeof(info)
    info.dwFlags = POLL_FLAGS
    if winmm.joyGetPosEx(index, ctypes.byref(info)) != 0:
        return None
    return info


def _scan() -> list[Device]:
    found = []
    for index in range(MAX_DEVICES):
        caps = _read_caps(index)
        if caps is None:
            continue
        if caps.wMid == MICROSOFT:
            continue
        if _poll(index) is None:
            continue
        found.append(Device(index, caps))
    return found


def devices(force: bool = False) -> list[Device]:
    """Connected wheels, re-enumerated at most every RESCAN_INTERVAL.

    Rescanning is what makes a wheel plugged in — or a G HUB restart — recover on its own instead of
    needing Neptune restarted. joyGetDevCapsW on 16 slots is cheap, but not per-tick cheap.
    """
    global _devices, _last_scan
    now = time.monotonic()
    if force or now - _last_scan >= RESCAN_INTERVAL:
        _last_scan = now
        try:
            _devices = _scan()
        except Exception:
            _devices = []
    return _devices


def _device_for(binding: dict) -> Device | None:
    """Resolve a stored binding to a live device.

    Matched by vendor/product first so the binding survives the device index shifting — which it
    does whenever another controller is plugged in ahead of it.
    """
    vendor = binding.get("vendor")
    product = binding.get("product")
    available = devices()
    if vendor is not None and product is not None:
        for device in available:
            if device.vendor == vendor and device.product == product:
                return device
    index = binding.get("device")
    for device in available:
        if device.index == index:
            return device
    return None


def connected() -> bool:
    return bool(devices())


def wheel_names() -> list[str]:
    return [device.name for device in devices()]


def _pov_matches(pov: int, wanted: int) -> bool:
    if pov == POV_CENTERED or pov > 36000:
        return False
    delta = abs(pov - wanted)
    delta = min(delta, 36000 - delta)
    return delta <= POV_TOLERANCE


def is_down(binding: dict) -> bool:
    """Whether a stored wheel binding is currently held."""
    device = _device_for(binding)
    if device is None:
        return False
    state = _poll(device.index)
    if state is None:
        return False
    code = int(binding.get("code", -1))
    if code >= POV_BASE:
        return _pov_matches(int(state.dwPOV), code - POV_BASE)
    if 0 <= code < MAX_BUTTONS:
        return bool(state.dwButtons & (1 << code))
    return False


def make_binding(device: Device, code: int) -> dict:
    """A persistable binding. Vendor and product are stored so it survives re-indexing."""
    if code >= POV_BASE:
        label = next((name for name, angle in POV_DIRECTIONS if angle == code - POV_BASE), "D-Pad")
    else:
        label = device.button_label(code)
    return {
        "kind": "wheel",
        "code": int(code),
        "label": label,
        "device": device.index,
        "vendor": device.vendor,
        "product": device.product,
        "device_name": device.name,
    }


def poll_any() -> dict | None:
    """The wheel button or hat direction currently held, across every connected wheel."""
    for device in devices():
        state = _poll(device.index)
        if state is None:
            continue
        buttons = int(state.dwButtons)
        if buttons:
            for index in range(min(device.buttons, MAX_BUTTONS)):
                if buttons & (1 << index):
                    return make_binding(device, index)
        pov = int(state.dwPOV)
        if pov != POV_CENTERED and pov <= 36000:
            for _, angle in POV_DIRECTIONS:
                if _pov_matches(pov, angle):
                    return make_binding(device, POV_BASE + angle)
    return None
