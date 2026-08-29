"""Keyboard, controller and racing-wheel input with configurable bindings.

Three binding kinds, all resolved through `is_down` so feature modules never care which:
  'key'   — keyboard and extra mouse buttons
  'pad'   — XInput controllers
  'wheel' — racing wheels, via core.wheels
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from neptune.core import wheels

user32 = ctypes.WinDLL("user32")

KEY_TABLE = [
    ("X", 0x58),
    ("Z", 0x5A),
    ("C", 0x43),
    ("V", 0x56),
    ("B", 0x42),
    ("N", 0x4E),
    ("F", 0x46),
    ("G", 0x47),
    ("H", 0x48),
    ("J", 0x4A),
    ("K", 0x4B),
    ("L", 0x4C),
    ("Q", 0x51),
    ("E", 0x45),
    ("R", 0x52),
    ("T", 0x54),
    ("Y", 0x59),
    ("U", 0x55),
    ("Left Ctrl", 0xA2),
    ("Right Ctrl", 0xA3),
    ("Left Alt", 0xA4),
    ("Right Alt", 0xA5),
    ("Left Shift", 0xA0),
    ("Space", 0x20),
    ("Caps Lock", 0x14),
    ("Tab", 0x09),
    ("F1", 0x70),
    ("F2", 0x71),
    ("F3", 0x72),
    ("F4", 0x73),
    ("F5", 0x74),
    ("F6", 0x75),
    ("F7", 0x76),
    ("F8", 0x77),
    ("F9", 0x78),
    ("F10", 0x79),
    ("F11", 0x7A),
    ("F12", 0x7B),
    ("Numpad 0", 0x60),
    ("Numpad 1", 0x61),
    ("Numpad 2", 0x62),
    ("Numpad 3", 0x63),
    ("Numpad 4", 0x64),
    ("Numpad 5", 0x65),
    ("Numpad 6", 0x66),
    ("Numpad 7", 0x67),
    ("Numpad 8", 0x68),
    ("Numpad 9", 0x69),
    ("Numpad .", 0x6E),
    ("Mouse 3", 0x04),
    ("Mouse 4", 0x05),
    ("Mouse 5", 0x06),
]
KEY_BY_CODE = {code: label for label, code in KEY_TABLE}

ESCAPE_VK = 0x1B
BACKSPACE_VK = 0x08

PAD_TABLE = [
    ("A", 0x1000),
    ("B", 0x2000),
    ("X", 0x4000),
    ("Y", 0x8000),
    ("LB", 0x0100),
    ("RB", 0x0200),
    ("Left Stick", 0x0040),
    ("Right Stick", 0x0080),
    ("Start", 0x0010),
    ("Back", 0x0020),
    ("D-Pad Up", 0x0001),
    ("D-Pad Down", 0x0002),
    ("D-Pad Left", 0x0004),
    ("D-Pad Right", 0x0008),
    ("Left Trigger", 0x10000),
    ("Right Trigger", 0x20000),
]
PAD_BY_CODE = {code: label for label, code in PAD_TABLE}
TRIGGER_THRESHOLD = 60
LEFT_TRIGGER = 0x10000
RIGHT_TRIGGER = 0x20000


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", XINPUT_GAMEPAD)]


def _load_xinput():
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            library = ctypes.WinDLL(name)
        except OSError:
            continue
        if hasattr(library, "XInputGetState"):
            return library
    return None


_xinput = _load_xinput()


def key_down(code: int) -> bool:
    return (user32.GetAsyncKeyState(code) & 0x8000) != 0


def controller_connected() -> bool:
    if not _xinput:
        return False
    state = XINPUT_STATE()
    return _xinput.XInputGetState(0, ctypes.byref(state)) == 0


def pad_down(code: int, index: int = 0) -> bool:
    if not _xinput:
        return False
    state = XINPUT_STATE()
    if _xinput.XInputGetState(index, ctypes.byref(state)) != 0:
        return False
    gamepad = state.Gamepad
    if code == LEFT_TRIGGER:
        return gamepad.bLeftTrigger >= TRIGGER_THRESHOLD
    if code == RIGHT_TRIGGER:
        return gamepad.bRightTrigger >= TRIGGER_THRESHOLD
    return bool(gamepad.wButtons & code)


def make_binding(kind: str, code: int) -> dict:
    table = KEY_BY_CODE if kind == "key" else PAD_BY_CODE
    return {"kind": kind, "code": int(code), "label": table.get(code, "Unknown")}


def source_label(binding: dict | None) -> str:
    """The device word shown before a binding's own label."""
    kind = (binding or {}).get("kind")
    if kind == "pad":
        return "Controller"
    if kind == "wheel":
        return (binding or {}).get("device_name") or "Wheel"
    return "Key"


def describe(binding: dict | None) -> str:
    """Human label for a binding."""
    if not binding or not binding.get("kind"):
        return "Not bound"
    return f"{source_label(binding)} · {binding.get('label', 'Unknown')}"


def is_down(binding: dict | None) -> bool:
    if not binding:
        return False
    try:
        kind = binding["kind"]
        if kind == "key":
            return key_down(binding["code"])
        if kind == "pad":
            return pad_down(binding["code"])
        if kind == "wheel":
            return wheels.is_down(binding)
    except Exception:
        return False
    return False


def poll_any_key() -> dict | None:
    """The key currently held, or a clear request for Escape and Backspace."""
    if key_down(ESCAPE_VK) or key_down(BACKSPACE_VK):
        return {"clear": True}
    for _, code in KEY_TABLE:
        if key_down(code):
            return make_binding("key", code)
    return None


def poll_any_pad() -> dict | None:
    """The controller button currently held."""
    if not _xinput:
        return None
    state = XINPUT_STATE()
    if _xinput.XInputGetState(0, ctypes.byref(state)) != 0:
        return None
    gamepad = state.Gamepad
    if gamepad.bLeftTrigger >= TRIGGER_THRESHOLD:
        return make_binding("pad", LEFT_TRIGGER)
    if gamepad.bRightTrigger >= TRIGGER_THRESHOLD:
        return make_binding("pad", RIGHT_TRIGGER)
    for _, code in PAD_TABLE:
        if code in (LEFT_TRIGGER, RIGHT_TRIGGER):
            continue
        if gamepad.wButtons & code:
            return make_binding("pad", code)
    return None


def poll_any_wheel() -> dict | None:
    """The racing-wheel button or hat direction currently held."""
    try:
        return wheels.poll_any()
    except Exception:
        return None


def poll_any_input() -> dict | None:
    """Capture from whichever device the user touches — keyboard, pad or wheel.

    Keyboard first so Escape and Backspace can always clear a binding, even with a wheel attached.
    """
    return poll_any_key() or poll_any_pad() or poll_any_wheel()


def wheel_connected() -> bool:
    try:
        return wheels.connected()
    except Exception:
        return False


def wheel_names() -> list[str]:
    try:
        return wheels.wheel_names()
    except Exception:
        return []


class EdgeDetector:
    """Fires once per press rather than continuously while held."""

    def __init__(self):
        self._was_down = False

    def pressed(self, binding: dict | None) -> bool:
        down = is_down(binding) if binding else False
        fired = down and not self._was_down
        self._was_down = down
        return fired

    def reset(self) -> None:
        self._was_down = False
