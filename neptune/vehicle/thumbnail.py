from __future__ import annotations

import os
import threading
import zipfile

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

from neptune.utils import swatchbin

# RC0 alone covers most of the roster (619/660); RC1-4 hold the rest (mostly newer
# additions). Together they're the full 660/660 — verified against the game's own
# Data_Car table.
RC_ZIP_NAMES = ("RC0.zip", "RC1.zip", "RC2.zip", "RC3.zip", "RC4.zip")
RC_SUBDIR = ("media", "Stripped")


def find(car_id: int | None, process=None) -> bytes | None:
    """PNG-encoded image bytes for this car, or None if it isn't available."""
    if car_id is None or process is None:
        return None
    exe_path = process.executable_path
    if not exe_path:
        return None
    stripped_dir = os.path.join(os.path.dirname(exe_path), *RC_SUBDIR)

    name = f"thumbnail_{car_id}_big.swatchbin"
    raw = None
    for zip_name in RC_ZIP_NAMES:
        raw = _read_entry(os.path.join(stripped_dir, zip_name), name)
        if raw is not None:
            break
    if raw is None:
        return None

    try:
        width, height, bgra = swatchbin.decode(raw)
    except swatchbin.SwatchbinError:
        return None

    image = QImage(bgra, width, height, QImage.Format_ARGB32)
    if image.isNull():
        return None
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        return None
    return bytes(buffer.data())


def find_async(car_id: int | None, process, callback) -> None:
    """Run `find` off the interface thread and hand the result back."""

    def run():
        try:
            callback(car_id, find(car_id, process))
        except Exception:
            pass

    threading.Thread(target=run, daemon=True, name="neptune-thumbnail").start()


def _read_entry(zip_path: str, name: str) -> bytes | None:
    if not os.path.isfile(zip_path):
        return None
    try:
        with zipfile.ZipFile(zip_path) as archive:
            # entries are inconsistently cased ("thumbnail_...big" vs "Thumbnail_...Big")
            actual = next((n for n in archive.namelist() if n.lower() == name.lower()), None)
            return archive.read(actual) if actual is not None else None
    except (OSError, zipfile.BadZipFile):
        return None
