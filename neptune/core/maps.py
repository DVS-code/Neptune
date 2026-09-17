"""Per-car tune storage.

A tune captures the engine, turbo and transmission settings for one car. Tunes are scoped to
the car they were saved on, so switching tunes never loads another car's setup.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from copy import deepcopy

from neptune.core import paths
from neptune.core.models import utc_now

TUNED_MODULES = ("engine", "turbo", "transmission")

STORE_SCHEMA = 2

MAX_NAME_LENGTH = 40
MAX_TUNES_PER_CAR = 12

_UNSAFE = re.compile(r"[^A-Za-z0-9 _\-()]+")


def clean_name(name: str) -> str:
    return _UNSAFE.sub("", (name or "").strip())[:MAX_NAME_LENGTH]


def _migrate(stored: dict) -> dict:
    """Bring legacy tune files forward without discarding unknown future keys."""
    data = dict(stored)
    try:
        schema = int(data.get("schema", 1) or 1)
    except (TypeError, ValueError, OverflowError):
        schema = 1
    # A file written by a newer Neptune keeps its own schema number.
    data["schema"] = max(schema, STORE_SCHEMA)
    cars = data.get("cars")
    if not isinstance(cars, dict):
        data["cars"] = {}
        return data
    for record in cars.values():
        if not isinstance(record, dict):
            continue
        tunes = record.get("tunes")
        if not isinstance(tunes, list):
            record["tunes"] = []
            continue
        for tune in tunes:
            if not isinstance(tune, dict):
                continue
            tune.setdefault("id", uuid.uuid4().hex)
            tune.setdefault("revision", 1)
            tune.setdefault("created_at", "")
            tune.setdefault("modified_at", tune.get("created_at", ""))
            tune.setdefault("results", {})
            for field in ("logs", "changes"):
                if not isinstance(tune.get(field), list):
                    tune[field] = []
    return data


def car_key(fingerprint) -> str | None:
    """Stable string key for a car's identity."""
    if not fingerprint:
        return None
    try:
        return "|".join(str(part) for part in fingerprint)
    except TypeError:
        return None


