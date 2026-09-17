"""DYNO: live engine output and tuning telemetry."""

from __future__ import annotations

import os
import time

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import ComboBox, DoubleSpinBox

from neptune import __version__
from neptune.core import carnames
from neptune.core import logs as log_store
from neptune.core.models import CarIdentity, LogMetadata, NeptuneLog, TransmissionTune, finite_float
from neptune.core.transmission import gear_rows
from neptune.features.boostgauge import BoostGaugeModule
from neptune.features.dragy import DragyModule
from neptune.memory import offsets as O
from neptune.ui import theme as T
from neptune.ui.dynooverlay import DynoOverlay
from neptune.ui.logoverlay import LogOverlay
from neptune.ui.widgets.buttons import Button, PrimaryButton
from neptune.ui.widgets.card import Banner, FieldRow, StatStrip, ToggleRow, bind_progressive
from neptune.ui.widgets.controls import SectionHeading, Segmented
from neptune.ui.widgets.dynograph import DynoGraph
from neptune.ui.widgets.gearing import GearingChart
from neptune.vehicle.vehicle import NM_RPM_TO_HP

DYNO_UNITS = ("hp / Nm", "kW / Nm", "hp / lb-ft", "kW / lb-ft")
ARMED_SAMPLES_KEPT = 20
PULL_START_THROTTLE = 0.9

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

LOG_TESTS = {
    "Launch": (0.0, 13.4112, "Come to a stop below the line. Hold full throttle and let Neptune detect movement automatically."),
    "0–60 MPH": (0.0, 26.8224, "Come to a complete stop, then use full throttle. Neptune starts on movement and stops at 60 MPH."),
    "0–100 MPH": (0.0, 44.704, "Come to a complete stop, then use full throttle. Neptune starts on movement and stops at 100 MPH."),
    "60–130 MPH": (26.8224, 58.1303, "Start below 55 MPH on a safe straight section. Use full throttle through 130 MPH; capture is automatic."),
    "100–200 KM/H": (27.7778, 55.5556, "Start below 95 KM/H on a safe straight section. Use full throttle through 200 KM/H; capture is automatic."),
    "150–250 KM/H": (41.6667, 69.4444, "Start below 140 KM/H on a safe straight section. Use full throttle through 250 KM/H; capture is automatic."),
    # Pulls have no speed range: they start when the throttle goes to full, moving or not.
    "Single Gear Pull": (0.0, 0.0, "Select a gear in the game and hold full throttle. Stop or lift to finish the capture."),
    "Full Acceleration Pull": (0.0, 0.0, "Use full throttle through a safe straight section. Stop or lift to finish the capture."),
    "Custom speed range": (None, None, "Choose the start and finish speeds below. Neptune arms the run and captures only the selected range."),
}


