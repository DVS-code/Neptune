"""Boost Gauge: a floating dial that sits over the game."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton

from neptune.core.module import FeatureModule
from neptune.ui.overlay import DEFAULT_POSITION, DEFAULT_SIZE, MAX_SIZE, MIN_SIZE, GaugeOverlay
from neptune.ui.widgets.card import Banner, FieldRow, ToggleRow
from neptune.ui.widgets.controls import Segmented
from neptune.ui.widgets.gaugeface import ACCENT_ORDER, MODES, GaugeFace
from neptune.ui.widgets.sliderrow import SliderRow

PREVIEW_SIZE = 168
PREVIEW_VALUE = 12.4

HINT_SHOW = 'Puts the gauge on top of the game. It follows the game window.'
HINT_SIZE = 'How large the gauge is drawn.'
HINT_POSITION = 'Unlock the gauge to drag it anywhere on the picture with the mouse.'
HINT_LOCK = ('Locked, clicks pass straight through to the game. '
             'Unlock it to drag the gauge, then lock it again.')
HINT_GLOW = 'Adds a soft halo behind the needle and numbers.'
HINT_PEAK = 'Marks the highest boost seen since the car loaded.'
HINT_GEAR = 'Shows the gear you are in under the reading.'
HINT_AUTO_RANGE = 'Sets the dial to suit how much boost the car can make.'
NOTE_OFFLINE = 'Attach to the game to show the gauge.'


class BoostGaugeModule(FeatureModule):
    name = 'boostgauge'
    title = 'Boost Gauge'
    subtitle = 'A floating boost gauge on top of the game.'
    icon = '◒'
    group = 'Vehicle'
    order = 40
    always_refresh = True

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

        self._overlay: GaugeOverlay | None = None
        self._enabled = False
        self._mode = 'Dial'
        self._accent = 'blue'
        self._size = DEFAULT_SIZE
        self._position = DEFAULT_POSITION
        self._glow = True
        self._peak = True
        self._gear = False
        self._locked = True
        self._auto_range = True
        self._process = None
        self._controls_dirty = False
        self._widgets: dict = {}

    def _sync_controls(self) -> None:
        for key, value in (('mode', self._mode), ('accent', self._accent.title())):
            widget = self._widgets.get(key)
            if widget is not None:
                widget.set_value(value)
        size = self._widgets.get('size')
        if size is not None:
            size.set_value(self._size)
        for key, value in (('enabled', self._enabled), ('glow', self._glow),
                           ('peak', self._peak), ('gear', self._gear),
                           ('locked', self._locked),
                           ('auto_range', self._auto_range)):
            widget = self._widgets.get(key)
            if widget is not None:
                widget.set_value(value)

    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle
        if vehicle is not None:
            self._process = vehicle.process
        if self._enabled:
            self._ensure_overlay()
        if self._overlay is not None:
            self._overlay.set_vehicle(vehicle)

    def on_car_changed(self, vehicle) -> None:
        self.vehicle = vehicle
        if self._overlay is not None:
            self._overlay.set_vehicle(vehicle)
            self._overlay.face.reset_peak()

    def on_car_reloaded(self, vehicle) -> None:
        self.vehicle = vehicle
        if self._overlay is not None:
            self._overlay.set_vehicle(vehicle)

    def on_detach(self) -> None:
        self.vehicle = None
        if self._overlay is not None:
            self._overlay.set_vehicle(None)

    def restore(self) -> None:
        """Reading boost changes nothing in the game, so only the peak is cleared."""
        if self._overlay is not None:
            self._overlay.face.reset_peak()

    def reset_controls(self) -> None:
        self._controls_dirty = True

    def shutdown(self) -> None:
        self._teardown()

    def _teardown(self) -> None:
        if self._overlay is not None:
            self._overlay.stop()
            self._overlay.deleteLater()
            self._overlay = None

    def _ensure_overlay(self) -> None:
        if self._overlay is None:
            self._overlay = GaugeOverlay()
            self._overlay.set_settings(self.settings)
            self._overlay.set_moved_callback(self._on_moved)
            self._apply_style(self._overlay.face)
            self._overlay.set_size(self._size)
            self._overlay.set_mode(self._mode)
            self._overlay.set_position(*self._position)
            self._overlay.set_auto_range(self._auto_range)
        self._overlay.set_vehicle(self.vehicle)
        pid = self._process.pid if self._process is not None else None
        self._overlay.start(pid)
        self._overlay.set_locked(self._locked)

    def _apply_style(self, face: GaugeFace) -> None:
        face.mode = self._mode
        face.accent = self._accent
        face.glow = self._glow
        face.show_peak = self._peak
        face.show_gear = self._gear
        face.set_unit(self.settings.get('pressure_unit'))
        face.update()

    def _restyle(self) -> None:
        preview = self._widgets.get('preview')
        if preview is not None:
            self._apply_style(preview)
            preview.set_value(PREVIEW_VALUE, gear=3)
        if self._overlay is not None:
            self._apply_style(self._overlay.face)

    def _on_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._teardown()
            return
        self._ensure_overlay()

    def _on_mode(self, mode: str) -> None:
        self._mode = mode
        if self._overlay is not None:
            self._overlay.set_mode(mode)
        self._restyle()

    def _on_accent(self, accent: str) -> None:
        self._accent = accent.lower()
        self._restyle()

    def _on_size(self, size: float) -> None:
        self._size = int(size)
        if self._overlay is not None:
            self._overlay.set_size(self._size)

    def build_page(self, page) -> None:
        show_card = page.add_card('Gauge')

        enabled = ToggleRow('Show the gauge', False, hint=HINT_SHOW)
        enabled.toggle.toggled_value.connect(self._on_enabled)
        self._widgets['enabled'] = enabled
        show_card.add(enabled)

        mode = Segmented(list(MODES), self._mode)
        mode.changed.connect(self._on_mode)
        self._widgets['mode'] = mode
        show_card.add(FieldRow('Style', mode))

        style_card = page.add_card('Look')

        preview_row = QHBoxLayout()
        preview_row.addStretch(1)
        preview = GaugeFace()
        preview.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
        preview.set_value(PREVIEW_VALUE, gear=3)
        self._widgets['preview'] = preview
        preview_row.addWidget(preview)
        preview_row.addStretch(1)
        style_card.add_layout(preview_row)

        colours = Segmented([name.title() for name in ACCENT_ORDER],
                            self._accent.title())
        colours.changed.connect(self._on_accent)
        self._widgets['accent'] = colours
        style_card.add(FieldRow('Colour', colours))

        glow = ToggleRow('Glow', True, hint=HINT_GLOW)
        glow.toggle.toggled_value.connect(
            lambda value: (setattr(self, '_glow', bool(value)), self._restyle()))
        self._widgets['glow'] = glow
        style_card.add(glow)

        peak = ToggleRow('Mark peak boost', True, hint=HINT_PEAK)
        peak.toggle.toggled_value.connect(
            lambda value: (setattr(self, '_peak', bool(value)), self._restyle()))
        self._widgets['peak'] = peak
        style_card.add(peak)

        gear = ToggleRow('Show gear', False, hint=HINT_GEAR)
        gear.toggle.toggled_value.connect(
            lambda value: (setattr(self, '_gear', bool(value)), self._restyle()))
        self._widgets['gear'] = gear
        style_card.add(gear)

        place_card = page.add_card('Placement', HINT_POSITION)

        size = SliderRow('Size', MIN_SIZE, MAX_SIZE, DEFAULT_SIZE, step=10,
                         decimals=0, unit='px', hint=HINT_SIZE)
        size.changed.connect(self._on_size)
        self._widgets['size'] = size
        place_card.add(size)

        locked = ToggleRow('Lock in place', True, hint=HINT_LOCK)
        locked.toggle.toggled_value.connect(self._on_locked)
        self._widgets['locked'] = locked
        place_card.add(locked)

        reset = QPushButton('Move back to the corner')
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(lambda: self._jump_to(*DEFAULT_POSITION))
        place_card.add(reset)

        scale_card = page.add_card('Scale')
        auto = ToggleRow('Match the car', True, hint=HINT_AUTO_RANGE)
        auto.toggle.toggled_value.connect(self._on_auto_range)
        self._widgets['auto_range'] = auto
        scale_card.add(auto)

        banner = Banner(NOTE_OFFLINE, 'info')
        self._widgets['banner'] = banner
        page.add(banner)

        self._restyle()

    def _on_locked(self, locked: bool) -> None:
        self._locked = bool(locked)
        if self._overlay is not None:
            self._overlay.set_locked(self._locked)

    def _on_auto_range(self, enabled: bool) -> None:
        self._auto_range = bool(enabled)
        if self._overlay is not None:
            self._overlay.set_auto_range(self._auto_range)

    def _on_moved(self, position) -> None:
        self._position = position

    def _jump_to(self, x: float, y: float) -> None:
        self._position = (x, y)
        if self._overlay is not None:
            self._overlay.set_position(x, y)

    def refresh(self, vehicle) -> None:
        if self._controls_dirty:
            self._controls_dirty = False
            self._sync_controls()

        if self._overlay is not None and self._overlay.vehicle() is not vehicle:
            self._overlay.set_vehicle(vehicle)

        banner = self._widgets.get('banner')
        if banner is None:
            return
        if vehicle is None:
            banner.set(NOTE_OFFLINE, 'info')
        elif not self._enabled:
            banner.setVisible(False)
        elif not self._locked:
            banner.set('Drag the gauge where you want it, then lock it again.', 'info')
        else:
            banner.setVisible(False)

    def save_state(self) -> dict:
        return {
            'enabled': self._enabled,
            'mode': self._mode,
            'accent': self._accent,
            'size': self._size,
            'position': list(self._position),
            'glow': self._glow,
            'peak': self._peak,
            'gear': self._gear,
            'locked': self._locked,
            'auto_range': self._auto_range,
        }

    def load_state(self, data: dict) -> None:
        """Apply a saved gauge setup.

        ⚠️ A preset is a file on disk, so every field is untrusted. A bare `int()` on a
        hand-edited `size` used to raise and abandon the load part-way, leaving the gauge
        with some settings applied and the rest stale. Every field falls back to its
        default instead — the same rule the engine and suspension pages already follow.
        """
        data = data or {}

        mode = data.get('mode', 'Dial')
        self._mode = mode if mode in MODES else 'Dial'

        accent = data.get('accent', 'blue')
        self._accent = accent if accent in ACCENT_ORDER else 'blue'

        # OverflowError as well as ValueError: `int(float('inf'))` raises the former, and
        # a stored infinity is exactly the sort of thing a corrupt preset carries.
        try:
            size = float(data.get('size', DEFAULT_SIZE))
            if size != size:
                raise ValueError('nan')
            self._size = max(MIN_SIZE, min(MAX_SIZE, int(size)))
        except (TypeError, ValueError, OverflowError):
            self._size = DEFAULT_SIZE

        position = data.get('position') or list(DEFAULT_POSITION)
        try:
            self._position = (float(position[0]), float(position[1]))
        except (TypeError, ValueError, IndexError):
            self._position = DEFAULT_POSITION
        if any(value != value for value in self._position):
            self._position = DEFAULT_POSITION
        self._glow = bool(data.get('glow', True))
        self._peak = bool(data.get('peak', True))
        self._gear = bool(data.get('gear', False))
        self._locked = bool(data.get('locked', True))
        self._auto_range = bool(data.get('auto_range', True))

        self._controls_dirty = True

        if self._overlay is not None:
            self._overlay.set_size(self._size)
            self._overlay.set_mode(self._mode)
            self._overlay.set_position(*self._position)
            self._overlay.set_locked(self._locked)
            self._overlay.set_auto_range(self._auto_range)
        self._restyle()

        wanted = bool(data.get('enabled', False))
        toggle = self._widgets.get('enabled')
        if toggle is not None:
            toggle.set_value(wanted)
        self._on_enabled(wanted)
