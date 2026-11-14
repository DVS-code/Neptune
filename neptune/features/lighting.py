"""Lighting: light switches, signals, fades and lamp colours for the player's car.

All three levers are documented in `neptune/vehicle/lamps.py`:

* light switches (headlights, DRL, high beam, fog, sidelights, brake lights) write the
  lighting input bytes the game reads every frame;
* signals and the independent front/rear lamp switches remap one operand of the affected
  lamp programs, and fades set the game's own per-channel output smoothing;
* colours write the RGB tint (and, for strobe/rewiring, the channel) of each lamp material's
  GPU-mapped constant buffer.

Every write is owned: the stock value is captured before the first write and written back on
restore, on disable, and when a control returns to "Game". Records are dropped, never
written, once the car or its render scene is rebuilt, because the old addresses are then
somebody else's memory.
"""

from __future__ import annotations

import colorsys
import contextlib
import copy
import math
import time

from PySide6.QtGui import QColor
from qfluentwidgets import ColorPickerButton, ComboBox

from neptune.core import input as inp
from neptune.core.module import FeatureModule
from neptune.memory import offsets as O
from neptune.ui.widgets.buttons import Button, PrimaryButton
from neptune.ui.widgets.card import Banner, FieldRow, StatStrip, ToggleRow, bind_progressive
from neptune.ui.widgets.controls import BindButton, Segmented
from neptune.ui.widgets.sliderrow import SliderRow
from neptune.vehicle import lamps

L = O.Lighting
R = O.LampRegister

FLAG_INTERVAL = 0.05  # light switches are re-asserted at 20 Hz
VERIFY_INTERVAL = 1.0  # graph/scene identity and a full re-assert of owned writes
COLOUR_INTERVAL = 1.0 / 30.0  # animated colours
RESCAN_BACKOFF = 5.0
SAVE_DELAY = 0.8

SWITCH = ("Game", "On", "Off")
FOLLOW = ("Follow", "On", "Off")
MODES = ("Stock", "Colour", "Rainbow", "Cycle")
STROBES = ("Off", "Native", "Custom")
UNWIRED = ("Off", "Indicators", "Hazard", "DRL", "Brake", "Tail")

SIGNAL_CHANNELS = frozenset({20, 21, 22, *range(40, 46)})
FLASH_CHANNELS = frozenset(range(49, 53))
FRONT_LAMPS = frozenset({13, 17, 71})
REAR_LAMPS = frozenset({32, 47, 58, 59, 69})
UNWIRED_CHANNEL = 70

# Colour groups, keyed by the channel a lamp MATERIAL lights from.
GROUPS = (
    ("drl", "Daytime running lights", frozenset({15, 38, 39, 60, 61, 62, 63, 64})),
    ("headlights", "Headlights", frozenset({13, 17, 37, 71})),
    (
        "tail",
        "Tail and brake lights",
        frozenset({0, 19, 32, 33, 34, 35, 36, 53, 54, 56, 57, 58, 59, 69}),
    ),
    ("reverse", "Reverse lights", frozenset({1, 55})),
    ("indicators", "Indicators", frozenset({20, 21, 22, *range(40, 46)})),
    ("fog", "Fog lights", frozenset({18, 48})),
    ("sidelights", "Sidelights and markers", frozenset({46, 47, 65, 66, 67, 68, 72})),
    ("unwired", "Unwired lamps", frozenset({UNWIRED_CHANNEL})),
)
GROUP_TITLES = {key: title for key, title, _ in GROUPS}

FLAG_SWITCHES = (
    ("high_beam", L.HIGH_BEAM, "High beam", "Main-beam headlights."),
    ("fog", L.FOG, "Fog lights", "Front and rear fog lamps, where the car has them."),
    ("sidelights", L.SIDELIGHTS, "Sidelights", "Parking and position lights."),
    ("brake_lights", L.BRAKE_FORCE, "Brake lights", "Holds the brake lights on without braking."),
)
SIGNALS = (
    ("hazard", R.HAZARD, "Hazards"),
    ("left", R.LEFT, "Left indicator"),
    ("right", R.RIGHT, "Right indicator"),
)

DEFAULT_COLOUR = {
    "mode": "Stock",
    "rgb": [1.0, 1.0, 1.0],
    "rgb2": [0.0, 0.3, 1.0],
    "brightness": 1.0,
    "speed": 0.25,
    "strobe": "Off",
    "strobe_hz": 4.0,
}
DEFAULT_STATE = {
    "headlights": "Game",
    "drl": "Game",
    "high_beam": False,
    "fog": False,
    "sidelights": False,
    "brake_lights": False,
    "hazard": False,
    "left": False,
    "right": False,
    "front": "Follow",
    "rear": "Follow",
    "fade": 0.0,
    "fade_signals": False,
    "unwired": "Off",
    "colours": {key: dict(DEFAULT_COLOUR) for key, _, _ in GROUPS},
}
TRANSIENT_KEYS = ("hazard", "left", "right")

