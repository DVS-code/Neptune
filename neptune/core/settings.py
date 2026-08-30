"""Persistent application settings."""

from __future__ import annotations

import json
import os
import threading

from neptune.core import paths
from neptune.memory import offsets as O

SPEED_UNITS = ("km/h", "mph")
PRESSURE_UNITS = ("psi", "bar")
HEIGHT_UNITS = ("m", "in")

DEFAULTS = {
    "speed_unit": "km/h",
    "pressure_unit": "psi",
    "height_unit": "m",
    "decimals": 2,
    "atmospheric_psi": O.ATMOSPHERIC_PSI_DEFAULT,
    "auto_attach": True,
    "restore_on_exit": True,
    "airride_volume": 70,
    "maybach_volume": 70,
    "check_for_updates": True,
    "skip_update_version": "",
    "bindings": {},
}


def _same_control(left: dict | None, right: dict | None) -> bool:
    """True when two bindings are the same physical control.

    Only the kind and the code identify a control; the label is display text that can
    differ between builds for the same button.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if not left.get("kind") or not right.get("kind"):
        return False
    return left.get("kind") == right.get("kind") and left.get("code") == right.get("code")


class Settings:
    """Application settings, persisted as one JSON document."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(paths.data_dir(), "settings.json")
        self._lock = threading.RLock()
        self._data = dict(DEFAULTS)
        self._listeners: list = []
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                with open(self.path, encoding="utf-8") as handle:
                    stored = json.load(handle)
                if isinstance(stored, dict):
                    for key, value in stored.items():
                        if key in DEFAULTS:
                            self._data[key] = value
            except (OSError, ValueError):
                self._data = dict(DEFAULTS)
            self._drop_duplicate_bindings()
        O.set_atmospheric_psi(self._data.get("atmospheric_psi", O.ATMOSPHERIC_PSI_DEFAULT))

    def _drop_duplicate_bindings(self) -> None:
        """Enforce one-control-one-feature on settings written before that rule existed.

        Without this an upgrading user keeps a duplicate until they rebind something, and
        one press would fire both features. The first key wins so the result is stable
        rather than dependent on dict order.
        """
        stored = self._data.get("bindings")
        if not isinstance(stored, dict):
            return
        kept: dict[str, dict] = {}
        claimed: list[dict] = []
        for key in sorted(stored):
            binding = stored[key]
            if not isinstance(binding, dict) or not binding.get("kind"):
                continue
            if any(_same_control(binding, taken) for taken in claimed):
                continue
            claimed.append(binding)
            kept[key] = binding
        if kept != stored:
            self._data["bindings"] = kept

    def save(self) -> bool:
        with self._lock:
            try:
                temporary = self.path + ".tmp"
                with open(temporary, "w", encoding="utf-8") as handle:
                    json.dump(self._data, handle, indent=2)
                os.replace(temporary, self.path)
                return True
            except OSError:
                return False

    def get(self, key: str, fallback=None):
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key, fallback))

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
        if key == "atmospheric_psi":
            O.set_atmospheric_psi(value)
        self.save()
        self._notify(key)

    def binding(self, key: str) -> dict | None:
        stored = self.get("bindings", {}) or {}
        value = stored.get(key)
        if isinstance(value, dict) and "kind" in value and "code" in value:
            return value
        return None

    def set_binding(self, key: str, binding: dict | None) -> None:
        """Assign a control to `key`, taking it from anything else that held it.

        A control can only drive one feature. Sharing one would fire both at once —
        an air-ride drop and a scramble burst from the same press — so the previous
        owner is unassigned rather than silently left to double-fire.
        """
        with self._lock:
            stored = dict(self._data.get("bindings") or {})
            if binding and isinstance(binding, dict) and "kind" in binding:
                for other, existing in list(stored.items()):
                    if other != key and _same_control(existing, binding):
                        stored.pop(other, None)
                stored[key] = binding
            else:
                stored.pop(key, None)
            self._data["bindings"] = stored
        self.save()
        self._notify("bindings")

    def binding_owner(self, binding: dict | None) -> str | None:
        """Which key currently holds this control, if any."""
        if not binding:
            return None
        stored = self.get("bindings", {}) or {}
        for key, existing in stored.items():
            if _same_control(existing, binding):
                return key
        return None

    def subscribe(self, callback) -> None:
        self._listeners.append(callback)

    def _notify(self, key: str) -> None:
        for callback in list(self._listeners):
            try:
                callback(key)
            except Exception:
                continue

    def speed(self, metres_per_second: float | None) -> tuple[float, str]:
        """Convert m/s to the user's speed unit."""
        if metres_per_second is None:
            return (0.0, self.get("speed_unit"))
        unit = self.get("speed_unit")
        if unit == "mph":
            return (metres_per_second * O.MS_TO_MPH, unit)
        return (metres_per_second * O.MS_TO_KPH, unit)

    def speed_to_ms(self, value: float) -> float:
        """Convert a value in the user's speed unit back to m/s."""
        if self.get("speed_unit") == "mph":
            return value / O.MS_TO_MPH
        return value / O.MS_TO_KPH

    def pressure(self, psi: float | None) -> tuple[float, str]:
        """Convert psi to the user's pressure unit."""
        if psi is None:
            return (0.0, self.get("pressure_unit"))
        unit = self.get("pressure_unit")
        if unit == "bar":
            return (psi * O.PSI_TO_BAR, unit)
        return (psi, unit)

    def height(self, metres: float | None) -> tuple[float, str]:
        """Convert metres to the user's height unit."""
        if metres is None:
            return (0.0, self.get("height_unit"))
        unit = self.get("height_unit")
        if unit == "in":
            return (metres * O.METRE_TO_INCH, unit)
        return (metres, unit)

    def format_pressure(self, psi: float | None) -> str:
        if psi is None:
            return "--"
        value, unit = self.pressure(psi)
        decimals = 2 if unit == "bar" else 1
        return f"{value:.{decimals}f} {unit}"

    def format_speed(self, metres_per_second: float | None) -> str:
        if metres_per_second is None:
            return "--"
        value, unit = self.speed(metres_per_second)
        return f"{value:.0f} {unit}"

    def format_height(self, metres: float | None) -> str:
        if metres is None:
            return "--"
        value, unit = self.height(metres)
        decimals = 2 if unit == "in" else 3
        return f"{value:.{decimals}f} {unit}"
