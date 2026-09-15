"""DYNO: live engine output and tuning telemetry."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel

from neptune.features.boostgauge import BoostGaugeModule
from neptune.features.dragy import DragyModule
from neptune.ui import theme as T
from neptune.ui.dynooverlay import DynoOverlay
from neptune.ui.widgets.card import Banner, FieldRow, StatStrip, ToggleRow
from neptune.ui.widgets.controls import Segmented
from neptune.ui.widgets.dynograph import DynoGraph
from neptune.vehicle.vehicle import NM_RPM_TO_HP

DYNO_UNITS = ("hp / Nm", "kW / Nm", "hp / lb-ft", "kW / lb-ft")
NM_TO_LBFT = 0.737562
HP_TO_KW = 0.7457

HINT_DYNO = (
    "Every number on this page comes from the live car and engine data. "
    "No telemetry setting is required."
)
HINT_GRAPH = "Engine output curve. Blue is torque; violet is power."
HINT_UNITS = "Units for the graph and peak output; changing this never writes to the car."
HINT_LIVE = "A live snapshot of the engine and vehicle channels used by the graph and gauge."
HINT_OVERLAY = "Shows the dyno graph and figures above the game window."
HINT_OVERLAY_LOCK = "Unlock only to drag the panel; lock it again so clicks pass to the game."
NOTE_DYNO_ACTIVE = "Dyno active."
NOTE_OFFLINE = "Attach to the game and load a car to read the dyno."


class DynoModule(DragyModule):
    """Keep Dragy and Boost Gauge on one DYNO page while adding dyno data."""

    name = "dragy"  # Preserve the preset key used by earlier Neptune builds.
    title = "DYNO"
    subtitle = "Power, torque, Dragy runs and boost."
    icon = "dragy.png"
    group = "Vehicle"
    order = 35

    def __init__(self, settings):
        super().__init__(settings)
        self._boost = BoostGaugeModule(settings)
        self._dyno_controls_dirty = False
        self._dyno_widgets: dict = {}
        self._show_torque = True
        self._show_power = True
        self._units = DYNO_UNITS[0]
        self._dyno_overlay: DynoOverlay | None = None
        self._overlay_enabled = False
        self._overlay_mode = "Graph"
        self._overlay_locked = True
        self._overlay_position = (0.04, 0.10)

    def tick_process(self, process) -> None:
        super().tick_process(process)
        self._boost.tick_process(process)

    def on_attach(self, vehicle) -> None:
        super().on_attach(vehicle)
        self._boost.on_attach(vehicle)
        if self._dyno_overlay is not None:
            self._dyno_overlay.set_vehicle(vehicle)
        if self._overlay_enabled:
            self._ensure_dyno_overlay()

    def on_car_changed(self, vehicle) -> None:
        super().on_car_changed(vehicle)
        self._boost.on_car_changed(vehicle)
        if self._dyno_overlay is not None:
            self._dyno_overlay.set_vehicle(vehicle)

    def on_car_reloaded(self, vehicle) -> None:
        super().on_car_reloaded(vehicle)
        self._boost.on_car_reloaded(vehicle)
        if self._dyno_overlay is not None:
            self._dyno_overlay.set_vehicle(vehicle)

    def on_detach(self) -> None:
        super().on_detach()
        self._boost.on_detach()
        if self._dyno_overlay is not None:
            self._dyno_overlay.set_vehicle(None)

    def restore(self) -> None:
        super().restore()
        self._boost.restore()

    def reset_controls(self) -> None:
        super().reset_controls()
        self._boost.reset_controls()
        self._dyno_controls_dirty = True

    def shutdown(self) -> None:
        super().shutdown()
        self._boost.shutdown()
        if self._dyno_overlay is not None:
            self._dyno_overlay.stop()
            self._dyno_overlay.deleteLater()
            self._dyno_overlay = None

    def _ensure_dyno_overlay(self) -> DynoOverlay | None:
        if self._dyno_overlay is None:
            self._dyno_overlay = DynoOverlay()
            self._dyno_overlay.set_moved_callback(self._on_overlay_moved)
            self._dyno_overlay.set_mode(self._overlay_mode)
            self._dyno_overlay.set_locked(self._overlay_locked)
            self._dyno_overlay.set_position(*self._overlay_position)
        self._dyno_overlay.set_vehicle(self.vehicle)
        pid = self._process.pid if self._process is not None else None
        self._dyno_overlay.start(pid)
        return self._dyno_overlay

    def set_dyno_overlay(self, enabled: bool) -> None:
        self._overlay_enabled = bool(enabled)
        if not self._overlay_enabled:
            if self._dyno_overlay is not None:
                self._dyno_overlay.stop()
            return
        self._ensure_dyno_overlay()

    def _set_overlay_mode(self, mode: str) -> None:
        if mode not in ("Graph", "Numbers"):
            return
        self._overlay_mode = mode
        if self._dyno_overlay is not None:
            self._dyno_overlay.set_mode(mode)
        self._dyno_controls_dirty = True

    def _set_overlay_locked(self, locked: bool) -> None:
        self._overlay_locked = bool(locked)
        if self._dyno_overlay is not None:
            self._dyno_overlay.set_locked(self._overlay_locked)
        self._dyno_controls_dirty = True

    def _on_overlay_moved(self, position) -> None:
        self._overlay_position = position

    def _engine_curve(self, vehicle):
        """Build the plot from the live engine model curve."""
        if vehicle is None:
            return None
        try:
            curve = vehicle.curve()
            step = vehicle.rpm_per_index
            if len(curve) < 2 or step is None or step <= 0.0:
                return None
            scale = vehicle.boost_multiplier() * step
            torque = []
            power = []
            for index, raw in enumerate(curve[:-1]):
                rpm = index * step
                nm = max(0.0, float(raw) * scale)
                torque.append(nm)
                power.append(nm * rpm / NM_RPM_TO_HP)
            return torque, power, step
        except (TypeError, ValueError, OverflowError):
            return None

    def _output_unit(self, kind: str) -> tuple[float, str]:
        """(factor from Nm or hp, unit label) for "torque" or "power" in the selected units."""
        if kind == "torque":
            return (NM_TO_LBFT, "lb-ft") if self._units.endswith("lb-ft") else (1.0, "Nm")
        return (HP_TO_KW, "kW") if self._units.startswith("kW") else (1.0, "hp")

    def _format_output(self, value: float, kind: str) -> tuple[str, str]:
        factor, unit = self._output_unit(kind)
        return f"{value * factor:.0f}", unit

    def _legend_text(self, kind: str) -> str:
        return f"● {kind.capitalize()}  {self._output_unit(kind)[1]}"

    def _set_units(self, value: str) -> None:
        if value in DYNO_UNITS:
            self._units = value
            self._dyno_controls_dirty = True

    def build_page(self, page) -> None:
        dyno_card = page.add_card("Dyno", HINT_DYNO)

        units = Segmented(list(DYNO_UNITS), self._units)
        units.changed.connect(self._set_units)
        self._dyno_widgets["units"] = units
        dyno_card.add(FieldRow("Units", units, hint=HINT_UNITS))

        graph = DynoGraph()
        graph.setToolTip(HINT_GRAPH)
        self._dyno_widgets["graph"] = graph
        dyno_card.add(graph)

        legend = QHBoxLayout()
        legend.setContentsMargins(0, 0, 0, 0)
        legend.setSpacing(16)
        torque_label = QLabel(self._legend_text("torque"))
        torque_label.setStyleSheet(f"color: {T.INFO};")
        self._dyno_widgets["legend_torque"] = torque_label
        power_label = QLabel(self._legend_text("power"))
        power_label.setStyleSheet(f"color: {T.ACCENT_BRIGHT};")
        self._dyno_widgets["legend_power"] = power_label
        legend.addWidget(torque_label)
        legend.addWidget(power_label)
        legend.addStretch(1)
        dyno_card.add_layout(legend)

        overlay_card = page.add_card(
            "DYNO overlay",
            "A panel that follows the game window, like Dragy.",
        )
        show_overlay = ToggleRow("Show over the game", self._overlay_enabled, hint=HINT_OVERLAY)
        show_overlay.toggle.toggled_value.connect(self.set_dyno_overlay)
        self._dyno_widgets["overlay"] = show_overlay
        overlay_card.add(show_overlay)

        overlay_mode = Segmented(["Graph", "Numbers"], self._overlay_mode)
        overlay_mode.changed.connect(self._set_overlay_mode)
        self._dyno_widgets["overlay_mode"] = overlay_mode
        overlay_card.add(FieldRow("Overlay view", overlay_mode))

        overlay_locked = ToggleRow(
            "Lock overlay",
            self._overlay_locked,
            hint=HINT_OVERLAY_LOCK,
        )
        overlay_locked.toggle.toggled_value.connect(self._set_overlay_locked)
        self._dyno_widgets["overlay_locked"] = overlay_locked
        overlay_card.add(overlay_locked)

        display_card = page.add_card("Graph traces", "Choose which dyno traces are displayed.")
        show_torque = ToggleRow("Show torque", self._show_torque)
        show_torque.toggle.toggled_value.connect(self._set_show_torque)
        self._dyno_widgets["show_torque"] = show_torque
        display_card.add(show_torque)

        show_power = ToggleRow("Show power", self._show_power)
        show_power.toggle.toggled_value.connect(self._set_show_power)
        self._dyno_widgets["show_power"] = show_power
        display_card.add(show_power)

        peak_card = page.add_card("Peak output", "Calculated from the live engine curve.")
        peaks = StatStrip()
        for key, label in (
            ("torque", "Peak torque"),
            ("power", "Peak power"),
            ("torque_rpm", "Torque RPM"),
            ("power_rpm", "Power RPM"),
        ):
            peaks.add(key, label, "--")
        self._dyno_widgets["peaks"] = peaks
        peak_card.add(peaks)

        live_card = page.add_card("Live channels", HINT_LIVE)
        live = StatStrip()
        for key, label in (
            ("rpm", "RPM"),
            ("speed", "Speed"),
            ("throttle", "Throttle"),
            ("boost", "Boost"),
            ("gear", "Gear"),
        ):
            live.add(key, label, "--")
        self._dyno_widgets["live"] = live
        live_card.add(live)

        setup_card = page.add_card("Engine details", "Engine fields used to validate the graph.")
        setup = StatStrip()
        for key, label in (
            ("curve", "Curve points"),
            ("step", "RPM step"),
            ("redline", "Rev ceiling"),
            ("driveline", "Final drive"),
        ):
            setup.add(key, label, "--")
        self._dyno_widgets["setup"] = setup
        setup_card.add(setup)

        status = Banner(NOTE_OFFLINE, "info")
        self._dyno_widgets["status"] = status
        page.add(status)

        # Existing Dragy controls remain available on the same DYNO page.
        super().build_page(page)

        # The existing boost gauge page is now part of DYNO rather than a separate nav item.
        page.add_card(
            "Boost Gauge",
            "The boost gauge, now grouped with dyno tools.",
        )
        self._boost.build_page(page)

    def _set_show_torque(self, enabled: bool) -> None:
        self._show_torque = bool(enabled)
        self._dyno_controls_dirty = True

    def _set_show_power(self, enabled: bool) -> None:
        self._show_power = bool(enabled)
        self._dyno_controls_dirty = True

    def _sync_dyno_controls(self) -> None:
        units = self._dyno_widgets.get("units")
        if units is not None:
            units.set_value(self._units)
        for key, kind in (("legend_torque", "torque"), ("legend_power", "power")):
            label = self._dyno_widgets.get(key)
            if label is not None:
                label.setText(self._legend_text(kind))
        overlay_mode = self._dyno_widgets.get("overlay_mode")
        if overlay_mode is not None:
            overlay_mode.set_value(self._overlay_mode)
        for key, value in (
            ("show_torque", self._show_torque),
            ("show_power", self._show_power),
            ("overlay", self._overlay_enabled),
            ("overlay_locked", self._overlay_locked),
        ):
            row = self._dyno_widgets.get(key)
            if row is not None:
                row.set_value(value)

    def _update_dyno_overlay(
        self,
        torque,
        power,
        step=100.0,
        rpm=None,
        redline=None,
        peak_torque="--",
        peak_power="--",
        boost="--",
        gear="--",
        status="Waiting for dyno data",
    ) -> None:
        if self._dyno_overlay is None:
            return
        self._dyno_overlay.update_values(
            torque,
            power,
            step,
            rpm,
            redline,
            self._show_torque,
            self._show_power,
            peak_torque,
            peak_power,
            boost,
            gear,
            status,
            torque_unit=self._output_unit("torque")[1],
            power_unit=self._output_unit("power")[1],
        )

    def refresh(self, vehicle) -> None:
        super().refresh(vehicle)
        self._boost.refresh(vehicle)

        if self._dyno_controls_dirty:
            self._dyno_controls_dirty = False
            self._sync_dyno_controls()

        graph = self._dyno_widgets.get("graph")
        peaks = self._dyno_widgets.get("peaks")
        live = self._dyno_widgets.get("live")
        setup = self._dyno_widgets.get("setup")
        status = self._dyno_widgets.get("status")
        if graph is None or peaks is None or live is None or setup is None or status is None:
            return
        # always_refresh (inherited from Dragy) runs this ~11 times a second on every page.
        # With the DYNO page hidden and no overlay, nothing shows these reads.
        if not graph.isVisible() and not self._overlay_enabled:
            return

        if vehicle is None:
            graph.set_data([], [], show_torque=self._show_torque, show_power=self._show_power)
            peaks.reset()
            live.reset()
            setup.reset()
            status.set(NOTE_OFFLINE, "info")
            self._update_dyno_overlay([], [], status=NOTE_OFFLINE)
            return

        data = self._engine_curve(vehicle)
        if data is None:
            graph.set_data([], [], show_torque=self._show_torque, show_power=self._show_power)
            peaks.reset()
            live.reset()
            setup.reset()
            status.set("Dyno data is unavailable for this car; no values are shown.", "warn")
            self._update_dyno_overlay(
                [], [], status="Dyno data unavailable; no values are shown."
            )
            return

        torque, power, step = data
        rpm = vehicle.rpm
        speed_value, speed_unit = self.settings.speed(vehicle.speed_ms)
        boost_value, boost_unit = self.settings.pressure(vehicle.boost_gauge)
        live.set("rpm", f"{rpm:.0f}" if rpm is not None else "--", unit="rpm")
        live.set("speed", f"{speed_value:.0f}", unit=speed_unit)
        live.set(
            "throttle",
            f"{vehicle.throttle * 100.0:.0f}" if vehicle.throttle is not None else "--",
            unit="%",
        )
        live.set("boost", f"{boost_value:.1f}", unit=boost_unit)
        live.set("gear", str(vehicle.gear) if vehicle.gear is not None else "--", unit="")

        rpm_check = vehicle.engine_speed_rpm
        rpm_delta = abs(rpm - rpm_check) if rpm is not None and rpm_check is not None else None
        reader_ok = rpm_delta is not None and rpm_delta <= 1.0
        setup.set("curve", str(len(torque)), unit="points")
        setup.set("step", f"{step:.0f}", unit="rpm")
        setup.set("redline", f"{(vehicle.rev_ceiling or 0.0):.0f}", unit="rpm")
        final_drive = vehicle.final_drive
        setup.set("driveline", f"{final_drive:.3f}" if final_drive is not None else "--", unit="")
        if rpm_delta is None:
            graph.set_data([], [], show_torque=self._show_torque, show_power=self._show_power)
            peaks.reset()
            status.set("Waiting for the RPM cross-check.", "info")
            self._update_dyno_overlay(
                [],
                [],
                step,
                rpm,
                vehicle.rev_ceiling,
                boost=f"{boost_value:.1f} {boost_unit}",
                gear=str(vehicle.gear) if vehicle.gear is not None else "--",
                status="Waiting for the RPM cross-check.",
            )
            return
        if not reader_ok:
            graph.set_data([], [], show_torque=self._show_torque, show_power=self._show_power)
            peaks.reset()
            status.set("RPM cross-check mismatch; dyno values are held.", "warn")
            self._update_dyno_overlay(
                [],
                [],
                step,
                rpm,
                vehicle.rev_ceiling,
                boost=f"{boost_value:.1f} {boost_unit}",
                gear=str(vehicle.gear) if vehicle.gear is not None else "--",
                status="RPM reader mismatch; output held.",
            )
            return

        torque_factor, torque_unit = self._output_unit("torque")
        power_factor, power_unit = self._output_unit("power")
        torque_shown = [value * torque_factor for value in torque]
        power_shown = [value * power_factor for value in power]
        graph.set_data(
            torque_shown,
            power_shown,
            step,
            rpm,
            vehicle.rev_ceiling,
            self._show_torque,
            self._show_power,
            torque_unit=torque_unit,
            power_unit=power_unit,
        )
        # Same peaks as Vehicle.peaks, from the lists already built (no second turbo read).
        torque_index = max(range(len(torque)), key=torque.__getitem__)
        power_index = max(range(len(power)), key=power.__getitem__)
        torque_nm, torque_rpm = torque[torque_index], torque_index * step
        power_hp, power_rpm = power[power_index], power_index * step
        torque_value, torque_unit = self._format_output(torque_nm, "torque")
        power_value, power_unit = self._format_output(power_hp, "power")
        peaks.set("torque", torque_value, unit=torque_unit)
        peaks.set("power", power_value, unit=power_unit)
        peaks.set("torque_rpm", f"{torque_rpm:.0f}", unit="rpm")
        peaks.set("power_rpm", f"{power_rpm:.0f}", unit="rpm")
        status.set(NOTE_DYNO_ACTIVE, "info")
        self._update_dyno_overlay(
            torque_shown,
            power_shown,
            step,
            rpm,
            vehicle.rev_ceiling,
            peak_torque=f"{torque_value} {torque_unit}",
            peak_power=f"{power_value} {power_unit}",
            boost=f"{boost_value:.1f} {boost_unit}",
            gear=str(vehicle.gear) if vehicle.gear is not None else "--",
            status=NOTE_DYNO_ACTIVE,
        )

    def save_state(self) -> dict:
        state = super().save_state()
        state["dyno"] = {
            "units": self._units,
            "show_torque": self._show_torque,
            "show_power": self._show_power,
            "overlay": self._overlay_enabled,
            "overlay_mode": self._overlay_mode,
            "overlay_locked": self._overlay_locked,
            "overlay_position": list(self._overlay_position),
        }
        state["boost_gauge"] = self._boost.save_state()
        return state

    def load_state(self, data: dict) -> None:
        data = data or {}
        super().load_state(data)
        dyno = data.get("dyno") or {}
        units = dyno.get("units", DYNO_UNITS[0])
        self._units = units if units in DYNO_UNITS else DYNO_UNITS[0]
        self._show_torque = bool(dyno.get("show_torque", True))
        self._show_power = bool(dyno.get("show_power", True))
        self._overlay_mode = (
            dyno.get("overlay_mode") if dyno.get("overlay_mode") in ("Graph", "Numbers") else "Graph"
        )
        self._overlay_locked = bool(dyno.get("overlay_locked", True))
        position = dyno.get("overlay_position")
        if isinstance(position, (list, tuple)) and len(position) == 2:
            try:
                x, y = float(position[0]), float(position[1])
                if x == x and y == y:
                    self._overlay_position = (max(0.0, min(1.0, x)), max(0.0, min(1.0, y)))
            except (TypeError, ValueError):
                pass
        self._overlay_enabled = bool(dyno.get("overlay", False))
        if self._overlay_enabled:
            self._ensure_dyno_overlay()
        elif self._dyno_overlay is not None:
            self._dyno_overlay.stop()
        self._boost.load_state(data.get("boost_gauge") or {})
        self._dyno_controls_dirty = True