HINT_ENABLE = (
    "Lets Neptune drive the car's lights. Turning this off returns every light, signal, fade and "
    "colour to the game."
)
HINT_HEADLIGHTS = "Game follows the time of day. On or Off takes manual control of the headlights."
HINT_FRONT = (
    "Front headlamps independent of the rear lights. Uses the engine-running signal, so it needs "
    "the engine on."
)
HINT_REAR = "Rear running lights independent of the headlights. Needs the engine on."
HINT_FADE = "How long lights take to fade on and off. 0 is the game's instant switching."
HINT_FADE_SIGNALS = "Also soften indicators and hazards. Off keeps them crisp."
HINT_UNWIRED = (
    "Lamps this car authors but never lights (channel 'Null'). Rear turn signals, fog lamps and side "
    "markers are often wired this way. Pick what should light them."
)
HINT_MODE = (
    "Stock keeps the authored colour. Colour sets one colour. Rainbow sweeps the hue. "
    "Cycle blends between two colours."
)
HINT_BRIGHTNESS = "Emission strength. Above 100% the lamp glows brighter than stock."
HINT_STROBE = "Native uses the game's flashing pattern (needs the engine on). Custom flashes at the rate below."
NOTE_OFFLINE = "Attach to the game and get in a car to control its lights."


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _rgb_list(value, fallback) -> list[float]:
    try:
        values = [float(v) for v in value][:3]
        if len(values) == 3 and all(math.isfinite(v) for v in values):
            return [_clamp(v, 0.0, 1.0) for v in values]
    except (TypeError, ValueError):
        pass
    return list(fallback)


def colour_at(config: dict, now: float, stock_rgb) -> tuple[float, float, float] | None:
    """The RGB a group's lamps should show at `now`, or None to leave them stock."""
    mode = config.get("mode", "Stock")
    if (
        mode == "Stock"
        and config.get("strobe") != "Custom"
        and config.get("brightness", 1.0) == 1.0
    ):
        return None
    speed = _clamp(float(config.get("speed", 0.25)), 0.01, 5.0)
    if mode == "Colour":
        rgb = config.get("rgb", [1.0, 1.0, 1.0])
    elif mode == "Rainbow":
        rgb = colorsys.hsv_to_rgb((now * speed) % 1.0, 1.0, 1.0)
    elif mode == "Cycle":
        blend = 0.5 - 0.5 * math.cos(2.0 * math.pi * now * speed)
        a, b = config.get("rgb", [1, 1, 1]), config.get("rgb2", [0, 0, 1])
        rgb = [a[i] + (b[i] - a[i]) * blend for i in range(3)]
    else:
        rgb = stock_rgb
    brightness = _clamp(float(config.get("brightness", 1.0)), 0.0, O.LampShader.MAX_CHANNEL_VALUE)
    rgb = [_clamp(v * brightness, 0.0, O.LampShader.MAX_CHANNEL_VALUE) for v in rgb]
    if config.get("strobe") == "Custom":
        hz = _clamp(float(config.get("strobe_hz", 4.0)), 0.2, 30.0)
        if (now * hz) % 1.0 >= 0.5:
            rgb = [0.0, 0.0, 0.0]
    return (rgb[0], rgb[1], rgb[2])


def group_of(channel: int) -> str | None:
    for key, _, channels in GROUPS:
        if channel in channels:
            return key
    return None


SIDED_PARTS = ("light", "lamp", "marker", "markers", "signal", "indicator", "mirror", "repeater")


def lamp_side(model: str) -> str | None:
    """'left'/'right' from a sided model name such as taillightl_a / taillightr_a.

    Only a trailing l/r after a lamp-part word counts, so bumperr_a (rear bumper) or
    taillightchmsl_a (centre brake light) are not mistaken for one side."""
    stem = model.lower().replace("\\", "/").split("/")[-1].split("_")[0]
    for suffix, side in (("l", "left"), ("r", "right")):
        if stem.endswith(suffix) and stem[:-1].endswith(SIDED_PARTS):
            return side
    return None


def unwired_channel(choice: str, model: str) -> int | None:
    """The channel an unwired lamp should light from, by its side of the car."""
    side = lamp_side(model)
    if choice == "Indicators":
        return 21 if side == "left" else 20 if side == "right" else 22
    return {"Hazard": 22, "DRL": 15, "Brake": 0, "Tail": 32}.get(choice)


