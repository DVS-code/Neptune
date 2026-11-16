"""Engine on/off, owned by the Engine page.

Switching uses the game's own ignition setter (`neptune/vehicle/ignition.py`), so engine sound,
lights and the rest of the car follow the game's own logic. While Neptune holds the engine off it
also holds drive power at 0, so the car cannot keep driving on a stopped engine. If the game
starts the engine itself (a respawn, a race start), Neptune lets go rather than fighting it.

Starting the engine from Neptune can blip the revs: the engine's live speed is carried up to a
share of the rev range, held briefly and eased back down, then released so the engine settles on
its own idle. (Raising the idle target alone does not work: the game slews it far too slowly.)
Verified in game: the engine follows and the car does not move. The timing was chosen by ear.
"""

from __future__ import annotations

import math
import threading
import time

from neptune.core import input as inp
from neptune.memory import remote
from neptune.ui.widgets.card import Banner, FieldRow, ToggleRow
from neptune.ui.widgets.controls import BindButton
from neptune.ui.widgets.sliderrow import SliderRow
from neptune.vehicle import ignition

BINDING = "engine.ignition"
HINT_IGNITION = (
    "Switches the engine off and on. While it is off the car has no drive power. Restore "
    "everything, and closing Neptune, start it again."
)


BLIP_RISE = 0.35  # seconds from idle to the peak
BLIP_HOLD = 0.30
BLIP_FALL = 0.70  # seconds back down to idle
BLIP_PERCENT_MIN, BLIP_PERCENT_MAX = 10.0, 80.0
HINT_BLIP = "Gives the revs a short blip when Neptune starts the engine, like a real start-up."
HINT_BLIP_PERCENT = "How high the blip goes, as a share of the rev range from idle to the limiter."


def blip_rpm(elapsed: float, idle: float, peak: float) -> float | None:
    """The engine speed `elapsed` seconds into a start-up blip, or None once it is over."""
    if elapsed < 0.0:
        return idle
    if elapsed < BLIP_RISE:
        t = elapsed / BLIP_RISE
        return idle + (peak - idle) * (1.0 - (1.0 - t) ** 2)  # fast in, eases to the top
    elapsed -= BLIP_RISE
    if elapsed < BLIP_HOLD:
        return peak
    elapsed -= BLIP_HOLD
    if elapsed < BLIP_FALL:
        t = elapsed / BLIP_FALL
        return peak + (idle - peak) * (0.5 - 0.5 * math.cos(math.pi * t))  # settles smoothly
    return None


