"""Runtime control of the game's normalized launch-RPM window."""

from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass

from neptune.memory.process import Process
from neptune.memory.scanner import ModuleImage, SignatureError

LC_DEFAULT_MIN_NORM = 0.30
LC_DEFAULT_MAX_NORM = 0.80
LC_NORM_MIN = 0.0
LC_NORM_MAX = 1.0
LC_MIN_GAP = 0.01
LC_REAPPLY_SECONDS = 0.25

# The first four fields are stable in the current build; the two normalized RPM
# bounds are deliberately wildcards because the game can change its live copy.
LC_SIGNATURES = (
    "00 00 80 3F 00 00 00 40 0A D7 23 3C 00 00 F0 41 ?? ?? ?? ?? ?? ?? ?? ??",
    "00 00 80 3F 00 00 00 40 0A D7 23 3C 00 00 C8 42 ?? ?? ?? ?? ?? ?? ?? ??",
)


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value))


@dataclass(frozen=True)
class LaunchControlAddresses:
    """The two module-backed fields consumed by launch-mode physics."""

    block: int
    block_rva: int
    min_norm: int
    max_norm: int

    @property
    def rva(self) -> int:
        return self.block_rva


def resolve_launch_control(process: Process) -> LaunchControlAddresses:
    """Find the module-backed launch block in the current executable image.

    Fixed RVAs break whenever a game update moves the block, so it is found
    by signature instead. Restricting the search to the main module avoids the
    unrelated low-memory mirror and gives the physics reader the copy it uses.
    """

    image = ModuleImage.capture(process)
    candidates: list[tuple[int, float, float]] = []
    for signature in LC_SIGNATURES:
        for address in image.find_all(signature, limit=32):
            offset = address - image.base
            try:
                values = struct.unpack_from("<6f", image.data, offset)
            except (struct.error, ValueError):
                continue
            min_norm, max_norm = values[4:6]
            if (
                _finite(min_norm)
                and _finite(max_norm)
                and 0.0 <= min_norm <= 1.5
                and 0.0 <= max_norm <= 1.5
                and min_norm < max_norm
            ):
                candidates.append((address, min_norm, max_norm))

    if not candidates:
        raise SignatureError("The current game build has no supported launch-RPM block.")

    block, _min_norm, _max_norm = candidates[0]
    return LaunchControlAddresses(
        block=block,
        block_rva=block - image.base,
        min_norm=block + 16,
        max_norm=block + 20,
    )


class LaunchControlController:
    """Own, apply, and restore the live launch-mode RPM bounds."""

    def __init__(self) -> None:
        self.process: Process | None = None
        self.addresses: LaunchControlAddresses | None = None
        self.stock_min_norm: float | None = None
        self.stock_max_norm: float | None = None
        self.min_norm = LC_DEFAULT_MIN_NORM
        self.max_norm = LC_DEFAULT_MAX_NORM
        self.enabled = False
        self.error: str | None = None
        self._last_write = 0.0

    @property
    def ready(self) -> bool:
        return (
            self.process is not None
            and self.addresses is not None
            and self.stock_min_norm is not None
            and self.stock_max_norm is not None
        )

    @property
    def stock_values(self) -> tuple[float, float] | None:
        if self.stock_min_norm is None or self.stock_max_norm is None:
            return None
        return self.stock_min_norm, self.stock_max_norm

    def attach(self, process: Process) -> bool:
        """Resolve and capture stock values for a newly attached process."""

        if self.process is not None and self.process.pid == process.pid:
            # Same game: keep the stock values captured first. After a detach the runtime
            # hands over a new handle and closes the old one, so always take the new one.
            self.process = process
            if self.ready:
                return True
            if self.error is not None:
                return False

        self.restore()
        self.clear()
        self.process = process
        try:
            self.addresses = resolve_launch_control(process)
            minimum = process.f32(self.addresses.min_norm)
            maximum = process.f32(self.addresses.max_norm)
            if not (
                _finite(minimum)
                and _finite(maximum)
                and 0.0 <= minimum <= 1.5
                and 0.0 <= maximum <= 1.5
                and minimum < maximum
            ):
                raise SignatureError("The launch-RPM fields failed live validation.")
            self.stock_min_norm = float(minimum)
            self.stock_max_norm = float(maximum)
            self.error = None
            return True
        except Exception as error:
            self.addresses = None
            self.stock_min_norm = None
            self.stock_max_norm = None
            self.error = str(error)
            return False

    def set_targets(self, minimum: float, maximum: float) -> None:
        """Set normalized targets while preserving a valid ordered window."""

        minimum = max(LC_NORM_MIN, min(LC_NORM_MAX - LC_MIN_GAP, float(minimum)))
        maximum = max(LC_NORM_MIN + LC_MIN_GAP, min(LC_NORM_MAX, float(maximum)))
        if maximum <= minimum:
            maximum = min(LC_NORM_MAX, minimum + LC_MIN_GAP)
            minimum = min(minimum, maximum - LC_MIN_GAP)
        if abs(self.min_norm - minimum) < 1e-7 and abs(self.max_norm - maximum) < 1e-7:
            return
        self.min_norm = minimum
        self.max_norm = maximum
        self._last_write = 0.0

    def set_enabled(self, enabled: bool) -> bool:
        self.enabled = bool(enabled)
        if not self.enabled:
            return self.restore()
        self._last_write = 0.0
        return self.apply()

    def tick(self, now: float | None = None) -> bool:
        # After a failed write, wait for the user to re-enable or move a slider instead of
        # retrying the write and the restore on every frame.
        if not self.enabled or not self.ready or self.error is not None:
            return False
        now = time.monotonic() if now is None else float(now)
        if now - self._last_write < LC_REAPPLY_SECONDS:
            return True
        return self.apply(now)

    def apply(self, now: float | None = None) -> bool:
        if not self.ready or self.process is None or self.addresses is None:
            return False

        minimum = self.process.set_f32(self.addresses.min_norm, self.min_norm)
        maximum = self.process.set_f32(self.addresses.max_norm, self.max_norm)
        if not (minimum and maximum):
            self.error = "The launch-RPM write was rejected by the game process."
            self.restore()
            return False

        read_min = self.process.f32(self.addresses.min_norm)
        read_max = self.process.f32(self.addresses.max_norm)
        if (
            read_min is None
            or read_max is None
            or abs(read_min - self.min_norm) > 1e-5
            or abs(read_max - self.max_norm) > 1e-5
        ):
            self.error = "The launch-RPM write did not verify in the game process."
            self.restore()
            return False

        self.error = None
        self._last_write = time.monotonic() if now is None else float(now)
        return True

    def restore(self) -> bool:
        """Return both fields to the values captured at attach time."""

        if not self.ready or self.process is None or self.addresses is None:
            return False
        if not self.process.alive:
            return False
        minimum = self.process.set_f32(self.addresses.min_norm, self.stock_min_norm)
        maximum = self.process.set_f32(self.addresses.max_norm, self.stock_max_norm)
        if minimum and maximum:
            self._last_write = 0.0
        return bool(minimum and maximum)

    def clear(self) -> None:
        """Forget a process without touching a process that may already be gone."""

        self.process = None
        self.addresses = None
        self.stock_min_norm = None
        self.stock_max_norm = None
        self.enabled = False
        self.error = None
        self._last_write = 0.0
