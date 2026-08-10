"""Engine: torque curve, torque multiplier, rev limit and anti-lag."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton

from neptune.core import input as inp
from neptune.core.module import FeatureModule
from neptune.ui import theme as T
from neptune.ui.widgets.card import Banner, StatStrip, ToggleRow
from neptune.ui.widgets.controls import BindButton
from neptune.ui.widgets.sliderrow import SliderRow
from neptune.ui.widgets.torquegraph import TorqueGraph

SPEED_CAP_HYSTERESIS_KPH = 3.0
REAPPLY_INTERVAL = 1.0
MIN_CURVE_POINTS = 8

TORQUE_MIN = 0.25
TORQUE_MAX = 4.0
REV_MIN = 3000
REV_MAX = 12000
HOLD_MIN = 1500
HOLD_MAX = 9000

BOUNCE_BAND = 350
CUT_MS = 30
LIFT_MS = 30

HINT_TORQUE = 'Multiplies engine torque across the whole rev range.'
HINT_REV = 'Where the car limits and changes up. Lowering always works; raising is capped by how much the engine can pull.'
HINT_ANTILAG = 'Hold the bound control to sit on the limiter and build boost. Release to launch.'
HINT_SPEED_CAP = 'Anti-lag releases once the car passes this speed, so it behaves like a launch control.'


def _contiguous_runs(pending: dict[int, float]) -> list[tuple[int, list[float]]]:
    runs: list[tuple[int, list[float]]] = []
    for index in sorted(pending):
        if runs and index == runs[-1][0] + len(runs[-1][1]):
            runs[-1][1].append(pending[index])
        else:
            runs.append((index, [pending[index]]))
    return runs


class EngineModule(FeatureModule):
    name = 'engine'
    title = 'Engine'
    subtitle = 'Torque delivery, rev limit and launch behaviour.'
    icon = '◆'
    group = 'Vehicle'
    order = 10

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

        self.stock_curve: list[float] = []
        self.stock_ceiling: float | None = None
        self.stock_redline: float | None = None

        self._torque_multiplier = 1.0
        self._rev_limit: float | None = None
        self._last_reapply = 0.0
        self._pending_edits: dict[int, float] = {}

        self._armed = False
        self._engaged = False
        self._ready = False
        self._cut = False
        self._phase_started = 0.0
        self._hold_rpm = 4000
        self._build_boost = True
        self._turbo = None

        self._speed_cap_enabled = False
        self._speed_cap_ms = 40.0 / 3.6
        self._over_cap = False

        self._controls_dirty = False
        self._widgets: dict = {}

    def bind_turbo(self, turbo) -> None:
        self._turbo = turbo

    def binding(self) -> dict | None:
        return self.settings.binding('engine.antilag')

    def bindings(self) -> list[dict]:
        return [{
            'key': 'engine.antilag',
            'label': 'Hold for anti-lag',
            'description': 'Hold to sit on the limiter and build boost.',
        }]

    def on_attach(self, vehicle) -> None:
        """Capture stock values. Never touches widgets: this runs off the interface thread."""
        self.vehicle = vehicle
        self.stock_curve = vehicle.curve()
        if self._rev_limit is None:
            self.stock_ceiling = vehicle.rev_ceiling
            self.stock_redline = vehicle.redline
        self._controls_dirty = True

    def on_car_changed(self, vehicle) -> None:
        self._engaged = False
        self._ready = False
        self._torque_multiplier = 1.0
        self._rev_limit = None
        self._pending_edits.clear()
        self.on_attach(vehicle)

    def on_car_reloaded(self, vehicle) -> None:
        self.vehicle = vehicle
        if self._rev_limit is not None:
            self._apply_rev_limit()
        elif self._torque_multiplier != 1.0:
            self._apply_curve()

    def on_detach(self) -> None:
        self.vehicle = None
        self._engaged = False
        self._ready = False

    def restore(self) -> None:
        vehicle = self.vehicle
        self._engaged = False
        self._ready = False
        self._torque_multiplier = 1.0
        self._rev_limit = None
        self._pending_edits.clear()
        if vehicle is None or not self.stock_curve:
            return
        vehicle.set_curve(self.stock_curve)
        if self.stock_ceiling:
            vehicle.set_rev_ceiling(self.stock_ceiling)

    def reset_controls(self) -> None:
        self._armed = False
        self._engaged = False
        self._ready = False
        self._torque_multiplier = 1.0
        self._rev_limit = None

        torque = self._widgets.get('torque')
        if torque is not None:
            torque.set_value(1.0)
        arm = self._widgets.get('arm')
        if arm is not None:
            arm.set_value(False)
        self._sync_rev_slider()

    def tick(self, vehicle) -> None:
        self.vehicle = vehicle
        if not self.stock_curve:
            return

        if self._rev_limit is not None and not self._engaged:
            now = time.monotonic()
            if now - self._last_reapply >= REAPPLY_INTERVAL:
                self._last_reapply = now
                self._apply_rev_limit()

        if self._pending_edits and not self._engaged:
            pending = self._pending_edits
            self._pending_edits = {}
            for start, values in _contiguous_runs(pending):
                vehicle.set_curve_from(start, values)

        if not self._armed:
            if self._engaged:
                self._release()
            return

        wanted = inp.is_down(self.binding())
        if wanted and self._speed_cap_enabled and self._past_speed_cap(vehicle):
            wanted = False

        if wanted and not self._engaged:
            self._engage()
        elif not wanted and self._engaged:
            self._release()

        if self._engaged:
            self._bounce(vehicle)
            if self._build_boost and self._turbo is not None:
                self._turbo.ramp_turbine()

    def _past_speed_cap(self, vehicle) -> bool:
        speed = vehicle.speed_ms if vehicle is not None else None
        if speed is None:
            return False
        hysteresis = SPEED_CAP_HYSTERESIS_KPH / 3.6
        if self._over_cap:
            if speed <= self._speed_cap_ms - hysteresis:
                self._over_cap = False
        elif speed > self._speed_cap_ms:
            self._over_cap = True
        return self._over_cap

    def _limiter_value(self) -> float:
        terminal = self.stock_curve[-1]
        if terminal >= 0:
            peak = max(abs(value) for value in self.stock_curve[:-1]) or 1.0
            terminal = -0.2 * peak
        return terminal

    def _engage(self) -> None:
        vehicle = self.vehicle
        if vehicle is None or len(self.stock_curve) < MIN_CURVE_POINTS:
            return
        self._engaged = True
        self._cut = False
        rpm = vehicle.rpm or 0.0
        self._ready = rpm <= self._hold_rpm
        if self._ready:
            self._set_wall(vehicle, True)
            self._phase_started = time.monotonic()

    def _release(self) -> None:
        self._engaged = False
        self._cut = False
        self._ready = False
        if self.vehicle is not None and self.stock_curve:
            self._apply_curve()
        if self._turbo is not None:
            self._turbo.reset_ramp()

    def _bounce(self, vehicle) -> None:
        rpm = vehicle.rpm or 0.0
        if not self._ready:
            if rpm <= self._hold_rpm:
                self._ready = True
                self._set_wall(vehicle, True)
                self._phase_started = time.monotonic()
            return

        now = time.monotonic()
        elapsed = (now - self._phase_started) * 1000.0
        if self._cut:
            if elapsed >= CUT_MS and rpm <= self._hold_rpm - BOUNCE_BAND:
                self._set_wall(vehicle, False)
                self._phase_started = now
        elif elapsed >= LIFT_MS or rpm >= self._hold_rpm:
            self._set_wall(vehicle, True)
            self._phase_started = now

    def _set_wall(self, vehicle, engaged: bool) -> None:
        curve = self.stock_curve
        count = len(curve)
        per_index = vehicle.rpm_per_index or 100.0
        base = max(4, min(int(round(self._hold_rpm / per_index)), count - 2))
        lifted = max(4, min(int(round((self._hold_rpm + BOUNCE_BAND) / per_index)),
                            count - 2))
        edge = base if engaged else lifted
        tail = list(curve[base:edge]) + [self._limiter_value()] * (count - 1 - edge)
        vehicle.set_curve_from(base, tail)
        self._cut = engaged

    def _apply_curve(self) -> None:
        vehicle = self.vehicle
        if vehicle is None or not self.stock_curve or self._engaged:
            return
        body = [value * self._torque_multiplier for value in self.stock_curve[:-1]]
        vehicle.set_curve(body + [self.stock_curve[-1]])

    def _apply_rev_limit(self) -> None:
        vehicle = self.vehicle
        if vehicle is None:
            return
        if self._rev_limit is None:
            if self.stock_ceiling:
                vehicle.set_rev_ceiling(self.stock_ceiling)
        else:
            vehicle.set_rev_ceiling(self._rev_limit)
        self._apply_curve()

    def _sync_rev_slider(self) -> None:
        slider = self._widgets.get('rev')
        if slider is None:
            return
        target = self._rev_limit or self.stock_ceiling or self.stock_redline or 7000.0
        slider.set_value(target)

    def _on_torque(self, value: float) -> None:
        self._torque_multiplier = float(value)
        self._apply_curve()

    def _on_rev_limit(self, value: float) -> None:
        reference = self.stock_ceiling or self.stock_redline or 7000.0
        self._rev_limit = None if abs(value - reference) < 1.0 else float(value)
        self._apply_rev_limit()

    def _on_hold_rpm(self, value: float) -> None:
        self._hold_rpm = int(value)

    def _on_speed_cap(self, value: float) -> None:
        self._speed_cap_ms = self.settings.speed_to_ms(value)
        self._over_cap = False

    def _on_speed_cap_enabled(self, enabled: bool) -> None:
        self._speed_cap_enabled = bool(enabled)
        self._over_cap = False
        slider = self._widgets.get('speed_cap')
        if slider is not None:
            slider.set_enabled(bool(enabled))

    def _on_point_changed(self, index: int, value: float) -> None:
        if not self._engaged:
            self._pending_edits[index] = value

    def _commit_curve(self) -> None:
        vehicle = self.vehicle
        graph = self._widgets.get('graph')
        if vehicle is None or graph is None or not graph.live or self._engaged:
            return
        vehicle.set_curve_from(0, list(graph.live)[:-1])
        self._pending_edits.clear()

    def _reset_curve(self) -> None:
        vehicle = self.vehicle
        if vehicle is None or not self.stock_curve:
            return
        vehicle.set_curve(self.stock_curve)
        self._torque_multiplier = 1.0
        self._pending_edits.clear()
        slider = self._widgets.get('torque')
        if slider is not None:
            slider.set_value(1.0)
        graph = self._widgets.get('graph')
        if graph is not None:
            graph.clear_selection()

    def build_page(self, page) -> None:
        curve_card = page.add_card(
            'Torque curve',
            'Drag to reshape. Right-drag to select a range, then drag any selected point '
            'to move the whole group.')

        graph = TorqueGraph()
        graph.point_changed.connect(self._on_point_changed)
        self._widgets['graph'] = graph
        curve_card.add(graph)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        apply_button = QPushButton('Apply curve')
        apply_button.setObjectName('Primary')
        apply_button.setCursor(Qt.PointingHandCursor)
        apply_button.clicked.connect(self._commit_curve)
        actions.addWidget(apply_button)

        double_button = QPushButton('Double')
        double_button.setCursor(Qt.PointingHandCursor)
        double_button.clicked.connect(lambda: graph.scale_selection(2.0))
        actions.addWidget(double_button)

        halve_button = QPushButton('Halve')
        halve_button.setCursor(Qt.PointingHandCursor)
        halve_button.clicked.connect(lambda: graph.scale_selection(0.5))
        actions.addWidget(halve_button)

        reset_button = QPushButton('Reset to stock')
        reset_button.setCursor(Qt.PointingHandCursor)
        reset_button.clicked.connect(self._reset_curve)
        actions.addWidget(reset_button)
        actions.addStretch(1)
        curve_card.add_layout(actions)

        power_card = page.add_card('Power and limit')
        torque = SliderRow('Torque', TORQUE_MIN, TORQUE_MAX, 1.0, step=0.05,
                           decimals=2, unit='x', hint=HINT_TORQUE)
        torque.changed.connect(self._on_torque)
        self._widgets['torque'] = torque
        power_card.add(torque)

        rev = SliderRow('Rev limit', REV_MIN, REV_MAX, 7000, step=50,
                        decimals=0, unit='rpm', hint=HINT_REV)
        rev.changed.connect(self._on_rev_limit)
        self._widgets['rev'] = rev
        power_card.add(rev)

        antilag_card = page.add_card('Anti-lag', HINT_ANTILAG)
        arm = ToggleRow('Enable anti-lag', False)
        arm.toggle.toggled_value.connect(lambda value: setattr(self, '_armed', value))
        self._widgets['arm'] = arm
        antilag_card.add(arm)

        bind_button = BindButton(self.binding())
        bind_button.bound.connect(
            lambda binding: self.settings.set_binding('engine.antilag', binding))
        self._widgets['bind'] = bind_button

        from neptune.ui.widgets.card import FieldRow
        antilag_card.add(FieldRow('Hold control', bind_button))

        hold = SliderRow('Hold at', HOLD_MIN, HOLD_MAX, 4000, step=100,
                         decimals=0, unit='rpm')
        hold.changed.connect(self._on_hold_rpm)
        self._widgets['hold'] = hold
        antilag_card.add(hold)

        boost_toggle = ToggleRow('Build boost while holding', True)
        boost_toggle.toggle.toggled_value.connect(
            lambda value: setattr(self, '_build_boost', value))
        self._widgets['build_boost'] = boost_toggle
        antilag_card.add(boost_toggle)

        antilag_card.add_divider()

        cap_toggle = ToggleRow('Release above a speed', False, hint=HINT_SPEED_CAP)
        cap_toggle.toggle.toggled_value.connect(self._on_speed_cap_enabled)
        self._widgets['speed_cap_toggle'] = cap_toggle
        antilag_card.add(cap_toggle)

        _, unit = self.settings.speed(0)
        cap = SliderRow('Release above', 5, 200, 40, step=5, decimals=0, unit=unit)
        cap.changed.connect(self._on_speed_cap)
        cap.set_enabled(False)
        self._widgets['speed_cap'] = cap
        antilag_card.add(cap)

        live_card = page.add_card('Live')
        stats = StatStrip()
        stats.add('state', 'Anti-lag', 'Off')
        stats.add('torque', 'Peak torque', '--')
        stats.add('power', 'Peak power', '--')
        self._widgets['stats'] = stats
        live_card.add(stats)

        banner = Banner('', 'warn')
        banner.setVisible(False)
        self._widgets['banner'] = banner
        live_card.add(banner)

    def refresh(self, vehicle) -> None:
        graph = self._widgets.get('graph')
        stats = self._widgets.get('stats')
        banner = self._widgets.get('banner')
        if graph is None or stats is None:
            return

        if self._controls_dirty:
            self._controls_dirty = False
            self._sync_rev_slider()
            torque = self._widgets.get('torque')
            if torque is not None:
                torque.set_value(self._torque_multiplier)

        if vehicle is None:
            graph.set_data([], [], editable=False)
            stats.reset()
            if banner is not None:
                banner.setVisible(False)
            return

        graph.set_data(self.stock_curve, vehicle.curve(),
                       vehicle.rpm_per_index or 100.0, vehicle.rpm or 0.0,
                       vehicle.rev_ceiling, editable=not self._engaged)

        if self._engaged:
            stats.set('state', f'Holding {self._hold_rpm}', T.ACCENT_BRIGHT)
        elif self._armed:
            stats.set('state', 'Armed', T.OK)
        else:
            stats.set('state', 'Off', T.TEXT_FAINT)

        torque_nm, torque_rpm = vehicle.peak_torque()
        power_hp, power_rpm = vehicle.peak_power()
        stats.set('torque', f'{torque_nm:.0f}', unit=f'Nm @ {torque_rpm}')
        stats.set('power', f'{power_hp:.0f}', unit=f'hp @ {power_rpm}')

        if banner is not None:
            if not vehicle.can_tune_curve():
                banner.set('This engine uses a layout Neptune cannot reshape. '
                           'Torque and rev limit still work.', 'warn')
            else:
                banner.setVisible(False)

    def save_state(self) -> dict:
        return {
            'torque': self._torque_multiplier,
            'rev_limit': self._rev_limit,
            'hold_rpm': self._hold_rpm,
            'build_boost': self._build_boost,
            'speed_cap_enabled': self._speed_cap_enabled,
            'speed_cap_ms': self._speed_cap_ms,
        }

    def load_state(self, data: dict) -> None:
        data = data or {}

        self._hold_rpm = int(data.get('hold_rpm', 4000))
        hold = self._widgets.get('hold')
        if hold is not None:
            hold.set_value(self._hold_rpm)

        self._build_boost = bool(data.get('build_boost', True))
        boost_toggle = self._widgets.get('build_boost')
        if boost_toggle is not None:
            boost_toggle.set_value(self._build_boost)

        self._speed_cap_enabled = bool(data.get('speed_cap_enabled', False))
        self._speed_cap_ms = float(data.get('speed_cap_ms', 40.0 / 3.6))
        self._over_cap = False
        cap_toggle = self._widgets.get('speed_cap_toggle')
        if cap_toggle is not None:
            cap_toggle.set_value(self._speed_cap_enabled)
        cap = self._widgets.get('speed_cap')
        if cap is not None:
            value, _unit = self.settings.speed(self._speed_cap_ms)
            cap.set_value(value)
            cap.set_enabled(self._speed_cap_enabled)

        rev_limit = data.get('rev_limit')
        self._rev_limit = float(rev_limit) if rev_limit else None
        self._sync_rev_slider()

        multiplier = float(data.get('torque', 1.0))
        torque = self._widgets.get('torque')
        if torque is not None:
            torque.set_value(multiplier)
        self._torque_multiplier = multiplier
        self._apply_rev_limit() if self._rev_limit is not None else self._apply_curve()