class IgnitionController:
    def __init__(self, settings, stock_idle=None):
        self.settings = settings
        self._stock_idle = stock_idle or (lambda: None)  # Engine's captured stock idle, if any
        self._blip_requested = False
        self._blip: tuple[float, float, float, int] | None = None  # started, idle, peak, entity
        self._edge = inp.EdgeDetector()
        self._busy = threading.Lock()
        self._widgets: dict = {}
        self._message: tuple[str, str] | None = None
        self._live_on: bool | None = None
        self._forget()

    def _forget(self) -> None:
        self._entity = 0
        self._held_off = False
        self._stock_power: float | None = None

    # ------------------------------------------------------------------ lifecycle (Engine forwards)

    def on_car_changed(self) -> None:
        self._forget()

    def restore(self, vehicle) -> None:
        self._blip_requested = False
        self._end_blip(vehicle)
        if vehicle is not None and self._held_off and vehicle.entity == self._entity:
            self._switch(vehicle, True, blip=False)
        self._forget()

    def tick(self, vehicle) -> None:
        if vehicle is None:
            return
        if self._edge.pressed(self.settings.binding(BINDING)):
            self.toggle(vehicle)
        process, entity = vehicle.process, vehicle.entity
        self._live_on = ignition.engine_on(process, entity)
        self._tick_blip(vehicle)
        if not self._held_off:
            return
        if entity != self._entity:
            self._forget()  # a different car: the old one is not ours to write
            return
        if self._live_on:
            # The game started the engine itself: give the car its power back and let go.
            if self._stock_power is not None:
                ignition.set_drive_power(process, entity, self._stock_power)
            self._forget()
            return
        if ignition.drive_power(process, entity) not in (None, 0.0):
            ignition.set_drive_power(process, entity, 0.0)

    # ------------------------------------------------------------------ start-up blip

    def _blip_enabled(self) -> bool:
        return bool(self.settings.get("ignition_blip", True))

    def _blip_percent(self) -> float:
        try:
            value = float(self.settings.get("ignition_blip_percent", 35.0))
        except (TypeError, ValueError):
            value = 35.0
        return max(BLIP_PERCENT_MIN, min(BLIP_PERCENT_MAX, value))

    def _tick_blip(self, vehicle) -> None:
        if self._blip_requested:
            self._blip_requested = False
            if self._blip_enabled() and self._live_on:
                idle = self._stock_idle() or vehicle.idle_rpm
                top = vehicle.rev_ceiling or vehicle.redline
                if idle and top and top > idle:
                    peak = idle + (top - idle) * self._blip_percent() / 100.0
                    self._blip = (time.monotonic(), float(idle), float(peak), vehicle.entity)
        if self._blip is None:
            return
        started, idle, peak, entity = self._blip
        if vehicle.entity != entity:
            self._blip = None  # a different car: never write into it
            return
        target = blip_rpm(time.monotonic() - started, idle, peak) if self._live_on else None
        if target is None:
            self._end_blip(vehicle)
        else:
            vehicle.set_engine_speed_rpm(target)

    def _end_blip(self, vehicle) -> None:
        # Nothing to put back: the blip only drives the engine's own speed, and the engine
        # settles onto its idle by itself once Neptune stops writing.
        self._blip = None

    # ------------------------------------------------------------------ switching

    def toggle(self, vehicle) -> None:
        if vehicle is None:
            return
        on = ignition.engine_on(vehicle.process, vehicle.entity)
        if on is None:
            self._message = ("The car's engine state could not be read.", "warn")
            return
        self.set(vehicle, not on)

    def set(self, vehicle, on: bool) -> None:
        """Switch on a short-lived thread so the interface never waits on the game."""
        if vehicle is None or not self._busy.acquire(blocking=False):
            return

        def work():
            try:
                self._switch(vehicle, on)
            finally:
                self._busy.release()

        threading.Thread(target=work, name="neptune-ignition", daemon=True).start()

    def _switch(self, vehicle, on: bool, blip: bool = True) -> None:
        process, entity = vehicle.process, vehicle.entity
        try:
            if not on:
                power = ignition.drive_power(process, entity)
                if not self._held_off:
                    self._stock_power = power if power is not None else 1.0
                    self._entity = entity
                if ignition.set_engine(process, entity, False):
                    ignition.set_drive_power(process, entity, 0.0)
                    self._held_off = True
                    self._message = None
                else:
                    self._message = ("The game did not switch the engine off.", "warn")
            else:
                started = ignition.set_engine(process, entity, True)
                if self._stock_power is not None and entity == self._entity:
                    ignition.set_drive_power(process, entity, self._stock_power)
                if started:
                    self._forget()
                    self._message = None
                    self._blip_requested = blip
                else:
                    self._message = ("The game did not start the engine.", "warn")
        except (ignition.IgnitionError, remote.RemoteCallError) as error:
            self._message = (str(error), "warn")

    # ------------------------------------------------------------------ page

    def build_card(self, page, engine_module) -> None:
        card = page.add_card("Engine", "Switch the engine off and on.")
        row = ToggleRow("Engine running", True, hint=HINT_IGNITION)
        row.toggle.toggled_value.connect(lambda value: self._from_ui(engine_module, bool(value)))
        self._widgets["row"] = row
        card.add(row)
        button = BindButton(self.settings.binding(BINDING), settings=self.settings, key=BINDING)
        button.bound.connect(lambda binding: self.settings.set_binding(BINDING, binding))
        card.add(
            FieldRow(
                "Engine on/off control",
                button,
                hint="Toggles the engine while the game is focused.",
            )
        )
        blip = ToggleRow("Rev on start-up", self._blip_enabled(), hint=HINT_BLIP)
        blip.toggle.toggled_value.connect(
            lambda value: self.settings.remember("ignition_blip", bool(value))
        )
        card.add(blip)
        amount = SliderRow(
            "Start-up rev",
            BLIP_PERCENT_MIN,
            BLIP_PERCENT_MAX,
            self._blip_percent(),
            step=1.0,
            decimals=0,
            unit="%",
            hint=HINT_BLIP_PERCENT,
        )
        amount.changed.connect(
            lambda value: self.settings.remember("ignition_blip_percent", round(float(value)))
        )
        card.add(amount)
        banner = Banner("", "warn")
        banner.setVisible(False)
        self._widgets["banner"] = banner
        card.add(banner)

    def _from_ui(self, engine_module, on: bool) -> None:
        if self._live_on is not None and on != self._live_on:
            self.set(engine_module.vehicle, on)

    def refresh(self, vehicle) -> None:
        row, banner = self._widgets.get("row"), self._widgets.get("banner")
        if row is None or banner is None:
            return
        row.setEnabled(vehicle is not None and self._live_on is not None)
        if self._live_on is not None and row.value() != self._live_on:
            row.set_value(self._live_on)
        if self._message:
            banner.set(*self._message)
            banner.setVisible(True)
        else:
            banner.setVisible(False)
