"""Suspension: ride height and air-ride drop."""

from __future__ import annotations

import threading
import time

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from neptune.core import input as inp
from neptune.core.module import FeatureModule
from neptune.memory import offsets as O
from neptune.ui import theme as T
from neptune.ui.widgets.card import Banner, FieldRow, StatStrip, ToggleRow, bind_progressive
from neptune.ui.widgets.controls import BindButton, SectionHeading, Segmented
from neptune.ui.widgets.sliderrow import SliderRow
from neptune.ui.widgets.transitioncurve import (
    DEFAULT_CURVE,
    TransitionCurve,
    curve_value,
    normalise_curve,
)

RAMP_HZ = 60
DEFAULT_RAMP_SECONDS = 2.5
SETTLE_SECONDS = 0.5

LOWER_PERCENT_MAX = 90.0
RAISE_PERCENT_MAX = 50.0
DROP_PERCENT_DEFAULT = 60.0
DEFAULT_FLOOR_PERCENT = 15.0


BOUNCE_MIN_M = 0.02
BOUNCE_MAX_M = 0.60
BOUNCE_DEFAULT_LOW = 0.08
BOUNCE_DEFAULT_HIGH = 0.30
BOUNCE_SPEED_MIN = 0.2
BOUNCE_SPEED_MAX = 4.0
BOUNCE_DEFAULT_SPEED = 1.0
BOUNCE_HZ = 60


HYDRAULICS_LIFT_MIN_M = 0.05
HYDRAULICS_LIFT_MAX_M = 0.80
HYDRAULICS_DEFAULT_LIFT_M = 0.20

HYDRAULICS_SOUND_COOLDOWN = 0.10

# The grip offsets themselves live in `offsets.Wheels`, with the rest of the wheel block.
GRIP_MIN = 1.0
GRIP_MAX = 3.0
GRIP_REAPPLY_INTERVAL = 1.0
HINT_GRIP = "Scales tyre grip. 1.00x is stock, and the compound's own behaviour is kept."
HINT_CAMBER_ENABLED = (
    "Enables the camber sliders. Turning it off puts camber back to this car's stock."
)
HINT_TRACK_ENABLED = (
    "Enables the track-width sliders. Turning it off puts track width back to stock."
)
HINT_TOE_ENABLED = "Enables the toe sliders. Turning it off puts toe back to stock."
HINT_HEIGHT_ENABLED = (
    "Enables the ride-height sliders. Turning it off returns the car to stock height."
)
HINT_GRIP_ENABLED = (
    "Enables the grip multipliers. Turning it off puts the tyres straight back to stock."
)
HINT_GRIP_LATERAL = "Cornering (lateral) grip."
HINT_GRIP_LONGITUDINAL = "Straight-line (longitudinal) grip."
HINT_GRIP_BOTH = "Sets both grips to the same value."

HYDRAULICS_MANUAL_GROUPS = {
    "front": (0, 1),
    "rear": (2, 3),
    "left": (0, 3),
    "right": (1, 2),
    "fl": (0,),
    "fr": (1,),
    "rl": (3,),
    "rr": (2,),
}

HYDRAULICS_DOWN_POSES = {
    "front_down": (0, 1),
    "rear_down": (2, 3),
    "left_down": (0, 3),
    "right_down": (1, 2),
}

HYDRAULICS_ALL_ACTIONS = {
    "all_up": 1.0,
    "all_down": -1.0,
}

# Precomputed once so tick() doesn't rebuild these tuples every pass.
HYDRAULICS_GROUP_KEYS = tuple(HYDRAULICS_MANUAL_GROUPS)
HYDRAULICS_DOWN_POSE_KEYS = tuple(HYDRAULICS_DOWN_POSES)
HYDRAULICS_ALL_ACTION_KEYS = tuple(HYDRAULICS_ALL_ACTIONS)

HARD_FLOOR_M = 0.01

AXLE_OVERLAP = 0.7
FRONT_WHEELS = (0, 1)
WHEEL_COUNT = 4

SEQUENCE_LABELS = {"together": "Together", "front": "Front first", "rear": "Rear first"}

CAMBER_MIN_DEG = -20.0
CAMBER_MAX_DEG = 20.0
CAMBER_RAMP_HZ = 20
CAMBER_AIR_DEFAULT_FRONT = -6.0
CAMBER_AIR_DEFAULT_REAR = -6.0

TRACK_MIN_MM = -150.0
TRACK_MAX_MM = 150.0

TOE_MIN_DEG = -15.0
TOE_MAX_DEG = 15.0

HINT_AXLE = "Raises or lowers this axle, as a share of normal ride height."
HINT_CAMBER = "Static camber, per wheel — each corner is independent."
HINT_CAMBER_MIRROR = "One value per axle instead of four — the right wheel mirrors the left."
HINT_TRACK = "Track width, per wheel — how far outward each corner sits."
HINT_TRACK_MIRROR = "One value per axle instead of four — both wheels move together."
HINT_TOE = "Static toe, per wheel — each corner is independent."
HINT_TOE_MIRROR = "One value per axle instead of four — the right wheel mirrors the left."
HINT_DROP = "How far air ride drops the car."
HINT_AIRRIDE_ENABLED = (
    "Enables the air-ride drop/lift action and its key binding. Turning it off returns the car "
    "to the configured ride height."
)
HINT_AIR_CAMBER = (
    "Moves camber from the values above to the lowered values whenever air ride drops."
)
HINT_CAMBER_CURVE = (
    "Shapes the camber animation. Drag the middle points: left-to-right is time and "
    "bottom-to-top is progress toward the lowered angle."
)
HINT_FLOOR = "The lowest the car may ever sit. Raise it if the car scrapes or bounces."
HINT_RAMP = "How long the car takes to move between heights."
HINT_SEQUENCE = "The order the axles move in. The raise plays the same order in reverse."
HINT_BOUNCE = "Rocks the car up and down between the two heights, over and over."
HINT_BOUNCE_RANGE = "The lowest and highest the car sits while bouncing."
HINT_BOUNCE_SPEED = "How quickly the car cycles between the two heights."
HINT_BOUNCE_AUDIO = "Plays a track on loop while the bounce is running."

NOTE_SOLID_REAR_AXLE = (
    "This car's rear suspension is a solid axle — camber, track width and toe changes "
    "have no effect on the rear wheels here."
)
NOTE_RIGID_BODY_TIRES = (
    "This car uses rigid-body tire physics — camber, track width and toe changes may "
    "have no effect here."
)


def _axle_text(settings, first: float | None, second: float | None) -> str:
    if first is None or second is None:
        return "--"
    if abs(first - second) < 5e-4:
        return settings.format_height(first)
    return f"{settings.format_height(first)} / {settings.format_height(second)}"


