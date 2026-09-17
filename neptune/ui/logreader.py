"""Offline Neptune Log Reader / Analyzer window."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidgetItem,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import ComboBox, ListWidget, StrongBodyLabel, TableWidget

from neptune.core.analysis import GOALS, suggestions_for
from neptune.core.logs import boost_map_path, compare_logs, load_log, map_context
from neptune.core.models import NeptuneLog, TuneSuggestion
from neptune.memory import offsets as O
from neptune.ui import theme as T
from neptune.ui.widgets.buttons import Button, PrimaryButton
from neptune.ui.widgets.card import Banner, Card, StatStrip
from neptune.ui.widgets.controls import SectionHeading, Segmented
from neptune.ui.widgets.loggraph import GROUPS, LogGraph

SIDE_WIDTH = 320
CARD_MARGIN = 18  # Card's left and right content margin
HINT_ASSISTANT = "Suggestions cite the logged evidence. Nothing is applied until you preview and approve it."
HINT_EVENTS = "Click an event to move the graph cursor to it."
NOTE_ASSISTANT_OFF = "Enable Tuning Assistant in Settings to generate evidence-based proposals."
DEFAULT_UNITS = {"power": "hp", "torque": "Nm", "pressure": "psi"}
METRICS = {  # key: (label, unit or unit kind, conversion)
    "duration": ("Duration", "s", None),
    "peak_power": ("Peak power", "power", "power"),
    "peak_torque": ("Peak torque", "torque", "torque"),
    "peak_boost": ("Peak boost", "pressure", "pressure"),
    "peak_rpm": ("Peak RPM", "rpm", None),
}


def _hint(text: str = "") -> QLabel:
    """Muted wrapped text, styled like the row hints on every page."""
    label = QLabel(text)
    label.setObjectName("RowHint")
    label.setWordWrap(True)
    return label


def _table(rows: int, headers: list[str]) -> TableWidget:
    table = TableWidget()
    table.setRowCount(rows)
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setBorderVisible(True)
    table.setBorderRadius(T.CONTROL_RADIUS)
    table.setWordWrap(False)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    table.setEditTriggers(TableWidget.NoEditTriggers)
    table.setSelectionMode(TableWidget.NoSelection)
    return table


def _factors(units: dict) -> dict[str, float]:
    """Stored metrics are hp, Nm and psi; these convert them to the units the run was logged in."""
    return {
        "power": O.HP_TO_KW if units.get("power") == "kW" else 1.0,
        "torque": O.NM_TO_LBFT if units.get("torque") == "lb-ft" else 1.0,
        "pressure": O.PSI_TO_BAR if units.get("pressure") == "bar" else 1.0,
    }


class LogReaderWindow(QDialog):
    edit_preset_requested = Signal(object)
    duplicate_preset_requested = Signal(object)
    apply_suggestion_requested = Signal(object, object, object)
    open_map_requested = Signal(object)
    map_cell_changed = Signal(object)

    def __init__(self, parent=None, assistant_enabled: bool = False):
        super().__init__(parent)
        self.setObjectName("Root")  # the app background from theme.stylesheet(), not Fusion's grey
        self.setWindowTitle("Neptune Log Reader / Analyzer")
        self.resize(1180, 800)
        self._log: NeptuneLog | None = None
        self._path = ""
        self._assistant_enabled = bool(assistant_enabled)
        self._tune_state_provider = None
        self._suggestion_items: list[TuneSuggestion] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(T.PAGE_PADDING, 22, T.PAGE_PADDING, 22)
        root.setSpacing(T.CARD_GAP)

        header = QHBoxLayout()
        header.addWidget(SectionHeading("Neptune Log Reader", "Analyze a saved run offline."), 1)
        open_button = PrimaryButton("Open .nlog")
        open_button.clicked.connect(self.open_file)
        header.addWidget(open_button, 0, Qt.AlignTop)
        root.addLayout(header)

        run_card = Card()
        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        identity_column = QVBoxLayout()
        identity_column.setSpacing(4)
        identity = StrongBodyLabel("No log loaded")
        self._identity = identity
        identity_column.addWidget(identity)
        details = _hint()
        details.setVisible(False)
        self._details = details
        identity_column.addWidget(details)
        run_row.addLayout(identity_column, 1)
        edit = Button("Edit Preset")
        edit.clicked.connect(lambda: self.edit_preset_requested.emit(self._log))
        run_row.addWidget(edit, 0, Qt.AlignTop)
        duplicate = PrimaryButton("Duplicate && Edit")  # a single & is a keyboard mnemonic
        duplicate.clicked.connect(lambda: self.duplicate_preset_requested.emit(self._log))
        run_row.addWidget(duplicate, 0, Qt.AlignTop)
        open_map = Button("Open Boost Map overlay")
        open_map.clicked.connect(lambda: self.open_map_requested.emit(self._log))
        run_row.addWidget(open_map, 0, Qt.AlignTop)
        run_card.add_layout(run_row)
        stats = StatStrip()
        for key, label in (("quality", "Run quality"), ("duration", "Duration"), ("power", "Peak power"), ("torque", "Peak torque"), ("boost", "Peak boost"), ("rpm", "Peak RPM")):
            stats.add(key, label, "--")
        self._stats = stats
        run_card.add(stats)
        root.addWidget(run_card)

        content = QHBoxLayout()
        content.setSpacing(T.CARD_GAP)
        graph_card = Card()
        graph_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        group_row = QHBoxLayout()
        group = Segmented(list(GROUPS), next(iter(GROUPS)))
        group.changed.connect(self._set_group)
        group_row.addWidget(group)
        group_row.addStretch(1)
        graph_card.add_layout(group_row)
        self._graph = LogGraph()
        self._graph.cursor_changed.connect(self._set_cursor)
        graph_card.body.addWidget(self._graph, 1)
        current = _hint("Current point: --")
        self._current = current
        graph_card.add(current)
        path_label = _hint("Boost Map path: --")
        self._map_context = path_label
        graph_card.add(path_label)
        content.addWidget(graph_card, 1)

        side = QWidget()
        side.setFixedWidth(SIDE_WIDTH)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(T.CARD_GAP)

        assistant = Card("Tuning Assistant", HINT_ASSISTANT)
        goal = ComboBox()
        for name in GOALS:
            goal.addItem(name or "Any goal", userData=name)
        self._goal = goal
        assistant.add(goal)
        suggest = Button("Analyze evidence")
        suggest.clicked.connect(self._show_suggestions)
        assistant.add(suggest)
        suggestions = _hint(NOTE_ASSISTANT_OFF)
        self._suggestions = suggestions
        assistant.add(suggestions)
        suggestion_choice = ComboBox()
        suggestion_choice.currentIndexChanged.connect(self._render_suggestion)
        suggestion_choice.setVisible(False)
        self._suggestion_choice = suggestion_choice
        assistant.add(suggestion_choice)
        preview = Button("Preview proposal")
        preview.setEnabled(False)
        preview.clicked.connect(self._preview_suggestion)
        self._preview = preview
        assistant.add(preview)
        side_layout.addWidget(assistant)

        events_card = Card("Events", HINT_EVENTS)
        events_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        events = ListWidget()
        events.setMinimumHeight(120)
        events.itemClicked.connect(self._event_clicked)
        self._events = events
        events_card.body.addWidget(events, 1)
        side_layout.addWidget(events_card, 1)
        content.addWidget(side)
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
        factors = _factors(units)
        car = log.metadata.car.friendly_name or log.metadata.car.media_name or "Unknown car"
        tune = log.metadata.tune_name or "Unsaved tune"
        revision = f" V{log.metadata.tune_revision}" if log.metadata.tune_revision else ""
        self._identity.setText(car)
        self._details.setText(f"{tune}{revision}  ·  {log.metadata.test_type}  ·  {log.metadata.created_at}")
        self._details.setVisible(True)
        self._stats.set("quality", log.analysis.quality, T.OK if log.analysis.quality in ("Excellent", "Good") else T.WARN if log.analysis.quality == "Poor" else T.ERR)
        self._stats.set("duration", f"{log.analysis.duration:.2f}" if log.analysis.duration is not None else "--", unit="s")
        self._stats.set("power", f"{log.analysis.metrics['peak_power'] * factors['power']:.0f}" if "peak_power" in log.analysis.metrics else "--", unit=power_unit)
        self._stats.set("torque", f"{log.analysis.metrics['peak_torque'] * factors['torque']:.0f}" if "peak_torque" in log.analysis.metrics else "--", unit=torque_unit)
        self._stats.set("boost", f"{log.analysis.metrics['peak_boost'] * factors['pressure']:.1f}" if "peak_boost" in log.analysis.metrics else "--", unit=pressure_unit)
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
            self._set_suggestion_text(NOTE_ASSISTANT_OFF)
            self._suggestion_choice.setVisible(False)
            self._preview.setEnabled(False)
            return
        tune_state = None
        if self._tune_state_provider is not None:
            try:
                tune_state = self._tune_state_provider(self._log)
            except Exception:
                tune_state = None
        suggestions = suggestions_for(self._log, self._goal.currentData() or "", tune_state)
        self._suggestion_items = suggestions
        self._suggestion_choice.blockSignals(True)
        self._suggestion_choice.clear()
        self._suggestion_choice.addItems([suggestion.title for suggestion in suggestions[:8]])
        self._suggestion_choice.blockSignals(False)
        self._suggestion_choice.setVisible(bool(suggestions))
        if not suggestions:
            self._set_suggestion_text("No evidence-backed suggestion was found for this goal.")
            self._preview.setEnabled(False)
            return
        self._suggestion_choice.setCurrentIndex(0)
        self._render_suggestion(0)

    def _set_suggestion_text(self, text: str) -> None:
        self._suggestions.setText(text)
        # A wrapped label does not reserve its wrapped height in a crowded column, so its lines got
        # squeezed under the buttons: pin that height for the side column's fixed text width.
        self._suggestions.setMinimumHeight(self._suggestions.heightForWidth(SIDE_WIDTH - 2 * CARD_MARGIN))

    def _render_suggestion(self, index: int) -> None:
        if not self._suggestion_items or not 0 <= index < len(self._suggestion_items):
            self._preview.setEnabled(False)
            return
        suggestion = self._suggestion_items[index]
        self._set_suggestion_text(f"{suggestion.title}: {suggestion.summary}\nEvidence: {suggestion.evidence}")
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
        self.setObjectName("Root")
        self.setWindowTitle("Preview tune suggestion")
        self.resize(720, 420)
        self._table = _table(len(suggestion.changes), ["Apply", "Module", "Setting", "Current", "Proposed"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for row, change in enumerate(suggestion.changes):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked)
            self._table.setItem(row, 0, check)
            for column, value in enumerate((change.field, change.setting, change.current, change.proposed), 1):
                self._table.setItem(row, column, QTableWidgetItem(str(value)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        layout.addWidget(StrongBodyLabel(suggestion.title))
        layout.addWidget(_hint(suggestion.evidence))
        layout.addWidget(self._table, 1)
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
        self.setObjectName("Root")
        self.setWindowTitle("Compare Neptune Logs")
        self.resize(700, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(T.PAGE_PADDING, 22, T.PAGE_PADDING, 22)
        layout.setSpacing(T.CARD_GAP)
        layout.addWidget(
            SectionHeading("Log comparison", "Differences are reported as observations; this view does not claim causation.")
        )
        left, left_message = load_log(left_path)
        right, right_message = load_log(right_path)
        if left is None or right is None:
            layout.addWidget(Banner(left_message or right_message, "error"))
            layout.addStretch(1)
            return
        compared = compare_logs(left, right)

        runs = Card()
        for tag, log in (("A", left), ("B", right)):
            car = log.metadata.car.friendly_name or log.metadata.car.media_name or "Unknown car"
            runs.add(StrongBodyLabel(f"{tag}: {car}"))
            runs.add(_hint(f"{log.metadata.tune_name or 'Unsaved tune'}  ·  {log.metadata.test_type}  ·  quality {log.analysis.quality}"))
        layout.addWidget(runs)
        if compared["compatible"]:
            layout.addWidget(Banner("These runs are comparable: same test, car and speed range.", "ok"))
        else:
            layout.addWidget(Banner(" ".join(compared["reasons"]), "warn"))

        # Values in A's units; both logs store hp, Nm and psi, so one conversion fits both.
        units = left.metadata.units
        factors = _factors(units)
        metrics = compared.get("metrics", {})
        table = _table(len(metrics), ["Metric", "A", "B", "Difference"])
        for row, (key, values) in enumerate(metrics.items()):
            label, unit, factor_key = METRICS.get(key, (key.replace("_", " ").capitalize(), "", None))
            unit = units.get(unit, DEFAULT_UNITS[unit]) if unit in DEFAULT_UNITS else unit
            factor = factors[factor_key] if factor_key else 1.0
            decimals = 2 if key in ("duration", "peak_boost") else 0
            cells = (
                f"{label} ({unit})" if unit else label,
                f"{values['left'] * factor:.{decimals}f}",
                f"{values['right'] * factor:.{decimals}f}",
                f"{values['difference'] * factor:+.{decimals}f}",
            )
            for column, text in enumerate(cells):
                table.setItem(row, column, QTableWidgetItem(text))
        layout.addWidget(table, 1)