def merge_state(data) -> dict:
    """A complete, validated state from saved data that may be partial or stale."""
    state = copy.deepcopy(DEFAULT_STATE)
    if not isinstance(data, dict):
        return state
    for key in ("headlights", "drl"):
        if data.get(key) in SWITCH:
            state[key] = data[key]
    for key in ("front", "rear"):
        if data.get(key) in FOLLOW:
            state[key] = data[key]
    for key in (
        "high_beam",
        "fog",
        "sidelights",
        "brake_lights",
        "hazard",
        "left",
        "right",
        "fade_signals",
    ):
        if key in data:
            state[key] = bool(data[key])
    with contextlib.suppress(TypeError, ValueError):
        state["fade"] = _clamp(float(data.get("fade", 0.0)), 0.0, 5.0)
    if data.get("unwired") in UNWIRED:
        state["unwired"] = data["unwired"]
    colours = data.get("colours") if isinstance(data.get("colours"), dict) else {}
    for key, config in colours.items():
        if key not in state["colours"] or not isinstance(config, dict):
            continue
        target = state["colours"][key]
        if config.get("mode") in MODES:
            target["mode"] = config["mode"]
        if config.get("strobe") in STROBES:
            target["strobe"] = config["strobe"]
        target["rgb"] = _rgb_list(config.get("rgb"), target["rgb"])
        target["rgb2"] = _rgb_list(config.get("rgb2"), target["rgb2"])
        for name, low, high in (
            ("brightness", 0.0, 4.0),
            ("speed", 0.01, 5.0),
            ("strobe_hz", 0.2, 30.0),
        ):
            with contextlib.suppress(TypeError, ValueError):
                target[name] = _clamp(float(config.get(name, target[name])), low, high)
    return state


