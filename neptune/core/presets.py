"""Named setups covering every module a per-car tune does not own."""

from __future__ import annotations

import json
import os
import re

from neptune.core import paths
from neptune.core.maps import TUNED_MODULES
from neptune.core.module import ModuleRegistry

MAX_NAME_LENGTH = 48
_UNSAFE = re.compile(r"[^A-Za-z0-9 _\-()]+")


def clean_name(name: str) -> str:
    return _UNSAFE.sub("", (name or "").strip())[:MAX_NAME_LENGTH]


def list_presets() -> list[str]:
    try:
        return sorted(
            entry[:-5] for entry in os.listdir(paths.preset_dir()) if entry.endswith(".json")
        )
    except OSError:
        return []


def save_preset(name: str, registry: ModuleRegistry) -> tuple[bool, str]:
    cleaned = clean_name(name)
    if not cleaned:
        return False, "Enter a name for this preset."

    data = {}
    for module in registry:
        if module.name in TUNED_MODULES:
            continue
        try:
            state = module.save_state()
        except Exception:
            continue
        if state:
            data[module.name] = state

    try:
        target = os.path.join(paths.preset_dir(), cleaned + ".json")
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    except OSError:
        return False, "Could not write the preset file."
    return True, f'Saved "{cleaned}".'


def load_preset(name: str, registry: ModuleRegistry) -> tuple[bool, str]:
    cleaned = clean_name(name)
    target = os.path.join(paths.preset_dir(), cleaned + ".json")
    if not os.path.exists(target):
        return False, "That preset no longer exists."

    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return False, "Could not read the preset file."
    if not isinstance(data, dict):
        return False, "That preset file is not valid."

    for key, state in data.items():
        if key in TUNED_MODULES:
            continue
        module = registry.get(key)
        if module is None:
            continue
        try:
            module.load_state(state)
        except Exception:
            continue
    return True, f'Loaded "{cleaned}".'


def delete_preset(name: str) -> tuple[bool, str]:
    cleaned = clean_name(name)
    target = os.path.join(paths.preset_dir(), cleaned + ".json")
    if not os.path.exists(target):
        return False, "That preset no longer exists."
    try:
        os.remove(target)
    except OSError:
        return False, "Could not delete the preset file."
    return True, f'Deleted "{cleaned}".'
