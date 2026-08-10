"""Where Neptune keeps its data and bundled assets."""
from __future__ import annotations

import os
import sys

APP_DIR_NAME = 'Neptune'


def _frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def app_dir() -> str:
    """The directory the application is running from."""
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_dir() -> str:
    """Where settings, presets and maps are written."""
    portable = os.path.join(app_dir(), 'data')
    try:
        os.makedirs(portable, exist_ok=True)
        probe = os.path.join(portable, '.writable')
        with open(probe, 'w', encoding='utf-8') as handle:
            handle.write('')
        os.remove(probe)
        return portable
    except OSError:
        pass

    roaming = os.environ.get('APPDATA') or os.path.expanduser('~')
    fallback = os.path.join(roaming, APP_DIR_NAME)
    os.makedirs(fallback, exist_ok=True)
    return fallback


def preset_dir() -> str:
    directory = os.path.join(data_dir(), 'presets')
    os.makedirs(directory, exist_ok=True)
    return directory


def asset(name: str) -> str | None:
    """Locate a bundled asset, working from source and from a built executable."""
    roots = []
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        roots += [os.path.join(bundle, 'assets'), bundle]
    roots += [os.path.join(app_dir(), 'assets'), app_dir()]
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate):
            return candidate
    return None
