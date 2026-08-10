"""Persistent application settings."""
from __future__ import annotations

import json
import os
import threading

from neptune.core import paths
from neptune.memory import offsets as O

SPEED_UNITS = ('km/h', 'mph')
PRESSURE_UNITS = ('psi', 'bar')
HEIGHT_UNITS = ('m', 'in')

DEFAULTS = {
    'speed_unit': 'km/h',
    'pressure_unit': 'psi',
    'height_unit': 'm',
    'decimals': 2,
    'atmospheric_psi': O.ATMOSPHERIC_PSI_DEFAULT,
    'auto_attach': True,
    'restore_on_exit': True,
    'bindings': {},
}


class Settings:
    """Application settings, persisted as one JSON document."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(paths.data_dir(), 'settings.json')
        self._lock = threading.RLock()
        self._data = dict(DEFAULTS)
        self._listeners: list = []
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                with open(self.path, encoding='utf-8') as handle:
                    stored = json.load(handle)
                if isinstance(stored, dict):
                    for key, value in stored.items():
                        if key in DEFAULTS:
                            self._data[key] = value
            except (OSError, ValueError):
                self._data = dict(DEFAULTS)
        O.set_atmospheric_psi(self._data.get('atmospheric_psi',
                                             O.ATMOSPHERIC_PSI_DEFAULT))

    def save(self) -> bool:
        with self._lock:
            try:
                temporary = self.path + '.tmp'
                with open(temporary, 'w', encoding='utf-8') as handle:
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
        if key == 'atmospheric_psi':
            O.set_atmospheric_psi(value)
        self.save()
        self._notify(key)

    def binding(self, key: str) -> dict | None:
        stored = self.get('bindings', {}) or {}
        value = stored.get(key)
        if isinstance(value, dict) and 'kind' in value and 'code' in value:
            return value
        return None

    def set_binding(self, key: str, binding: dict | None) -> None:
        with self._lock:
            stored = dict(self._data.get('bindings') or {})
            if binding and isinstance(binding, dict) and 'kind' in binding:
                stored[key] = binding
            else:
                stored.pop(key, None)
            self._data['bindings'] = stored
        self.save()
        self._notify('bindings')

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
            return (0.0, self.get('speed_unit'))
        unit = self.get('speed_unit')
        if unit == 'mph':
            return (metres_per_second * O.MS_TO_MPH, unit)
        return (metres_per_second * O.MS_TO_KPH, unit)

    def speed_to_ms(self, value: float) -> float:
        """Convert a value in the user's speed unit back to m/s."""
        if self.get('speed_unit') == 'mph':
            return value / O.MS_TO_MPH
        return value / O.MS_TO_KPH

    def pressure(self, psi: float | None) -> tuple[float, str]:
        """Convert psi to the user's pressure unit."""
        if psi is None:
            return (0.0, self.get('pressure_unit'))
        unit = self.get('pressure_unit')
        if unit == 'bar':
            return (psi * O.PSI_TO_BAR, unit)
        return (psi, unit)

    def height(self, metres: float | None) -> tuple[float, str]:
        """Convert metres to the user's height unit."""
        if metres is None:
            return (0.0, self.get('height_unit'))
        unit = self.get('height_unit')
        if unit == 'in':
            return (metres * O.METRE_TO_INCH, unit)
        return (metres, unit)

    def format_pressure(self, psi: float | None) -> str:
        if psi is None:
            return '--'
        value, unit = self.pressure(psi)
        decimals = 2 if unit == 'bar' else 1
        return f'{value:.{decimals}f} {unit}'

    def format_speed(self, metres_per_second: float | None) -> str:
        if metres_per_second is None:
            return '--'
        value, unit = self.speed(metres_per_second)
        return f'{value:.0f} {unit}'

    def format_height(self, metres: float | None) -> str:
        if metres is None:
            return '--'
        value, unit = self.height(metres)
        decimals = 2 if unit == 'in' else 3
        return f'{value:.{decimals}f} {unit}'
