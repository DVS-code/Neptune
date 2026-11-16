"""Animations: doors, panels, pop-ups, driver visibility, wipers and max detail.

Panel events go through the game's own animation setter (see `neptune/vehicle/animation.py`),
run on a worker thread so neither the interface nor the 100 Hz tick ever waits on the game.
Driver visibility, wipers and max detail are plain memory writes.

Every change is owned: the panels Neptune opened are closed again, and the driver, the
presentation flag, the wiper timing and the render mode are put back, on disable, on Restore
everything, and on exit. Nothing is written into a car or scene that has since been replaced.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time

from neptune.core import input as inp
from neptune.core.module import FeatureModule
from neptune.memory import offsets as O
from neptune.memory import remote
from neptune.ui.widgets.buttons import Button, PrimaryButton
from neptune.ui.widgets.card import Banner, FieldRow, StatStrip, ToggleRow, bind_progressive
from neptune.ui.widgets.controls import BindButton, Segmented
from neptune.ui.widgets.sliderrow import SliderRow
from neptune.vehicle import animation as anim
from neptune.vehicle import ignition, render

# key, label, event stems. Every stem fires as "<stem>_open" / "<stem>_close"; cars without that
# part simply ignore it, so variants are listed together rather than per car.
PARTS = (
    ("door_lf", "Left front door", ("doorLF",)),
    ("door_rf", "Right front door", ("doorRF",)),
    ("door_lr", "Left rear door", ("doorLR",)),
    ("door_rr", "Right rear door", ("doorRR",)),
    ("hood", "Hood", ("hood",)),
    ("trunk", "Trunk", ("trunk",)),
    ("roof", "Roof", ("roof",)),
    ("storage", "Storage", ("storage", "storageL", "storageM", "storageR")),
    ("aero", "Active aero", ("wing", "wingF")),
    ("vents", "Vents", ("vent",)),
    ("popups", "Pop-up headlights", ("headlightL", "headlightR", "headlights")),
)
PART_STEMS = {key: stems for key, _, stems in PARTS}
DOORS = ("door_lf", "door_rf", "door_lr", "door_rr")
WIPER_MODES = ("Game", "On")
WIPER_SPEED_MIN, WIPER_SPEED_MAX = 0.5, 3.0
MAX_DETAIL_INTERVAL = 0.5
WIPER_INTERVAL = 0.05

HINT_ENABLE = (
    "Lets Neptune open and close the car's doors and panels, hide the driver, run the wipers and "
    "switch max detail. Turning it off closes what Neptune opened and puts everything back."
)
HINT_PANELS = "Cars only move the parts they have. A part this car does not have stays still."
HINT_DRIVER = "Hides the driver and what they wear. Steering and pedals keep animating."
HINT_WIPERS = "On runs the wipers continuously, rain or not. Game leaves them to the weather."
HINT_WIPER_SPEED = "1.0 is the car's normal sweep. Higher is faster."
HINT_MAX_DETAIL = (
    "Renders your car in the game's full-detail mode while you drive in Free Roam. The game "
    "leaves it when you open Photo Mode or a menu; switch it on again afterwards."
)
NOTE_OFFLINE = "Attach to the game and get in a car to animate it."


def events_for(keys, opening: bool) -> list[str]:
    suffix = "open" if opening else "close"
    return [f"{stem}_{suffix}" for key in keys for stem in PART_STEMS[key]]


class _Worker:
    """One background thread that runs game calls in order."""

    def __init__(self):
        self._jobs: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="neptune-animations", daemon=True)
        self._thread.start()

    def submit(self, job) -> None:
        self._jobs.put(job)

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            with contextlib.suppress(Exception):
                job()

    def stop(self) -> None:
        self._jobs.put(None)


class AnimationsModule(FeatureModule):
    name = "animations"
    title = "Animations"
    subtitle = "Doors, panels, pop-ups, driver, wipers and max detail."
    icon = "animations.png"
    group = "Vehicle"
    order = 36

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._enabled = bool(settings.get("animations_enabled"))
        self._widgets: dict = {}
        self._controls_dirty = True
        self._message: tuple[str, str] | None = None
        self._worker = _Worker()
        self._edges = {key: inp.EdgeDetector() for key, _, _ in self._binding_specs()}
        self._open: dict[str, bool] = {key: False for key in PART_STEMS}
        self._driver_hidden = False
        self._wipers = "Game"
        self._wiper_speed = 1.0
        self._max_detail = False
        self._forget()

    # ------------------------------------------------------------------ ownership

    def _forget(self) -> None:
        """Drop ownership WITHOUT writing: the car or scene it referred to is gone."""
        self._entity = 0
        self._presentation_stock: int | None = None
        self._opened: set[str] = set()
        self._driver_address = 0
        self._driver_stock: bool | None = None
        self._wiper_stock_duration: float | None = None
        self._last_wiper = 0.0
        self._render_controller = 0
        self._render_owned = False
        self._render_resolving = False
        self._last_render = 0.0

    def _claim(self, entity: int) -> None:
        """Start owning writes on `entity`; anything owned on another car is dropped unwritten."""
        if entity != self._entity:
            self._forget()
            self._entity = entity

    def _owns_car(self, vehicle) -> bool:
        return vehicle is not None and vehicle.entity == self._entity

    # ------------------------------------------------------------------ lifecycle

    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle

    def on_car_changed(self, vehicle) -> None:
        self.vehicle = vehicle
        self._forget()
        self._open = {key: False for key in PART_STEMS}
        self._driver_hidden = False
        self._controls_dirty = True

    def on_car_reloaded(self, vehicle) -> None:
        self.vehicle = vehicle
        self._forget()
        self._open = {key: False for key in PART_STEMS}
        self._controls_dirty = True

    def on_detach(self) -> None:
        self.vehicle = None
        self._forget()

    def shutdown(self) -> None:
        self._worker.stop()

    def restore(self) -> None:
        vehicle = self.vehicle
        if self._owns_car(vehicle):
            self._release(vehicle)
        self._forget()
        self._open = {key: False for key in PART_STEMS}
        self._driver_hidden = False
        self._wipers = "Game"
        self._max_detail = False
        self._controls_dirty = True

    def reset_controls(self) -> None:
        self.restore()

    def _release(self, vehicle) -> None:
        """Put back everything this module changed on the car it still owns. Synchronous."""
        process, entity = vehicle.process, vehicle.entity
        if self._opened:
            with contextlib.suppress(Exception):
                self._pulse(process, entity, events_for(sorted(self._opened), False))
        if self._driver_address and self._driver_stock is not None:
            anim.set_driver_visible(process, self._driver_address, self._driver_stock)
        if self._wiper_stock_duration is not None:
            anim.set_wiper_duration(process, entity, self._wiper_stock_duration)
        if self._render_owned and self._render_controller:
            state = render.read_state(process, self._render_controller)
            if state is not None and state.settled and state.current == O.MaxDetail.DETAIL:
                render.request(process, self._render_controller, state, O.MaxDetail.FREE_ROAM)
        if self._presentation_stock is not None:
            anim.set_presentation(process, entity, self._presentation_stock)

    # ------------------------------------------------------------------ panel events

    def _pulse(self, process, entity, events: list[str]) -> None:
        """Assert the events for about one update, then release them, as the game does."""
        anim.fire(process, entity, events, True)
        time.sleep(O.Animation.PULSE_SECONDS)
        anim.fire(process, entity, events, False)

    def _move(self, keys, opening: bool) -> None:
        """Queue opening or closing `keys` on the current player car."""
        keys = [key for key in keys if key in PART_STEMS]
        vehicle = self.vehicle
        if not keys or vehicle is None or not self._enabled:
            return
        for key in keys:
            self._open[key] = opening
        self._controls_dirty = True

        def job():
            process, entity = vehicle.process, vehicle.entity
            if self.vehicle is None or self.vehicle.entity != entity:
                return
            try:
                if process.u32(entity + O.Ignition.CAR_MODE) != 0:
                    raise anim.AnimationError("Animations only work on your own car.")
                self._claim(entity)
                if self._presentation_stock is None:
                    stock = anim.presentation(process, entity)
                    if stock is None:
                        raise anim.AnimationError("The car's presentation state could not be read.")
                    self._presentation_stock = stock
                anim.set_presentation(process, entity, 1)
                self._pulse(process, entity, events_for(keys, opening))
                if opening:
                    self._opened.update(keys)
                else:
                    self._opened.difference_update(keys)
                self._message = None
            except (anim.AnimationError, remote.RemoteCallError) as error:
                self._message = (str(error), "warn")

        self._worker.submit(job)

    def _toggle_part(self, key: str) -> None:
        self._move([key], not self._open.get(key, False))

    def _set_part(self, key: str, opening: bool) -> None:
        if self._open.get(key) != opening:
            self._move([key], opening)

    def _all(self, opening: bool) -> None:
        self._move([key for key in PART_STEMS], opening)

    # ------------------------------------------------------------------ tick

    def tick(self, vehicle) -> None:
        self.vehicle = vehicle
        if vehicle is None:
            return
        self._poll_bindings()
        if not self._enabled:
            return
        process, entity = vehicle.process, vehicle.entity
        if process.u32(entity + O.Ignition.CAR_MODE) != 0:
            return
        if self._driver_hidden or self._wipers != "Game" or self._max_detail:
            self._claim(entity)
        now = time.monotonic()
        self._tick_driver(process, entity)
        if now - self._last_wiper >= WIPER_INTERVAL:
            self._last_wiper = now
            self._tick_wipers(process, entity)
        if now - self._last_render >= MAX_DETAIL_INTERVAL:
            self._last_render = now
            self._tick_max_detail(process)

    def _tick_driver(self, process, entity) -> None:
        if not self._driver_hidden and self._driver_stock is None:
            return
        if not self._driver_address:
            try:
                self._driver_address = anim.driver_field(process, entity)
            except anim.AnimationError as error:
                self._message = (str(error), "warn")
                self._driver_hidden = False
                self._controls_dirty = True
                return
        current = anim.driver_visible(process, self._driver_address)
        if current is None:
            return
        if self._driver_hidden:
            if self._driver_stock is None:
                self._driver_stock = current
            if current:
                anim.set_driver_visible(process, self._driver_address, False)
        elif self._driver_stock is not None:
            anim.set_driver_visible(process, self._driver_address, self._driver_stock)
            self._driver_stock = None

    def _tick_wipers(self, process, entity) -> None:
        if self._wipers == "Game":
            if self._wiper_stock_duration is not None:
                anim.set_wiper_duration(process, entity, self._wiper_stock_duration)
                self._wiper_stock_duration = None
            return
        state = anim.wiper_state(process, entity)
        if state is None or not ignition.engine_on(process, entity):
            return
        if (
            self._wiper_stock_duration is None
            and state["state"] < O.Wipers.REQUEST
            and state["duration"] > 0.1
        ):
            self._wiper_stock_duration = state["duration"]
        parked = state["state"] < O.Wipers.REQUEST and abs(state["phase"]) <= 0.001
        if not parked:
            return
        if abs(self._wiper_speed - 1.0) < 0.01 or self._wiper_stock_duration is None:
            anim.request_sweep(process, entity)
        else:
            anim.request_sweep(process, entity, self._wiper_stock_duration / self._wiper_speed)

    def _tick_max_detail(self, process) -> None:
        if not self._max_detail and not self._render_owned:
            return
        if not self._render_controller:
            if not self._render_resolving:
                self._render_resolving = True

                def resolve():
                    try:
                        self._render_controller = (
                            render.controller(process, render.resolve_global(process)) or 0
                        )
                        if not self._render_controller:
                            raise render.RenderError(
                                "This game build's max detail is not supported yet."
                            )
                    except render.RenderError as error:
                        self._message = (str(error), "warn")
                        self._max_detail = False
                        self._controls_dirty = True
                    finally:
                        self._render_resolving = False

                self._worker.submit(resolve)
            return
        state = render.read_state(process, self._render_controller)
        if state is None or not state.settled:
            return
        M = O.MaxDetail
        if self._max_detail:
            if self._render_owned and state.current != M.DETAIL:
                # The game left detail mode itself (Photo Mode, a menu): it owns the mode now.
                self._render_owned = False
                self._max_detail = False
                self._controls_dirty = True
            elif not self._render_owned:
                if state.current == M.FREE_ROAM and state.option == 0:
                    self._render_owned = render.request(
                        process, self._render_controller, state, M.DETAIL
                    )
                elif state.current != M.DETAIL:
                    self._message = (
                        "Max detail can only be switched on while driving in Free Roam.",
                        "info",
                    )
                    self._max_detail = False
                    self._controls_dirty = True
        elif self._render_owned:
            if state.current == M.DETAIL:
                render.request(process, self._render_controller, state, M.FREE_ROAM)
            self._render_owned = False

    # ------------------------------------------------------------------ keybinds

    @staticmethod
    def _binding_specs() -> list[tuple[str, str, str]]:
        """Every animation is bindable: one key per part, plus the groups and switches."""
        specs = [
            (
                "animations.all",
                "Open/close everything",
                "Opens every panel, or closes them all again.",
            ),
            ("animations.doors", "All doors", "Opens or closes all doors together."),
        ]
        specs += [
            (f"animations.{key}", label, f"Opens or closes the {label.lower()}.")
            for key, label, _ in PARTS
        ]
        specs += [
            ("animations.driver", "Hide driver", "Hides or shows the driver."),
            ("animations.wipers", "Wipers", "Switches the wipers on, or back to the game."),
            ("animations.max_detail", "Max detail", "Switches max detail on or off."),
        ]
        return specs

    def bindings(self) -> list[dict]:
        return [
            {"key": k, "label": label, "description": d} for k, label, d in self._binding_specs()
        ]

    def _poll_bindings(self) -> None:
        for key, _, _ in self._binding_specs():
            if self._edges[key].pressed(self.settings.binding(key)) and self._enabled:
                self._on_binding(key)

    def _on_binding(self, key: str) -> None:
        if key == "animations.all":
            self._all(not any(self._open.values()))
        elif key == "animations.doors":
            self._move(DOORS, not any(self._open[k] for k in DOORS))
        elif key.split(".", 1)[1] in PART_STEMS:
            self._toggle_part(key.split(".", 1)[1])
        elif key == "animations.driver":
            self._set_driver_hidden(not self._driver_hidden)
        elif key == "animations.wipers":
            self._set_wipers("Game" if self._wipers == "On" else "On")
        elif key == "animations.max_detail":
            self._set_max_detail(not self._max_detail)

    # ------------------------------------------------------------------ state changes (UI)

    def _set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self.settings.remember("animations_enabled", enabled)
        if not enabled:
            self.restore()
        self._controls_dirty = True

    def _set_driver_hidden(self, hidden: bool) -> None:
        self._driver_hidden = bool(hidden)
        self._controls_dirty = True

    def _set_wipers(self, mode: str) -> None:
        if mode in WIPER_MODES:
            self._wipers = mode
            self._controls_dirty = True

    def _set_wiper_speed(self, speed: float) -> None:
        self._wiper_speed = max(WIPER_SPEED_MIN, min(WIPER_SPEED_MAX, float(speed)))

    def _set_max_detail(self, on: bool) -> None:
        self._max_detail = bool(on)
        self._message = None
        self._controls_dirty = True

    # ------------------------------------------------------------------ page

    def build_page(self, page) -> None:
        w = self._widgets
        enable_card = page.add_card("Animations")
        enabled = ToggleRow("Enable animation controls", self._enabled, hint=HINT_ENABLE)
        enabled.toggle.toggled_value.connect(self._set_enabled)
        w["enabled"] = enabled
        enable_card.add(enabled)

        panels = page.add_card("Doors and panels", HINT_PANELS)
        for key, label, _ in PARTS:
            row = ToggleRow(label, False)
            row.toggle.toggled_value.connect(lambda value, k=key: self._set_part(k, bool(value)))
            w[f"part_{key}"] = row
            panels.add(row)
        open_all, close_all = PrimaryButton("Open everything"), Button("Close everything")
        open_all.clicked.connect(lambda: self._all(True))
        close_all.clicked.connect(lambda: self._all(False))
        panels.add(open_all)
        panels.add(close_all)

        driver_card = page.add_card("Driver")
        driver = ToggleRow("Hide driver", False, hint=HINT_DRIVER)
        driver.toggle.toggled_value.connect(self._set_driver_hidden)
        w["driver"] = driver
        driver_card.add(driver)

        wiper_card = page.add_card("Wipers")
        wipers = Segmented(list(WIPER_MODES), self._wipers)
        wipers.changed.connect(self._set_wipers)
        w["wipers"] = wipers
        wiper_card.add(FieldRow("Wipers", wipers, hint=HINT_WIPERS))
        speed = SliderRow(
            "Wiper speed",
            WIPER_SPEED_MIN,
            WIPER_SPEED_MAX,
            1.0,
            step=0.1,
            decimals=1,
            unit="x",
            hint=HINT_WIPER_SPEED,
        )
        speed.changed.connect(self._set_wiper_speed)
        w["wiper_speed"] = speed
        wiper_card.add(speed)

        detail_card = page.add_card("Max detail")
        detail = ToggleRow("Max detail", False, hint=HINT_MAX_DETAIL)
        detail.toggle.toggled_value.connect(self._set_max_detail)
        w["max_detail"] = detail
        detail_card.add(detail)

        keys = page.add_card("Controls", "Shortcuts work while the game is focused.")
        for key, label, hint in self._binding_specs():
            button = BindButton(self.settings.binding(key), settings=self.settings, key=key)
            button.bound.connect(lambda binding, k=key: self.settings.set_binding(k, binding))
            keys.add(FieldRow(label, button, hint=hint))

        status_card = page.add_card("Status")
        stats = StatStrip()
        stats.add("open", "Open parts")
        stats.add("driver", "Driver")
        stats.add("wipers", "Wipers")
        stats.add("detail", "Max detail")
        w["stats"] = stats
        status_card.add(stats)
        banner = Banner(NOTE_OFFLINE, "info")
        w["banner"] = banner
        page.add(banner)
        bind_progressive(enabled, panels, driver_card, wiper_card, detail_card, keys, status_card)

    def _sync_controls(self) -> None:
        w = self._widgets
        if "enabled" in w:
            w["enabled"].set_value(self._enabled)
        for key in PART_STEMS:
            row = w.get(f"part_{key}")
            if row is not None:
                row.set_value(self._open.get(key, False))
        if "driver" in w:
            w["driver"].set_value(self._driver_hidden)
        if "wipers" in w:
            w["wipers"].set_value(self._wipers)
        if "max_detail" in w:
            w["max_detail"].set_value(self._max_detail)

    def refresh(self, vehicle) -> None:
        if self._controls_dirty:
            self._controls_dirty = False
            self._sync_controls()
        stats, banner = self._widgets.get("stats"), self._widgets.get("banner")
        if stats is None or banner is None:
            return
        if vehicle is None:
            stats.reset()
            banner.set(NOTE_OFFLINE, "info")
            banner.setVisible(True)
            return
        stats.set("open", str(sum(self._open.values())))
        stats.set("driver", "Hidden" if self._driver_hidden else "Visible")
        stats.set(
            "wipers", self._wipers if self._wipers == "Game" else f"On ({self._wiper_speed:.1f}x)"
        )
        stats.set("detail", "On" if self._render_owned else "Off")
        if not self._enabled:
            banner.set("Animation controls are off. Turn them on to animate this car.", "info")
        elif self._message:
            banner.set(*self._message)
        else:
            banner.setVisible(False)
            return
        banner.setVisible(True)

    # ------------------------------------------------------------------ presets

    def save_state(self) -> dict:
        return {
            "enabled": self._enabled,
            "driver_hidden": self._driver_hidden,
            "wipers": self._wipers,
            "wiper_speed": self._wiper_speed,
        }

    def load_state(self, data: dict) -> None:
        data = data if isinstance(data, dict) else {}
        if not data:
            return
        self._set_enabled(bool(data.get("enabled", self._enabled)))
        self._driver_hidden = bool(data.get("driver_hidden", False))
        if data.get("wipers") in WIPER_MODES:
            self._wipers = data["wipers"]
        with contextlib.suppress(TypeError, ValueError):
            self._set_wiper_speed(float(data.get("wiper_speed", 1.0)))
        self._controls_dirty = True
