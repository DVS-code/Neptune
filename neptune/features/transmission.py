"""Transmission tuner using the verified final-drive and ratio fields."""

from __future__ import annotations

import time

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from neptune.core import transmission as calc
from neptune.core.models import MAX_FORWARD_GEARS, TransmissionTune
from neptune.core.module import FeatureModule
from neptune.ui.widgets.buttons import Button
from neptune.ui.widgets.card import Banner, StatStrip, ToggleRow, bind_progressive
from neptune.ui.widgets.gearing import GearingChart
from neptune.ui.widgets.sliderrow import SliderRow

MIN_FINAL = 0.1
MAX_FINAL = 15.0
MIN_RATIO = 0.05
MAX_RATIO = 10.0
CAPTURE_RETRY_SECONDS = 1.0


class TransmissionModule(FeatureModule):
    name = "transmission"
    title = "Transmission"
    subtitle = "Final drive, ratios and shift landing analysis."
    icon = "tunes.png"
    group = "Vehicle"
    order = 30

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.stock: TransmissionTune | None = None
        self._desired: TransmissionTune | None = None
        self._applied_signature = None
        self._written = False
        self._curve: list[float] = []
        self._curve_step: float | None = None
        self._declared_gear_count: int | None = None
        self._observed_max_gear = 0
        self._observed_shift_edges = 0
        self._last_live_gear: int | None = None
        self._last_capture = 0.0
        self._enabled = bool(settings.get("transmission_enabled"))
        self._chart_signature = None
        self._controls_dirty = False
        self._widgets: dict = {}

    @property
    def tune(self) -> TransmissionTune | None:
        """The gearing this page applies: the edited tune, else the car's stock."""
        return self._desired or self.stock

    def _capture_stock(self, vehicle) -> None:
        self._last_capture = time.monotonic()
        try:
            tune = TransmissionTune.from_vehicle(vehicle)
        except Exception:
            return
        if tune.validate():
            return
        self.stock = tune
        declared = tune.gear_count
        try:
            count = int(vehicle.gear_count or 0)
            declared = count if 1 <= count <= 10 else declared
        except (TypeError, ValueError, OverflowError):
            pass
        self._declared_gear_count = declared
        try:
            curve = list(vehicle.curve() or [])
            step = float(vehicle.rpm_per_index or 0.0)
            self._curve = curve if len(curve) >= 2 and step > 0.0 else []
            self._curve_step = step if self._curve else None
        except (TypeError, ValueError, OverflowError):
            self._curve = []
            self._curve_step = None
        if self._desired is None:
            self._desired = TransmissionTune.from_dict(tune.to_dict())
        else:
            self._fit_to_car(self._desired)
        self._controls_dirty = True

    def _fit_to_car(self, tune: TransmissionTune) -> None:
        """Never keep more ratios than this car has slots for: extra writes land past its gears."""
        if self.stock is not None and len(tune.ratios) > len(self.stock.ratios):
            del tune.ratios[len(self.stock.ratios) :]
            tune.gear_count = len(tune.ratios)

    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle
        self._applied_signature = None
        self._written = False
        if vehicle is not None:
            self._capture_stock(vehicle)

    def on_car_changed(self, vehicle) -> None:
        self.stock = None
        self._desired = None
        self._declared_gear_count = None
        self._observed_max_gear = 0
        self._observed_shift_edges = 0
        self._last_live_gear = None
        self._curve = []
        self._curve_step = None
        self._controls_dirty = True
        self.on_attach(vehicle)

    def on_car_reloaded(self, vehicle) -> None:
        self.vehicle = vehicle
        self._applied_signature = None
        self._apply(vehicle)

    def on_detach(self) -> None:
        self.vehicle = None
        self._applied_signature = None
        self._observed_max_gear = 0
        self._observed_shift_edges = 0
        self._last_live_gear = None

    def restore(self) -> None:
        if self.stock is None:
            return
        if self._written and self.vehicle is not None:
            self._write(self.vehicle, self.stock)
        self._written = False
        self._desired = TransmissionTune.from_dict(self.stock.to_dict())
        self._applied_signature = None
        self._controls_dirty = True

    def reset_controls(self) -> None:
        self._desired = TransmissionTune.from_dict(self.stock.to_dict()) if self.stock is not None else None
        self._applied_signature = None
        self._controls_dirty = True

    def _ready_tune(self) -> TransmissionTune | None:
        """The desired tune when it is valid and fits the attached car."""
        tune = self._desired
        if tune is None or self.stock is None or tune.validate():
            return None
        return tune if len(tune.ratios) <= len(self.stock.ratios) else None

    def _write(self, vehicle, tune: TransmissionTune) -> bool:
        ok = vehicle.set_final_drive(tune.final_drive)
        for index, ratio in enumerate(tune.ratios):
            ok = vehicle.set_gear_ratio(index, ratio) and ok
        return ok

    def _apply(self, vehicle) -> None:
        if not self._enabled or vehicle is None or self._desired is None:
            return
        signature = (vehicle.entity, vehicle.config, self._desired.final_drive, tuple(self._desired.ratios))
        if signature == self._applied_signature:
            return
        tune = self._ready_tune()
        if tune is not None and self._write(vehicle, tune):
            self._applied_signature = signature
            self._written = True

    def tick(self, vehicle) -> None:
        self.vehicle = vehicle
        if self._enabled:  # the shift readouts only show while the page is on
            self._observe_gear(vehicle)
        if self.stock is None and time.monotonic() - self._last_capture >= CAPTURE_RETRY_SECONDS:
            self._capture_stock(vehicle)
        self._apply(vehicle)

    def _observe_gear(self, vehicle) -> None:
        try:
            gear = int(vehicle.gear)
        except (TypeError, ValueError, OverflowError):
            return
        if gear > 0:
            self._observed_max_gear = max(self._observed_max_gear, gear)
        if self._last_live_gear is not None and gear != self._last_live_gear:
            self._observed_shift_edges += 1
        self._last_live_gear = gear

    def _set_final(self, value: float) -> None:
        if self._desired is None:
            return
        self._desired.final_drive = max(MIN_FINAL, min(MAX_FINAL, float(value)))
        self._controls_dirty = True

    def _set_ratio(self, index: int, value: float) -> None:
        if self._desired is None or not 0 <= index < len(self._desired.ratios):
            return
        self._desired.ratios[index] = max(MIN_RATIO, min(MAX_RATIO, float(value)))
        self._controls_dirty = True

    def _restore_stock_gear(self, index: int) -> None:
        if self.stock is None or self._desired is None or index >= len(self.stock.ratios):
            return
        self._desired.ratios[index] = self.stock.ratios[index]
        self._controls_dirty = True

    def _set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self.settings.remember("transmission_enabled", enabled)
        if not enabled:
            self.restore()
        self._controls_dirty = True

    def _sync_controls(self) -> None:
        toggle = self._widgets.get("enabled")
        if toggle is not None:
            toggle.set_value(self._enabled)
        if self._desired is None:
            return
        final = self._widgets.get("final")
        if final is not None:
            final.set_value(self._desired.final_drive or 0.0)
        for index, row in enumerate(self._widgets.get("ratio_rows") or ()):
            if index < len(self._desired.ratios):
                self._widgets["ratios"][index].set_value(self._desired.ratios[index])
            row.setVisible(index < len(self._desired.ratios))

    def _redline(self) -> float:
        vehicle = self.vehicle
        if vehicle is None:
            return 8000.0
        return float(vehicle.rev_ceiling or vehicle.redline or 8000.0)

    def build_page(self, page) -> None:
        enable_card = page.add_card("Transmission")
        enabled = ToggleRow(
            "Enable transmission controls",
            self._enabled,
            hint="Shows the gearing controls and permits Neptune to apply final-drive and ratio changes.",
        )
        enabled.toggle.toggled_value.connect(self._set_enabled)
        self._widgets["enabled"] = enabled
        enable_card.add(enabled)

        setup = page.add_card(
            "Gearing setup",
            "Changes apply to the car as you make them. Gear-count editing is intentionally withheld "
            "until a safe runtime owner is proven.",
        )
        final = SliderRow(
            "Final drive",
            MIN_FINAL,
            MAX_FINAL,
            3.5,
            step=0.01,
            decimals=2,
            hint="Final-drive ratio. Changes the speed and RPM relationship in every gear.",
        )
        final.changed.connect(self._set_final)
        self._widgets["final"] = final
        setup.add(final)

        ratios_panel = QWidget()
        ratio_layout = QVBoxLayout(ratios_panel)
        ratio_layout.setContentsMargins(0, 0, 0, 0)
        ratio_layout.setSpacing(8)
        ratios = []
        rows = []
        for index in range(MAX_FORWARD_GEARS):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            slider = SliderRow(
                f"Gear {index + 1}", MIN_RATIO, MAX_RATIO, 1.0, step=0.01, decimals=2, hint="Forward gear ratio."
            )
            slider.changed.connect(lambda value, i=index: self._set_ratio(i, value))
            row_layout.addWidget(slider, 1)
            restore = Button("Restore")
            restore.clicked.connect(lambda _checked=False, i=index: self._restore_stock_gear(i))
            row_layout.addWidget(restore)
            ratio_layout.addWidget(row)
            ratios.append(slider)
            rows.append(row)
        self._widgets["ratios"] = ratios
        self._widgets["ratio_rows"] = rows
        setup.add(ratios_panel)

        restore = Button("Restore stock transmission")
        restore.clicked.connect(self.restore)
        setup.add(restore)

        unavailable = Banner(
            "Add/remove gear is unavailable: the current runtime research does not prove a safe gear-count "
            "write. The read-only gear count remains visible.",
            "warn",
        )
        page.add(unavailable)

        chart_card = page.add_card(
            "Gearing visualization",
            "Bar length shows the estimated speed at redline. Amber marks show next-gear landing RPM; cyan "
            "marks show the torque-equality shift estimate when the engine curve is available.",
        )
        chart = GearingChart()
        self._widgets["chart"] = chart
        chart_card.add(chart)

        stats_card = page.add_card("Shift analysis")
        stats = StatStrip()
        stats.add("count", "Readable ratios", "--")
        stats.add("declared", "Declared gears", "--")
        stats.add("observed", "Observed max gear", "--")
        stats.add("shifts", "Observed shifts", "--")
        stats.add("redline", "Redline", "--", "rpm")
        stats.add("powerband", "Estimated powerband", "--")
        self._widgets["stats"] = stats
        stats_card.add(stats)

        status = Banner("Attach to the game and load a car to read its transmission.", "info")
        self._widgets["status"] = status
        page.add(status)
        bind_progressive(enabled, setup, unavailable, chart_card, stats_card)

    def refresh(self, vehicle) -> None:
        if self._controls_dirty:
            self._controls_dirty = False
            self._sync_controls()
        stats = self._widgets.get("stats")
        chart = self._widgets.get("chart")
        status = self._widgets.get("status")
        if stats is None or chart is None or status is None:
            return
        if vehicle is None:
            stats.reset()
            chart.set_data([], 8000.0)
            self._chart_signature = None
            status.set("Attach to the game and load a car to read its transmission.", "info")
            return
        redline = self._redline()
        tune = self._desired
        gear = vehicle.gear
        signature = (tune.final_drive, tuple(tune.ratios), redline, gear) if tune is not None else (redline, gear)
        if signature != self._chart_signature:
            # Shift estimates walk the torque curve per gear; only redo them when an input moved.
            self._chart_signature = signature
            rows = (
                calc.gear_rows(tune, redline, torque_curve=self._curve, rpm_step=self._curve_step)
                if tune is not None
                else []
            )
            chart.set_data(rows, redline, live_gear=gear)
        count = len(tune.ratios) if tune is not None else 0
        stats.set("count", str(count), unit="gears")
        stats.set("declared", str(self._declared_gear_count or count), unit="gears")
        stats.set("observed", str(self._observed_max_gear or "--"), unit="gear")
        stats.set("shifts", str(self._observed_shift_edges), unit="edges")
        stats.set("redline", f"{redline:.0f}", unit="rpm")
        stats.set("powerband", f"{redline * 0.55:.0f}–{redline * 0.95:.0f}", unit="rpm")
        if self._ready_tune() is None:
            # Say WHY, and what to do about it. The usual cause is simply that the car is
            # still loading, where the ratio array reads as zeros for a moment.
            status.set(
                "Could not read this car's gearing yet. Drive out into the world, then "
                "reload the car if it does not appear.",
                "warn",
            )
        elif not self._enabled:
            status.set("Transmission controls are disabled. Enable them to apply gearing changes.", "info")
        elif self._declared_gear_count and self._declared_gear_count > count:
            status.set(
                f"Manual check: game declares {self._declared_gear_count} gears but only {count} positive "
                "ratio slots are readable. Use X/B to test the highest reachable gear; Neptune performs no "
                "gear-count write.",
                "warn",
            )
        else:
            status.set(
                "Transmission ready. Manual check: X upshifts, B downshifts; Neptune records the highest "
                "observed gear without writing gear count.",
                "info",
            )

    def save_state(self) -> dict:
        tune = self.tune
        return {"enabled": self._enabled, "transmission": tune.to_dict() if tune is not None else {}}

    def load_state(self, data: dict) -> None:
        data = data if isinstance(data, dict) else {}
        tune = TransmissionTune.from_dict(data.get("transmission", data))
        if not tune.ratios:
            return
        self._set_enabled(bool(data.get("enabled", True)))  # turning off restores stock gearing first
        self._fit_to_car(tune)
        self._desired = tune
        self._applied_signature = None
        self._controls_dirty = True
