"""Saved Neptune logs and the offline reader entry point."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QLabel, QListWidgetItem, QMessageBox
from qfluentwidgets import ListWidget

from neptune.core import logs as log_store
from neptune.core.module import FeatureModule
from neptune.ui.boostmapworkspace import BoostMapWorkspace
from neptune.ui.logreader import LogCompareWindow, LogReaderWindow
from neptune.ui.widgets.buttons import Button, PrimaryButton
from neptune.ui.widgets.card import Banner

NOTE_EMPTY = "No saved .nlog files yet. Generate a log from the DYNO page while driving."


class LogsModule(FeatureModule):
    name = "logs"
    title = "Logs"
    subtitle = "Open, analyze and compare saved Neptune runs offline."
    icon = "logs.png"
    group = "Tool"
    order = 70
    ticks = False

    def __init__(self, settings, registry=None):
        super().__init__()
        self.settings = settings
        self._registry = registry
        self._widgets: dict = {}
        self._page = None
        self._reader: LogReaderWindow | None = None
        self._compare: LogCompareWindow | None = None
        self._map_workspace: BoostMapWorkspace | None = None

    def _window(self):
        return self._page.window() if self._page is not None else None

    def _tunes(self):
        return self._registry.get("tunes") if self._registry is not None else None

    def _selected_path(self) -> str | None:
        listing = self._widgets.get("list")
        item = listing.currentItem() if listing is not None else None
        return item.data(Qt.UserRole) if item is not None else None

    def open_reader(self, path: str | None = None) -> None:
        if self._reader is None:
            self._reader = LogReaderWindow(assistant_enabled=bool(self.settings.get("tuning_assistant")))
            self._reader.finished.connect(self._reader_closed)
            self._reader.edit_preset_requested.connect(lambda log: self._open_tune_from_log(log, False))
            self._reader.duplicate_preset_requested.connect(lambda log: self._open_tune_from_log(log, True))
            self._reader.apply_suggestion_requested.connect(self._apply_suggestion)
            self._reader.set_tune_state_provider(self._tune_state_for_log)
            self._reader.open_map_requested.connect(self._open_map_overlay)
        else:
            # The Settings toggle may have changed since this window opened.
            self._reader.set_assistant_enabled(bool(self.settings.get("tuning_assistant")))
        self._reader.show()
        self._reader.raise_()
        self._reader.activateWindow()
        if path:
            self._reader.open_path(path)

    def _open_tune_from_log(self, log, duplicate: bool) -> None:
        tunes = self._tunes()
        window = self._window()
        if tunes is None:
            QMessageBox.information(window, "Tune workflow", "The Tunes page is not available.")
            return
        ok, message = tunes.open_log_revision(log, duplicate)
        if window is not None:
            window.show_page("tunes")
        if not ok:
            QMessageBox.information(window, "Tune workflow", message)

    def _tune_state_for_log(self, log) -> dict | None:
        tunes = self._tunes()
        if tunes is None:
            return None
        index, _ = tunes.log_revision(log)
        state = tunes.store.tune(tunes.current_car_key, index).get("state") if index >= 0 else None
        return state if isinstance(state, dict) else None

    def _apply_suggestion(self, log, suggestion, selected_indices) -> None:
        tunes = self._tunes()
        window = self._window()
        if tunes is None:
            QMessageBox.information(window, "Tune workflow", "The Tunes page is not available.")
            return
        ok, message = tunes.apply_suggestion_from_log(log, suggestion, selected_indices)
        QMessageBox.information(window, "Tune workflow", message)
        if ok and window is not None:
            window.show_page("tunes")

    def _open_map_overlay(self, log) -> None:
        if log is None:
            return
        turbo = (self._tune_state_for_log(log) or {}).get("turbo")
        turbo = turbo if isinstance(turbo, dict) else {}
        max_rpm, rows = log_store.map_context(log)
        rows = turbo.get("map_rows", rows) if isinstance(turbo.get("map_rows"), int) else rows
        values = turbo.get("map_points") if isinstance(turbo.get("map_points"), list) else None
        workspace = BoostMapWorkspace(values, rows, max_rpm, self._window(), editable=False)
        # A parented QWidget draws inside its parent; the flag makes it a window of its own.
        workspace.setWindowFlag(Qt.Window, True)
        workspace.setObjectName("Root")  # the app background, not Fusion's grey
        workspace.setAttribute(Qt.WA_DeleteOnClose, True)
        workspace.setWindowTitle(f"Boost Map overlay — {log.metadata.car.friendly_name or 'logged car'}")
        workspace.map.set_log_overlay(*log_store.boost_map_path(log.samples, max_rpm, rows))
        if self._reader is not None:
            self._reader.map_cell_changed.connect(workspace.map.set_log_cursor)
        self._map_workspace = workspace
        workspace.show()

    def _reader_closed(self) -> None:
        self._reader = None

    def _compare_selected(self) -> None:
        listing = self._widgets.get("list")
        selected = listing.selectedItems() if listing is not None else []
        if len(selected) != 2:
            QMessageBox.information(self._window(), "Compare logs", "Select exactly two saved logs.")
            return
        self._compare = LogCompareWindow(
            selected[0].data(Qt.UserRole), selected[1].data(Qt.UserRole), self._window()
        )
        self._compare.show()

    def build_page(self, page) -> None:
        self._page = page
        intro = page.add_card(
            "Neptune Log Reader", "Logs are versioned .nlog files and can be opened without Forza running."
        )
        open_button = PrimaryButton("Open Log Reader")
        open_button.clicked.connect(lambda: self.open_reader(self._selected_path()))
        intro.add(open_button)
        about = QLabel(
            "A loaded log keeps its car identity, tune revision, transmission setup, samples, event "
            "markers and run-quality reasons together."
        )
        about.setObjectName("RowHint")
        about.setWordWrap(True)
        intro.add(about)

        card = page.add_card("Saved runs")
        listing = ListWidget()
        listing.setMinimumHeight(250)
        listing.setSelectionMode(QAbstractItemView.ExtendedSelection)
        listing.itemDoubleClicked.connect(lambda _item: self.open_reader(self._selected_path()))
        self._widgets["list"] = listing
        card.add(listing)
        refresh = Button("Refresh list")
        refresh.clicked.connect(self._refresh)
        card.add(refresh)
        compare = Button("Compare selected logs")
        compare.clicked.connect(self._compare_selected)
        card.add(compare)

        status = Banner(NOTE_EMPTY, "info")
        self._widgets["status"] = status
        page.add(status)
        self._refresh()

    def _refresh(self) -> None:
        listing = self._widgets.get("list")
        if listing is None:
            return
        listing.clear()
        files = log_store.list_logs()
        for path in files:
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.UserRole, path)
            listing.addItem(item)
        count = len(files)
        self._widgets["status"].set(
            f"{count} saved run{'s' if count != 1 else ''}. Double-click one to analyze it offline."
            if files
            else NOTE_EMPTY,
            "ok" if files else "info",
        )
