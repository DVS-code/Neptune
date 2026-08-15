"""Turbo: boost ceiling, power delivery, spool and per-gear boost."""
from __future__ import annotations

import math
import time

from PySide6.QtWidgets import QVBoxLayout, QWidget

from neptune.core.module import FeatureModule
from neptune.memory import offsets as O
from neptune.ui import theme as T
from neptune.ui.widgets.boostmap import COLUMNS as MAP_COLUMNS
from neptune.ui.widgets.boostmap import BoostMap, multiplier_at
from neptune.ui.widgets.card import Banner, StatStrip, ToggleRow
from neptune.ui.widgets.sliderrow import SliderRow

MAX_BOOST_RAW = 900.0
STOCK_BOOST_RANGE = (0.5, 400.0)
MAX_GEARS = 10
DEFAULT_GEARS = 6

DEFAULT_SPOOL_RATE = 2500.0
RAMP_RATE_PER_SECOND = 0.6

HINT_MAX_BOOST = 'The most boost the turbo can make. Power scales with it.'
HINT_TORQUE = 'More power at the same boost pressure.'
HINT_LOW_AIRFLOW = 'Torque scaling while off boost.'
HINT_SPOOL_LOAD = 'How hard the turbo works before full boost. Higher spins up further first.'
HINT_MIN_BOOST = 'Floor for the boost reading. Gauge only.'
HINT_LAG = 'Limits how fast the turbo spools. Lower is laggier.'
HINT_BY_GEAR = 'Scale boost separately in each gear.'
HINT_MAP = ('Boost multiplier at each engine speed. Drag to select cells, then scroll or use the '
            'buttons. The bright outline is where the engine is now.')

NOTE_SUPERCHARGED = ('This car is supercharged, so the turbo controls do nothing here. '
                     'Use the Engine tab to change its power.')
NOTE_NATURAL = ('This car has no turbo or supercharger. '
                'Use the Engine tab to change its power.')