def _position(value, fallback: tuple[float, float]) -> tuple[float, float]:
    """A stored relative overlay position, clamped to the game window, else `fallback`."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        x, y = finite_float(value[0]), finite_float(value[1])
        if x is not None and y is not None:
            return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
    return fallback


class DynoModule(DragyModule):
    """Keep Dragy and Boost Gauge on one DYNO page while adding dyno data."""

    name = "dragy"  # Preserve the preset key used by earlier Neptune builds.
    title = "DYNO"
    subtitle = "Power, torque, Dragy runs and boost."
    icon = "dragy.png"
    group = "Vehicle"
    order = 35

    def __init__(self, settings, registry=None):
        super().__init__(settings)
        self._registry = registry
        self._page = None
        self._boost = BoostGaugeModule(settings)
        self._dyno_enabled = bool(settings.get("dyno_enabled"))
        self._dyno_controls_dirty = False
        self._dyno_widgets: dict = {}
        self._show_torque = True
        self._show_power = True
        self._units = DYNO_UNITS[0]
        # Not `_overlay_*`: DragyModule already owns `_overlay_enabled` for its own panel, and
        # sharing it made either overlay switch the other on or freeze it.
        self._dyno_overlay: DynoOverlay | None = None
        self._dyno_overlay_enabled = False
        self._dyno_overlay_mode = "Graph"
        self._dyno_overlay_locked = True
        self._dyno_overlay_position = (0.04, 0.10)
        self._log_overlay: LogOverlay | None = None
        self._log_overlay_enabled = False
        self._log_overlay_locked = True
        self._log_overlay_position = (0.04, 0.72)
        self._log_units = {
            "output": DYNO_UNITS[0],
            "speed": self.settings.get("speed_unit"),
            "pressure": self.settings.get("pressure_unit"),
        }
        self._capture_state = "idle"
        self._capture_config: dict = {}
        self._capture_samples = []
        self._capture_started_at: float | None = None
        self._capture_last_speed: float | None = None
        self._capture_last_throttle: float | None = None
        self._pending_log: NeptuneLog | None = None
        self._result_shown = False
        self._capture_widgets: dict = {}
        self._capture_curve: list[float] = []
        self._capture_curve_step = 100.0
        self._capture_curve_scale = 1.0
        self._gearing_signature = None

    def tick_process(self, process) -> None:
        super().tick_process(process)
        self._boost.tick_process(process)

    def needs_refresh(self) -> bool:
        """Refresh with the page hidden only while something outside the page shows live data."""
        if self._boost._enabled:
            return True
        return self._dyno_enabled and (
            self._overlay_enabled
            or self._dyno_overlay_enabled
            or self._log_overlay_enabled
            or self._capture_state in ("armed", "running", "done")
        )

    def on_attach(self, vehicle) -> None:
        super().on_attach(vehicle)
        self._boost.on_attach(vehicle)
        if self._dyno_overlay is not None:
            self._dyno_overlay.set_vehicle(vehicle)
        if self._dyno_overlay_enabled:
            self._ensure_dyno_overlay()
        if self._log_overlay_enabled:
            self._ensure_log_overlay()

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
        # Runtime thread: the log overlay is a widget, so refresh() repaints it, never this.
        if self._capture_state in ("armed", "running"):
            self._capture_state = "idle"
            self._capture_samples.clear()
        self._capture_curve = []

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
        if self._log_overlay is not None:
            self._log_overlay.stop()
            self._log_overlay.deleteLater()
            self._log_overlay = None

    def tick(self, vehicle) -> None:
        if not self._dyno_enabled:
            return
        super().tick(vehicle)
        if self._capture_state in ("armed", "running") and vehicle is not None:
            self._capture_tick(vehicle)

    def _module(self, name: str):
        return self._registry.get(name) if self._registry is not None else None

    def _metadata(self, test_type: str, config: dict, sample_rate_hz: float = 0.0) -> LogMetadata:
        vehicle = self.vehicle
        friendly_name = carnames.label(vehicle.media_name, vehicle.car_id) if vehicle is not None else ""
        tunes = self._module("tunes")
        identity = CarIdentity.from_vehicle(vehicle, friendly_name, tunes.current_car_key if tunes else "")
        tune_id = tune_name = ""
        tune_revision = None
        current = tunes.current_tune() if tunes is not None else None
        if current:
            tune_id = str(current.get("id") or "")
            tune_name = str(current.get("name") or "")
            try:
                tune_revision = int(current.get("revision"))
            except (TypeError, ValueError):
                tune_revision = None
        module = self._module("transmission")
        tune = module.tune if module is not None else None
        transmission = TransmissionTune.from_dict(tune.to_dict()) if tune is not None else TransmissionTune()
        configured_units = config.get("units") if isinstance(config.get("units"), dict) else {}
        output = configured_units.get("output", self._log_units.get("output", DYNO_UNITS[0]))
        power_unit, torque_unit = self._output_units(output)
        units = {
            "speed": configured_units.get("speed", self._log_units.get("speed", self.settings.get("speed_unit"))),
            "pressure": configured_units.get("pressure", self._log_units.get("pressure", self.settings.get("pressure_unit"))),
            "power": power_unit,
            "torque": torque_unit,
        }
        return LogMetadata(
            neptune_version=__version__,
            game_build=O.GAME_BUILD,
            test_type=test_type,
            units=units,
            sample_rate_hz=sample_rate_hz,
            car=identity,
            tune_id=tune_id,
            tune_name=tune_name,
            tune_revision=tune_revision,
            transmission=transmission,
            test_config=dict(config),
        )

    @staticmethod
    def _output_units(label: str) -> tuple[str, str]:
        if label not in DYNO_UNITS:
            label = DYNO_UNITS[0]
        power, torque = label.split(" / ", 1)
        return power, torque

    def _unit_summary(self) -> str:
        return f"{self._log_units.get('output', DYNO_UNITS[0])} | {self._log_units.get('speed', 'km/h')} | {self._log_units.get('pressure', 'psi')}"

    def _capture_tick(self, vehicle) -> None:
        """Runtime thread. Records one sample and moves the capture state; never touches widgets."""
        now = time.monotonic()
        rows = int(self._capture_config.get("boost_map_rows", 1) or 1)
        redline = float(self._capture_config.get("redline_rpm") or 8000.0)
        engine = self._module("engine")
        turbo = self._module("turbo")
        sample = log_store.sample_from_vehicle(
            vehicle,
            now,
            boost_multiplier=vehicle.boost_multiplier(),
            anti_lag=engine is not None and engine.anti_lag_active,
            launch_control=engine is not None and engine.launch_active,
            scramble=turbo is not None and turbo.scramble_active,
        )
        sample.torque, sample.power = self._capture_output(sample.rpm)  # no second rpm read
        cell = log_store.boost_map_cell(sample, redline, rows)
        sample.boost_cell = list(cell) if cell is not None else None
        self._capture_samples.append(sample)
        if self._capture_state == "armed" and len(self._capture_samples) > 2 * ARMED_SAMPLES_KEPT:
            # Only the lead-in before the start is kept. Armed for minutes must not grow forever.
            del self._capture_samples[:-ARMED_SAMPLES_KEPT]
        speed = sample.speed_ms
        throttle = sample.throttle or 0.0
        previous_throttle = self._capture_last_throttle
        self._capture_last_throttle = throttle
        if speed is None:
            return
        start = self._capture_config.get("start_ms")
        end = self._capture_config.get("end_ms")
        previous = self._capture_last_speed
        self._capture_last_speed = speed
        if self._capture_state == "armed":
            if end is None:
                started = previous_throttle is not None and previous_throttle < PULL_START_THROTTLE <= throttle
            elif start is None or start <= 0.0:
                started = previous is not None and previous <= 0.5 < speed and throttle >= 0.5
            else:
                started = previous is not None and previous <= start < speed
            if started:
                self._capture_state = "running"
                self._capture_started_at = now
                del self._capture_samples[:-ARMED_SAMPLES_KEPT]
        elapsed = now - (self._capture_started_at or now)
        # A pull may start from a stop, so standing still only ends it once it had time to move.
        lifted = end is None and (
            (elapsed > 0.25 and sample.throttle is not None and sample.throttle < 0.2)
            or (elapsed > 2.0 and speed <= 0.5)
        )
        if self._capture_state == "running" and (
            (end and speed >= end) or lifted or elapsed > 120.0
        ):
            self._finish_capture()

    def _finish_capture(self) -> None:
        if self._capture_state not in ("armed", "running"):
            return
        self._capture_state = "done"
        samples = list(self._capture_samples)
        duration = samples[-1].timestamp - samples[0].timestamp if len(samples) > 1 else 0.0
        rate = (len(samples) - 1) / duration if duration > 0.0 else 0.0
        metadata = self._metadata(
            self._capture_config.get("test_type", "Full acceleration pull"), dict(self._capture_config), rate
        )
        log = NeptuneLog(metadata, samples)
        log.analysis = log_store.analyze_log(metadata, log.samples)
        self._pending_log = log
        self._result_shown = False
        self._capture_curve = []

    def _show_capture_result(self) -> None:
        if self._pending_log is None or self._result_shown:
            return
        self._result_shown = True
        log = self._pending_log
        reasons = "\n".join(f"• {reason}" for reason in log.analysis.reasons) or "No quality warnings."
        box = QMessageBox(self._window())
        box.setWindowTitle("Run complete")
        box.setText(f"Run Quality: {log.analysis.quality}")
        box.setInformativeText(f"{len(log.samples):,} samples · {log.analysis.duration or 0.0:.2f} s\n\n{reasons}")
        save = box.addButton("Save Log", QMessageBox.AcceptRole)
        open_reader = box.addButton("Save && Open Reader", QMessageBox.ActionRole)  # a single & is a mnemonic
        box.addButton("Discard", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (save, open_reader):
            self._pending_log = None
            self._capture_state = "idle"
            return
        ok, message, path = log_store.save_log(log)
        if not ok:
            QMessageBox.warning(box, "Save log", message)
        else:
            self._associate_log(path, log)
            logs = self._module("logs")
            if open_reader is clicked and logs is not None:
                logs.open_reader(path)
        self._pending_log = None
        self._capture_state = "idle"

    def _associate_log(self, path: str, log: NeptuneLog) -> None:
        tunes = self._module("tunes")
        if tunes is None or tunes.current_tune() is None:
            return
        key = tunes.current_car_key
        tunes.store.record_log(key, tunes.store.active_index(key), os.path.basename(path), log.analysis.metrics)

    def _window(self):
        return self._page.window() if self._page is not None else None

    def _generate_log(self) -> None:
        dialog = QDialog(self._window())
        dialog.setObjectName("Root")  # the app background from theme.stylesheet(), not Fusion's grey
        dialog.setWindowTitle("Generate Neptune Log")
        dialog.setMinimumWidth(600)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        layout.addWidget(
            SectionHeading(
                "Generate Neptune Log",
                "Select a structured test. Neptune arms the run and detects its start/end conditions automatically.",
            )
        )
        selector = ComboBox()
        selector.addItems(list(LOG_TESTS))
        selector.setMinimumWidth(260)
        layout.addWidget(FieldRow("Test", selector))
        procedure = QLabel()
        procedure.setObjectName("RowHint")
        procedure.setWordWrap(True)
        layout.addWidget(procedure)

        output_units = Segmented(list(DYNO_UNITS), self._log_units.get("output", self._units))
        layout.addWidget(FieldRow("Output", output_units))
        speed_units = Segmented(["km/h", "mph"], self._log_units.get("speed", self.settings.get("speed_unit")))
        layout.addWidget(FieldRow("Speed", speed_units))
        pressure_units = Segmented(["psi", "bar"], self._log_units.get("pressure", self.settings.get("pressure_unit")))
        layout.addWidget(FieldRow("Pressure", pressure_units))

        custom_controls = QWidget()
        custom_layout = QHBoxLayout(custom_controls)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(8)
        custom_start = DoubleSpinBox()
        custom_start.setRange(0.0, 500.0)
        custom_start.setDecimals(1)
        custom_start.setSingleStep(1.0)
        custom_start.setValue(0.0)
        custom_layout.addWidget(custom_start)
        to_label = QLabel("to")
        to_label.setObjectName("RowHint")
        custom_layout.addWidget(to_label)
        custom_end = DoubleSpinBox()
        custom_end.setRange(0.1, 500.0)
        custom_end.setDecimals(1)
        custom_end.setSingleStep(1.0)
        custom_end.setValue(60.0)
        custom_layout.addWidget(custom_end)
        custom_unit = Segmented(["mph", "km/h"], speed_units.value())
        custom_layout.addWidget(custom_unit)
        # One row, so its label hides with the fields instead of staying behind on its own.
        custom_row = FieldRow("Custom range", custom_controls)
        layout.addWidget(custom_row)

        def update_procedure(label: str) -> None:
            procedure.setText(LOG_TESTS.get(label, (0, 0, ""))[2])
            visible = label == "Custom speed range"
            custom_row.setVisible(visible)
            if visible:
                custom_unit.set_value(speed_units.value())

        speed_units.changed.connect(custom_unit.set_value)

        selector.currentTextChanged.connect(update_procedure)
        update_procedure(selector.currentText())
        layout.addStretch(1)

        def arm() -> None:
            if selector.currentText() == "Custom speed range" and custom_end.value() <= custom_start.value():
                QMessageBox.warning(dialog, "Custom speed range", "The finish speed must be higher than the start speed.")
                return
            dialog.accept()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = Button("Cancel")
        cancel.clicked.connect(dialog.reject)
        buttons.addWidget(cancel)
        arm_button = PrimaryButton("ARM")
        arm_button.clicked.connect(arm)
        buttons.addWidget(arm_button)
        layout.addLayout(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        self._log_units = {
            "output": output_units.value(),
            "speed": speed_units.value(),
            "pressure": pressure_units.value(),
        }
        label = selector.currentText()
        start, end, _ = LOG_TESTS[label]
        if label == "Custom speed range":
            custom_unit_value = custom_unit.value()
            start = custom_start.value() / (O.MS_TO_MPH if custom_unit_value == "mph" else O.MS_TO_KPH)
            end = custom_end.value() / (O.MS_TO_MPH if custom_unit_value == "mph" else O.MS_TO_KPH)
        turbo = self._module("turbo")
        self._capture_config = {
            "test_type": label,
            "start_ms": start,
            "end_ms": end if end > 0 else None,
            "full_throttle": True,
            "redline_rpm": self.vehicle.rev_ceiling if self.vehicle is not None else None,
            "boost_map_rows": turbo._map_rows if turbo is not None else 1,
            "units": dict(self._log_units),
        }
        if label == "Custom speed range":
            self._capture_config["custom_unit"] = custom_unit.value()
        self._capture_samples = []
        self._capture_last_speed = None
        self._capture_last_throttle = None
        self._capture_started_at = None
        self._result_shown = False
        self._capture_curve = []
        self._capture_curve_step = 100.0
        self._capture_curve_scale = 1.0
        if self.vehicle is not None:
            try:
                curve = list(self.vehicle.curve() or [])
                step = float(self.vehicle.rpm_per_index or 0.0)
                scale = float(self.vehicle.boost_multiplier() or 1.0) * step
                if len(curve) >= 2 and step > 0.0:
                    self._capture_curve = curve
                    self._capture_curve_step = step
                    self._capture_curve_scale = scale
            except (TypeError, ValueError, OverflowError):
                self._capture_curve = []
        # Last: the runtime thread starts sampling as soon as this flips, so the curve comes first.
        self._capture_state = "armed"
        status = self._capture_widgets.get("status")
        if status is not None:
            status.set(f"Armed: {label}. Follow the procedure above; Neptune will capture automatically.", "ok")
        self._update_log_overlay()

    def _capture_output(self, rpm) -> tuple[float | None, float | None]:
        try:
            rpm = float(rpm)
            if not self._capture_curve or self._capture_curve_step <= 0.0:
                return None, None
            index = max(0, min(len(self._capture_curve) - 1, round(rpm / self._capture_curve_step)))
            torque = max(0.0, float(self._capture_curve[index]) * self._capture_curve_scale)
            return torque, torque * rpm / NM_RPM_TO_HP
        except (TypeError, ValueError, OverflowError):
            return None, None

    def _update_gearing_chart(self, vehicle, redline: float | None, gear: int | None) -> None:
        chart = self._dyno_widgets.get("gearing_chart")
        if chart is None or not chart.isVisible():
            return
        if vehicle is None:
            self._gearing_signature = None
            chart.set_data([], 8000.0)
            return
        transmission = self._module("transmission")
        tune = transmission.tune if transmission is not None else None
        if tune is None or tune.validate():
            tune = TransmissionTune.from_vehicle(vehicle)
        redline = float(redline or 8000.0)
        # The chart only changes with the gearing, the redline or the live gear: skip identical refreshes.
        signature = (tune.final_drive, tuple(tune.ratios), redline, gear)
        if signature == self._gearing_signature:
            return
        self._gearing_signature = signature
        chart.set_data(gear_rows(tune, redline), redline, live_gear=gear)

    def _ensure_overlay(self):
        """Dragy's panel, only while DYNO is on: Dragy does not time with DYNO off."""
        return super()._ensure_overlay() if self._dyno_enabled else None

    def _ensure_dyno_overlay(self) -> DynoOverlay | None:
        if not self._dyno_enabled:
            return None
        if self._dyno_overlay is None:
            self._dyno_overlay = DynoOverlay()
            self._dyno_overlay.set_moved_callback(self._on_overlay_moved)
            self._dyno_overlay.set_mode(self._dyno_overlay_mode)
            self._dyno_overlay.set_locked(self._dyno_overlay_locked)
            self._dyno_overlay.set_position(*self._dyno_overlay_position)
            power_unit, torque_unit = self._output_units(self._units)
            self._dyno_overlay.set_units(torque_unit, power_unit)
        self._dyno_overlay.set_vehicle(self.vehicle)
        pid = self._process.pid if self._process is not None else None
        self._dyno_overlay.start(pid)
        return self._dyno_overlay

    def _ensure_log_overlay(self) -> LogOverlay | None:
        if not self._dyno_enabled:
            return None
        if self._log_overlay is None:
            self._log_overlay = LogOverlay()
            self._log_overlay.set_moved_callback(self._on_log_overlay_moved)
            self._log_overlay.set_locked(self._log_overlay_locked)
            self._log_overlay.set_position(*self._log_overlay_position)
        pid = self._process.pid if self._process is not None else None
        self._log_overlay.start(pid)
        self._update_log_overlay()
        return self._log_overlay

    def _update_log_overlay(self) -> None:
        if self._log_overlay is None:
            return
        state = self._capture_state
        if state == "idle":
            status = "Choose Generate Log in Neptune"
        elif state == "armed":
            status = "Waiting for valid start conditions"
        elif state == "running":
            status = "Capturing live vehicle data"
        elif state == "done":
            status = "Run complete - review and save it"
        else:
            status = "Waiting for capture"
        self._log_overlay.update_capture(
            self._capture_config.get("test_type", "No log armed"),
            state,
            len(self._capture_samples),
            self._unit_summary(),
            status,
        )

    def _on_log_overlay_moved(self, position) -> None:
        self._log_overlay_position = position

    def set_log_overlay(self, enabled: bool) -> None:
        self._log_overlay_enabled = bool(enabled)
        if self._log_overlay_enabled:
            self._ensure_log_overlay()
        elif self._log_overlay is not None:
            self._log_overlay.stop()

    def _set_log_overlay_locked(self, locked: bool) -> None:
        self._log_overlay_locked = bool(locked)
        if self._log_overlay is not None:
            self._log_overlay.set_locked(self._log_overlay_locked)

    def _set_dyno_enabled(self, enabled: bool) -> None:
        self._dyno_enabled = bool(enabled)
        self.settings.remember("dyno_enabled", self._dyno_enabled)
        # Dragy only times while DYNO is on (tick() is gated), so its panel follows the switch too.
        overlays = (
            (self._overlay, self._overlay_enabled, self._ensure_overlay),
            (self._dyno_overlay, self._dyno_overlay_enabled, self._ensure_dyno_overlay),
            (self._log_overlay, self._log_overlay_enabled, self._ensure_log_overlay),
        )
        for overlay, wanted, ensure in overlays:
            if self._dyno_enabled and wanted:
                ensure()
            elif overlay is not None:
                overlay.stop()
        if not self._dyno_enabled and self._capture_state in ("armed", "running"):
            self._capture_state = "idle"
            self._capture_samples.clear()
        self._dyno_controls_dirty = True

    def set_dyno_overlay(self, enabled: bool) -> None:
        self._dyno_overlay_enabled = bool(enabled)
        if self._dyno_overlay_enabled:
            self._ensure_dyno_overlay()
        elif self._dyno_overlay is not None:
            self._dyno_overlay.stop()

    def _set_overlay_mode(self, mode: str) -> None:
        if mode not in ("Graph", "Numbers"):
            return
        self._dyno_overlay_mode = mode
        if self._dyno_overlay is not None:
            self._dyno_overlay.set_mode(mode)
        self._dyno_controls_dirty = True

    def _set_overlay_locked(self, locked: bool) -> None:
        self._dyno_overlay_locked = bool(locked)
        if self._dyno_overlay is not None:
            self._dyno_overlay.set_locked(self._dyno_overlay_locked)
        self._dyno_controls_dirty = True

    def _on_overlay_moved(self, position) -> None:
        self._dyno_overlay_position = position

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

    def _format_output(self, value: float, kind: str) -> tuple[str, str]:
        if kind == "torque":
            if self._units.endswith("lb-ft"):
                return f"{value * O.NM_TO_LBFT:.0f}", "lb-ft"
            return f"{value:.0f}", "Nm"
        if self._units.startswith("kW"):
            return f"{value * O.HP_TO_KW:.0f}", "kW"
        return f"{value:.0f}", "hp"

    def _set_units(self, value: str) -> None:
        if value in DYNO_UNITS:
            self._units = value
            power_unit, torque_unit = self._output_units(value)
            graph = self._dyno_widgets.get("graph")
            if graph is not None:
                graph.set_units(torque_unit, power_unit)
            torque_label = self._dyno_widgets.get("legend_torque")
            if torque_label is not None:
                torque_label.setText(f"● Torque  {torque_unit}")
            power_label = self._dyno_widgets.get("legend_power")
            if power_label is not None:
                power_label.setText(f"● Power  {power_unit}")
            if self._dyno_overlay is not None:
                self._dyno_overlay.set_units(torque_unit, power_unit)
            self._dyno_controls_dirty = True

    def build_page(self, page) -> None:
        self._page = page
        enable_card = page.add_card("DYNO")
        enabled = ToggleRow(
            "Enable DYNO controls",
            self._dyno_enabled,
            hint="Shows the dyno, logging, Dragy and grouped Boost Gauge controls. Turning it off disarms "
            "captures and stops the DYNO, log and Dragy overlays.",
        )
        enabled.toggle.toggled_value.connect(self._set_dyno_enabled)
        self._dyno_widgets["enabled"] = enabled
        enable_card.add(enabled)

        detail_cards = []

        generate_card = page.add_card("Structured testing", "Arm a guided pull and let Neptune detect the valid start and end conditions while you drive.")
        detail_cards.append(generate_card)
        generate = PrimaryButton("Generate Log")
        generate.clicked.connect(self._generate_log)
        generate_card.add(generate)
        capture_status = Banner("No log armed.", "info")
        self._capture_widgets["status"] = capture_status
        generate_card.add(capture_status)

        log_overlay = ToggleRow(
            "Show log status over the game",
            self._log_overlay_enabled,
            hint="Shows the current log test, state, sample count and units above the game window.",
        )
        log_overlay.toggle.toggled_value.connect(self.set_log_overlay)
        self._dyno_widgets["log_overlay"] = log_overlay
        generate_card.add(log_overlay)

        log_overlay_locked = ToggleRow(
            "Lock log overlay",
            self._log_overlay_locked,
            hint="Unlock only to drag the log status panel; lock it again to keep game clicks passing through.",
        )
        log_overlay_locked.toggle.toggled_value.connect(self._set_log_overlay_locked)
        self._dyno_widgets["log_overlay_locked"] = log_overlay_locked
        generate_card.add(log_overlay_locked)

        dyno_card = page.add_card("Dyno", HINT_DYNO)
        detail_cards.append(dyno_card)

        units = Segmented(list(DYNO_UNITS), self._units)
        units.changed.connect(self._set_units)
        self._dyno_widgets["units"] = units
        dyno_card.add(FieldRow("Units", units, hint=HINT_UNITS))

        graph = DynoGraph()
        power_unit, torque_unit = self._output_units(self._units)
        graph.set_units(torque_unit, power_unit)
        graph.setToolTip(HINT_GRAPH)
        self._dyno_widgets["graph"] = graph
        dyno_card.add(graph)

        gearing_card = page.add_card(
            "DYNO + Transmission",
            "Use the same verified gearing model beside the dyno curve. Amber markers show estimated shift landings.",
        )
        detail_cards.append(gearing_card)
        gearing_chart = GearingChart()
        self._dyno_widgets["gearing_chart"] = gearing_chart
        gearing_card.add(gearing_chart)

        legend = QHBoxLayout()
        legend.setContentsMargins(0, 0, 0, 0)
        legend.setSpacing(16)
        torque_label = QLabel(f"● Torque  {torque_unit}")
        torque_label.setStyleSheet(f"color: {T.INFO};")
        power_label = QLabel(f"● Power  {power_unit}")
        power_label.setStyleSheet(f"color: {T.ACCENT_BRIGHT};")
        self._dyno_widgets["legend_torque"] = torque_label
        self._dyno_widgets["legend_power"] = power_label
        legend.addWidget(torque_label)
        legend.addWidget(power_label)
        legend.addStretch(1)
        dyno_card.add_layout(legend)

        overlay_card = page.add_card(
            "DYNO overlay",
            "A panel that follows the game window, like Dragy.",
        )
        detail_cards.append(overlay_card)
        show_overlay = ToggleRow("Show over the game", self._dyno_overlay_enabled, hint=HINT_OVERLAY)
        show_overlay.toggle.toggled_value.connect(self.set_dyno_overlay)
        self._dyno_widgets["overlay"] = show_overlay
        overlay_card.add(show_overlay)

        overlay_mode = Segmented(["Graph", "Numbers"], self._dyno_overlay_mode)
        overlay_mode.changed.connect(self._set_overlay_mode)
        self._dyno_widgets["overlay_mode"] = overlay_mode
        overlay_card.add(FieldRow("Overlay view", overlay_mode))

        overlay_locked = ToggleRow(
            "Lock overlay",
            self._dyno_overlay_locked,
            hint=HINT_OVERLAY_LOCK,
        )
        overlay_locked.toggle.toggled_value.connect(self._set_overlay_locked)
        self._dyno_widgets["overlay_locked"] = overlay_locked
        overlay_card.add(overlay_locked)

        display_card = page.add_card("Graph traces", "Choose which dyno traces are displayed.")
        detail_cards.append(display_card)
        show_torque = ToggleRow("Show torque", self._show_torque)
        show_torque.toggle.toggled_value.connect(self._set_show_torque)
        self._dyno_widgets["show_torque"] = show_torque
        display_card.add(show_torque)

        show_power = ToggleRow("Show power", self._show_power)
        show_power.toggle.toggled_value.connect(self._set_show_power)
        self._dyno_widgets["show_power"] = show_power
        display_card.add(show_power)

        peak_card = page.add_card("Peak output", "Calculated from the live engine curve.")
        detail_cards.append(peak_card)
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
        detail_cards.append(live_card)
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
        detail_cards.append(setup_card)
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
        super().build_page(page, card_sink=detail_cards)

        # The existing boost gauge page is now part of DYNO rather than a separate nav item.
        boost_card = page.add_card(
            "Boost Gauge",
            "The boost gauge, now grouped with dyno tools.",
        )
        detail_cards.append(boost_card)
        self._boost.build_page(page, container=boost_card)

        bind_progressive(enabled, *detail_cards)

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
        overlay_mode = self._dyno_widgets.get("overlay_mode")
        if overlay_mode is not None:
            overlay_mode.set_value(self._dyno_overlay_mode)
        for key, value in (
            ("enabled", self._dyno_enabled),
            ("show_torque", self._show_torque),
            ("show_power", self._show_power),
            ("overlay", self._dyno_overlay_enabled),
            ("overlay_locked", self._dyno_overlay_locked),
            ("log_overlay", self._log_overlay_enabled),
            ("log_overlay_locked", self._log_overlay_locked),
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
        )

    def refresh(self, vehicle) -> None:
        self._update_log_overlay()
        # Before the early return: a preset that turns DYNO off must still flip its toggle off.
        if self._dyno_controls_dirty:
            self._dyno_controls_dirty = False
            self._sync_dyno_controls()
        if not self._dyno_enabled:
            self._boost.refresh(vehicle)
            return
        if self._capture_state == "done":
            self._show_capture_result()
        capture_status = self._capture_widgets.get("status")
        if capture_status is not None and self._capture_state in ("armed", "running"):
            capture_status.set(
                "Capturing…" if self._capture_state == "running" else "Armed — waiting for valid start conditions.",
                "ok" if self._capture_state == "running" else "info",
            )
        super().refresh(vehicle)
        self._boost.refresh(vehicle)

        graph = self._dyno_widgets.get("graph")
        peaks = self._dyno_widgets.get("peaks")
        live = self._dyno_widgets.get("live")
        setup = self._dyno_widgets.get("setup")
        status = self._dyno_widgets.get("status")
        if graph is None or peaks is None or live is None or setup is None or status is None:
            return
        # needs_refresh() also runs this page hidden for the boost gauge, Dragy or log overlays;
        # none of those shows what follows.
        if not graph.isVisible() and not self._dyno_overlay_enabled:
            return
        # Each of these is a process read: read once per refresh, not once per use.
        redline = vehicle.rev_ceiling if vehicle is not None else None
        gear = vehicle.gear if vehicle is not None else None
        self._update_gearing_chart(vehicle, redline, gear)

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
        throttle = vehicle.throttle
        live.set("throttle", f"{throttle * 100.0:.0f}" if throttle is not None else "--", unit="%")
        live.set("boost", f"{boost_value:.1f}", unit=boost_unit)
        gear_text = str(gear) if gear is not None else "--"
        live.set("gear", gear_text, unit="")

        rpm_check = vehicle.engine_speed_rpm
        rpm_delta = abs(rpm - rpm_check) if rpm is not None and rpm_check is not None else None
        reader_ok = rpm_delta is not None and rpm_delta <= 1.0
        setup.set("curve", str(len(torque)), unit="points")
        setup.set("step", f"{step:.0f}", unit="rpm")
        setup.set("redline", f"{(redline or 0.0):.0f}", unit="rpm")
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
                redline,
                boost=f"{boost_value:.1f} {boost_unit}",
                gear=gear_text,
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
                redline,
                boost=f"{boost_value:.1f} {boost_unit}",
                gear=gear_text,
                status="RPM reader mismatch; output held.",
            )
            return

        graph.set_data(
            torque,
            power,
            step,
            rpm,
            redline,
            self._show_torque,
            self._show_power,
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
            torque,
            power,
            step,
            rpm,
            redline,
            peak_torque=f"{torque_value} {torque_unit}",
            peak_power=f"{power_value} {power_unit}",
            boost=f"{boost_value:.1f} {boost_unit}",
            gear=gear_text,
            status=NOTE_DYNO_ACTIVE,
        )

    def save_state(self) -> dict:
        state = super().save_state()
        state["dyno"] = {
            "enabled": self._dyno_enabled,
            "units": self._units,
            "show_torque": self._show_torque,
            "show_power": self._show_power,
            "overlay": self._dyno_overlay_enabled,
            "overlay_mode": self._dyno_overlay_mode,
            "overlay_locked": self._dyno_overlay_locked,
            "overlay_position": list(self._dyno_overlay_position),
            "log_overlay": self._log_overlay_enabled,
            "log_overlay_locked": self._log_overlay_locked,
            "log_overlay_position": list(self._log_overlay_position),
            "log_units": dict(self._log_units),
        }
        state["boost_gauge"] = self._boost.save_state()
        return state

    def load_state(self, data: dict) -> None:
        data = data or {}
        dyno = data.get("dyno") or {}
        # Before Dragy loads: its overlay only starts while DYNO is on.
        self._dyno_enabled = bool(dyno.get("enabled", True))
        super().load_state(data)
        units = dyno.get("units")
        self._set_units(units if units in DYNO_UNITS else DYNO_UNITS[0])  # graph, legend and overlay too
        self._show_torque = bool(dyno.get("show_torque", True))
        self._show_power = bool(dyno.get("show_power", True))
        mode = dyno.get("overlay_mode")
        self._dyno_overlay_mode = mode if mode in ("Graph", "Numbers") else "Graph"
        self._dyno_overlay_locked = bool(dyno.get("overlay_locked", True))
        self._dyno_overlay_position = _position(dyno.get("overlay_position"), self._dyno_overlay_position)
        self._dyno_overlay_enabled = bool(dyno.get("overlay", False))
        self._log_overlay_enabled = bool(dyno.get("log_overlay", False))
        self._log_overlay_locked = bool(dyno.get("log_overlay_locked", True))
        self._log_overlay_position = _position(dyno.get("log_overlay_position"), self._log_overlay_position)
        stored_log_units = dyno.get("log_units")
        if isinstance(stored_log_units, dict):
            output = stored_log_units.get("output")
            speed = stored_log_units.get("speed")
            pressure = stored_log_units.get("pressure")
            self._log_units = {
                "output": output if output in DYNO_UNITS else self._units,
                "speed": speed if speed in ("km/h", "mph") else self.settings.get("speed_unit"),
                "pressure": pressure if pressure in ("psi", "bar") else self.settings.get("pressure_unit"),
            }
        else:
            self._log_units = {
                "output": self._units,
                "speed": self.settings.get("speed_unit"),
                "pressure": self.settings.get("pressure_unit"),
            }
        self._boost.load_state(data.get("boost_gauge") or {})
        self._set_dyno_enabled(self._dyno_enabled)  # start or stop every overlay to match