class LightingModule(FeatureModule):
    name = "lighting"
    title = "Lighting"
    subtitle = "Light switches, signals, fades and lamp colours."
    icon = "lighting.png"
    group = "Vehicle"
    order = 35

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._enabled = bool(settings.get("lighting_enabled"))
        self.state = merge_state(settings.get("lighting_state"))
        for key in TRANSIENT_KEYS:
            self.state[key] = False
        self._widgets: dict = {}
        self._selected_group = "drl"
        self._controls_dirty = True
        self._state_dirty_at = 0.0
        self._edges = {key: inp.EdgeDetector() for key, _, _ in self._binding_specs()}
        self._scan = lamps.MaterialScan()
        self._forget()

    # ------------------------------------------------------------------ ownership records

    def _forget(self) -> None:
        """Drop every record WITHOUT writing: the memory they point at is no longer ours."""
        self._entity = 0
        self._graph: lamps.LampGraph | None = None
        self._stock_operands: dict[int, int] = {}
        self._operands_written: set[int] = set()
        self._operand_plan: dict[int, int] = {}
        self._plan_signature = None
        self._slew_stock: dict[int, float] = {}
        self._slews_written: set[int] = set()
        self._flag_stock: dict[int, int] = {}
        self._materials: list[lamps.LampMaterial] = []
        self._material_tint: dict[int, tuple] = {}
        self._material_channel: dict[int, int] = {}
        self._scene = 0
        self._last_scan = 0.0
        self._last_flags = 0.0
        self._last_verify = 0.0
        self._last_colour = 0.0
        self._engine = False
        self._groups_present: list[str] = []
        self._groups_dirty = True

    def _adopt(self, vehicle) -> None:
        self._forget()
        self._entity = vehicle.entity
        self._capture_graph(vehicle)
        self._scene = lamps.pose(vehicle.process, vehicle.entity)

    def _capture_graph(self, vehicle) -> None:
        graph = lamps.read_graph(vehicle.process, vehicle.entity)
        self._graph = graph
        self._stock_operands = lamps.stock_operands(graph) if graph else {}
        self._slew_stock = {ch: c.slew for ch, c in graph.controllers.items()} if graph else {}
        self._operands_written = set()
        self._slews_written = set()
        self._plan_signature = None

    # ------------------------------------------------------------------ lifecycle

    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle
        self._forget()

    def on_car_changed(self, vehicle) -> None:
        self.vehicle = vehicle
        self._forget()
        self._controls_dirty = True

    def on_car_reloaded(self, vehicle) -> None:
        # Fresh memory: re-capture and let the next tick re-assert what is held.
        self.vehicle = vehicle
        self._forget()

    def on_detach(self) -> None:
        self.vehicle = None
        self._forget()

    def shutdown(self) -> None:
        self._save_now()

    def restore(self) -> None:
        vehicle = self.vehicle
        if vehicle is not None and vehicle.entity == self._entity:
            self._release_all(vehicle)
        self._forget()

    def reset_controls(self) -> None:
        self.state = copy.deepcopy(DEFAULT_STATE)
        self._plan_signature = None
        self._touch()

    def _release_all(self, vehicle) -> None:
        process, entity = vehicle.process, vehicle.entity
        for index in list(self._material_tint) + list(self._material_channel):
            if index < len(self._materials) and lamps.material_current(
                process, self._materials[index]
            ):
                material = self._materials[index]
                lamps.write_tint(process, material, material.stock_rgb)
                lamps.write_material_channel(process, material, material.channel)
        self._material_tint.clear()
        self._material_channel.clear()
        if self._graph is not None and lamps.graph_still_current(process, entity, self._graph):
            for address in self._operands_written:
                lamps.write_operand(process, address, self._stock_operands[address])
            for channel in self._slews_written:
                controller = self._graph.controllers.get(channel)
                if controller is not None:
                    lamps.write_slew(process, controller, self._slew_stock.get(channel, 0.0))
        self._operands_written.clear()
        self._slews_written.clear()
        # The automatic selector goes back last, so the game never sees a half-restored pair.
        for offset in sorted(self._flag_stock, key=lambda o: o == L.SELECTOR):
            lamps.write_flag(process, entity, offset, self._flag_stock[offset])
        self._flag_stock.clear()

    # ------------------------------------------------------------------ tick

    def tick(self, vehicle) -> None:
        self.vehicle = vehicle
        if vehicle is None:
            return
        self._poll_bindings()
        if not self._enabled:
            return
        process = vehicle.process
        if not lamps.is_player(process, vehicle.entity):
            return
        now = time.monotonic()
        if vehicle.entity != self._entity:
            self._adopt(vehicle)
        if now - self._last_verify >= VERIFY_INTERVAL:
            self._last_verify = now
            self._verify(vehicle, now)
            self._apply_programs(vehicle, force=True)
            self._apply_colours(vehicle, now, force=True)
        if now - self._last_flags >= FLAG_INTERVAL:
            self._last_flags = now
            self._apply_flags(vehicle)
            self._apply_programs(vehicle)
        if now - self._last_colour >= COLOUR_INTERVAL:
            self._last_colour = now
            self._apply_colours(vehicle, now)
        self._take_scan(vehicle)

    def _verify(self, vehicle, now: float) -> None:
        process, entity = vehicle.process, vehicle.entity
        self._engine = lamps.engine_running(process, entity)
        if self._graph is None or not lamps.graph_still_current(process, entity, self._graph):
            # The graph was rebuilt (Photo Mode, garage): its old addresses are not ours.
            self._capture_graph(vehicle)
        scene = lamps.pose(process, entity)
        stale = scene != self._scene or (
            self._materials and not lamps.material_current(process, self._materials[0])
        )
        if stale:
            self._scene = scene
            self._materials = []
            self._material_tint.clear()
            self._material_channel.clear()
            self._groups_dirty = True
            self._last_scan = 0.0
        if not self._materials and not self._scan.busy and now - self._last_scan >= RESCAN_BACKOFF:
            self._last_scan = now
            self._scan.start(process, entity, scene)

    def _take_scan(self, vehicle) -> None:
        result = self._scan.take()
        if result is None:
            return
        entity, scene, materials = result
        if entity != vehicle.entity or scene != self._scene:
            return  # superseded by a car or scene change while it ran
        self._materials = materials
        self._material_tint.clear()
        self._material_channel.clear()
        # A lamp still showing a non-stock colour or channel (an earlier session, a crash, a
        # rescan) is adopted as owned, so choosing Stock or restoring really puts it back.
        process = vehicle.process
        for index, material in enumerate(materials):
            tint = lamps.read_tint(process, material)
            if tint is not None and tuple(round(v, 4) for v in tint) != tuple(
                round(v, 4) for v in material.stock_rgb
            ):
                self._material_tint[index] = tuple(round(v, 4) for v in tint)
            channel = lamps.read_material_channel(process, material)
            if channel is not None and channel != material.channel:
                self._material_channel[index] = channel
        self._groups_dirty = True

    # ------------------------------------------------------------------ light switches

    def _desired_flags(self) -> dict[int, int | None]:
        state = self.state
        desired: dict[int, int | None] = {}
        head = state["headlights"]
        desired[L.SELECTOR] = None if head == "Game" else 0
        desired[L.MANUAL] = None if head == "Game" else (1 if head == "On" else 0)
        drl = state["drl"]
        desired[L.DRL] = None if drl == "Game" else (1 if drl == "On" else 0)
        for key, offset, _, _ in FLAG_SWITCHES:
            desired[offset] = 1 if state[key] else None
        return desired

    def _apply_flags(self, vehicle) -> None:
        process, entity = vehicle.process, vehicle.entity
        desired = self._desired_flags()
        for offset, value in sorted(desired.items(), key=lambda kv: kv[0] == L.SELECTOR):
            current = lamps.read_flag(process, entity, offset)
            if current is None or current > 1:
                continue  # not a lighting flag we understand; never write it
            if value is None:
                if offset in self._flag_stock:
                    lamps.write_flag(process, entity, offset, self._flag_stock.pop(offset))
                continue
            if offset not in self._flag_stock:
                self._flag_stock[offset] = current
            if current != value:
                lamps.write_flag(process, entity, offset, value)

    # ------------------------------------------------------------------ programs and fades

    def _remaps(self) -> list[lamps.Remap]:
        state = self.state
        every = frozenset(self._graph.channels()) if self._graph else frozenset()
        remaps = []
        for key, channels in (("front", FRONT_LAMPS), ("rear", REAR_LAMPS)):
            if state[key] != "Follow":
                remaps.append(
                    lamps.Remap(channels, R.LOW_BEAM, R.ENGINE if state[key] == "On" else R.ZERO)
                )
        for key, register, _ in SIGNALS:
            if state[key]:
                remaps.append(lamps.Remap(every, register, R.ENGINE))
        return remaps

    def _fade_channels(self) -> dict[int, float]:
        if self._graph is None:
            return {}
        fade = float(self.state["fade"])
        if fade <= 0.0:
            return {}
        skip = FLASH_CHANNELS | (frozenset() if self.state["fade_signals"] else SIGNAL_CHANNELS)
        return {ch: fade for ch in self._graph.controllers if ch not in skip}

    def _apply_programs(self, vehicle, force: bool = False) -> None:
        graph = self._graph
        if graph is None:
            return
        process = vehicle.process
        signature = (
            graph.sentinel,
            repr(self._remaps()),
            self.state["fade"],
            self.state["fade_signals"],
        )
        if signature == self._plan_signature and not force:
            return
        self._plan_signature = signature
        plan = lamps.plan_operands(graph, self._remaps())
        for address, register in plan.items():
            if force or address not in self._operands_written:
                if lamps.read_operand(process, address) != register:
                    lamps.write_operand(process, address, register)
                self._operands_written.add(address)
        for address in list(self._operands_written - plan.keys()):
            lamps.write_operand(process, address, self._stock_operands[address])
            self._operands_written.discard(address)
        fades = self._fade_channels()
        for channel, seconds in fades.items():
            controller = graph.controllers[channel]
            lamps.write_slew(process, controller, seconds)
            self._slews_written.add(channel)
        for channel in list(self._slews_written - fades.keys()):
            controller = graph.controllers.get(channel)
            if controller is not None:
                lamps.write_slew(process, controller, self._slew_stock.get(channel, 0.0))
            self._slews_written.discard(channel)

    # ------------------------------------------------------------------ colours

    def _material_target(self, material: lamps.LampMaterial) -> int:
        """The channel a material should light from: rewired, strobing, or authored."""
        if material.channel == UNWIRED_CHANNEL:
            wired = unwired_channel(self.state["unwired"], material.model)
            return wired if wired is not None else material.channel
        group = group_of(material.channel)
        if group and self.state["colours"][group]["strobe"] == "Native":
            return O.NATIVE_STROBE_CHANNEL
        return material.channel

    def _apply_colours(self, vehicle, now: float, force: bool = False) -> None:
        process = vehicle.process
        for index, material in enumerate(self._materials):
            group = group_of(material.channel)
            config = self.state["colours"].get(group) if group else None
            rgb = colour_at(config, now, material.stock_rgb) if config else None
            if rgb is None:
                if index in self._material_tint:
                    lamps.write_tint(process, material, material.stock_rgb)
                    del self._material_tint[index]
            else:
                rounded = tuple(round(v, 4) for v in rgb)
                if force or self._material_tint.get(index) != rounded:
                    lamps.write_tint(process, material, rgb)
                    self._material_tint[index] = rounded
            target = self._material_target(material)
            if target == material.channel:
                if index in self._material_channel:
                    lamps.write_material_channel(process, material, material.channel)
                    del self._material_channel[index]
            elif force or self._material_channel.get(index) != target:
                lamps.write_material_channel(process, material, target)
                self._material_channel[index] = target

    # ------------------------------------------------------------------ keybinds

    @staticmethod
    def _binding_specs() -> list[tuple[str, str, str]]:
        return [
            (
                "lighting.headlights",
                "Cycle headlights",
                "Steps the headlights through Game, On and Off.",
            ),
            (
                "lighting.all",
                "All lights on/off",
                "Turns headlights, DRL and sidelights on together, or back to the game.",
            ),
            ("lighting.high_beam", "High beam", "Toggles high beam."),
            ("lighting.hazard", "Hazards", "Toggles the hazard lights."),
            ("lighting.left", "Left indicator", "Toggles the left indicator."),
            ("lighting.right", "Right indicator", "Toggles the right indicator."),
            (
                "lighting.strobe",
                "DRL strobe",
                "Toggles a custom strobe on the daytime running lights.",
            ),
        ]

    def bindings(self) -> list[dict]:
        return [
            {"key": k, "label": label, "description": d} for k, label, d in self._binding_specs()
        ]

    def _poll_bindings(self) -> None:
        for key, _, _ in self._binding_specs():
            if self._edges[key].pressed(self.settings.binding(key)) and self._enabled:
                self._on_binding(key)

    def _on_binding(self, key: str) -> None:
        state = self.state
        if key == "lighting.headlights":
            state["headlights"] = SWITCH[(SWITCH.index(state["headlights"]) + 1) % len(SWITCH)]
        elif key == "lighting.all":
            self._all_lights(not self._all_on())
            return
        elif key == "lighting.high_beam":
            state["high_beam"] = not state["high_beam"]
        elif key in ("lighting.hazard", "lighting.left", "lighting.right"):
            self._toggle_signal(key.split(".")[1])
            return
        elif key == "lighting.strobe":
            drl = state["colours"]["drl"]
            drl["strobe"] = "Off" if drl["strobe"] == "Custom" else "Custom"
        self._touch()

    # ------------------------------------------------------------------ state changes (UI)

    def _touch(self) -> None:
        self._controls_dirty = True
        self._state_dirty_at = time.monotonic()

    def _save_now(self) -> None:
        if not self._state_dirty_at:
            return
        self._state_dirty_at = 0.0
        saved = copy.deepcopy(self.state)
        for key in TRANSIENT_KEYS:
            saved.pop(key, None)
        self.settings.remember("lighting_state", saved)

    def _all_on(self) -> bool:
        s = self.state
        return s["headlights"] == "On" and s["drl"] == "On" and s["sidelights"]

    def _all_lights(self, on: bool) -> None:
        s = self.state
        if on:
            s.update(headlights="On", drl="On", sidelights=True, front="Follow", rear="Follow")
        else:
            s.update(
                headlights="Game",
                drl="Game",
                sidelights=False,
                high_beam=False,
                fog=False,
                brake_lights=False,
                front="Follow",
                rear="Follow",
            )
        self._touch()

    def _toggle_signal(self, key: str) -> None:
        turning_on = not self.state[key]
        if turning_on and key in ("left", "right"):
            self.state["left" if key == "right" else "right"] = False  # one direction at a time
        self.state[key] = turning_on
        self._touch()

    def _set(self, key: str, value) -> None:
        if self.state.get(key) == value:
            return
        self.state[key] = value
        self._touch()

    def _set_colour(self, key: str, value) -> None:
        config = self.state["colours"][self._selected_group]
        if config.get(key) == value:
            return
        config[key] = value
        self._touch()

    def _set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self.settings.remember("lighting_enabled", enabled)
        if not enabled:
            self.restore()
        self._controls_dirty = True

    def _copy_to_all(self) -> None:
        source = copy.deepcopy(self.state["colours"][self._selected_group])
        for key in self.state["colours"]:
            if key != "unwired":
                self.state["colours"][key] = copy.deepcopy(source)
        self._touch()

    def _rescan(self) -> None:
        self._materials = []
        self._material_tint.clear()
        self._material_channel.clear()
        self._last_scan = 0.0
        self._groups_dirty = True

    # ------------------------------------------------------------------ page

    def build_page(self, page) -> None:
        w = self._widgets
        enable_card = page.add_card("Lighting")
        enabled = ToggleRow("Enable lighting controls", self._enabled, hint=HINT_ENABLE)
        enabled.toggle.toggled_value.connect(self._set_enabled)
        w["enabled"] = enabled
        enable_card.add(enabled)

        switches = page.add_card(
            "Light switches", "Game leaves a light to the game. Changes show straight away."
        )
        for key, hint, label in (
            ("headlights", HINT_HEADLIGHTS, "Headlights"),
            ("drl", "Daytime running lights.", "Daytime running lights"),
        ):
            control = Segmented(list(SWITCH), self.state[key])
            control.changed.connect(lambda value, k=key: self._set(k, value))
            w[key] = control
            switches.add(FieldRow(label, control, hint=hint))
        for key, _, label, hint in FLAG_SWITCHES:
            row = ToggleRow(label, self.state[key], hint=hint)
            row.toggle.toggled_value.connect(lambda value, k=key: self._set(k, bool(value)))
            w[key] = row
            switches.add(row)
        all_on, back = PrimaryButton("All lights on"), Button("Return all to game")
        all_on.clicked.connect(lambda: self._all_lights(True))
        back.clicked.connect(lambda: self._all_lights(False))
        switches.add(all_on)
        switches.add(back)

        signals = page.add_card(
            "Signals",
            "Indicators and hazards blink with the car's own pattern. Needs the engine on.",
        )
        for key, _, label in SIGNALS:
            row = ToggleRow(label, self.state[key])
            row.toggle.toggled_value.connect(
                lambda value, k=key: self._signal_from_ui(k, bool(value))
            )
            w[key] = row
            signals.add(row)

        independent = page.add_card("Independent lamps", "Light the front or rear on its own.")
        for key, label, hint in (
            ("front", "Front headlamps", HINT_FRONT),
            ("rear", "Rear running lights", HINT_REAR),
        ):
            control = Segmented(list(FOLLOW), self.state[key])
            control.changed.connect(lambda value, k=key: self._set(k, value))
            w[key] = control
            independent.add(FieldRow(label, control, hint=hint))

        fade_card = page.add_card("Fade")
        fade = SliderRow(
            "Fade time",
            0.0,
            3.0,
            self.state["fade"],
            step=0.05,
            decimals=2,
            unit="s",
            hint=HINT_FADE,
        )
        fade.changed.connect(lambda value: self._set("fade", round(float(value), 3)))
        w["fade"] = fade
        fade_card.add(fade)
        fade_signals = ToggleRow(
            "Fade signals too", self.state["fade_signals"], hint=HINT_FADE_SIGNALS
        )
        fade_signals.toggle.toggled_value.connect(
            lambda value: self._set("fade_signals", bool(value))
        )
        w["fade_signals"] = fade_signals
        fade_card.add(fade_signals)

        colour_card = page.add_card(
            "Colour",
            "Recolour any group of lamps on this car. Reverts if the game rebuilds the car.",
        )
        group = ComboBox()
        group.currentIndexChanged.connect(self._on_group_selected)
        w["group"] = group
        colour_card.add(
            FieldRow(
                "Lamps", group, hint="Lamp groups found on this car, with how many lamps each has."
            )
        )
        mode = Segmented(list(MODES), "Stock")
        mode.changed.connect(lambda value: self._set_colour("mode", value))
        w["mode"] = mode
        colour_card.add(FieldRow("Mode", mode, hint=HINT_MODE))
        for key, label in (("rgb", "Colour"), ("rgb2", "Cycle colour")):
            picker = ColorPickerButton(QColor(255, 255, 255), label)
            picker.colorChanged.connect(
                lambda colour, k=key: self._set_colour(
                    k, [colour.redF(), colour.greenF(), colour.blueF()]
                )
            )
            w[key] = picker
            colour_card.add(FieldRow(label, picker))
        brightness = SliderRow(
            "Brightness", 0.0, 400.0, 100.0, step=5.0, decimals=0, unit="%", hint=HINT_BRIGHTNESS
        )
        brightness.changed.connect(
            lambda value: self._set_colour("brightness", round(float(value) / 100.0, 3))
        )
        w["brightness"] = brightness
        colour_card.add(brightness)
        speed = SliderRow(
            "Cycle speed",
            0.05,
            2.0,
            0.25,
            step=0.05,
            decimals=2,
            unit="Hz",
            hint="How fast Rainbow and Cycle move.",
        )
        speed.changed.connect(lambda value: self._set_colour("speed", round(float(value), 3)))
        w["speed"] = speed
        colour_card.add(speed)
        strobe = Segmented(list(STROBES), "Off")
        strobe.changed.connect(lambda value: self._set_colour("strobe", value))
        w["strobe"] = strobe
        colour_card.add(FieldRow("Strobe", strobe, hint=HINT_STROBE))
        strobe_hz = SliderRow(
            "Strobe rate",
            0.5,
            20.0,
            4.0,
            step=0.5,
            decimals=1,
            unit="Hz",
            hint="Flashes per second for Custom strobe.",
        )
        strobe_hz.changed.connect(
            lambda value: self._set_colour("strobe_hz", round(float(value), 2))
        )
        w["strobe_hz"] = strobe_hz
        colour_card.add(strobe_hz)
        unwired = ComboBox()
        unwired.addItems(list(UNWIRED))
        unwired.setCurrentIndex(UNWIRED.index(self.state["unwired"]))
        unwired.currentIndexChanged.connect(
            lambda index: (
                self._set("unwired", UNWIRED[index]) if 0 <= index < len(UNWIRED) else None
            )
        )
        w["unwired"] = unwired
        w["unwired_row"] = FieldRow("Light unwired lamps as", unwired, hint=HINT_UNWIRED)
        colour_card.add(w["unwired_row"])
        copy_all, rescan = Button("Use this look on every lamp group"), Button("Rescan lamps")
        copy_all.clicked.connect(self._copy_to_all)
        rescan.clicked.connect(self._rescan)
        colour_card.add(copy_all)
        colour_card.add(rescan)

        keys = page.add_card("Controls", "Shortcuts work while the game is focused.")
        for key, label, hint in self._binding_specs():
            button = BindButton(self.settings.binding(key), settings=self.settings, key=key)
            button.bound.connect(lambda binding, k=key: self.settings.set_binding(k, binding))
            keys.add(FieldRow(label, button, hint=hint))

        status_card = page.add_card("Status")
        stats = StatStrip()
        stats.add("engine", "Engine")
        stats.add("headlights", "Headlights")
        stats.add("channels", "Lamp channels")
        stats.add("lamps", "Lamps found")
        w["stats"] = stats
        status_card.add(stats)
        banner = Banner(NOTE_OFFLINE, "info")
        w["banner"] = banner
        page.add(banner)
        bind_progressive(
            enabled, switches, signals, independent, fade_card, colour_card, keys, status_card
        )

    def _signal_from_ui(self, key: str, value: bool) -> None:
        if self.state[key] != value:
            self._toggle_signal(key)

    def _on_group_selected(self, index: int) -> None:
        group = self._widgets.get("group")
        if group is None or index < 0:
            return
        key = group.itemData(index)
        if key and key != self._selected_group:
            self._selected_group = key
            self._controls_dirty = True

    # ------------------------------------------------------------------ refresh (UI thread)

    def _sync_groups(self) -> None:
        combo = self._widgets.get("group")
        if combo is None:
            return
        self._groups_dirty = False
        counts: dict[str, int] = {}
        for material in self._materials:
            key = group_of(material.channel)
            if key:
                counts[key] = counts.get(key, 0) + 1
        present = [key for key, _, _ in GROUPS if counts.get(key)]
        if present == self._groups_present and combo.count():
            return
        self._groups_present = present
        combo.blockSignals(True)
        combo.clear()
        for key in present or ["drl"]:
            combo.addItem(f"{GROUP_TITLES[key]} ({counts.get(key, 0)})", userData=key)
        if self._selected_group not in present and present:
            self._selected_group = present[0]
        keys = present or ["drl"]
        combo.setCurrentIndex(
            keys.index(self._selected_group) if self._selected_group in keys else 0
        )
        combo.blockSignals(False)
        row = self._widgets.get("unwired_row")
        if row is not None:
            row.setVisible("unwired" in present)
        self._controls_dirty = True

    def _sync_controls(self) -> None:
        w, s = self._widgets, self.state
        if "enabled" in w:
            w["enabled"].set_value(self._enabled)
        for key in ("headlights", "drl", "front", "rear"):
            if key in w:
                w[key].set_value(s[key])
        if "unwired" in w:
            w["unwired"].blockSignals(True)
            w["unwired"].setCurrentIndex(UNWIRED.index(s["unwired"]))
            w["unwired"].blockSignals(False)
        for key in (
            "high_beam",
            "fog",
            "sidelights",
            "brake_lights",
            "hazard",
            "left",
            "right",
            "fade_signals",
        ):
            if key in w:
                w[key].set_value(s[key])
        if "fade" in w:
            w["fade"].set_value(s["fade"])
        config = s["colours"][self._selected_group]
        if "mode" in w:
            w["mode"].set_value(config["mode"])
            w["strobe"].set_value(config["strobe"])
            w["brightness"].set_value(config["brightness"] * 100.0)
            w["speed"].set_value(config["speed"])
            w["strobe_hz"].set_value(config["strobe_hz"])
            for key in ("rgb", "rgb2"):
                r, g, b = config[key]
                w[key].blockSignals(True)
                w[key].setColor(QColor.fromRgbF(r, g, b))
                w[key].blockSignals(False)

    def refresh(self, vehicle) -> None:
        if self._groups_dirty:
            self._sync_groups()
        if self._controls_dirty:
            self._controls_dirty = False
            self._sync_controls()
        if self._state_dirty_at and time.monotonic() - self._state_dirty_at >= SAVE_DELAY:
            self._save_now()
        stats, banner = self._widgets.get("stats"), self._widgets.get("banner")
        if stats is None or banner is None:
            return
        if vehicle is None:
            stats.reset()
            banner.set(NOTE_OFFLINE, "info")
            banner.setVisible(True)
            return
        process, entity = vehicle.process, vehicle.entity
        engine = lamps.engine_running(process, entity)
        stats.set("engine", "Running" if engine else "Off")
        selector = lamps.read_flag(process, entity, L.SELECTOR)
        head = lamps.read_flag(process, entity, L.AUTO if selector == 1 else L.MANUAL)
        stats.set(
            "headlights", ("On" if head == 1 else "Off") + (" (auto)" if selector == 1 else "")
        )
        stats.set("channels", str(len(self._graph.controllers)) if self._graph else "--")
        stats.set("lamps", "Scanning..." if self._scan.busy else str(len(self._materials)))
        needs_engine = any(self.state[k] for k in TRANSIENT_KEYS) or any(
            self.state[k] != "Follow" for k in ("front", "rear")
        )
        if not lamps.is_player(process, entity):
            banner.set("This is not the local player's car, so lighting is left alone.", "warn")
        elif not self._enabled:
            banner.set(
                "Lighting controls are off. Turn them on to control this car's lights.", "info"
            )
        elif needs_engine and not engine:
            banner.set(
                "Start the engine: signals and independent lamps run from the engine-running signal.",
                "warn",
            )
        elif self._enabled and not self._materials and not self._scan.busy and self._entity:
            banner.set(
                "No recolourable lamps found yet. Drive out into the world, then press Rescan lamps.",
                "info",
            )
        else:
            banner.setVisible(False)
            return
        banner.setVisible(True)

    # ------------------------------------------------------------------ presets

    def save_state(self) -> dict:
        return {"enabled": self._enabled, "lighting": copy.deepcopy(self.state)}

    def load_state(self, data: dict) -> None:
        data = data if isinstance(data, dict) else {}
        if "lighting" not in data:
            return
        self.state = merge_state(data.get("lighting"))
        self._plan_signature = None
        self._set_enabled(bool(data.get("enabled", True)))
        self._touch()
