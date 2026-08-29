"""Per-car tune storage.

A tune captures the engine and turbo settings for one car. Tunes are scoped to
the car they were saved on, so switching tunes never loads another car's setup.
"""
from __future__ import annotations

import json
import os
import re
import threading

from neptune.core import paths





TUNED_MODULES = ('engine', 'turbo')

MAX_NAME_LENGTH = 40
MAX_TUNES_PER_CAR = 12

_UNSAFE = re.compile(r'[^A-Za-z0-9 _\-()]+')


def clean_name(name: str) -> str:
    return _UNSAFE.sub('', (name or '').strip())[:MAX_NAME_LENGTH]


def car_key(fingerprint) -> str | None:
    """Stable string key for a car's identity."""
    if not fingerprint:
        return None
    try:
        return '|'.join(str(part) for part in fingerprint)
    except TypeError:
        return None


class TuneStore:
    """Every car's saved tunes, persisted as one JSON document."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(paths.data_dir(), 'tunes.json')
        self._lock = threading.RLock()
        self._data = {'cars': {}}
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                with open(self.path, encoding='utf-8') as handle:
                    stored = json.load(handle)
                if isinstance(stored, dict) and isinstance(stored.get('cars'), dict):
                    self._data = stored
            except (OSError, ValueError):
                self._data = {'cars': {}}

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

    def _car(self, key: str, create: bool = False) -> dict | None:
        cars = self._data.setdefault('cars', {})
        record = cars.get(key)
        if record is None and create:
            record = {'name': '', 'tunes': [], 'active': -1}
            cars[key] = record
        return record

    def car_name(self, key: str) -> str:
        record = self._car(key)
        return record.get('name', '') if record else ''

    def set_car_name(self, key: str, name: str) -> tuple[bool, str]:
        if not key:
            return False, 'No car detected.'
        cleaned = clean_name(name)
        if not cleaned:
            return False, 'Enter a name for this car.'
        with self._lock:
            record = self._car(key, create=True)
            record['name'] = cleaned
            return self.save(), f'Named this car "{cleaned}".'

    def known_cars(self) -> list[tuple[str, str, int]]:
        """(key, name, tune count) for every car with saved data."""
        cars = [(key, value.get('name', ''), len(value.get('tunes', [])))
                for key, value in self._data.get('cars', {}).items()]
        cars.sort(key=lambda item: (not item[1], item[1].lower(), item[0]))
        return cars

    def tunes_for(self, key: str) -> list[dict]:
        record = self._car(key)
        return list(record.get('tunes', [])) if record else []

    def active_index(self, key: str) -> int:
        record = self._car(key)
        return int(record.get('active', -1)) if record else -1

    def set_active(self, key: str, index: int) -> bool:
        with self._lock:
            record = self._car(key)
            if not record:
                return False
            record['active'] = int(index)
            return self.save()

    def add_tune(self, key: str, name: str, state: dict) -> tuple[bool, str]:
        if not key:
            return False, 'No car detected.'
        cleaned = clean_name(name)
        if not cleaned:
            return False, 'Enter a name for this tune.'
        with self._lock:
            record = self._car(key, create=True)
            tunes = record.setdefault('tunes', [])
            for tune in tunes:
                if tune.get('name', '').lower() == cleaned.lower():
                    tune['state'] = state
                    return self.save(), f'Updated "{cleaned}".'
            if len(tunes) >= MAX_TUNES_PER_CAR:
                return False, f'This car already has {MAX_TUNES_PER_CAR} tunes.'
            tunes.append({'name': cleaned, 'state': state})
            record['active'] = len(tunes) - 1
            return self.save(), f'Saved "{cleaned}".'

    def update_tune(self, key: str, index: int, state: dict) -> tuple[bool, str]:
        with self._lock:
            record = self._car(key)
            if not record or not 0 <= index < len(record.get('tunes', [])):
                return False, 'That tune no longer exists.'
            record['tunes'][index]['state'] = state
            name = record['tunes'][index].get('name', '')
            return self.save(), f'Updated "{name}".'

    def rename_tune(self, key: str, index: int, name: str) -> tuple[bool, str]:
        cleaned = clean_name(name)
        if not cleaned:
            return False, 'Enter a name for this tune.'
        with self._lock:
            record = self._car(key)
            if not record or not 0 <= index < len(record.get('tunes', [])):
                return False, 'That tune no longer exists.'
            record['tunes'][index]['name'] = cleaned
            return self.save(), f'Renamed to "{cleaned}".'

    def delete_tune(self, key: str, index: int) -> tuple[bool, str]:
        with self._lock:
            record = self._car(key)
            if not record or not 0 <= index < len(record.get('tunes', [])):
                return False, 'That tune no longer exists.'
            removed = record['tunes'].pop(index)
            active = int(record.get('active', -1))
            if active == index:
                record['active'] = -1
            elif active > index:
                record['active'] = active - 1
            return self.save(), f'Deleted "{removed.get("name", "")}".'

    def delete_car(self, key: str) -> bool:
        with self._lock:
            if self._data.get('cars', {}).pop(key, None) is None:
                return False
            return self.save()

    def next_index(self, key: str) -> int:
        """The tune the cycle key should move to, or -1 when this car has none."""
        tunes = self.tunes_for(key)
        if not tunes:
            return -1
        current = self.active_index(key)
        if current < 0 or current >= len(tunes):
            return 0
        return (current + 1) % len(tunes)
