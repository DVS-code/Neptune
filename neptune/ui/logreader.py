"""Offline Neptune Log Reader / Analyzer window."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from neptune.core.analysis import GOALS, suggestions_for
from neptune.core.logs import boost_map_path, compare_logs, load_log, map_context
from neptune.core.models import NeptuneLog, TuneSuggestion
from neptune.memory import offsets as O
from neptune.ui import theme as T
from neptune.ui.widgets.buttons import Button, PrimaryButton
from neptune.ui.widgets.card import Banner, StatStrip
from neptune.ui.widgets.loggraph import GROUPS, LogGraph


class LogReaderWindow(QDialog):
    edit_preset_requested = Signal(object)
    duplicate_preset_requested = Signal(object)
    apply_suggestion_requested = Signal(object, object, object)
    open_map_requested = Signal(object)
    map_cell_changed = Signal(object)

    def __init__(self, parent=None, assistant_enabled: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Neptune Log Reader / Analyzer")
        self.resize(1100, 760)
        self._log: NeptuneLog | None = None
        self._path = ""
        self._assistant_enabled = bool(assistant_enabled)
        self._value_labels: dict[str, QLabel] = {}
        self._tune_state_provider = None
        self._suggestion_items: list[TuneSuggestion] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 22)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Neptune Log Reader")
        title.setObjectName("StatValue")
        header.addWidget(title)
        header.addStretch(1)
        open_button = PrimaryButton("Open .nlog")
        open_button.clicked.connect(self.open_file)
        header.addWidget(open_button)
        root.addLayout(header)

        identity = QLabel("No log loaded")
        identity.setObjectName("StatValue")
        identity.setWordWrap(True)
        self._identity = identity
        root.addWidget(identity)

        stats = StatStrip()
        for key, label in (("quality", "Run quality"), ("duration", "Duration"), ("power", "Peak power"), ("torque", "Peak torque"), ("boost", "Peak boost"), ("rpm", "Peak RPM")):
            stats.add(key, label, "--")
        self._stats = stats
        root.addWidget(stats)

        content = QHBoxLayout()
        content.setSpacing(14)
        graph_column = QVBoxLayout()
        group = QComboBox()
        group.addItems(list(GROUPS))
        group.currentTextChanged.connect(self._set_group)
        graph_column.addWidget(group)
        self._graph = LogGraph()
        self._graph.cursor_changed.connect(self._set_cursor)
        graph_column.addWidget(self._graph, 1)
        current = QLabel("Current point: --")
        current.setStyleSheet(f"color: {T.TEXT_MUTED};")
        self._current = current
        graph_column.addWidget(current)
        map_context = QLabel("Boost Map path: --")
        map_context.setStyleSheet(f"color: {T.TEXT_MUTED};")
        self._map_context = map_context
        graph_column.addWidget(map_context)
        content.addLayout(graph_column, 1)

        side = QVBoxLayout()
        side.addWidget(QLabel("Tuning Assistant"))
        goal = QComboBox()
        goal.addItems(list(GOALS))
        self._goal = goal
        side.addWidget(goal)
        suggest = Button("Analyze evidence")
        suggest.clicked.connect(self._show_suggestions)
        side.addWidget(suggest)
        suggestions = QLabel("Enable Tuning Assistant in Settings to generate evidence-based proposals.")
        suggestions.setWordWrap(True)
        suggestions.setStyleSheet(f"color: {T.TEXT_MUTED};")
        self._suggestions = suggestions
        side.addWidget(suggestions)
        suggestion_choice = QComboBox()
        suggestion_choice.currentIndexChanged.connect(self._render_suggestion)
        suggestion_choice.setVisible(False)
        self._suggestion_choice = suggestion_choice
        side.addWidget(suggestion_choice)
        preview = Button("Preview proposal")
        preview.setEnabled(False)
        preview.clicked.connect(self._preview_suggestion)
        self._preview = preview
        side.addWidget(preview)
        side.addWidget(QLabel("Events"))
        events = QListWidget()
        events.itemClicked.connect(self._event_clicked)
        self._events = events
        side.addWidget(events, 1)
        edit = Button("Edit Preset")
        edit.clicked.connect(lambda: self.edit_preset_requested.emit(self._log))
        side.addWidget(edit)
        duplicate = PrimaryButton("Duplicate & Edit")
        duplicate.clicked.connect(lambda: self.duplicate_preset_requested.emit(self._log))
        side.addWidget(duplicate)
        open_map = Button("Open Boost Map overlay")
        open_map.clicked.connect(lambda: self.open_map_requested.emit(self._log))
        side.addWidget(open_map)
        content.addLayout(side)
        root.addLayout(content, 1)

        self._status = Banner("Open a saved .nlog file. This window does not require the game to be running.", "info")
        root.addWidget(self._status)

    def set_tune_state_provider(self, provider) -> None:
        self._tune_state_provider = provider

    def set_assistant_enabled(self, enabled: bool) -> None:
        self._assistant_enabled = bool(enabled)
        self._show_suggestions()

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Neptune log", "", "Neptune logs (*.nlog);;All files (*.*)")
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> bool:
        log, message = load_log(path)
        if log is None:
            self._status.set(message, "error")
            return False
        self._path = path
        self.set_log(log)
        return True

    def set_log(self, log: NeptuneLog) -> None:
        self._log = log
        self._graph.set_units(log.metadata.units)
        units = log.metadata.units
        power_unit = units.get("power", "hp")
        torque_unit = units.get("torque", "Nm")
        pressure_unit = units.get("pressure", "psi")
        power_factor = O.HP_TO_KW if power_unit == "kW" else 1.0
        torque_factor = O.NM_TO_LBFT if torque_unit == "lb-ft" else 1.0
        pressure_factor = O.PSI_TO_BAR if pressure_unit == "bar" else 1.0
        car = log.metadata.car.friendly_name or log.metadata.car.media_name or "Unknown car"
        tune = log.metadata.tune_name or "Unsaved tune"
        revision = f" V{log.metadata.tune_revision}" if log.metadata.tune_revision else ""
        self._identity.setText(f"{car}\n{tune}{revision}  ·  {log.metadata.test_type}\n{log.metadata.created_at}")
        self._stats.set("quality", log.analysis.quality, T.OK if log.analysis.quality in ("Excellent", "Good") else T.WARN if log.analysis.quality == "Poor" else T.ERR)
        self._stats.set("duration", f"{log.analysis.duration:.2f}" if log.analysis.duration is not None else "--", unit="s")
        self._stats.set("power", f"{log.analysis.metrics['peak_power'] * power_factor:.0f}" if "peak_power" in log.analysis.metrics else "--", unit=power_unit)
        self._stats.set("torque", f"{log.analysis.metrics['peak_torque'] * torque_factor:.0f}" if "peak_torque" in log.analysis.metrics else "--", unit=torque_unit)
        self._stats.set("boost", f"{log.analysis.metrics['peak_boost'] * pressure_factor:.1f}" if "peak_boost" in log.analysis.metrics else "--", unit=pressure_unit)
        self._stats.set("rpm", f"{log.analysis.metrics['peak_rpm']:.0f}" if "peak_rpm" in log.analysis.metrics else "--", unit="rpm")
        self._graph.set_log(log)
        hits, path = boost_map_path(log.samples, *map_context(log))
        self._map_context.setText(f"Boost Map path: {len(path)} transitions · {len(hits)} cells touched")
        self._events.clear()
        for index, event in enumerate(log.analysis.events):
            item = QListWidgetItem(f"{event.timestamp - log.samples[0].timestamp:.2f}s  {event.label}")
            item.setData(Qt.UserRole, index)
            self._events.addItem(item)
        self._status.set(f"Loaded {os.path.basename(self._path)} · {len(log.samples):,} samples", "ok")
        self._show_suggestions()

    def _set_group(self, group: str) -> None:
        self._graph.set_group(group)

    def _set_cursor(self, index: int) -> None:
        if self._log is None or not self._log.samples:
            return
        sample = self._log.samples[index]
        cell = sample.boost_cell
        cell_text = f" · map cell {tuple(cell)}" if cell is not None else ""
        units = self._log.metadata.units
        speed = sample.speed_ms
        if speed is not None:
            speed = speed * (O.MS_TO_MPH if units.get("speed") == "mph" else O.MS_TO_KPH)
        boost = sample.boost
        if boost is not None and units.get("pressure") == "bar":
            boost *= O.PSI_TO_BAR
        speed_text = f"{speed:.1f}" if speed is not None else "--"
        boost_text = f"{boost:.2f}" if boost is not None else "--"
        gear_text = sample.gear if sample.gear is not None else "--"
        self._current.setText(
            f"Current point: {sample.timestamp - self._log.samples[0].timestamp:.2f}s · "
            f"{speed_text} {units.get('speed', 'km/h')} · gear {gear_text} · "
            f"boost {boost_text} {units.get('pressure', 'psi')}{cell_text}"
        )
        self.map_cell_changed.emit(cell)

    def _event_clicked(self, item: QListWidgetItem) -> None:
        if self._log is None:
            return
        event_index = item.data(Qt.UserRole)
        event = self._log.analysis.events[event_index]
        index = min(range(len(self._log.samples)), key=lambda i: abs(self._log.samples[i].timestamp - event.timestamp))
        self._graph.set_cursor(index)
        self._set_cursor(index)

    def _show_suggestions(self) -> None:
        if self._log is None:
            return
        if not self._assistant_enabled:
            self._suggestions.setText("Enable Tuning Assistant in Settings to generate evidence-based proposals.")
            self._suggestion_choice.setVisible(False)
            self._preview.setEnabled(False)
            return
        tune_state = None
        if self._tune_state_provider is not None:
            try:
                tune_state = self._tune_state_provider(self._log)
            except Exception:
                tune_state = None
        suggestions = suggestions_for(self._log, self._goal.currentText(), tune_state)
        self._suggestion_items = suggestions
        self._suggestion_choice.blockSignals(True)
        self._suggestion_choice.clear()
        self._suggestion_choice.addItems([suggestion.title for suggestion in suggestions[:8]])
        self._suggestion_choice.blockSignals(False)
        self._suggestion_choice.setVisible(bool(suggestions))
        if not suggestions:
            self._suggestions.setText("No evidence-backed suggestion was found for this goal.")
            self._preview.setEnabled(False)
            return
        self._suggestion_choice.setCurrentIndex(0)
        self._render_suggestion(0)

    def _render_suggestion(self, index: int) -> None:
        if not self._suggestion_items or not 0 <= index < len(self._suggestion_items):
            self._preview.setEnabled(False)
            return
        suggestion = self._suggestion_items[index]
        self._suggestions.setText(f"{suggestion.title}: {suggestion.summary}\nEvidence: {suggestion.evidence}")
        self._preview.setEnabled(bool(suggestion.proposal_patches))

    def _preview_suggestion(self) -> None:
        index = self._suggestion_choice.currentIndex()
        if self._log is None or not 0 <= index < len(self._suggestion_items):
            return
        suggestion = self._suggestion_items[index]
        dialog = SuggestionPreviewDialog(suggestion, self)
        if dialog.exec() == QDialog.Accepted:
            self.apply_suggestion_requested.emit(self._log, suggestion, dialog.selected_indices())


class SuggestionPreviewDialog(QDialog):
    """Explicit review gate for evidence-backed changes."""

    def __init__(self, suggestion: TuneSuggestion, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preview tune suggestion")
        self.resize(720, 420)
        self._table = QTableWidget(len(suggestion.changes), 5)
        self._table.setHorizontalHeaderLabels(["Apply", "Module", "Setting", "Current", "Proposed"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        for row, change in enumerate(suggestion.changes):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked)
            self._table.setItem(row, 0, check)
            for column, value in enumerate((change.field, change.setting, change.current, change.proposed), 1):
                self._table.setItem(row, column, QTableWidgetItem(str(value)))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{suggestion.title}\n{suggestion.evidence}"))
        layout.addWidget(self._table)
        buttons = QHBoxLayout()
        apply_selected = PrimaryButton("Apply selected")
        apply_selected.clicked.connect(self.accept)
        buttons.addWidget(apply_selected)
        apply_all = PrimaryButton("Apply all")
        apply_all.clicked.connect(self._apply_all)
        buttons.addWidget(apply_all)
        cancel = Button("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def _apply_all(self) -> None:
        for row in range(self._table.rowCount()):
            self._table.item(row, 0).setCheckState(Qt.Checked)
        self.accept()

    def selected_indices(self) -> list[int]:
        return [
            row for row in range(self._table.rowCount())
            if self._table.item(row, 0).checkState() == Qt.Checked
        ]


class LogCompareWindow(QDialog):
    """Small evidence-only comparison view for two compatible saved runs."""

    def __init__(self, left_path: str, right_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compare Neptune Logs")
        self.resize(700, 520)
        layout = QVBoxLayout(self)
        title = QLabel("Log comparison")
        title.setObjectName("StatValue")
        layout.addWidget(title)
        report = QLabel()
        report.setWordWrap(True)
        layout.addWidget(report)
        left, left_message = load_log(left_path)
        right, right_message = load_log(right_path)
        if left is None or right is None:
            report.setText(left_message or right_message)
            return
        compared = compare_logs(left, right)
        lines = [
            f"A: {left.metadata.car.friendly_name or left.metadata.car.media_name} · {left.metadata.tune_name or 'unsaved'}",
            f"B: {right.metadata.car.friendly_name or right.metadata.car.media_name} · {right.metadata.tune_name or 'unsaved'}",
            f"Compatible: {'yes' if compared['compatible'] else 'no'}",
            f"Quality: {left.analysis.quality} → {right.analysis.quality}",
            "",
        ]
        for key, values in compared.get("metrics", {}).items():
            lines.append(f"{key}: {values['left']:.3f} → {values['right']:.3f}  ({values['difference']:+.3f})")
        if compared.get("reasons"):
            lines.extend(["", *compared["reasons"]])
        lines.extend(["", "Differences are reported as observations; this view does not claim causation."])
        report.setText("\n".join(lines))