class SuspensionModule(FeatureModule):
    name = "suspension"
    title = "Suspension"
    subtitle = "Ride height and air ride."
    icon = "spring.png"
    group = "Vehicle"
    order = 30

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

        self.stock: list[float] | None = None
        self._radii: list[float] | None = None
        self._front_percent = 0.0
        self._rear_percent = 0.0

        self.stock_camber: list[float] | None = None
        self._camber: list[float | None] = [None, None, None, None]  # FL, FR, RR, RL
        self._camber_mirror = True
        self._air_camber = False
        self._air_camber_front = CAMBER_AIR_DEFAULT_FRONT
        self._air_camber_rear = CAMBER_AIR_DEFAULT_REAR
        self._air_camber_preview = False
        self._air_camber_backup: tuple[float, float, list[float | None]] | None = None
        self._camber_curve = list(DEFAULT_CURVE)
        self._camber_thread: threading.Thread | None = None
        self._camber_cancel = threading.Event()

        self._rear_solid_axle: bool | None = None
        self._rigid_body_tires: bool | None = None

        self.stock_toe: list[float] | None = None
        self._toe: list[float | None] = [None, None, None, None]  # FL, FR, RR, RL
        self._toe_mirror = True

        self.stock_track: list[list[float] | None] = [None, None, None, None]  # 30-pt curve/wheel
        self._track: list[float | None] = [None, None, None, None]  # held delta, mm, FL/FR/RR/RL
        self._track_mirror = True
        self._drop_percent = DROP_PERCENT_DEFAULT
        self._floor_percent = DEFAULT_FLOOR_PERCENT
        self._ramp_seconds = DEFAULT_RAMP_SECONDS
        self._sequence = "together"
        # Keep the existing behaviour for older presets while allowing the
        # user to disarm the air-ride action without losing its settings.
        self._airride_enabled = True
        self._enabled = bool(settings.get("suspension_enabled"))
        self._lowered = False

        self._height_enabled = False
        self._camber_enabled = False
        self._track_enabled = False
        self._toe_enabled = False
        self._grip_enabled = False
        self._grip_lateral = 1.0
        self._grip_longitudinal = 1.0
        self._grip_stock: tuple[list[float], list[float]] | None = None
        self._last_grip_reapply = 0.0

        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._edge = inp.EdgeDetector()
        self._hydraulics_edges = {
            key: inp.EdgeDetector()
            for key in (
                "front", "rear", "left", "right", "fl", "fr", "rl", "rr",
                "front_down", "rear_down", "left_down", "right_down",
                "all_up", "all_down",
            )
        }
        self._controls_dirty = False
        self._widgets: dict = {}

        self._bounce = False
        self._bounce_low = BOUNCE_DEFAULT_LOW
        self._bounce_high = BOUNCE_DEFAULT_HIGH
        self._bounce_speed = BOUNCE_DEFAULT_SPEED
        self._bounce_audio = True
        self._bounce_thread: threading.Thread | None = None
        self._bounce_cancel = threading.Event()

        self._hydraulics = False

        self._hydraulics_thread: threading.Thread | None = None
        self._hydraulics_cancel = threading.Event()
        self._hydraulics_last_sound = 0.0
        self._hydraulics_manual_up = [False] * WHEEL_COUNT
        self._hydraulics_manual_height = HYDRAULICS_DEFAULT_LIFT_M
        self._hydraulics_manual_speed = 0.14
        self._hydraulics_down_pose: str | None = None

        from neptune.core.audio import Loop, OneShot

        self._slam = OneShot("sfx/slam.wav")
        self._maybach = Loop("sfx/maybach.mp3")
        self._hydraulic = OneShot("sfx/hydraulics.wav")
        self._hydraulic_hop = OneShot("sfx/hydraulics_hop.wav")
        self._slam.set_volume(self.settings.get("airride_volume"))
        self._maybach.set_volume(self.settings.get("maybach_volume"))
        self._hydraulic.set_volume(self.settings.get("hydraulics_volume"))
        self._hydraulic_hop.set_volume(self.settings.get("hydraulics_volume"))
        self.settings.subscribe(self._on_settings_changed)

    def _on_settings_changed(self, key: str) -> None:
        """Live-apply the volume sliders that live in Settings.

        `Settings._notify` passes only the KEY, not the value — read it back rather than
        expecting a second argument, or every notification raises and is swallowed.
        """
        if key == "airride_volume":
            self._slam.set_volume(self.settings.get("airride_volume"))
        elif key == "maybach_volume":
            self._maybach.set_volume(self.settings.get("maybach_volume"))
        elif key == "hydraulics_volume":
            self._hydraulic.set_volume(self.settings.get("hydraulics_volume"))
            self._hydraulic_hop.set_volume(self.settings.get("hydraulics_volume"))

    def _sync_controls(self) -> None:
        stock = self.stock_camber
        camber_keys = ("camber_fl", "camber_fr", "camber_rr", "camber_rl")
        camber_values = [
            self._camber[i] if self._camber[i] is not None else (stock[i] if stock else None)
            for i in range(4)
        ]
        track_keys = ("track_fl", "track_fr", "track_rr", "track_rl")
        track_values = [value if value is not None else 0.0 for value in self._track]
        toe_stock = self.stock_toe
        toe_keys = ("toe_fl", "toe_fr", "toe_rr", "toe_rl")
        toe_values = [
            self._toe[i] if self._toe[i] is not None else (toe_stock[i] if toe_stock else None)
            for i in range(4)
        ]
        for key, value in (
            ("grip_both", self._grip_lateral),
            ("grip_lateral", self._grip_lateral),
            ("grip_longitudinal", self._grip_longitudinal),
            ("front", self._front_percent),
            ("rear", self._rear_percent),
            ("drop", self._drop_percent),
            ("floor", self._floor_percent),
            ("ramp", self._ramp_seconds),
            ("bounce_low", self._bounce_low),
            ("bounce_high", self._bounce_high),
            ("bounce_speed", self._bounce_speed),
            ("hydraulics_manual_height", self._hydraulics_manual_height),
            ("hydraulics_manual_speed", self._hydraulics_manual_speed),
            ("air_camber_front", self._air_camber_front),
            ("air_camber_rear", self._air_camber_rear),
            *zip(camber_keys, camber_values, strict=True),
            ("camber_axle_front", camber_values[0]),
            ("camber_axle_rear", camber_values[3]),
            *zip(track_keys, track_values, strict=True),
            ("track_axle_front", track_values[0]),
            ("track_axle_rear", track_values[3]),
            *zip(toe_keys, toe_values, strict=True),
            ("toe_axle_front", toe_values[0]),
            ("toe_axle_rear", toe_values[3]),
        ):
            slider = self._widgets.get(key)
            if slider is not None and value is not None:
                slider.set_value(value)
        for key, state in (
            ("enabled", self._enabled),
            ("airride_enabled", self._airride_enabled),
            ("bounce", self._bounce),
            ("bounce_audio", self._bounce_audio),
            ("hydraulics", self._hydraulics),
            ("grip_enabled", self._grip_enabled),
            ("height_enabled", self._height_enabled),
            ("camber_enabled", self._camber_enabled),
            ("track_enabled", self._track_enabled),
            ("toe_enabled", self._toe_enabled),
            ("camber_mirror", self._camber_mirror),
            ("track_mirror", self._track_mirror),
            ("toe_mirror", self._toe_mirror),
            ("air_camber", self._air_camber),
        ):
            row = self._widgets.get(key)
            if row is not None:
                try:
                    row.toggle.set_value(bool(state))
                except Exception:
                    pass

        self._update_geometry_visibility()
        # Height, Speed and the bind rows are built hidden, so they only ever appear if
        # something re-asserts their visibility after the toggle changes. Every other
        # panel above is driven from here; hydraulics was not, which left the controls
        # invisible for the whole session.
        self._update_hydraulics_visibility()
        selector = self._widgets.get("sequence")
        if selector is not None:
            selector.set_value(SEQUENCE_LABELS[self._sequence])
        curve = self._widgets.get("camber_curve")
        if curve is not None:
            curve.set_values(self._camber_curve)
        for card_key in ("height_card", "camber_card"):
            card = self._widgets.get(card_key)
            if card is not None:
                card.setEnabled(not self._air_camber)
        trigger = self._widgets.get("airride_trigger")
        if trigger is not None:
            trigger.setEnabled(self._airride_enabled)

    def binding(self) -> dict | None:
        return self.settings.binding("suspension.airride")

    def _hydraulics_binding(self, bindings: dict, group: str) -> dict | None:
        value = bindings.get(f"suspension.hydraulics.{group}")
        if isinstance(value, dict) and "kind" in value and "code" in value:
            return value
        return None

    def bindings(self) -> list[dict]:
        items = [
            ("front", "Hydraulics Front", "Hop the front axle once, then return to normal."),
            ("rear", "Hydraulics Back", "Hop the rear axle once, then return to normal."),
            ("left", "Hydraulics Left", "Hop the left side once, then return to normal."),
            ("right", "Hydraulics Right", "Hop the right side once, then return to normal."),
            ("fl", "Hydraulics FL", "Hop the front-left corner once, then return to normal."),
            ("fr", "Hydraulics FR", "Hop the front-right corner once, then return to normal."),
            ("rl", "Hydraulics RL", "Hop the rear-left corner once, then return to normal."),
            ("rr", "Hydraulics RR", "Hop the rear-right corner once, then return to normal."),
            ("front_down", "Front Down Pose", "Move into the front-down pose."),
            ("rear_down", "Back Down Pose", "Move into the rear-down pose."),
            ("left_down", "Left Down Pose", "Move into the left-down pose."),
            ("right_down", "Right Down Pose", "Move into the right-down pose."),
            ("all_up", "All Up", "Raise all four wheels and hold."),
            ("all_down", "All Down", "Lower all four wheels and hold."),
        ]
        return [
            {
                "key": "suspension.airride",
                "label": "Air ride up and down",
                "description": "Drops the car, or lifts it back to your set height.",
            },
            *[
                {
                    "key": f"suspension.hydraulics.{key}",
                    "label": label,
                    "description": description,
                }
                for key, label, description in items
            ],
        ]


    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle
        self._capture_stock(vehicle)
        self._capture_camber_stock(vehicle)
        self._capture_track_stock(vehicle)
        self._capture_toe_stock(vehicle)
        self._capture_grip_stock(vehicle)
        self._check_rear_axle(vehicle)
        self._controls_dirty = True

    def _check_rear_axle(self, vehicle) -> None:
        """Detect a solid rear axle or rigid-body tire physics, so the camber,
        track-width and toe cards can warn that editing them may have no effect.
        See docs/SOLID_AXLE_SUSPENSION.md.

        Each check is left `None` (and re-tried next tick) until a reading
        actually succeeds, rather than defaulting to "not affected" — a
        transient read failure should not silently hide a warning that's
        actually needed.
        """
        if vehicle is None:
            return
        changed = False
        if self._rear_solid_axle is None:
            self._rear_solid_axle = vehicle.rear_axle_is_solid()
            changed = changed or self._rear_solid_axle is not None
        if self._rigid_body_tires is None:
            self._rigid_body_tires = vehicle.uses_rigid_body_tires()
            changed = changed or self._rigid_body_tires is not None
        if changed:
            self._update_axle_banner()

    def _update_axle_banner(self) -> None:
        if self._rigid_body_tires:
            text = NOTE_RIGID_BODY_TIRES
        elif self._rear_solid_axle:
            text = NOTE_SOLID_REAR_AXLE
        else:
            text = ""
        for key in ("camber_banner", "track_banner", "toe_banner"):
            banner = self._widgets.get(key)
            if banner is not None:
                banner.set(text, "warn")

    def _capture_stock(self, vehicle) -> None:
        if self._lowered or self._ramping or self._front_percent or self._rear_percent:
            return
        values = vehicle.ride_height if vehicle else None
        if not values or any(value is None for value in values):
            return
        if not all(0.01 < value < 2.0 for value in values):
            return
        self.stock = list(values)

        radii = vehicle.wheel_radius
        if radii and all(r is not None and 0.05 < r < 1.0 for r in radii):
            self._radii = list(radii)
        else:
            self._radii = None

    def _capture_camber_stock(self, vehicle) -> None:
        if any(value is not None for value in self._camber):
            return
        values = vehicle.camber if vehicle else None
        if not values or any(value is None for value in values):
            return
        self.stock_camber = list(values)

    def _capture_toe_stock(self, vehicle) -> None:
        if any(value is not None for value in self._toe):
            return
        values = vehicle.toe if vehicle else None
        if not values or any(value is None for value in values):
            return
        self.stock_toe = list(values)

    def _capture_track_stock(self, vehicle) -> None:
        if all(curve is not None for curve in self.stock_track):
            return
        if not vehicle:
            return
        curves = [vehicle.track_curve(wheel) for wheel in range(WHEEL_COUNT)]
        if any(curve is None for curve in curves):
            return
        self.stock_track = curves

    def _capture_grip_stock(self, vehicle) -> None:
        """Snapshot the per-wheel grip scale so the multipliers can re-apply from stock."""
        if vehicle is None:
            self._grip_stock = None
            return
        lateral = vehicle.wheel_read(O.Wheels.GRIP_LATERAL)
        longitudinal = vehicle.wheel_read(O.Wheels.GRIP_LONGITUDINAL)
        if lateral and longitudinal:
            self._grip_stock = (lateral, longitudinal)
        else:
            self._grip_stock = None

    def _write_grip(
        self,
        vehicle,
        lateral: float | None = None,
        longitudinal: float | None = None,
    ) -> bool:
        if vehicle is None or self._grip_stock is None:
            return False
        lateral_factor = self._grip_lateral if lateral is None else lateral
        longitudinal_factor = self._grip_longitudinal if longitudinal is None else longitudinal
        lateral_stock, longitudinal_stock = self._grip_stock
        # Always scale the captured stock value rather than the live one, so repeated
        # writes cannot compound.
        ok = vehicle.wheel_write(
            O.Wheels.GRIP_LATERAL, [value * lateral_factor for value in lateral_stock]
        )
        return (
            vehicle.wheel_write(
                O.Wheels.GRIP_LONGITUDINAL,
                [value * longitudinal_factor for value in longitudinal_stock],
            )
            and ok
        )

    def _set_grip_enabled(self, enabled: bool) -> None:
        """Arm the grip sliders. Turning it off puts the tyres back to stock immediately."""
        enabled = bool(enabled)
        if enabled == self._grip_enabled:
            return
        self._grip_enabled = enabled
        if self.vehicle is None:
            return
        if enabled:
            self._write_grip(self.vehicle)
        else:
            self._write_grip(self.vehicle, 1.0, 1.0)

    def _grip_live(self) -> bool:
        """Whether a slider move should reach the car. The toggle is the arming switch."""
        return self._grip_enabled and self.vehicle is not None

    def _set_grip_lateral(self, value: float) -> None:
        self._grip_lateral = max(GRIP_MIN, min(GRIP_MAX, float(value)))
        if self._grip_live():
            self._write_grip(self.vehicle)

    def _set_grip_longitudinal(self, value: float) -> None:
        self._grip_longitudinal = max(GRIP_MIN, min(GRIP_MAX, float(value)))
        if self._grip_live():
            self._write_grip(self.vehicle)

    def _set_grip_both(self, value: float) -> None:
        """Apply one multiplier to both grips at once."""
        factor = max(GRIP_MIN, min(GRIP_MAX, float(value)))
        self._grip_lateral = factor
        self._grip_longitudinal = factor
        for key in ("grip_lateral", "grip_longitudinal"):
            slider = self._widgets.get(key)
            if slider is not None:
                slider.set_value(factor)
        if self._grip_live():
            self._write_grip(self.vehicle)

    def _reapply_grip_if_rebaked(self, vehicle) -> None:
        """Re-apply the grip scale after the game rebuilds the tyre (car or compound change).

        These fields are a global scale, so the stored base is always re-applied as-is and
        never re-snapshotted — it cannot compound the way a curve rewrite could.
        """
        if vehicle is None or self._grip_stock is None or not self._grip_enabled:
            return
        if abs(self._grip_lateral - 1.0) < 1e-6 and abs(self._grip_longitudinal - 1.0) < 1e-6:
            return
        now = time.monotonic()
        if now - self._last_grip_reapply < GRIP_REAPPLY_INTERVAL:
            return
        self._last_grip_reapply = now
        lateral_stock, longitudinal_stock = self._grip_stock
        # One wheel is enough to notice a rebake; the game rebuilds all four together.
        live = vehicle.wheel_read(O.Wheels.GRIP_LATERAL)
        live_long = vehicle.wheel_read(O.Wheels.GRIP_LONGITUDINAL)
        if not live or not live_long:
            return
        live_lateral, live_longitudinal = live[0], live_long[0]
        if (
            abs(live_lateral - lateral_stock[0] * self._grip_lateral) <= 1e-3
            and abs(live_longitudinal - longitudinal_stock[0] * self._grip_longitudinal) <= 1e-3
        ):
            return
        self._write_grip(vehicle)

    def on_car_changed(self, vehicle) -> None:
        self._cancel_hydraulics_no_restore()
        if self._bounce:
            self._bounce = False
            self._bounce_cancel.set()
            bounce_thread = self._bounce_thread
            if bounce_thread is not None and bounce_thread.is_alive():
                bounce_thread.join(timeout=1.0)
            self._bounce_thread = None
            self._maybach.stop()
        self._cancel_ramp()
        self._cancel_camber_ramp()
        self._lowered = False
        self._front_percent = 0.0
        self._rear_percent = 0.0
        self.stock = None
        self._radii = None
        self._camber = [None, None, None, None]
        self._air_camber_preview = False
        self.stock_camber = None
        self._track = [None, None, None, None]
        self.stock_track = [None, None, None, None]
        self._toe = [None, None, None, None]
        self.stock_toe = None
        self._rear_solid_axle = None
        self._rigid_body_tires = None
        self._controls_dirty = True
        self.vehicle = vehicle
        self._capture_stock(vehicle)
        self._capture_camber_stock(vehicle)
        self._capture_track_stock(vehicle)
        self._capture_toe_stock(vehicle)
        self._capture_grip_stock(vehicle)
        self._check_rear_axle(vehicle)

    def on_car_reloaded(self, vehicle) -> None:
        self.vehicle = vehicle
        if not self._enabled:
            return
        if self.stock and (self._lowered or self._front_percent or self._rear_percent):
            self._write(self._target(self._lowered))
        if any(value is not None for value in self._camber) or self._air_camber:
            self._write_active_camber()
        if any(value is not None for value in self._track):
            self._write_track()
        if any(value is not None for value in self._toe):
            self._write_toe()

    def on_detach(self) -> None:
        self._bounce = False
        self._bounce_cancel.set()

        self._hydraulics = False
        self._hydraulics_cancel.set()

        self._maybach.stop()
        self._cancel_ramp()
        self._cancel_camber_ramp()
        self._grip_stock = None
        self.vehicle = None

    def restore(self) -> None:
        self._bounce = False
        self._bounce_cancel.set()
        self._maybach.stop()
        bounce_thread = self._bounce_thread
        if bounce_thread is not None and bounce_thread.is_alive():
            bounce_thread.join(timeout=1.0)
        self._bounce_thread = None

        self._hydraulics = False
        self._hydraulics_cancel.set()

        hydraulics_thread = self._hydraulics_thread
        if hydraulics_thread is not None and hydraulics_thread.is_alive():
            hydraulics_thread.join(timeout=1.0)

        self._hydraulics_thread = None

        self._cancel_ramp()
        self._cancel_camber_ramp()
        vehicle = self.vehicle
        if vehicle is not None and self.stock:
            try:
                vehicle.set_ride_height(self.stock)
            except Exception:
                pass
        if vehicle is not None and self.stock_camber:
            try:
                vehicle.set_camber(self.stock_camber)
            except Exception:
                pass
        if vehicle is not None and all(curve is not None for curve in self.stock_track):
            try:
                for wheel in range(WHEEL_COUNT):
                    vehicle.set_track_width(wheel, self.stock_track[wheel], 0.0)
            except Exception:
                pass
        if vehicle is not None and self.stock_toe:
            try:
                vehicle.set_toe(self.stock_toe)
            except Exception:
                pass
        if vehicle is not None and self._grip_stock is not None:
            try:
                self._write_grip(vehicle, 1.0, 1.0)
            except Exception:
                pass
        self._grip_lateral = 1.0
        self._grip_longitudinal = 1.0
        self._lowered = False
        self._front_percent = 0.0
        self._rear_percent = 0.0
        self._camber = [None, None, None, None]
        self._air_camber = False
        self._air_camber_preview = False
        self._air_camber_backup = None
        self._track = [None, None, None, None]
        self._toe = [None, None, None, None]
        self._controls_dirty = True

    def reset_controls(self) -> None:
        if self._bounce:
            self.set_bounce(False)
        if self._hydraulics:
            self.set_hydraulics(False)
        self._lowered = False
        self._front_percent = 0.0
        self._rear_percent = 0.0
        self._camber = [None, None, None, None]
        self._air_camber = False
        self._air_camber_front = CAMBER_AIR_DEFAULT_FRONT
        self._air_camber_rear = CAMBER_AIR_DEFAULT_REAR
        self._air_camber_preview = False
        self._air_camber_backup = None
        self._camber_curve = list(DEFAULT_CURVE)
        self._cancel_camber_ramp()
        self._track = [None, None, None, None]
        self._toe = [None, None, None, None]
        self._height_enabled = False
        self._camber_enabled = False
        self._track_enabled = False
        self._toe_enabled = False
        self._grip_enabled = False
        self._grip_lateral = 1.0
        self._grip_longitudinal = 1.0
        self._controls_dirty = True

    def tick(self, vehicle) -> None:
        self.vehicle = vehicle
        if self.stock is None:
            self._capture_stock(vehicle)
        if self.stock_camber is None:
            self._capture_camber_stock(vehicle)
        if any(curve is None for curve in self.stock_track):
            self._capture_track_stock(vehicle)
        if self.stock_toe is None:
            self._capture_toe_stock(vehicle)
        if not self._enabled:
            self._edge.reset()
            for edge in self._hydraulics_edges.values():
                edge.reset()
            return
        if self._rear_solid_axle is None or self._rigid_body_tires is None:
            self._check_rear_axle(vehicle)
        if self._airride_enabled and self._edge.pressed(self.binding()):
            self.toggle()
        elif not self._airride_enabled:
            # Do not let a key held while the feature is disabled fire as soon
            # as the user enables it again.
            self._edge.reset()

        if self._hydraulics:
            bindings = self.settings.get("bindings", {}) or {}

            for group in HYDRAULICS_GROUP_KEYS:
                edge = self._hydraulics_edges[group]
                if edge.pressed(self._hydraulics_binding(bindings, group)):
                    self._manual_hydraulics_press(group)

            for pose in HYDRAULICS_DOWN_POSE_KEYS:
                edge = self._hydraulics_edges[pose]
                if edge.pressed(self._hydraulics_binding(bindings, pose)):
                    self._manual_down_pose_press(pose)

            for action in HYDRAULICS_ALL_ACTION_KEYS:
                edge = self._hydraulics_edges[action]
                if edge.pressed(self._hydraulics_binding(bindings, action)):
                    self._manual_all_press(action)

        self._reapply_if_rebaked(vehicle)
        self._reapply_camber_if_rebaked(vehicle)
        self._reapply_track_if_rebaked(vehicle)
        self._reapply_toe_if_rebaked(vehicle)
        self._reapply_grip_if_rebaked(vehicle)

    def _reapply_if_rebaked(self, vehicle) -> None:
        """Re-apply ride height when the game has quietly put stock height back."""
        if not self.stock or self._ramping or self._bounce or self._hydraulics:
            return
        if not (self._lowered or self._front_percent or self._rear_percent):
            return
        expected = self._target(self._lowered)
        current = vehicle.ride_height if vehicle else None
        if not current or len(current) != len(expected):
            return
        if any(abs(a - b) > 1e-3 for a, b in zip(current, expected, strict=True)):
            self._write(expected)

    def _reapply_camber_if_rebaked(self, vehicle) -> None:
        """Re-apply camber when the game has quietly rebaked the axle's table."""
        if self._camber_ramping:
            return
        target = self._active_camber_target()
        if target is None:
            return
        current = vehicle.camber if vehicle else None
        if not current or len(current) != len(target):
            return
        if any(
            expected is not None and abs(actual - expected) > 0.05
            for expected, actual in zip(target, current, strict=True)
        ):
            self._write_camber(target)

    def _reapply_track_if_rebaked(self, vehicle) -> None:
        """Re-apply track width when the game has quietly rebaked the axle's table."""
        if vehicle is None or not any(value is not None for value in self._track):
            return
        for wheel, delta_mm in enumerate(self._track):
            if delta_mm is None:
                continue
            baseline = self.stock_track[wheel]
            if baseline is None:
                continue
            if vehicle.track_width_ok(wheel, baseline, delta_mm / 1000.0) is False:
                self._write_track()
                return

    def _reapply_toe_if_rebaked(self, vehicle) -> None:
        """Re-apply toe if the game has quietly rebaked the axle's table.

        `set_toe()` only writes the baked table (see its docstring), not the
        live `Wheels.TOE` field the `toe` property reads — so unlike camber and
        track, this can't compare against `vehicle.toe`, which never reflects
        our own writes at all. Spot-check the table itself instead.
        """
        if vehicle is None or not any(value is not None for value in self._toe):
            return
        target = self._configured_toe()
        for wheel, expected in enumerate(target):
            if expected is None:
                continue
            actual = vehicle.toe_baked(wheel)
            if actual is not None and abs(actual - expected) > 0.05:
                self._write_toe(target)
                return

    @property
    def _ramping(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def _camber_ramping(self) -> bool:
        thread = self._camber_thread
        return thread is not None and thread.is_alive()

    def _clamp(self, value: float, wheel: int = 0) -> float:
        """Keep a wheel from dropping below the floor.

        The floor is a share of this wheel's own clearance, so it means the same thing on any car.
        """
        percent = min(max(self._floor_percent, 0.0), 100.0)
        radius = self._radius_for(wheel)

        floor = max(self._stock_clearance(wheel) * (percent / 100.0), HARD_FLOOR_M)
        return max(floor + radius, value)

    def _radius_for(self, wheel: int) -> float:
        """Wheel radius, so clearance and hub height can be converted either way.

        Falls back to 0.0 when unknown, which degrades to the old hub-height behaviour rather than
        throwing the car at the ground.
        """
        radii = self._radii
        if not radii or wheel >= len(radii):
            return 0.0
        value = radii[wheel]
        return float(value) if value else 0.0

    def _stock_clearance(self, wheel: int) -> float:
        """This wheel's stock clearance above the hub — the quantity percentages scale."""
        stock = self.stock or []
        if wheel >= len(stock):
            return 0.0
        return max(0.0, stock[wheel] - self._radius_for(wheel))

    def _baseline(self) -> list[float]:
        """Stock height with the user's per-axle percentage applied."""
        stock = self.stock or []
        if len(stock) < WHEEL_COUNT:
            return list(stock)
        percents = (
            self._front_percent,
            self._front_percent,
            self._rear_percent,
            self._rear_percent,
        )
        out = []
        for wheel, (value, percent) in enumerate(zip(stock[:WHEEL_COUNT], percents, strict=True)):
            shift = self._stock_clearance(wheel) * (percent / 100.0)
            out.append(self._clamp(value + shift, wheel))
        return out

    def _target(self, lowered: bool) -> list[float]:
        baseline = self._baseline()
        if not lowered:
            return baseline
        out = []
        for wheel, value in enumerate(baseline):
            drop = self._stock_clearance(wheel) * (self._drop_percent / 100.0)
            out.append(self._clamp(value - drop, wheel))
        return out

    def _write(self, values) -> bool:
        vehicle = self.vehicle
        if vehicle is None or not values or any(value is None for value in values):
            return False
        return bool(vehicle.set_ride_height(values))

    def _configured_camber(self) -> list[float | None]:
        """Raised camber, filling unset corners from the captured stock values."""
        stock = self.stock_camber or [None] * WHEEL_COUNT
        return [
            value if value is not None else stock[index] for index, value in enumerate(self._camber)
        ]

    def _air_camber_target(self) -> list[float]:
        """Lowered target in the game's FL, FR, RR, RL wheel order."""
        return [
            self._air_camber_front,
            -self._air_camber_front,
            -self._air_camber_rear,
            self._air_camber_rear,
        ]

    def _active_camber_target(self) -> list[float | None] | None:
        """The camber `_reapply_camber_if_rebaked` should enforce right now.

        Raised is protected too, exactly like lowered — otherwise a game rebake
        after the raise ramp finishes can silently leave camber stuck at the
        lowered angle, with nothing to ever correct it back. The one exception
        is right after a preview drag on the lowered-target sliders (see
        `_set_air_camber_target`), which deliberately shows that angle while
        raised and would otherwise get fought on the very next tick.
        """
        if self._air_camber:
            if self._lowered:
                return self._air_camber_target()
            if self._air_camber_preview:
                return None
            target = self._configured_camber()
            return target if all(value is not None for value in target) else None
        if any(value is not None for value in self._camber):
            target = self._configured_camber()
            return target if any(value is not None for value in target) else None
        return None

    def _set_camber_enabled(self, enabled: bool) -> None:
        """Arm the camber sliders. Turning it off puts camber back to this car's stock."""
        enabled = bool(enabled)
        if enabled == self._camber_enabled:
            return
        self._camber_enabled = enabled
        self._update_geometry_visibility()
        if not enabled:
            self._reset_camber()
        elif self.vehicle is not None:
            self._write_active_camber()

    def _set_track_enabled(self, enabled: bool) -> None:
        """Arm the track-width sliders. Turning it off puts track width back to stock."""
        enabled = bool(enabled)
        if enabled == self._track_enabled:
            return
        self._track_enabled = enabled
        self._update_geometry_visibility()
        if not enabled:
            self._reset_track()
        elif self.vehicle is not None:
            self._write_track()

    def _set_toe_enabled(self, enabled: bool) -> None:
        """Arm the toe sliders. Turning it off puts toe back to stock."""
        enabled = bool(enabled)
        if enabled == self._toe_enabled:
            return
        self._toe_enabled = enabled
        self._update_geometry_visibility()
        if not enabled:
            self._reset_toe()
        elif self.vehicle is not None:
            self._write_toe()

    def _write_camber(self, values=None) -> bool:
        """Write held per-wheel camber. O.Wheels.ORDER is (FL, FR, RR, RL);
        vehicle.set_camber() takes one verbatim value per wheel, so each
        corner is independent here, no axle mirroring.
        """
        vehicle = self.vehicle
        if vehicle is None or not self._camber_enabled:
            return False
        return bool(vehicle.set_camber(self._camber if values is None else values))

    def _write_active_camber(self) -> bool:
        target = self._active_camber_target()
        return self._write_camber(target) if target is not None else False

    def _set_camber(self, wheel: int, value: float) -> None:
        self._cancel_camber_ramp()
        self._camber[wheel] = float(value)
        self._write_active_camber()

    def _set_camber_axle(self, axle: str, value: float) -> None:
        """Mirror-mode entry point: one value per axle, right wheel negated."""
        self._cancel_camber_ramp()
        value = float(value)
        if axle == "front":
            self._camber[0], self._camber[1] = value, -value
        else:
            self._camber[3], self._camber[2] = value, -value
        self._write_active_camber()

    def _set_camber_mirror(self, enabled: bool) -> None:
        self._camber_mirror = bool(enabled)
        # Also resyncs every slider (not just panel visibility) so the axle sliders
        # don't show a stale value left over from before an asymmetric per-wheel edit.
        self._sync_controls()

    def _set_air_camber(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._air_camber:
            return
        self._cancel_camber_ramp()
        self._air_camber_preview = False
        restore_stock = False
        if enabled:
            # Ride height and Camber become the animation's own raised/lowered targets while
            # this is on, so drop any custom offset back to stock — kept aside to restore
            # once this is switched off again.
            self._air_camber_backup = (self._front_percent, self._rear_percent, list(self._camber))
            self._air_camber = True
            self._reset_height()
            self._reset_camber()
        else:
            self._air_camber = False
            backup = self._air_camber_backup
            self._air_camber_backup = None
            if backup is not None:
                self._front_percent, self._rear_percent, self._camber = (
                    backup[0],
                    backup[1],
                    list(backup[2]),
                )
                if self.stock and self.vehicle is not None:
                    self._cancel_ramp()
                    start = self.vehicle.ride_height or self._target(self._lowered)
                    self._start_ramp(start, self._target(self._lowered), SETTLE_SECONDS)
                # No custom camber to restore — the animation may have left the live
                # camber at the lowered angle, so force it back to stock explicitly
                # rather than relying on _write_active_camber(), which no-ops when
                # there's nothing to enforce.
                restore_stock = not any(value is not None for value in self._camber)
        if restore_stock and self.stock_camber:
            self._write_camber(self.stock_camber)
        else:
            self._write_active_camber()
        self._sync_controls()

    def _set_air_camber_target(self, axle: str, value: float) -> None:
        if axle == "front":
            self._air_camber_front = float(value)
        else:
            self._air_camber_rear = float(value)
        # Preview the lowered target directly, whether or not the car is actually lowered
        # right now — every other slider in this app gives immediate feedback, and gating
        # this on `_lowered` just meant it silently did nothing while raised. Mark it as a
        # preview so `_active_camber_target` doesn't fight it back to stock while raised.
        if self._air_camber and not self._camber_ramping:
            self._air_camber_preview = not self._lowered
            self._write_camber(self._air_camber_target())

    def _set_camber_curve(self, values) -> None:
        self._camber_curve = list(values)

    def _reset_camber(self) -> None:
        self._cancel_camber_ramp()
        stock = self.stock_camber
        self._camber = list(stock) if stock else [None, None, None, None]
        for key, value in zip(
            ("camber_fl", "camber_fr", "camber_rr", "camber_rl"), self._camber, strict=True
        ):
            slider = self._widgets.get(key)
            if slider is not None and value is not None:
                slider.set_value(value)
        for key, value in (
            ("camber_axle_front", self._camber[0]),
            ("camber_axle_rear", self._camber[3]),
        ):
            slider = self._widgets.get(key)
            if slider is not None and value is not None:
                slider.set_value(value)
        self._write_camber()
        self._camber = [None, None, None, None]

    def _write_track(self) -> bool:
        """Write held per-wheel track-width delta. vehicle.set_track_width() takes a
        delta in metres against each wheel's stock curve, so a None entry leaves
        that wheel untouched and repeating the same call doesn't shift it further.
        """
        vehicle = self.vehicle
        if vehicle is None or not self._track_enabled:
            return False
        ok = True
        wrote = False
        for wheel, delta_mm in enumerate(self._track):
            if delta_mm is None:
                continue
            baseline = self.stock_track[wheel]
            if baseline is None:
                continue
            wrote = True
            ok = vehicle.set_track_width(wheel, baseline, delta_mm / 1000.0) and ok
        return wrote and ok

    def _set_track(self, wheel: int, value_mm: float) -> None:
        self._track[wheel] = float(value_mm)
        self._write_track()

    def _set_track_axle(self, axle: str, value_mm: float) -> None:
        """Mirror-mode entry point: one value per axle, both wheels move together."""
        value_mm = float(value_mm)
        if axle == "front":
            self._track[0] = self._track[1] = value_mm
        else:
            self._track[3] = self._track[2] = value_mm
        self._write_track()

    def _set_track_mirror(self, enabled: bool) -> None:
        self._track_mirror = bool(enabled)
        self._sync_controls()

    def _reset_track(self) -> None:
        vehicle = self.vehicle
        if vehicle is not None and all(curve is not None for curve in self.stock_track):
            for wheel, curve in enumerate(self.stock_track):
                vehicle.set_track_width(wheel, curve, 0.0)
        for key in (
            "track_fl",
            "track_fr",
            "track_rr",
            "track_rl",
            "track_axle_front",
            "track_axle_rear",
        ):
            slider = self._widgets.get(key)
            if slider is not None:
                slider.set_value(0.0)
        self._track = [None, None, None, None]

    def _configured_toe(self) -> list[float | None]:
        """Held toe, filling unset corners from the captured stock values."""
        stock = self.stock_toe or [None] * WHEEL_COUNT
        return [
            value if value is not None else stock[index] for index, value in enumerate(self._toe)
        ]

    def _write_toe(self, values=None) -> bool:
        vehicle = self.vehicle
        if vehicle is None or not self._toe_enabled:
            return False
        target = self._configured_toe() if values is None else values
        if any(value is None for value in target):
            return False
        return bool(vehicle.set_toe(target))

    def _set_toe(self, wheel: int, value: float) -> None:
        self._toe[wheel] = float(value)
        self._write_toe()

    def _set_toe_axle(self, axle: str, value: float) -> None:
        """Mirror-mode entry point: one value per axle, right wheel negated."""
        value = float(value)
        if axle == "front":
            self._toe[0], self._toe[1] = value, -value
        else:
            self._toe[3], self._toe[2] = value, -value
        self._write_toe()

    def _set_toe_mirror(self, enabled: bool) -> None:
        self._toe_mirror = bool(enabled)
        self._sync_controls()

    def _reset_toe(self) -> None:
        stock = self.stock_toe
        self._toe = list(stock) if stock else [None, None, None, None]
        for key, value in zip(("toe_fl", "toe_fr", "toe_rr", "toe_rl"), self._toe, strict=True):
            slider = self._widgets.get(key)
            if slider is not None and value is not None:
                slider.set_value(value)
        for key, value in (
            ("toe_axle_front", self._toe[0]),
            ("toe_axle_rear", self._toe[3]),
        ):
            slider = self._widgets.get(key)
            if slider is not None and value is not None:
                slider.set_value(value)
        self._write_toe()
        self._toe = [None, None, None, None]

    def set_airride_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._airride_enabled:
            return

        self._airride_enabled = enabled
        self._edge.reset()

        if not enabled and self._lowered and not self._bounce and not self._hydraulics:
            vehicle = self.vehicle
            start_camber = vehicle.camber if self._air_camber and vehicle is not None else None
            self._cancel_ramp()
            self._cancel_camber_ramp()
            self._lowered = False
            self._air_camber_preview = False
            if self.stock and vehicle is not None:
                start = vehicle.ride_height or self._target(True)
                self._start_ramp(
                    start,
                    self._target(False),
                    self._ramp_seconds,
                    reverse=True,
                )
                if self._air_camber and start_camber:
                    camber_target = self._configured_camber()
                    if all(value is not None for value in camber_target):
                        self._start_camber_ramp(start_camber, camber_target, self._ramp_seconds)

        self._controls_dirty = True

    def toggle(self) -> None:
        if not self._enabled or not self._airride_enabled:
            return
        if not self.stock or self.vehicle is None:
            return
        if self._bounce or self._hydraulics:
            return
        start_camber = self.vehicle.camber if self._air_camber else None
        self._cancel_ramp()
        self._cancel_camber_ramp()
        self._lowered = not self._lowered
        self._air_camber_preview = False

        if self._lowered:
            self._slam.play()
        start = self.vehicle.ride_height or self._target(not self._lowered)
        self._start_ramp(
            start, self._target(self._lowered), self._ramp_seconds, reverse=not self._lowered
        )
        if self._air_camber:
            camber_target = (
                self._air_camber_target() if self._lowered else self._configured_camber()
            )
            if start_camber and all(value is not None for value in camber_target):
                self._start_camber_ramp(start_camber, camber_target, self._ramp_seconds)

    def set_bounce(self, enabled: bool) -> None:
        """Start or stop the continuous up/down cycle."""
        enabled = bool(enabled) and self._enabled
        if enabled == self._bounce:
            return
        self._bounce = enabled
        if enabled:
            if self._hydraulics:
                self.set_hydraulics(False)
            if not self.stock or self.vehicle is None:
                self._bounce = False
                self._controls_dirty = True
                return
            self._cancel_ramp()
            self._bounce_cancel.clear()
            self._bounce_thread = threading.Thread(
                target=self._bounce_loop, daemon=True, name="neptune-maybach"
            )
            self._bounce_thread.start()
            if self._bounce_audio:
                self._maybach.start()
        else:
            self._stop_bounce()

    def _stop_bounce(self) -> None:
        self._maybach.stop()
        self._bounce_cancel.set()
        thread = self._bounce_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._bounce_thread = None

        if self.stock and self.vehicle is not None:
            current = self.vehicle.ride_height
            self._start_ramp(
                current or self._baseline(), self._target(self._lowered), SETTLE_SECONDS
            )

    def _set_bounce_audio(self, enabled: bool) -> None:
        self._bounce_audio = bool(enabled)

        if self._bounce and self._bounce_audio:
            self._maybach.start()
        else:
            self._maybach.stop()

    def _bounce_loop(self) -> None:
        """Ride the car between the two heights until switched off.

        A cosine gives a smooth turnaround at both ends; a linear ramp would snap direction and
        make the solver jolt — the same "ramp, never step" rule the air ride follows.
        """
        import math

        interval = 1.0 / BOUNCE_HZ
        phase = 0.0
        try:
            while not self._bounce_cancel.is_set():
                if self.vehicle is None or not self.stock:
                    return
                low, high = sorted((self._bounce_low, self._bounce_high))

                phase += 2.0 * math.pi * self._bounce_speed * interval
                if phase > 2.0 * math.pi:
                    phase -= 2.0 * math.pi
                fraction = (1.0 - math.cos(phase)) * 0.5
                target = low + (high - low) * fraction

                heights = [self._clamp(target, wheel) for wheel in range(WHEEL_COUNT)]
                if not self._write(heights):
                    return
                time.sleep(interval)
        except Exception:
            return

    def _pose_ramp_segment(self, start, end, seconds: float) -> bool:
        """Smoothly move all four ride-height targets for one pose stage."""
        if not start or not end or len(start) != len(end):
            return False
        seconds = max(0.05, float(seconds))
        steps = max(1, int(seconds * RAMP_HZ))
        interval = seconds / steps
        try:
            for step in range(steps + 1):
                if self._cancel.is_set() or self.vehicle is None or not self._hydraulics:
                    return False
                fraction = step / steps
                eased = fraction * fraction * (3.0 - 2.0 * fraction)
                values = [
                    origin + (destination - origin) * eased
                    for origin, destination in zip(start, end, strict=True)
                ]
                if not self._write(values):
                    return False
                if step < steps:
                    time.sleep(interval)
        except Exception:
            return False
        return True

    def _set_hydraulics_manual_height(self, value: float) -> None:
        self._hydraulics_manual_height = max(
            HYDRAULICS_LIFT_MIN_M,
            min(HYDRAULICS_LIFT_MAX_M, float(value)),
        )

    def _set_hydraulics_manual_speed(self, value: float) -> None:
        self._hydraulics_manual_speed = max(0.01, min(0.20, float(value)))

    def _manual_move_to(self, target: list[float], name: str) -> None:
        """Perform one user-commanded movement and HOLD the result."""
        if not self._hydraulics or self.vehicle is None or not self.stock:
            return

        current = self.vehicle.ride_height or self._baseline()
        if not current or len(current) < WHEEL_COUNT:
            return

        self._cancel_ramp()
        self._cancel.clear()

        # Exactly one sound for exactly one explicit command.
        self._play_hydraulic_sound(force=True, hop=True)

        self._thread = threading.Thread(
            target=self._pose_ramp_segment,
            daemon=True,
            name=f"neptune-hydraulics-{name}",
            args=(list(current), list(target), self._hydraulics_manual_speed),
        )
        self._thread.start()

    def _manual_pulse_to(
        self,
        target: list[float],
        baseline: list[float],
        name: str,
    ) -> None:
        """Perform one hydraulic hit: move up once, then return to baseline."""
        if not self._hydraulics or self.vehicle is None or not self.stock:
            return

        current = self.vehicle.ride_height or baseline
        if not current or len(current) < WHEEL_COUNT:
            return

        self._cancel_ramp()
        self._cancel.clear()

        # The sound is tied to the input edge, not to either half of the motion.
        self._play_hydraulic_sound(force=True, hop=True)

        self._thread = threading.Thread(
            target=self._manual_pulse_sequence,
            daemon=True,
            name=f"neptune-hydraulics-pulse-{name}",
            args=(
                list(current),
                list(target),
                list(baseline),
                self._hydraulics_manual_speed,
            ),
        )
        self._thread.start()

    def _manual_pulse_sequence(
        self,
        start: list[float],
        target: list[float],
        baseline: list[float],
        seconds: float,
    ) -> None:
        """Move to the selected hydraulic height once, then return to normal."""
        if not self._pose_ramp_segment(start, target, seconds):
            return
        if self._cancel.is_set() or not self._hydraulics:
            return
        self._pose_ramp_segment(target, baseline, seconds)

    def _manual_hydraulics_press(self, group: str) -> None:
        """Hop the selected axle/side/corner once, then return to normal."""
        if not self._hydraulics or self.vehicle is None or not self.stock:
            return

        wheels = HYDRAULICS_MANUAL_GROUPS.get(group)
        if not wheels:
            return

        baseline = self._baseline()
        if len(baseline) < WHEEL_COUNT:
            return

        target = list(baseline)
        amount = self._hydraulics_manual_height
        for wheel in wheels:
            target[wheel] = self._clamp(baseline[wheel] + amount, wheel)

        self._hydraulics_down_pose = None
        self._manual_pulse_to(target, baseline, f"up-{group}")

    def _manual_down_pose_press(self, pose: str) -> None:
        """Move once into a static front/back/left/right down pose and HOLD it."""
        if not self._hydraulics or self.vehicle is None or not self.stock:
            return

        wheels = HYDRAULICS_DOWN_POSES.get(pose)
        if not wheels:
            return

        baseline = self._baseline()
        if len(baseline) < WHEEL_COUNT:
            return

        # A pose is a clean static stance: selected wheels down, every other
        # wheel back at stock. This avoids stacking old poses together.
        target = list(baseline)
        amount = self._hydraulics_manual_height
        for wheel in wheels:
            target[wheel] = self._clamp(baseline[wheel] - amount, wheel)

        self._hydraulics_down_pose = pose
        self._manual_move_to(target, pose)

    def _manual_all_press(self, action: str) -> None:
        """Raise or lower all four wheels once and HOLD the resulting height."""
        if not self._hydraulics or self.vehicle is None or not self.stock:
            return

        direction = HYDRAULICS_ALL_ACTIONS.get(action)
        if direction is None:
            return

        baseline = self._baseline()
        if len(baseline) < WHEEL_COUNT:
            return

        amount = self._hydraulics_manual_height
        target = [
            self._clamp(baseline[wheel] + direction * amount, wheel)
            for wheel in range(WHEEL_COUNT)
        ]

        self._hydraulics_down_pose = None
        self._manual_move_to(target, action)

    def _update_geometry_visibility(self) -> None:
        """Show camber/track/toe controls per their own toggle AND their mirror mode.

        Each card has an on/off toggle and a mirror toggle, so a panel is visible only
        when the card is enabled and that mirror mode is selected. Called directly from
        the enable handlers as well as from `_sync_controls`, so toggling is immediate
        rather than waiting for the next interface refresh.
        """
        for prefix, on, mirrored in (
            ("camber", self._camber_enabled, self._camber_mirror),
            ("track", self._track_enabled, self._track_mirror),
            ("toe", self._toe_enabled, self._toe_mirror),
        ):
            wheels_panel = self._widgets.get(f"{prefix}_wheels_panel")
            if wheels_panel is not None:
                wheels_panel.setVisible(on and not mirrored)
            axle_panel = self._widgets.get(f"{prefix}_axle_panel")
            if axle_panel is not None:
                axle_panel.setVisible(on and mirrored)
            for suffix in ("mirror", "reset"):
                extra = self._widgets.get(f"{prefix}_{suffix}")
                if extra is not None:
                    extra.setVisible(on)

    def _update_hydraulics_visibility(self) -> None:
        """Show manual hydraulics details only while hydraulics is armed."""
        for key in (
            "hydraulics_up_panel",
            "hydraulics_pose_panel",
            "hydraulics_height_note",
            "hydraulics_manual_height",
            "hydraulics_manual_speed",
            "hydraulics_bind_note",
            "hydraulics_bindings_panel",
        ):
            widget = self._widgets.get(key)
            if widget is not None:
                widget.setVisible(self._hydraulics)

    def _set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self.settings.remember("suspension_enabled", enabled)
        if not enabled:
            self.restore()
        self._controls_dirty = True


    def set_hydraulics(self, enabled: bool) -> None:
        enabled = bool(enabled) and self._enabled
        if enabled == self._hydraulics:
            return

        if enabled:
            if not self.stock or self.vehicle is None:
                self._hydraulics = False
                self._controls_dirty = True
                return

            if self._bounce:
                self.set_bounce(False)

            self._hydraulics = True
            self._cancel_ramp()
            self._cancel.clear()

            self._hydraulics_down_pose = None
            for edge in self._hydraulics_edges.values():
                edge.reset()
            # Manual-only: turning Hydraulics on arms the controls but does not
            # move the suspension or play a sound by itself.
        else:
            self._hydraulics = False
            self._stop_hydraulics()

        # Reveal or hide Height, Speed and the bind rows to match the new state.
        self._controls_dirty = True

    def _cancel_hydraulics_no_restore(self) -> None:
        """Stop any in-flight hydraulics movement without touching ride height or
        disarming the feature itself; used on a car change.

        Deliberately does NOT set `self._hydraulics = False`. Manual hydraulics
        doesn't move anything on its own — it only reacts to explicit button
        presses/binds — so there is nothing unsafe about leaving it armed across
        a car swap. Forcing it off here used to leave the toggle showing off with
        no feedback, and re-enabling it during the brief window before the new
        car's `self.stock` is captured would silently fail and immediately flip
        back off. Staying armed means it just keeps working once the new car's
        stock height is captured, with no user action required.
        """
        self._hydraulics_cancel.set()
        thread = self._hydraulics_thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=1.0)
        self._hydraulics_thread = None
        self._hydraulics_down_pose = None
        for edge in self._hydraulics_edges.values():
            edge.reset()

    def _stop_hydraulics(self) -> None:
        self._hydraulics_manual_up = [False] * WHEEL_COUNT
        self._hydraulics_down_pose = None
        self._hydraulics_cancel.set()
        thread = self._hydraulics_thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=1.0)
        self._hydraulics_thread = None

        if self.stock and self.vehicle is not None:
            current = self.vehicle.ride_height
            self._cancel_ramp()
            self._start_ramp(
                current or self._baseline(),
                self._target(self._lowered),
                SETTLE_SECONDS,
            )

    def _play_hydraulic_sound(self, force: bool = False, hop: bool = False) -> None:
        """Play one hydraulic sound for an explicit manual action."""
        now = time.monotonic()
        cooldown = HYDRAULICS_SOUND_COOLDOWN

        if not force and now - self._hydraulics_last_sound < cooldown:
            return

        self._hydraulics_last_sound = now

        if hop:
            self._hydraulic_hop.play()
        else:
            self._hydraulic.play()

    def _start_ramp(self, start, end, seconds: float, reverse: bool = False) -> None:
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._ramp,
            daemon=True,
            name="neptune-airride",
            args=(start, end, seconds, reverse),
        )
        self._thread.start()

    def _cancel_ramp(self) -> None:
        self._cancel.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    def _start_camber_ramp(self, start, end, seconds: float) -> None:
        self._camber_cancel.clear()
        self._camber_thread = threading.Thread(
            target=self._camber_ramp,
            daemon=True,
            name="neptune-airride-camber",
            args=(list(start), list(end), float(seconds)),
        )
        self._camber_thread.start()

    def _cancel_camber_ramp(self) -> None:
        self._camber_cancel.set()
        thread = self._camber_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._camber_thread = None

    def _camber_ramp(self, start, end, seconds: float) -> None:
        """Animate table camber without overloading remote memory writes."""
        seconds = max(0.05, float(seconds))
        steps = max(1, int(seconds * CAMBER_RAMP_HZ))
        interval = seconds / steps
        curve = list(self._camber_curve)
        try:
            for step in range(steps + 1):
                if self._camber_cancel.is_set() or self.vehicle is None:
                    return
                fraction = curve_value(curve, step / steps)
                values = [
                    origin + (destination - origin) * fraction
                    for origin, destination in zip(start, end, strict=True)
                ]
                if not self._write_camber(values):
                    return
                if step < steps:
                    time.sleep(interval)
        except Exception:
            return

    def _axle_phase(self, wheel: int, reverse: bool) -> tuple[float, float]:
        if self._sequence == "together":
            return 0.0, 1.0
        is_front = wheel in FRONT_WHEELS
        front_leads = (self._sequence == "front") != reverse
        leads = is_front == front_leads
        lead = 0.0 if leads else (1.0 - AXLE_OVERLAP)
        return lead, AXLE_OVERLAP

    def _ramp(self, start, end, seconds: float, reverse: bool) -> None:
        if not start or not end or len(start) != len(end):
            return
        if any(value is None for value in start) or any(value is None for value in end):
            return

        steps = max(1, int(seconds * RAMP_HZ))
        interval = 1.0 / RAMP_HZ
        phases = [self._axle_phase(index, reverse) for index in range(len(start))]

        try:
            for step in range(steps + 1):
                if self._cancel.is_set() or self.vehicle is None:
                    return
                progress = step / steps
                values = []
                for index, (origin, destination) in enumerate(zip(start, end, strict=True)):
                    lead, span = phases[index]
                    fraction = 0.0 if span <= 0 else (progress - lead) / span
                    fraction = min(1.0, max(0.0, fraction))
                    eased = fraction * fraction * (3.0 - 2.0 * fraction)
                    values.append(origin + (destination - origin) * eased)
                if not self._write(values):
                    return
                time.sleep(interval)
        except Exception:
            return

    def _set_height_enabled(self, enabled: bool) -> None:
        """Arm the ride-height sliders. Turning it off ramps the car back to stock."""
        enabled = bool(enabled)
        if enabled == self._height_enabled:
            return
        self._height_enabled = enabled
        if not self.stock or self.vehicle is None:
            return
        self._cancel_ramp()
        start = self.vehicle.ride_height or self._target(self._lowered)
        # Off means stock height, not "leave it wherever the slider had it".
        end = self._target(self._lowered) if enabled else list(self.stock)
        self._start_ramp(start, end, SETTLE_SECONDS)

    def _set_offset(self, axle: str, value: float) -> None:
        if axle == "front":
            self._front_percent = float(value)
        else:
            self._rear_percent = float(value)
        if not self.stock or self.vehicle is None or not self._height_enabled:
            return
        self._cancel_ramp()
        start = self.vehicle.ride_height or self._target(self._lowered)
        self._start_ramp(start, self._target(self._lowered), SETTLE_SECONDS)

    def _set_floor(self, value: float) -> None:
        self._floor_percent = float(value)
        if not self.stock or self.vehicle is None:
            return
        target = self._target(self._lowered)
        current = self.vehicle.ride_height
        if (
            current
            and len(current) == len(target)
            and all(abs(a - b) < 1e-4 for a, b in zip(current, target, strict=True))
        ):
            return
        self._cancel_ramp()
        self._start_ramp(current or target, target, SETTLE_SECONDS)

    def _set_sequence(self, label: str) -> None:
        for key, text in SEQUENCE_LABELS.items():
            if text == label:
                self._sequence = key
                return

    def _reset_height(self) -> None:
        self._front_percent = 0.0
        self._rear_percent = 0.0
        for key in ("front", "rear"):
            slider = self._widgets.get(key)
            if slider is not None:
                slider.set_value(0.0)
        if self.stock and self.vehicle is not None:
            self._cancel_ramp()
            start = self.vehicle.ride_height or self._target(self._lowered)
            self._start_ramp(start, self._target(self._lowered), SETTLE_SECONDS)

    def _reset_grip(self) -> None:
        self._grip_lateral = 1.0
        self._grip_longitudinal = 1.0
        for key in ("grip_both", "grip_lateral", "grip_longitudinal"):
            slider = self._widgets.get(key)
            if slider is not None:
                slider.set_value(1.0)
        if self.vehicle is not None:
            self._write_grip(self.vehicle, 1.0, 1.0)

    def build_page(self, page) -> None:
        from neptune.ui.widgets.buttons import Button, PrimaryButton

        enable_card = page.add_card("Suspension")
        enabled = ToggleRow(
            "Enable suspension controls",
            self._enabled,
            hint="Shows the suspension controls and permits Neptune to apply ride-height, alignment, air-ride and hydraulics changes.",
        )
        enabled.toggle.toggled_value.connect(self._set_enabled)
        self._widgets["enabled"] = enabled
        enable_card.add(enabled)
        feature_widgets = []

        grip_card = page.add_card("Grip", HINT_GRIP)
        grip_toggle = ToggleRow("Grip", self._grip_enabled, hint=HINT_GRIP_ENABLED)
        grip_toggle.toggle.toggled_value.connect(self._set_grip_enabled)
        self._widgets["grip_enabled"] = grip_toggle
        grip_card.add(grip_toggle)

        both = SliderRow(
            "Both",
            GRIP_MIN,
            GRIP_MAX,
            1.0,
            step=0.05,
            decimals=2,
            unit="x",
            hint=HINT_GRIP_BOTH,
        )
        both.changed.connect(self._set_grip_both)
        self._widgets["grip_both"] = both
        grip_card.add(both)

        lateral = SliderRow(
            "Cornering",
            GRIP_MIN,
            GRIP_MAX,
            1.0,
            step=0.05,
            decimals=2,
            unit="x",
            hint=HINT_GRIP_LATERAL,
        )
        lateral.changed.connect(self._set_grip_lateral)
        self._widgets["grip_lateral"] = lateral
        grip_card.add(lateral)

        longitudinal = SliderRow(
            "Straight-line",
            GRIP_MIN,
            GRIP_MAX,
            1.0,
            step=0.05,
            decimals=2,
            unit="x",
            hint=HINT_GRIP_LONGITUDINAL,
        )
        longitudinal.changed.connect(self._set_grip_longitudinal)
        self._widgets["grip_longitudinal"] = longitudinal
        grip_card.add(longitudinal)

        grip_reset = Button("Reset to stock grip")
        grip_reset.clicked.connect(self._reset_grip)
        grip_card.add(grip_reset)
        bind_progressive(grip_toggle, both, lateral, longitudinal, grip_reset)
        feature_widgets.append(grip_card)

        height_card = page.add_card("Ride height", "Moves the height the car normally sits at.")
        feature_widgets.append(height_card)
        self._widgets["height_card"] = height_card

        height_toggle = ToggleRow("Ride height", self._height_enabled, hint=HINT_HEIGHT_ENABLED)
        height_toggle.toggle.toggled_value.connect(self._set_height_enabled)
        self._widgets["height_enabled"] = height_toggle
        height_card.add(height_toggle)

        front = SliderRow(
            "Front",
            -LOWER_PERCENT_MAX,
            RAISE_PERCENT_MAX,
            0.0,
            step=1,
            decimals=0,
            unit="%",
            hint=HINT_AXLE,
        )
        front.changed.connect(lambda value: self._set_offset("front", value))
        self._widgets["front"] = front
        height_card.add(front)

        rear = SliderRow(
            "Rear",
            -LOWER_PERCENT_MAX,
            RAISE_PERCENT_MAX,
            0.0,
            step=1,
            decimals=0,
            unit="%",
            hint=HINT_AXLE,
        )
        rear.changed.connect(lambda value: self._set_offset("rear", value))
        self._widgets["rear"] = rear
        height_card.add(rear)

        reset_button = Button("Reset to stock height")
        reset_button.clicked.connect(self._reset_height)
        height_card.add(reset_button)
        bind_progressive(height_toggle, front, rear, reset_button)

        camber_card = page.add_card("Camber", HINT_CAMBER)
        feature_widgets.append(camber_card)
        self._widgets["camber_card"] = camber_card

        camber_banner = Banner("", "warn")
        self._widgets["camber_banner"] = camber_banner
        camber_card.add(camber_banner)

        camber_toggle = ToggleRow("Camber", self._camber_enabled, hint=HINT_CAMBER_ENABLED)
        camber_toggle.toggle.toggled_value.connect(self._set_camber_enabled)
        self._widgets["camber_enabled"] = camber_toggle
        camber_card.add(camber_toggle)

        camber_mirror = ToggleRow("Mirror axles", self._camber_mirror, hint=HINT_CAMBER_MIRROR)
        camber_mirror.toggle.toggled_value.connect(self._set_camber_mirror)
        self._widgets["camber_mirror"] = camber_mirror
        camber_card.add(camber_mirror)

        stock_camber = self.stock_camber

        wheels_panel = QWidget()
        wheels_panel.setVisible(not self._camber_mirror)
        wheels_layout = QVBoxLayout(wheels_panel)
        wheels_layout.setContentsMargins(0, 0, 0, 0)
        wheels_layout.setSpacing(12)
        for wheel, (key, label) in enumerate(
            (
                ("camber_fl", "Front Left"),
                ("camber_fr", "Front Right"),
                ("camber_rr", "Rear Right"),
                ("camber_rl", "Rear Left"),
            )
        ):
            camber_slider = SliderRow(
                label,
                CAMBER_MIN_DEG,
                CAMBER_MAX_DEG,
                stock_camber[wheel] if stock_camber else 0.0,
                step=0.1,
                decimals=1,
                unit="°",
            )
            camber_slider.changed.connect(lambda value, w=wheel: self._set_camber(w, value))
            self._widgets[key] = camber_slider
            wheels_layout.addWidget(camber_slider)
        self._widgets["camber_wheels_panel"] = wheels_panel
        camber_card.add(wheels_panel)

        axle_panel = QWidget()
        axle_panel.setVisible(self._camber_mirror)
        axle_layout = QVBoxLayout(axle_panel)
        axle_layout.setContentsMargins(0, 0, 0, 0)
        axle_layout.setSpacing(12)
        for key, axle, label in (
            ("camber_axle_front", "front", "Front"),
            ("camber_axle_rear", "rear", "Rear"),
        ):
            axle_slider = SliderRow(
                label,
                CAMBER_MIN_DEG,
                CAMBER_MAX_DEG,
                stock_camber[0 if axle == "front" else 3] if stock_camber else 0.0,
                step=0.1,
                decimals=1,
                unit="°",
            )
            axle_slider.changed.connect(lambda value, a=axle: self._set_camber_axle(a, value))
            self._widgets[key] = axle_slider
            axle_layout.addWidget(axle_slider)
        self._widgets["camber_axle_panel"] = axle_panel
        camber_card.add(axle_panel)

        camber_reset_button = Button("Reset to stock camber")
        camber_reset_button.clicked.connect(self._reset_camber)
        self._widgets["camber_reset"] = camber_reset_button
        camber_card.add(camber_reset_button)

        track_card = page.add_card("Track Width", HINT_TRACK)
        feature_widgets.append(track_card)

        track_banner = Banner("", "warn")
        self._widgets["track_banner"] = track_banner
        track_card.add(track_banner)

        track_toggle = ToggleRow("Track width", self._track_enabled, hint=HINT_TRACK_ENABLED)
        track_toggle.toggle.toggled_value.connect(self._set_track_enabled)
        self._widgets["track_enabled"] = track_toggle
        track_card.add(track_toggle)

        track_mirror = ToggleRow("Mirror axles", self._track_mirror, hint=HINT_TRACK_MIRROR)
        track_mirror.toggle.toggled_value.connect(self._set_track_mirror)
        self._widgets["track_mirror"] = track_mirror
        track_card.add(track_mirror)

        track_wheels_panel = QWidget()
        track_wheels_panel.setVisible(not self._track_mirror)
        track_wheels_layout = QVBoxLayout(track_wheels_panel)
        track_wheels_layout.setContentsMargins(0, 0, 0, 0)
        track_wheels_layout.setSpacing(12)
        for wheel, (key, label) in enumerate(
            (
                ("track_fl", "Front Left"),
                ("track_fr", "Front Right"),
                ("track_rr", "Rear Right"),
                ("track_rl", "Rear Left"),
            )
        ):
            track_slider = SliderRow(
                label,
                TRACK_MIN_MM,
                TRACK_MAX_MM,
                0.0,
                step=1,
                decimals=0,
                unit="mm",
            )
            track_slider.changed.connect(lambda value, w=wheel: self._set_track(w, value))
            self._widgets[key] = track_slider
            track_wheels_layout.addWidget(track_slider)
        self._widgets["track_wheels_panel"] = track_wheels_panel
        track_card.add(track_wheels_panel)

        track_axle_panel = QWidget()
        track_axle_panel.setVisible(self._track_mirror)
        track_axle_layout = QVBoxLayout(track_axle_panel)
        track_axle_layout.setContentsMargins(0, 0, 0, 0)
        track_axle_layout.setSpacing(12)
        for key, axle, label in (
            ("track_axle_front", "front", "Front"),
            ("track_axle_rear", "rear", "Rear"),
        ):
            track_axle_slider = SliderRow(
                label,
                TRACK_MIN_MM,
                TRACK_MAX_MM,
                0.0,
                step=1,
                decimals=0,
                unit="mm",
            )
            track_axle_slider.changed.connect(lambda value, a=axle: self._set_track_axle(a, value))
            self._widgets[key] = track_axle_slider
            track_axle_layout.addWidget(track_axle_slider)
        self._widgets["track_axle_panel"] = track_axle_panel
        track_card.add(track_axle_panel)

        track_reset_button = Button("Reset to stock track width")
        track_reset_button.clicked.connect(self._reset_track)
        self._widgets["track_reset"] = track_reset_button
        track_card.add(track_reset_button)

        toe_card = page.add_card("Toe", HINT_TOE)
        feature_widgets.append(toe_card)

        toe_banner = Banner("", "warn")
        self._widgets["toe_banner"] = toe_banner
        toe_card.add(toe_banner)

        toe_toggle = ToggleRow("Toe", self._toe_enabled, hint=HINT_TOE_ENABLED)
        toe_toggle.toggle.toggled_value.connect(self._set_toe_enabled)
        self._widgets["toe_enabled"] = toe_toggle
        toe_card.add(toe_toggle)

        toe_mirror = ToggleRow("Mirror axles", self._toe_mirror, hint=HINT_TOE_MIRROR)
        toe_mirror.toggle.toggled_value.connect(self._set_toe_mirror)
        self._widgets["toe_mirror"] = toe_mirror
        toe_card.add(toe_mirror)

        stock_toe = self.stock_toe

        toe_wheels_panel = QWidget()
        toe_wheels_panel.setVisible(not self._toe_mirror)
        toe_wheels_layout = QVBoxLayout(toe_wheels_panel)
        toe_wheels_layout.setContentsMargins(0, 0, 0, 0)
        toe_wheels_layout.setSpacing(12)
        for wheel, (key, label) in enumerate(
            (
                ("toe_fl", "Front Left"),
                ("toe_fr", "Front Right"),
                ("toe_rr", "Rear Right"),
                ("toe_rl", "Rear Left"),
            )
        ):
            toe_slider = SliderRow(
                label,
                TOE_MIN_DEG,
                TOE_MAX_DEG,
                stock_toe[wheel] if stock_toe else 0.0,
                step=0.1,
                decimals=1,
                unit="°",
            )
            toe_slider.changed.connect(lambda value, w=wheel: self._set_toe(w, value))
            self._widgets[key] = toe_slider
            toe_wheels_layout.addWidget(toe_slider)
        self._widgets["toe_wheels_panel"] = toe_wheels_panel
        toe_card.add(toe_wheels_panel)

        toe_axle_panel = QWidget()
        toe_axle_panel.setVisible(self._toe_mirror)
        toe_axle_layout = QVBoxLayout(toe_axle_panel)
        toe_axle_layout.setContentsMargins(0, 0, 0, 0)
        toe_axle_layout.setSpacing(12)
        for key, axle, label in (
            ("toe_axle_front", "front", "Front"),
            ("toe_axle_rear", "rear", "Rear"),
        ):
            toe_axle_slider = SliderRow(
                label,
                TOE_MIN_DEG,
                TOE_MAX_DEG,
                stock_toe[0 if axle == "front" else 3] if stock_toe else 0.0,
                step=0.1,
                decimals=1,
                unit="°",
            )
            toe_axle_slider.changed.connect(lambda value, a=axle: self._set_toe_axle(a, value))
            self._widgets[key] = toe_axle_slider
            toe_axle_layout.addWidget(toe_axle_slider)
        self._widgets["toe_axle_panel"] = toe_axle_panel
        toe_card.add(toe_axle_panel)

        toe_reset_button = Button("Reset to stock toe")
        toe_reset_button.clicked.connect(self._reset_toe)
        self._widgets["toe_reset"] = toe_reset_button
        toe_card.add(toe_reset_button)

        air_card = page.add_card("Air ride", "Drops the car on a key press.")
        feature_widgets.append(air_card)

        airride_enabled = ToggleRow(
            "Enable air ride",
            self._airride_enabled,
            hint=HINT_AIRRIDE_ENABLED,
        )
        airride_enabled.toggle.toggled_value.connect(self.set_airride_enabled)
        self._widgets["airride_enabled"] = airride_enabled
        air_card.add(airride_enabled)

        toggle_button = PrimaryButton("Drop or lift now")
        toggle_button.clicked.connect(self.toggle)
        self._widgets["airride_trigger"] = toggle_button
        air_card.add(toggle_button)

        bind_button = BindButton(self.binding(), settings=self.settings, key="suspension.airride")
        bind_button.bound.connect(
            lambda binding: self.settings.set_binding("suspension.airride", binding)
        )
        self._widgets["bind"] = bind_button
        air_control_row = FieldRow("Control", bind_button)
        air_card.add(air_control_row)

        air_card.add_divider()

        drop = SliderRow(
            "Drop by",
            0.0,
            LOWER_PERCENT_MAX,
            DROP_PERCENT_DEFAULT,
            step=1,
            decimals=0,
            unit="%",
            hint=HINT_DROP,
        )
        drop.changed.connect(lambda value: setattr(self, "_drop_percent", float(value)))
        self._widgets["drop"] = drop
        air_card.add(drop)

        floor = SliderRow(
            "Lowest allowed",
            0.0,
            100.0,
            DEFAULT_FLOOR_PERCENT,
            step=1,
            decimals=0,
            unit="%",
            hint=HINT_FLOOR,
        )
        floor.changed.connect(self._set_floor)
        self._widgets["floor"] = floor
        air_card.add(floor)

        ramp = SliderRow(
            "Movement time",
            0.3,
            6.0,
            DEFAULT_RAMP_SECONDS,
            step=0.1,
            decimals=1,
            unit="s",
            hint=HINT_RAMP,
        )
        ramp.changed.connect(lambda value: setattr(self, "_ramp_seconds", float(value)))
        self._widgets["ramp"] = ramp
        air_card.add(ramp)

        sequence = Segmented(list(SEQUENCE_LABELS.values()), SEQUENCE_LABELS[self._sequence])
        sequence.changed.connect(self._set_sequence)
        self._widgets["sequence"] = sequence
        air_order_row = FieldRow("Order", sequence, hint=HINT_SEQUENCE)
        air_card.add(air_order_row)
        bind_progressive(
            airride_enabled,
            toggle_button,
            air_control_row,
            drop,
            floor,
            ramp,
            air_order_row,
        )

        air_camber_card = page.add_card("Air ride camber", HINT_AIR_CAMBER)
        feature_widgets.append(air_camber_card)

        air_camber = ToggleRow("Animate with air ride", self._air_camber, hint=HINT_AIR_CAMBER)
        air_camber.toggle.toggled_value.connect(self._set_air_camber)
        self._widgets["air_camber"] = air_camber
        air_camber_card.add(air_camber)
        air_camber_targets: list = []

        for key, axle, label, value in (
            (
                "air_camber_front",
                "front",
                "Lowered front",
                self._air_camber_front,
            ),
            (
                "air_camber_rear",
                "rear",
                "Lowered rear",
                self._air_camber_rear,
            ),
        ):
            target = SliderRow(
                label,
                CAMBER_MIN_DEG,
                CAMBER_MAX_DEG,
                value,
                step=0.1,
                decimals=1,
                unit="°",
                hint="The left-wheel angle when the car is fully lowered; the right mirrors it.",
            )
            target.changed.connect(lambda amount, a=axle: self._set_air_camber_target(a, amount))
            self._widgets[key] = target
            air_camber_targets.append(target)
            air_camber_card.add(target)

        curve = TransitionCurve(self._camber_curve)
        curve.changed.connect(self._set_camber_curve)
        self._widgets["camber_curve"] = curve
        air_camber_card.add(curve)

        reset_curve = Button("Reset camber curve")
        reset_curve.setToolTip(HINT_CAMBER_CURVE)
        reset_curve.clicked.connect(curve.reset)
        air_camber_card.add(reset_curve)
        bind_progressive(air_camber, *air_camber_targets, curve, reset_curve)

        bounce_card = page.add_card("Maybach bounce", "Rocks the car up and down on repeat.")
        feature_widgets.append(bounce_card)

        bounce_toggle = ToggleRow("Bounce", self._bounce, hint=HINT_BOUNCE)
        bounce_toggle.toggle.toggled_value.connect(self.set_bounce)
        self._widgets["bounce"] = bounce_toggle
        bounce_card.add(bounce_toggle)

        low = SliderRow(
            "Lowest",
            BOUNCE_MIN_M,
            BOUNCE_MAX_M,
            self._bounce_low,
            step=0.005,
            decimals=3,
            unit="m",
            hint=HINT_BOUNCE_RANGE,
        )
        low.changed.connect(lambda value: setattr(self, "_bounce_low", float(value)))
        self._widgets["bounce_low"] = low
        bounce_card.add(low)

        high = SliderRow(
            "Highest",
            BOUNCE_MIN_M,
            BOUNCE_MAX_M,
            self._bounce_high,
            step=0.005,
            decimals=3,
            unit="m",
        )
        high.changed.connect(lambda value: setattr(self, "_bounce_high", float(value)))
        self._widgets["bounce_high"] = high
        bounce_card.add(high)

        speed = SliderRow(
            "Speed",
            BOUNCE_SPEED_MIN,
            BOUNCE_SPEED_MAX,
            self._bounce_speed,
            step=0.1,
            decimals=1,
            unit="Hz",
            hint=HINT_BOUNCE_SPEED,
        )
        speed.changed.connect(lambda value: setattr(self, "_bounce_speed", float(value)))
        self._widgets["bounce_speed"] = speed
        bounce_card.add(speed)

        audio_toggle = ToggleRow(
            "Play sound",
            self._bounce_audio,
            hint=HINT_BOUNCE_AUDIO,
        )
        audio_toggle.toggle.toggled_value.connect(self._set_bounce_audio)
        self._widgets["bounce_audio"] = audio_toggle
        bounce_card.add(audio_toggle)
        bind_progressive(bounce_toggle, low, high, speed, audio_toggle)

        hydraulics_card = page.add_card(
            "Hydraulics",
            "Fully manual lowrider hydraulics. Nothing moves until you press a control.",
        )
        feature_widgets.append(hydraulics_card)

        hydraulics_toggle = ToggleRow(
            "Hydraulics",
            self._hydraulics,
            hint="Enable the manual controls. Toggling this does not move the suspension or play audio.",
        )
        hydraulics_toggle.toggle.toggled_value.connect(self.set_hydraulics)
        self._widgets["hydraulics"] = hydraulics_toggle
        hydraulics_card.add(hydraulics_toggle)

        # Row 1: direct up controls.
        up_panel = QWidget()
        up_row = QHBoxLayout(up_panel)
        up_row.setContentsMargins(0, 0, 0, 0)
        up_row.setSpacing(6)

        for group, label in (
            ("front", "Front"),
            ("rear", "Back"),
            ("left", "Left"),
            ("right", "Right"),
            ("fl", "FL"),
            ("fr", "FR"),
            ("rl", "RL"),
            ("rr", "RR"),
        ):
            button = Button(label)
            button.setMinimumWidth(0)
            button.clicked.connect(
                lambda _checked=False, g=group: self._manual_hydraulics_press(g)
            )
            up_row.addWidget(button, 1)

        self._widgets["hydraulics_up_panel"] = up_panel
        hydraulics_card.add(up_panel)

        # Row 2: static poses and whole-car positions.
        pose_panel = QWidget()
        pose_row = QHBoxLayout(pose_panel)
        pose_row.setContentsMargins(0, 0, 0, 0)
        pose_row.setSpacing(6)

        for pose, label in (
            ("front_down", "Front Down Pose"),
            ("rear_down", "Back Down Pose"),
            ("left_down", "Left Down Pose"),
            ("right_down", "Right Down Pose"),
        ):
            button = Button(label)
            button.setMinimumWidth(0)
            button.clicked.connect(
                lambda _checked=False, p=pose: self._manual_down_pose_press(p)
            )
            pose_row.addWidget(button, 1)

        for action, label in (("all_up", "All Up"), ("all_down", "All Down")):
            button = Button(label)
            button.setMinimumWidth(0)
            button.clicked.connect(
                lambda _checked=False, a=action: self._manual_all_press(a)
            )
            pose_row.addWidget(button, 1)

        self._widgets["hydraulics_pose_panel"] = pose_panel
        hydraulics_card.add(pose_panel)

        height_note = SectionHeading(
            "",
            "Height controls how far each command moves. Speed controls how long that one movement takes.",
        )
        self._widgets["hydraulics_height_note"] = height_note
        hydraulics_card.add(height_note)

        manual_height = SliderRow(
            "Height",
            HYDRAULICS_LIFT_MIN_M,
            HYDRAULICS_LIFT_MAX_M,
            self._hydraulics_manual_height,
            step=0.01,
            decimals=2,
            unit="m",
            hint="Distance from stock used by manual up, down-pose and all-up/all-down commands.",
        )
        manual_height.changed.connect(self._set_hydraulics_manual_height)
        self._widgets["hydraulics_manual_height"] = manual_height
        hydraulics_card.add(manual_height)

        manual_speed = SliderRow(
            "Speed",
            0.01,
            0.20,
            self._hydraulics_manual_speed,
            step=0.01,
            decimals=2,
            unit="s",
            hint="Time taken for one manual hydraulic movement. Lower is faster.",
        )
        manual_speed.changed.connect(self._set_hydraulics_manual_speed)
        self._widgets["hydraulics_manual_speed"] = manual_speed
        hydraulics_card.add(manual_speed)

        bind_note = SectionHeading(
            "",
            "Bind any manual hydraulics command to a keyboard or controller input.",
        )
        self._widgets["hydraulics_bind_note"] = bind_note
        hydraulics_card.add(bind_note)

        bindings_panel = QWidget()
        bindings_layout = QVBoxLayout(bindings_panel)
        bindings_layout.setContentsMargins(0, 0, 0, 0)
        bindings_layout.setSpacing(6)

        for action, label in (
            ("front", "Front"),
            ("rear", "Back"),
            ("left", "Left"),
            ("right", "Right"),
            ("fl", "FL"),
            ("fr", "FR"),
            ("rl", "RL"),
            ("rr", "RR"),
            ("front_down", "Front Down Pose"),
            ("rear_down", "Back Down Pose"),
            ("left_down", "Left Down Pose"),
            ("right_down", "Right Down Pose"),
            ("all_up", "All Up"),
            ("all_down", "All Down"),
        ):
            binding_key = f"suspension.hydraulics.{action}"
            bind = BindButton(
                self.settings.binding(binding_key),
                settings=self.settings,
                key=binding_key,
            )
            bind.bound.connect(
                lambda binding, k=binding_key: self.settings.set_binding(k, binding)
            )
            bindings_layout.addWidget(
                FieldRow(
                    label,
                    bind,
                    hint=f"Bind a key or controller input for {label}.",
                )
            )

        self._widgets["hydraulics_bindings_panel"] = bindings_panel
        hydraulics_card.add(bindings_panel)

        self._update_hydraulics_visibility()

        friction_card = page.add_card(
            "Tire friction", "Live per-wheel slip/friction, as the game reports it."
        )
        friction = StatStrip()
        friction.add("friction_fl", "Front Left", "--")
        friction.add("friction_fr", "Front Right", "--")
        friction.add("friction_rr", "Rear Right", "--")
        friction.add("friction_rl", "Rear Left", "--")
        self._widgets["friction"] = friction
        friction_card.add(friction)

        live_card = page.add_card("Live")
        stats = StatStrip()
        stats.add("state", "State", "Stock")
        stats.add("front", "Front", "--")
        stats.add("rear", "Rear", "--")
        stats.add("camber_fl_live", "Camber Front Left", "--")
        stats.add("camber_fr_live", "Camber Front Right", "--")
        stats.add("camber_rr_live", "Camber Rear Right", "--")
        stats.add("camber_rl_live", "Camber Rear Left", "--")
        self._widgets["stats"] = stats
        live_card.add(stats)

        # These three cards are driven by their own toggle plus a mirror toggle, so they
        # cannot use bind_progressive; set their initial visibility explicitly.
        self._update_geometry_visibility()

        bind_progressive(enabled, *feature_widgets)
        self._update_axle_banner()

    def refresh(self, vehicle) -> None:
        stats = self._widgets.get("stats")
        if stats is None:
            return

        friction = self._widgets.get("friction")
        if friction is not None:
            values = vehicle.wheel_read(O.Wheels.FRICTION) if vehicle is not None else None
            if values and len(values) == WHEEL_COUNT:
                for key, value in zip(
                    ("friction_fl", "friction_fr", "friction_rr", "friction_rl"),
                    values,
                    strict=True,
                ):
                    friction.set(key, f"{value:.2f}", unit="")
            else:
                friction.reset()

        if self._controls_dirty:
            self._controls_dirty = False
            self._sync_controls()

        if vehicle is None or not self.stock:
            stats.reset()
            return

        current = vehicle.ride_height
        if not current:
            return

        if self._ramping:
            stats.set("state", "Moving", T.WARN)
        elif self._lowered:
            stats.set("state", "Lowered", T.ACCENT_BRIGHT)
        else:
            stats.set("state", "Stock", T.TEXT)

        stats.set("front", _axle_text(self.settings, current[0], current[1]), unit="")
        stats.set("rear", _axle_text(self.settings, current[2], current[3]), unit="")

        camber = vehicle.camber
        if camber:
            for key, value in zip(
                ("camber_fl_live", "camber_fr_live", "camber_rr_live", "camber_rl_live"),
                camber,
                strict=True,
            ):
                stats.set(key, f"{value:.2f}", unit="°")

    def save_state(self) -> dict:
        return {
            "enabled": self._enabled,
            "units": "percent",
            "front": self._front_percent,
            "rear": self._rear_percent,
            "drop": self._drop_percent,
            "floor": self._floor_percent,
            "ramp_seconds": self._ramp_seconds,
            "sequence": self._sequence,
            "airride_enabled": self._airride_enabled,
            "bounce_low": self._bounce_low,
            "bounce_high": self._bounce_high,
            "bounce_speed": self._bounce_speed,
            "bounce_audio": self._bounce_audio,
            "air_camber": self._air_camber,
            "air_camber_front": self._air_camber_front,
            "air_camber_rear": self._air_camber_rear,
            "camber_curve": list(self._camber_curve),
            "camber_fl": self._camber[0],
            "camber_fr": self._camber[1],
            "camber_rr": self._camber[2],
            "camber_rl": self._camber[3],
            "camber_mirror": self._camber_mirror,
            "track_fl": self._track[0],
            "track_fr": self._track[1],
            "track_rr": self._track[2],
            "track_rl": self._track[3],
            "track_mirror": self._track_mirror,
            "toe_fl": self._toe[0],
            "toe_fr": self._toe[1],
            "toe_rr": self._toe[2],
            "toe_rl": self._toe[3],
            "toe_mirror": self._toe_mirror,
            "height_enabled": self._height_enabled,
            "camber_enabled": self._camber_enabled,
            "track_enabled": self._track_enabled,
            "toe_enabled": self._toe_enabled,
            "hydraulics_manual_height": self._hydraulics_manual_height,
            "hydraulics_manual_speed": self._hydraulics_manual_speed,
            "grip_enabled": self._grip_enabled,
            "grip_lateral": self._grip_lateral,
            "grip_longitudinal": self._grip_longitudinal,
        }

    def load_state(self, data: dict) -> None:
        data = data or {}

        enabled = bool(data.get("enabled", True))
        self._set_enabled(enabled)  # turning off puts the car back to stock first
        toggle = self._widgets.get("enabled")
        if toggle is not None:
            toggle.set_value(enabled)
        airride_enabled = bool(data.get("airride_enabled", True))

        def _number(key, fallback, low=None, high=None):
            """A preset is a file on disk: treat every field as untrusted.

            A bare `float(...)` here raised on a hand-edited or corrupt preset (a string, None,
            or NaN), which aborted the whole load and left the module half-populated. Bad fields
            now fall back to the default instead.
            """
            try:
                value = float(data.get(key, fallback))
            except (TypeError, ValueError):
                return fallback
            if value != value:
                return fallback
            if low is not None:
                value = max(low, value)
            if high is not None:
                value = min(high, value)
            return value

        legacy = data.get("units") != "percent"
        typical = 0.15

        def _percent(key: str, fallback: float, low: float, high: float) -> float:
            if legacy and key in data:
                metres = _number(key, 0.0, -1.0, 1.0)
                return max(low, min(high, metres / typical * 100.0))
            return _number(key, fallback, low, high)

        self._front_percent = _percent("front", 0.0, -LOWER_PERCENT_MAX, RAISE_PERCENT_MAX)
        self._rear_percent = _percent("rear", 0.0, -LOWER_PERCENT_MAX, RAISE_PERCENT_MAX)
        self._drop_percent = _percent("drop", DROP_PERCENT_DEFAULT, 0.0, LOWER_PERCENT_MAX)
        self._floor_percent = _percent("floor", DEFAULT_FLOOR_PERCENT, 0.0, 100.0)
        self._ramp_seconds = _number("ramp_seconds", DEFAULT_RAMP_SECONDS, 0.3, 6.0)
        self._edge.reset()

        sequence = data.get("sequence")
        if sequence in SEQUENCE_LABELS:
            self._sequence = sequence

        self._bounce_low = _number("bounce_low", BOUNCE_DEFAULT_LOW, BOUNCE_MIN_M, BOUNCE_MAX_M)
        self._bounce_high = _number("bounce_high", BOUNCE_DEFAULT_HIGH, BOUNCE_MIN_M, BOUNCE_MAX_M)
        self._bounce_speed = _number(
            "bounce_speed", BOUNCE_DEFAULT_SPEED, BOUNCE_SPEED_MIN, BOUNCE_SPEED_MAX
        )
        self._bounce_audio = bool(data.get("bounce_audio", True))

        self._air_camber = bool(data.get("air_camber", False))
        self._air_camber_front = _number(
            "air_camber_front", CAMBER_AIR_DEFAULT_FRONT, CAMBER_MIN_DEG, CAMBER_MAX_DEG
        )
        self._air_camber_rear = _number(
            "air_camber_rear", CAMBER_AIR_DEFAULT_REAR, CAMBER_MIN_DEG, CAMBER_MAX_DEG
        )
        self._camber_curve = normalise_curve(data.get("camber_curve", DEFAULT_CURVE))

        self._camber = [
            (
                _number(key, 0.0, CAMBER_MIN_DEG, CAMBER_MAX_DEG)
                if data.get(key) is not None
                else None
            )
            for key in ("camber_fl", "camber_fr", "camber_rr", "camber_rl")
        ]
        self._camber_mirror = bool(data.get("camber_mirror", True))
        if self._enabled and any(value is not None for value in self._camber):
            self._write_active_camber()

        self._track = [
            (_number(key, 0.0, TRACK_MIN_MM, TRACK_MAX_MM) if data.get(key) is not None else None)
            for key in ("track_fl", "track_fr", "track_rr", "track_rl")
        ]
        self._track_mirror = bool(data.get("track_mirror", True))
        if self._enabled and any(value is not None for value in self._track):
            self._write_track()

        self._toe = [
            (_number(key, 0.0, TOE_MIN_DEG, TOE_MAX_DEG) if data.get(key) is not None else None)
            for key in ("toe_fl", "toe_fr", "toe_rr", "toe_rl")
        ]
        self._toe_mirror = bool(data.get("toe_mirror", True))
        if self._enabled and any(value is not None for value in self._toe):
            self._write_toe()

        self._height_enabled = bool(data.get("height_enabled", False))
        self._camber_enabled = bool(data.get("camber_enabled", False))
        self._track_enabled = bool(data.get("track_enabled", False))
        self._toe_enabled = bool(data.get("toe_enabled", False))
        self._hydraulics_manual_height = _number(
            "hydraulics_manual_height",
            HYDRAULICS_DEFAULT_LIFT_M,
            HYDRAULICS_LIFT_MIN_M,
            HYDRAULICS_LIFT_MAX_M,
        )
        self._hydraulics_manual_speed = _number("hydraulics_manual_speed", 0.14, 0.01, 0.20)
        self._grip_enabled = bool(data.get("grip_enabled", False))
        self._grip_lateral = _number("grip_lateral", 1.0, GRIP_MIN, GRIP_MAX)
        self._grip_longitudinal = _number("grip_longitudinal", 1.0, GRIP_MIN, GRIP_MAX)
        if self._enabled and self._grip_enabled and self._grip_live() and self._grip_stock:
            self._write_grip(self.vehicle)

        # Last, once heights and camber are loaded: disabling air ride while the car sits
        # dropped has to lift it to the new heights, or it stays down with the drop locked.
        self.set_airride_enabled(airride_enabled)

        self._controls_dirty = True