class TuneStore:
    """Every car's saved tunes, persisted as one JSON document."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(paths.data_dir(), "tunes.json")
        self._lock = threading.RLock()
        self._data = {"schema": STORE_SCHEMA, "cars": {}}
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                with open(self.path, encoding="utf-8") as handle:
                    stored = json.load(handle)
                if isinstance(stored, dict) and isinstance(stored.get("cars"), dict):
                    self._data = _migrate(stored)
            except (OSError, ValueError):
                self._data = {"schema": STORE_SCHEMA, "cars": {}}

    def save(self) -> bool:
        with self._lock:
            return paths.write_json(self.path, self._data)

    def _car(self, key: str, create: bool = False) -> dict | None:
        cars = self._data.setdefault("cars", {})
        record = cars.get(key)
        if record is None and create:
            record = {"name": "", "tunes": [], "active": -1}
            cars[key] = record
        return record

    def car_name(self, key: str) -> str:
        record = self._car(key)
        return record.get("name", "") if record else ""

    def set_car_name(self, key: str, name: str) -> tuple[bool, str]:
        if not key:
            return False, "No car detected."
        cleaned = clean_name(name)
        if not cleaned:
            return False, "Enter a name for this car."
        with self._lock:
            record = self._car(key, create=True)
            record["name"] = cleaned
            return self.save(), f'Named this car "{cleaned}".'

    def known_cars(self) -> list[tuple[str, str, int]]:
        """(key, name, tune count) for every car with saved data."""
        cars = [
            (key, value.get("name", ""), len(value.get("tunes", [])))
            for key, value in self._data.get("cars", {}).items()
        ]
        cars.sort(key=lambda item: (not item[1], item[1].lower(), item[0]))
        return cars

    def tunes_for(self, key: str) -> list[dict]:
        record = self._car(key)
        return list(record.get("tunes", [])) if record else []

    def active_index(self, key: str) -> int:
        record = self._car(key)
        return int(record.get("active", -1)) if record else -1

    def set_active(self, key: str, index: int) -> bool:
        with self._lock:
            record = self._car(key)
            if not record:
                return False
            record["active"] = int(index)
            return self.save()

    def add_tune(self, key: str, name: str, state: dict) -> tuple[bool, str]:
        if not key:
            return False, "No car detected."
        cleaned = clean_name(name)
        if not cleaned:
            return False, "Enter a name for this tune."
        with self._lock:
            record = self._car(key, create=True)
            tunes = record.setdefault("tunes", [])
            for tune in tunes:
                if tune.get("name", "").lower() == cleaned.lower():
                    if tune.get("logs"):
                        return False, "This revision has recorded logs; duplicate it before editing."
                    tune["state"] = state
                    tune["modified_at"] = utc_now()
                    return self.save(), f'Updated "{cleaned}".'
            if len(tunes) >= MAX_TUNES_PER_CAR:
                return False, f"This car already has {MAX_TUNES_PER_CAR} tunes."
            now = utc_now()
            tunes.append(
                {
                    "id": uuid.uuid4().hex,
                    "name": cleaned,
                    "revision": 1,
                    "created_at": now,
                    "modified_at": now,
                    "state": state,
                    "logs": [],
                    "results": {},
                    "changes": [],
                }
            )
            record["active"] = len(tunes) - 1
            return self.save(), f'Saved "{cleaned}".'

    def update_tune(self, key: str, index: int, state: dict) -> tuple[bool, str]:
        with self._lock:
            record = self._car(key)
            if not record or not 0 <= index < len(record.get("tunes", [])):
                return False, "That tune no longer exists."
            if record["tunes"][index].get("logs"):
                return False, "This revision has recorded logs; duplicate it before editing."
            record["tunes"][index]["state"] = state
            record["tunes"][index]["modified_at"] = utc_now()
            name = record["tunes"][index].get("name", "")
            return self.save(), f'Updated "{name}".'

    def rename_tune(self, key: str, index: int, name: str) -> tuple[bool, str]:
        cleaned = clean_name(name)
        if not cleaned:
            return False, "Enter a name for this tune."
        with self._lock:
            record = self._car(key)
            if not record or not 0 <= index < len(record.get("tunes", [])):
                return False, "That tune no longer exists."
            record["tunes"][index]["name"] = cleaned
            record["tunes"][index]["modified_at"] = utc_now()
            return self.save(), f'Renamed to "{cleaned}".'

    def tune(self, key: str, index: int) -> dict | None:
        record = self._car(key)
        tunes = record.get("tunes", []) if record else []
        return tunes[index] if 0 <= index < len(tunes) and isinstance(tunes[index], dict) else None

    def find_revision(self, key: str, tune_id: str, name: str, revision: int | None) -> int:
        """Index of the tune a log was recorded with: by id, else by name and revision. -1 if gone."""
        tunes = self.tunes_for(key)
        if tune_id:
            for index, tune in enumerate(tunes):
                if tune.get("id") == tune_id:
                    return index
        if name:
            for index, tune in enumerate(tunes):
                if tune.get("name") == name and tune.get("revision") == revision:
                    return index
        return -1

    def duplicate_tune(self, key: str, index: int) -> tuple[bool, str, int]:
        with self._lock:
            record = self._car(key)
            source = self.tune(key, index)
            if not record or source is None:
                return False, "That tune no longer exists.", -1
            if len(record.get("tunes", [])) >= MAX_TUNES_PER_CAR:
                return False, f"This car already has {MAX_TUNES_PER_CAR} tunes.", -1
            revision = int(source.get("revision", 1) or 1) + 1
            base = clean_name(f"{source.get('name', 'Tune')} V{revision}") or f"Tune V{revision}"
            names = {item.get("name", "").lower() for item in record.get("tunes", [])}
            candidate = base
            suffix = 2
            while candidate.lower() in names:
                # Trim the base, not the suffix: clean_name() cuts at MAX_NAME_LENGTH, and a
                # suffix cut off a 40-character name gave back the same name and looped forever.
                tail = f" ({suffix})"
                candidate = clean_name(base[: MAX_NAME_LENGTH - len(tail)] + tail)
                suffix += 1
            now = utc_now()
            clone = deepcopy(source)
            clone.update(
                {
                    "id": uuid.uuid4().hex,
                    "name": candidate,
                    "revision": revision,
                    "created_at": now,
                    "modified_at": now,
                    "logs": [],
                    "results": {},
                    "changes": [{"summary": f"Duplicated from {source.get('name', 'previous revision')}"}],
                }
            )
            record.setdefault("tunes", []).append(clone)
            record["active"] = len(record["tunes"]) - 1
            ok = self.save()
            return ok, (f'Created "{candidate}".' if ok else "Could not save the duplicated tune."), record["active"]

    def record_log(self, key: str, index: int, log_id: str, results: dict | None = None) -> bool:
        with self._lock:
            tune = self.tune(key, index)
            if tune is None:
                return False
            logs = tune.setdefault("logs", [])
            if log_id not in logs:
                logs.append(log_id)
            if isinstance(results, dict):
                tune["results"] = dict(results)
            tune["modified_at"] = utc_now()
            return self.save()

    def add_change(self, key: str, index: int, summary: str) -> bool:
        """Append a bounded human-readable reason to a tune revision."""
        with self._lock:
            tune = self.tune(key, index)
            if tune is None or not str(summary or "").strip():
                return False
            changes = tune.setdefault("changes", [])
            changes.append({"summary": str(summary).strip()[:200], "at": utc_now()})
            del changes[:-32]
            tune["modified_at"] = utc_now()
            return self.save()

    def delete_tune(self, key: str, index: int) -> tuple[bool, str]:
        with self._lock:
            record = self._car(key)
            if not record or not 0 <= index < len(record.get("tunes", [])):
                return False, "That tune no longer exists."
            removed = record["tunes"].pop(index)
            active = int(record.get("active", -1))
            if active == index:
                record["active"] = -1
            elif active > index:
                record["active"] = active - 1
            return self.save(), f'Deleted "{removed.get("name", "")}".'

    def delete_car(self, key: str) -> bool:
        with self._lock:
            if self._data.get("cars", {}).pop(key, None) is None:
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