class TurboModule(FeatureModule):
    name = 'turbo'
    title = 'Turbo'
    subtitle = 'Boost ceiling, power delivery and spool.'
    icon = '●'
    group = 'Vehicle'
    order = 20

    FIELDS = ('max_boost', 'power_max', 'max_scale', 'low_airflow',
              'turbine_limit', 'min_boost')

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

        self.stock: dict[str, float | None] = {}
        self._stock_valid = False
        self._multipliers = {'max_boost': 1.0, 'power_max': 1.0, 'max_scale': 1.0,
                             'low_airflow': 1.0, 'turbine_limit': 1.0}
        self._min_boost_percent = 0.0
        self._by_gear = False
        self._gear_multipliers = {gear: 1.0 for gear in range(1, MAX_GEARS + 1)}
        self._applied_signature = None
        self._blower_peak = -999.0

        self._lag_enabled = False
        self._lag_rate = DEFAULT_SPOOL_RATE
        self._lag_value: float | None = None
        self._lag_time: float | None = None
        self._ramp_time: float | None = None
        self._ramp_value = 0.0

        self._map_enabled = False
        self._map_points = [1.0] * MAP_COLUMNS
        self._map_rows = 1
        self._map_max_rpm = 8000.0

        self._gear_count = DEFAULT_GEARS
        self._controls_dirty = False
        self._widgets: dict = {}

    def _is_tuned(self) -> bool:
        return (self._by_gear or self._min_boost_percent != 0.0
                or self._map_is_shaped()
                or any(abs(value - 1.0) > 1e-6 for value in self._multipliers.values()))

    def _map_is_shaped(self) -> bool:
        """True when the boost map is on AND actually bent away from flat."""
        return self._map_enabled and any(
            abs(value - 1.0) > 1e-6 for value in self._map_points)

    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle


        if vehicle is None:
            return
        self._capture_stock(vehicle)
        self._read_gear_count(vehicle)
        self._read_rev_range(vehicle)

    def _read_rev_range(self, vehicle) -> None:
        """Set the boost map's RPM axis from this car's own rev ceiling."""
        try:
            top = vehicle.rev_ceiling or vehicle.redline
        except Exception:
            return
        if not top or not (1000.0 <= top <= 30000.0):
            return

        top = 500.0 * math.ceil(top / 500.0)
        if abs(top - self._map_max_rpm) > 1.0:
            self._map_max_rpm = top
            self._controls_dirty = True

    def _read_gear_count(self, vehicle) -> None:
        """How many forward gears this car has."""
        try:
            ratios = vehicle.gears()
        except Exception:
            return
        count = sum(1 for ratio in ratios if ratio and ratio > 0.05)
        count = max(1, min(MAX_GEARS, count or DEFAULT_GEARS))
        if count != self._gear_count:
            self._gear_count = count
            self._controls_dirty = True

    def _capture_stock(self, vehicle) -> None:


        if vehicle is None or self._is_tuned():
            return
        values = {field: vehicle.turbo_get(field) for field in self.FIELDS}
        ceiling = values.get('max_boost')
        if ceiling is None or not (STOCK_BOOST_RANGE[0] <= ceiling <= STOCK_BOOST_RANGE[1]):
            return
        self.stock = values
        self._stock_valid = True
        self._applied_signature = None

    def on_car_changed(self, vehicle) -> None:
        self._blower_peak = -999.0
        self._multipliers = {key: 1.0 for key in self._multipliers}
        self._min_boost_percent = 0.0
        self._by_gear = False
        self._gear_multipliers = {gear: 1.0 for gear in range(1, MAX_GEARS + 1)}
        self._map_points = [1.0] * MAP_COLUMNS
        self._map_enabled = False
        self._applied_signature = None
        self._stock_valid = False
        self._controls_dirty = True
        self.vehicle = vehicle
        self._capture_stock(vehicle)

    def on_car_reloaded(self, vehicle) -> None:
        self.vehicle = vehicle
        self._applied_signature = None
        if self._is_tuned():
            self._apply(vehicle)

    def on_detach(self) -> None:
        self.vehicle = None
        self._lag_value = None

    def restore(self) -> None:
        vehicle = self.vehicle
        if vehicle is not None and self._stock_valid:
            for field in self.FIELDS:
                value = self.stock.get(field)
                if value is not None:
                    vehicle.turbo_set(field, value)
        self._multipliers = {key: 1.0 for key in self._multipliers}
        self._min_boost_percent = 0.0
        self._by_gear = False
        self._gear_multipliers = {gear: 1.0 for gear in range(1, MAX_GEARS + 1)}
        self._map_points = [1.0] * MAP_COLUMNS
        self._map_enabled = False
        self._applied_signature = None
        self._lag_value = None

    def reset_controls(self) -> None:
        self._multipliers = {key: 1.0 for key in self._multipliers}
        self._min_boost_percent = 0.0
        self._by_gear = False
        self._lag_enabled = False
        self._gear_multipliers = {gear: 1.0 for gear in range(1, MAX_GEARS + 1)}
        self._map_points = [1.0] * MAP_COLUMNS
        self._map_enabled = False
        self._applied_signature = None
        self._controls_dirty = True

    def _gear_factor(self, vehicle) -> float:
        if not self._by_gear:
            return 1.0
        gear = vehicle.gear
        return self._gear_multipliers.get(gear, 1.0)

    def _map_factor(self, vehicle) -> float:
        """The boost map's multiplier at the engine's current RPM."""
        if not self._map_enabled:
            return 1.0
        rpm = vehicle.rpm
        if rpm is None or rpm != rpm:
            return 1.0


        load = None
        if self._map_rows > 1:
            throttle = vehicle.throttle
            load = None if throttle is None else 1.0 - throttle
        return multiplier_at(self._map_points, self._map_rows, rpm,
                             self._map_max_rpm, load)

    def _apply(self, vehicle) -> None:
        if not self._stock_valid:
            return
        factor = self._gear_factor(vehicle)
        mapped = self._map_factor(vehicle)


        signature = (tuple(sorted(self._multipliers.items())),
                     round(factor, 4), round(self._min_boost_percent, 4),
                     round(mapped, 3))
        if signature == self._applied_signature:
            return

        stock = self.stock
        ceiling = stock.get('max_boost')
        boost_factor = self._multipliers['max_boost'] * factor
        if ceiling is not None:
            vehicle.turbo_set('max_boost', min(ceiling * boost_factor, MAX_BOOST_RAW))


        if stock.get('max_scale') is not None:
            vehicle.turbo_set('max_scale',
                              stock['max_scale'] * self._multipliers['max_scale']
                              * boost_factor * mapped)


        if stock.get('low_airflow') is not None:
            vehicle.turbo_set('low_airflow',
                              stock['low_airflow'] * self._multipliers['low_airflow'])
        if stock.get('turbine_limit') is not None:
            vehicle.turbo_set('turbine_limit',
                              stock['turbine_limit'] * self._multipliers['turbine_limit'])
        if ceiling is not None and stock.get('min_boost') is not None:
            fraction = self._min_boost_percent / 100.0
            floor = stock['min_boost'] + (ceiling - stock['min_boost']) * fraction
            vehicle.turbo_set('min_boost', floor)

        self._applied_signature = signature

    def tick(self, vehicle) -> None:
        self.vehicle = vehicle
        if vehicle is None:
            return
        if not self._stock_valid:
            self._capture_stock(vehicle)
            self._read_gear_count(vehicle)
            self._read_rev_range(vehicle)
            return

        live = vehicle.boost_raw
        if self._is_tuned() and live is not None:
            if math.isnan(live) or math.isinf(live) or live < -1.0:
                self.restore()
                return

        if self._is_tuned():
            self._apply(vehicle)

        if self._lag_enabled:
            self._limit_spool(vehicle)

    def _limit_spool(self, vehicle) -> None:
        now = time.monotonic()
        current = vehicle.turbine
        if current is None:
            self._lag_time = now
            return
        if self._lag_value is None or self._lag_time is None:
            self._lag_value = current
            self._lag_time = now
            return

        elapsed = now - self._lag_time
        self._lag_time = now
        if current <= self._lag_value:
            self._lag_value = current
            return

        allowed = self._lag_value + self._lag_rate * elapsed
        if allowed < current:
            self._lag_value = allowed
            vehicle.set_turbine(allowed)
        else:
            self._lag_value = current

    def _turbine_ceiling(self) -> float | None:
        vehicle = self.vehicle
        if vehicle is None:
            return None
        live = vehicle.turbo_get('turbine_limit')
        if live:
            return live
        return self.stock.get('turbine_limit') if self._stock_valid else None

    def ramp_turbine(self, ceiling_fraction: float = 1.0,
                     rate: float = RAMP_RATE_PER_SECOND) -> None:
        """Climb the turbine steadily while anti-lag holds."""
        vehicle = self.vehicle
        if vehicle is None:
            return
        limit = self._turbine_ceiling()
        if not limit:
            return

        ceiling = limit * max(0.0, min(1.0, ceiling_fraction))
        now = time.monotonic()
        current = vehicle.turbine or 0.0
        if self._ramp_time is None:
            self._ramp_time = now
            self._ramp_value = current
            return

        elapsed = now - self._ramp_time
        self._ramp_time = now
        base = max(current, self._ramp_value)
        stepped = min(ceiling, base + limit * rate * elapsed)
        self._ramp_value = stepped
        if stepped > current:
            vehicle.set_turbine(stepped)

    def reset_ramp(self) -> None:
        self._ramp_time = None
        self._ramp_value = 0.0

    def _set_multiplier(self, field: str, value: float) -> None:
        self._multipliers[field] = float(value)
        self._applied_signature = None

    def _set_min_boost(self, value: float) -> None:
        self._min_boost_percent = float(value)
        self._applied_signature = None

    def _set_by_gear(self, enabled: bool) -> None:
        self._by_gear = bool(enabled)
        self._applied_signature = None
        panel = self._widgets.get('gear_panel')
        if panel is not None:
            panel.setVisible(bool(enabled))

    def _set_gear_multiplier(self, gear: int, value: float) -> None:
        self._gear_multipliers[gear] = float(value)
        self._applied_signature = None

    def _set_map_enabled(self, enabled: bool) -> None:
        self._map_enabled = bool(enabled)
        self._applied_signature = None
        for key in ('map', 'map_tools', 'map_axis_row'):
            widget = self._widgets.get(key)
            if widget is not None:
                widget.setVisible(bool(enabled))


        if not enabled:
            self._restore_scale()

    def _restore_scale(self) -> None:
        vehicle = self.vehicle
        if vehicle is None or not self._stock_valid:
            return
        base = self.stock.get('max_scale')
        if base is not None:
            vehicle.turbo_set('max_scale', base * self._multipliers['max_scale'])

    def _set_map_axis(self, label: str) -> None:
        """Switch the table between a 1D RPM row and a 2D RPM x throttle grid."""
        rows = 4 if label.startswith('RPM x') else 1
        widget = self._widgets.get('map')
        if widget is not None:
            widget.set_rows(rows)
            self._map_points = widget.flat()
        self._map_rows = rows
        self._applied_signature = None

    def _on_map_changed(self) -> None:
        widget = self._widgets.get('map')
        if widget is not None:
            self._map_points = widget.flat()
            self._map_rows = widget.rows()
        self._applied_signature = None

    def _flatten_map(self) -> None:
        self._map_points = [1.0] * (MAP_COLUMNS * self._map_rows)
        widget = self._widgets.get('map')
        if widget is not None:
            widget.flatten()
        self._applied_signature = None
        self._restore_scale()

    def _set_lag_enabled(self, enabled: bool) -> None:
        self._lag_enabled = bool(enabled)
        self._lag_value = None
        slider = self._widgets.get('lag_rate')
        if slider is not None:
            slider.set_enabled(bool(enabled))

    def _sync_controls(self) -> None:
        mapping = {
            'max_boost': 'max_boost',
            'torque': 'max_scale', 'low_airflow': 'low_airflow',
            'spool_load': 'turbine_limit',
        }
        for widget_key, field in mapping.items():
            slider = self._widgets.get(widget_key)
            if slider is not None:
                slider.set_value(self._multipliers[field])

        min_boost = self._widgets.get('min_boost')
        if min_boost is not None:
            min_boost.set_value(self._min_boost_percent)

        lag_toggle = self._widgets.get('lag_toggle')
        if lag_toggle is not None:
            lag_toggle.set_value(self._lag_enabled)
        lag_rate = self._widgets.get('lag_rate')
        if lag_rate is not None:
            lag_rate.set_value(self._lag_rate)
            lag_rate.set_enabled(self._lag_enabled)

        map_widget = self._widgets.get('map')
        if map_widget is not None:
            map_widget.set_flat(self._map_points, self._map_rows)
            map_widget.set_max_rpm(self._map_max_rpm)
            map_widget.setVisible(self._map_enabled)
        map_toggle = self._widgets.get('map_toggle')
        if map_toggle is not None:
            map_toggle.set_value(self._map_enabled)
        tools = self._widgets.get('map_tools')
        if tools is not None:
            tools.setVisible(self._map_enabled)
        axis_row = self._widgets.get('map_axis_row')
        if axis_row is not None:
            axis_row.setVisible(self._map_enabled)
        axis = self._widgets.get('map_axis')
        if axis is not None:
            axis.set_value('RPM x throttle' if self._map_rows > 1 else 'RPM only')

        by_gear = self._widgets.get('by_gear')
        if by_gear is not None:
            by_gear.set_value(self._by_gear)
        panel = self._widgets.get('gear_panel')
        if panel is not None:
            panel.setVisible(self._by_gear)
        for gear, slider in (self._widgets.get('gear_sliders') or {}).items():
            slider.set_value(self._gear_multipliers.get(gear, 1.0))
            slider.setVisible(gear <= self._gear_count)

    def build_page(self, page) -> None:
        boost_card = page.add_card('Boost')
        specs = [
            ('max_boost', 'Max boost', 'max_boost', 0.5, 8.0, HINT_MAX_BOOST),
            ('torque', 'Extra torque', 'max_scale', 0.5, 8.0, HINT_TORQUE),
            ('low_airflow', 'Off-boost torque', 'low_airflow', 0.25, 4.0, HINT_LOW_AIRFLOW),
        ]
        for widget_key, label, field, low, high, hint in specs:
            slider = SliderRow(label, low, high, 1.0, step=0.05, decimals=2,
                               unit='x', hint=hint)
            slider.changed.connect(
                lambda value, name=field: self._set_multiplier(name, value))
            self._widgets[widget_key] = slider
            boost_card.add(slider)

        spool_card = page.add_card('Spool')
        spool_load = SliderRow('Spool load', 0.5, 8.0, 1.0, step=0.05, decimals=2,
                               unit='x', hint=HINT_SPOOL_LOAD)
        spool_load.changed.connect(
            lambda value: self._set_multiplier('turbine_limit', value))
        self._widgets['spool_load'] = spool_load
        spool_card.add(spool_load)

        min_boost = SliderRow('Minimum boost', 0, 100, 0, step=5, decimals=0,
                              unit='%', hint=HINT_MIN_BOOST)
        min_boost.changed.connect(self._set_min_boost)
        self._widgets['min_boost'] = min_boost
        spool_card.add(min_boost)

        spool_card.add_divider()

        lag_toggle = ToggleRow('Add turbo lag', False, hint=HINT_LAG)
        lag_toggle.toggle.toggled_value.connect(self._set_lag_enabled)
        self._widgets['lag_toggle'] = lag_toggle
        spool_card.add(lag_toggle)

        lag_rate = SliderRow('Spool speed', 200, 8000, DEFAULT_SPOOL_RATE,
                             step=100, decimals=0, unit='/s')
        lag_rate.changed.connect(lambda value: setattr(self, '_lag_rate', float(value)))
        lag_rate.set_enabled(False)
        self._widgets['lag_rate'] = lag_rate
        spool_card.add(lag_rate)

        map_card = page.add_card('Boost map', HINT_MAP)
        map_toggle = ToggleRow('Use the boost map', False)
        map_toggle.toggle.toggled_value.connect(self._set_map_enabled)
        self._widgets['map_toggle'] = map_toggle
        map_card.add(map_toggle)

        from neptune.ui.widgets.card import FieldRow as _FieldRow
        from neptune.ui.widgets.controls import Segmented as _Segmented
        axis = _Segmented(['RPM only', 'RPM x throttle'], 'RPM only')
        axis.changed.connect(self._set_map_axis)
        self._widgets['map_axis'] = axis
        self._widgets['map_axis_row'] = _FieldRow('Table', axis)
        self._widgets['map_axis_row'].setVisible(False)
        map_card.add(self._widgets['map_axis_row'])

        boost_map = BoostMap()
        boost_map.changed.connect(self._on_map_changed)
        boost_map.setVisible(False)
        self._widgets['map'] = boost_map
        map_card.add(boost_map)


        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QHBoxLayout as _HBox
        from PySide6.QtWidgets import QPushButton, QWidget as _Widget
        tools = _Widget()
        row = _HBox(tools)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        operations = (
            ('-5%', lambda: boost_map.scale_selection(0.95), 'Take 5% off the selection'),
            ('+5%', lambda: boost_map.scale_selection(1.05), 'Add 5% to the selection'),
            ('Interpolate', boost_map.interpolate_selection,
             'Straight-line fill between the ends of the selection'),
            ('Smooth', boost_map.smooth_selection, 'Average out spikes in the selection'),
            ('Flatten', self._flatten_map, 'Put the whole table back to 1.00x'),
        )
        for label, handler, tip in operations:
            button = QPushButton(label)
            button.setCursor(_Qt.PointingHandCursor)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            row.addWidget(button)
        tools.setVisible(False)
        self._widgets['map_tools'] = tools
        map_card.add(tools)

        gear_card = page.add_card('Boost by gear', HINT_BY_GEAR)
        by_gear = ToggleRow('Enable boost by gear', False)
        by_gear.toggle.toggled_value.connect(self._set_by_gear)
        self._widgets['by_gear'] = by_gear
        gear_card.add(by_gear)

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(12)
        gear_sliders = {}
        for gear in range(1, MAX_GEARS + 1):
            slider = SliderRow(f'Gear {gear}', 0.25, 3.0, 1.0, step=0.05,
                               decimals=2, unit='x')
            slider.changed.connect(
                lambda value, index=gear: self._set_gear_multiplier(index, value))
            slider.setVisible(gear <= self._gear_count)
            gear_sliders[gear] = slider
            panel_layout.addWidget(slider)
        panel.setVisible(False)
        self._widgets['gear_panel'] = panel
        self._widgets['gear_sliders'] = gear_sliders
        gear_card.add(panel)

        live_card = page.add_card('Live')
        stats = StatStrip()
        stats.add('boost', 'Boost', '--')
        stats.add('ceiling', 'Ceiling', '--')
        stats.add('turbine', 'Turbine', '--')
        stats.add('type', 'Induction', '--')
        self._widgets['stats'] = stats
        live_card.add(stats)

        banner = Banner('', 'warn')
        banner.setVisible(False)
        self._widgets['banner'] = banner
        live_card.add(banner)

    def refresh(self, vehicle) -> None:
        stats = self._widgets.get('stats')
        banner = self._widgets.get('banner')
        if stats is None:
            return

        if self._controls_dirty:
            self._controls_dirty = False
            self._sync_controls()

        if vehicle is None:
            stats.reset()
            if banner is not None:
                banner.setVisible(False)
            return

        ceiling = vehicle.turbo_get('max_boost')
        turbine_limit = vehicle.turbo_get('turbine_limit')

        blower_live = vehicle.boost_raw_blower
        if blower_live is not None and blower_live > self._blower_peak:
            self._blower_peak = blower_live
        blower_ceiling = vehicle.blower_ceiling

        natural = O.is_naturally_aspirated(ceiling, turbine_limit,
                                           self._blower_peak, blower_ceiling)
        blown = O.is_supercharged(self._blower_peak, blower_ceiling)
        gauge = vehicle.boost_gauge

        if natural:
            stats.set('boost', '--', T.TEXT_FAINT, unit='')
            stats.set('ceiling', '--', T.TEXT_FAINT, unit='')
        elif blown:
            peak = max(self._blower_peak, blower_ceiling or -999.0)
            stats.set('boost', self.settings.format_pressure(gauge),
                      T.ACCENT_BRIGHT if (gauge or 0) > 1 else None, unit='')
            stats.set('ceiling',
                      self.settings.format_pressure(O.boost_to_gauge(peak)), unit='')
        else:
            invalid = gauge is not None and (gauge < 0 or gauge != gauge)
            colour = T.ERR if invalid else (
                T.ACCENT_BRIGHT if (gauge or 0) > 1 else None)
            stats.set('boost', self.settings.format_pressure(gauge), colour, unit='')
            stats.set('ceiling',
                      self.settings.format_pressure(O.boost_to_gauge(ceiling))
                      if ceiling else '--', unit='')

        map_widget = self._widgets.get('map')
        if map_widget is not None and self._map_enabled:
            load = None
            if self._map_rows > 1:
                throttle = vehicle.throttle
                load = None if throttle is None else 1.0 - throttle
            map_widget.set_live(vehicle.rpm, load)

        turbine = vehicle.turbine
        stats.set('turbine', f'{turbine:.0f}' if turbine is not None else '--', unit='')
        stats.set('type', O.aspiration_label(vehicle.aspiration, ceiling, turbine_limit,
                                             self._blower_peak, blower_ceiling), unit='')

        if banner is not None:
            if blown:
                banner.set(NOTE_SUPERCHARGED, 'warn')
            elif natural:
                banner.set(NOTE_NATURAL, 'info')
            else:
                banner.setVisible(False)

    def save_state(self) -> dict:
        return {
            'multipliers': dict(self._multipliers),
            'min_boost_percent': self._min_boost_percent,
            'by_gear': self._by_gear,
            'gear_multipliers': {str(k): v for k, v in self._gear_multipliers.items()},
            'lag_enabled': self._lag_enabled,
            'lag_rate': self._lag_rate,
            'map_enabled': self._map_enabled,
            'map_points': list(self._map_points),
            'map_rows': self._map_rows,
        }

    def load_state(self, data: dict) -> None:
        data = data or {}


        for key, value in (data.get('multipliers') or {}).items():
            if key not in self._multipliers:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number != number or number in (float('inf'), float('-inf')):
                continue
            self._multipliers[key] = max(0.0, min(16.0, number))


        legacy_power = float((data.get('multipliers') or {}).get('power_max', 1.0))
        if abs(legacy_power - 1.0) > 1e-6:
            self._multipliers['max_scale'] *= legacy_power
        self._multipliers['power_max'] = 1.0

        self._min_boost_percent = float(data.get('min_boost_percent', 0.0))
        self._by_gear = bool(data.get('by_gear', False))
        for key, value in (data.get('gear_multipliers') or {}).items():
            try:
                self._gear_multipliers[int(key)] = float(value)
            except (TypeError, ValueError):
                continue

        self._lag_enabled = bool(data.get('lag_enabled', False))
        self._lag_rate = float(data.get('lag_rate', DEFAULT_SPOOL_RATE))

        self._map_enabled = bool(data.get('map_enabled', False))
        try:
            self._map_rows = max(1, min(6, int(data.get('map_rows', 1))))
        except (TypeError, ValueError):
            self._map_rows = 1
        stored = data.get('map_points')
        if isinstance(stored, (list, tuple)) and stored:
            points = []
            for index in range(MAP_COLUMNS * self._map_rows):
                try:
                    value = float(stored[index]) if index < len(stored) else 1.0
                except (TypeError, ValueError):
                    value = 1.0
                if value != value:
                    value = 1.0
                points.append(max(0.0, min(4.0, value)))
            self._map_points = points
        else:
            self._map_points = [1.0] * MAP_COLUMNS
        self._applied_signature = None
        self._sync_controls()
