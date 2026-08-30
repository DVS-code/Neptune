"""Tunes: saved engine and turbo setups for the car you are driving."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidgetItem
from qfluentwidgets import LineEdit, ListWidget

from neptune.core import carnames
from neptune.core import input as inp
from neptune.core import maps as store
from neptune.core.module import FeatureModule
from neptune.ui.widgets.buttons import Button, DangerButton, PrimaryButton
from neptune.ui.widgets.card import Banner, FieldRow
from neptune.ui.widgets.controls import BindButton

ACTIVE_MARK = "●"
INACTIVE_MARK = "○"

HINT_CYCLE = "Switches to your next saved tune for this car without leaving the game."
NOTE_NO_CAR = "Drive out into the world and Neptune will pick up your car."


class TunesModule(FeatureModule):
    name = "tunes"
    title = "Tunes"
    subtitle = "Save engine and turbo setups per car and switch between them."
    icon = "tunes.png"
    group = "Vehicle"
    order = 50

    def __init__(self, registry, settings):
        super().__init__()
        self.registry = registry
        self.settings = settings
        self.store = store.TuneStore()

        self._key: str | None = None
        self._edge = inp.EdgeDetector()
        self._message = ""
        self._message_ok = True
        self._dirty = True
        self._widgets: dict = {}

    def binding(self) -> dict | None:
        return self.settings.binding("tunes.cycle")

    def bindings(self) -> list[dict]:
        return [
            {
                "key": "tunes.cycle",
                "label": "Switch to next tune",
                "description": "Cycles through the tunes saved for the car you are in.",
            }
        ]

    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle
        self._key = store.car_key(vehicle.fingerprint()) if vehicle else None
        self._adopt_name(vehicle)
        self._dirty = True

    def on_car_changed(self, vehicle) -> None:
        self.vehicle = vehicle
        self._key = store.car_key(vehicle.fingerprint()) if vehicle else None
        self._adopt_name(vehicle)
        self._dirty = True

    def _adopt_name(self, vehicle) -> None:
        """Name the car from the game's own record, so nobody has to type one.

        Only fills a blank: a name the user chose themselves is never overwritten.
        """
        if vehicle is None or not self._key or self.store.car_name(self._key):
            return
        name = carnames.pretty(vehicle.media_name)
        if name:
            self.store.set_car_name(self._key, name)

    def on_car_reloaded(self, vehicle) -> None:
        self.vehicle = vehicle
        if not self._key:
            return
        active = self.store.active_index(self._key)
        if active >= 0:
            self._apply(active, announce=False)

    def on_detach(self) -> None:
        self.vehicle = None
        self._key = None
        self._dirty = True

    def tick(self, _vehicle) -> None:
        if self._edge.pressed(self.binding()):
            self._cycle()

    def _capture(self) -> dict:
        state = {}
        for name in store.TUNED_MODULES:
            module = self.registry.get(name)
            if module is None:
                continue
            try:
                captured = module.save_state()
            except Exception:
                continue
            if captured:
                state[name] = captured
        return state

    def _apply(self, index: int, announce: bool = True) -> bool:
        if not self._key:
            return False
        tunes = self.store.tunes_for(self._key)
        if not 0 <= index < len(tunes):
            return False

        entry = tunes[index]
        for name, state in (entry.get("state") or {}).items():
            if name not in store.TUNED_MODULES:
                continue
            module = self.registry.get(name)
            if module is None:
                continue
            try:
                module.load_state(state)
            except Exception:
                self._notify(f'Could not apply "{entry.get("name", "")}".', False)
                return False

        self.store.set_active(self._key, index)
        if announce:
            self._notify(f'Applied "{entry.get("name", "")}".', True)
        self._dirty = True
        return True

    def _cycle(self) -> None:
        if not self._key:
            self._notify("No car detected.", False)
            return
        index = self.store.next_index(self._key)
        if index < 0:
            self._notify("No tunes saved for this car yet.", False)
            return
        self._apply(index)

    def _notify(self, message: str, ok: bool = True) -> None:
        self._message = message
        self._message_ok = ok
        self._dirty = True

    def _selected_index(self) -> int:
        listing = self._widgets.get("list")
        if listing is None:
            return -1
        item = listing.currentItem()
        return listing.row(item) if item is not None else -1

    def _on_name_car(self) -> None:
        field = self._widgets.get("car_name")
        if field is None or not self._key:
            self._notify("No car detected.", False)
            return
        ok, message = self.store.set_car_name(self._key, field.text())
        if ok:
            field.clear()
        self._notify(message, ok)

    def _on_save(self) -> None:
        field = self._widgets.get("tune_name")
        if field is None:
            return
        if not self._key:
            self._notify("No car detected.", False)
            return
        ok, message = self.store.add_tune(self._key, field.text(), self._capture())
        if ok:
            field.clear()
        self._notify(message, ok)

    def _on_apply(self) -> None:
        index = self._selected_index()
        if index < 0:
            self._notify("Select a tune first.", False)
            return
        self._apply(index)

    def _on_overwrite(self) -> None:
        index = self._selected_index()
        if index < 0:
            self._notify("Select a tune first.", False)
            return
        ok, message = self.store.update_tune(self._key, index, self._capture())
        self._notify(message, ok)

    def _on_delete(self) -> None:
        index = self._selected_index()
        if index < 0:
            self._notify("Select a tune first.", False)
            return
        ok, message = self.store.delete_tune(self._key, index)
        self._notify(message, ok)

    def build_page(self, page) -> None:
        car_card = page.add_card(
            "This car", "Named from the game. Rename it if you want your own label."
        )

        car_label = QLabel("No car detected")
        car_label.setObjectName("StatValue")
        self._widgets["car_label"] = car_label
        car_card.add(car_label)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        car_name = LineEdit()
        car_name.setPlaceholderText("Rename this car")
        car_name.returnPressed.connect(self._on_name_car)
        self._widgets["car_name"] = car_name
        name_row.addWidget(car_name, 1)

        name_button = Button("Save name")
        name_button.clicked.connect(self._on_name_car)
        name_row.addWidget(name_button)
        car_card.add_layout(name_row)

        save_card = page.add_card(
            "Save current setup",
            "Captures both tabs in full: torque, rev limit, launch and speed cap from "
            "Engine, and every boost setting from Turbo — including boost by gear and "
            "the boost map.",
        )
        save_row = QHBoxLayout()
        save_row.setSpacing(8)

        tune_name = LineEdit()
        tune_name.setPlaceholderText("Tune name, for example Street or Drag")
        tune_name.returnPressed.connect(self._on_save)
        self._widgets["tune_name"] = tune_name
        save_row.addWidget(tune_name, 1)

        save_button = PrimaryButton("Save tune")
        save_button.clicked.connect(self._on_save)
        save_row.addWidget(save_button)
        save_card.add_layout(save_row)

        list_card = page.add_card("Saved tunes")
        listing = ListWidget()
        listing.setMinimumHeight(180)
        listing.itemDoubleClicked.connect(lambda _item: self._on_apply())
        self._widgets["list"] = listing
        list_card.add(listing)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        apply_button = PrimaryButton("Apply")
        apply_button.clicked.connect(self._on_apply)
        actions.addWidget(apply_button)

        overwrite_button = Button("Overwrite")
        overwrite_button.clicked.connect(self._on_overwrite)
        actions.addWidget(overwrite_button)

        delete_button = DangerButton("Delete")
        delete_button.clicked.connect(self._on_delete)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        list_card.add_layout(actions)

        cycle_card = page.add_card("Switch while driving")
        bind_button = BindButton(self.binding(), settings=self.settings, key="tunes.cycle")
        bind_button.bound.connect(lambda binding: self.settings.set_binding("tunes.cycle", binding))
        self._widgets["bind"] = bind_button
        cycle_card.add(FieldRow("Control", bind_button, hint=HINT_CYCLE))

        banner = Banner(NOTE_NO_CAR, "info")
        self._widgets["banner"] = banner
        page.add(banner)

    def refresh(self, _vehicle) -> None:
        if not self._dirty:
            return
        self._dirty = False

        car_label = self._widgets.get("car_label")
        listing = self._widgets.get("list")
        banner = self._widgets.get("banner")
        if car_label is None or listing is None:
            return

        if not self._key:
            car_label.setText("No car detected")
            listing.clear()
            if banner is not None:
                banner.set(
                    self._message or NOTE_NO_CAR,
                    "error" if self._message and not self._message_ok else "info",
                )
            return

        name = self.store.car_name(self._key)
        if not name and self.vehicle is not None:
            name = carnames.pretty(self.vehicle.media_name)
        car_label.setText(name or "Unnamed car")

        current = listing.currentRow()
        listing.clear()
        active = self.store.active_index(self._key)
        for index, tune in enumerate(self.store.tunes_for(self._key)):
            mark = ACTIVE_MARK if index == active else INACTIVE_MARK
            item = QListWidgetItem(f"{mark}   {tune.get('name', '')}")
            listing.addItem(item)
        if 0 <= current < listing.count():
            listing.setCurrentRow(current)

        if banner is not None:
            if self._message:
                banner.set(self._message, "ok" if self._message_ok else "error")
            elif listing.count() == 0:
                banner.set("Set up the Engine and Turbo tabs, then save that as a tune.", "info")
            else:
                banner.setVisible(False)
