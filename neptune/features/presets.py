"""Presets: save and reload whole setups."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout
from qfluentwidgets import LineEdit, ListWidget

from neptune.core import presets as store
from neptune.core.module import FeatureModule
from neptune.ui.widgets.buttons import DangerButton, PrimaryButton
from neptune.ui.widgets.card import Banner

CAPTION = ('Presets cover suspension, the boost gauge and Dragy. '
           'Engine and turbo setups are saved per car in the Tunes tab.')


class PresetsModule(FeatureModule):
    name = 'presets'
    title = 'Presets'
    subtitle = 'Save and reload whole setups.'
    icon = '▣'
    group = 'Tool'
    order = 80
    ticks = False

    def __init__(self, registry, settings):
        super().__init__()
        self.registry = registry
        self.settings = settings
        self._widgets: dict = {}

    def build_page(self, page) -> None:
        save_card = page.add_card('Save current setup', CAPTION)
        row = QHBoxLayout()
        row.setSpacing(8)

        name = LineEdit()
        name.setPlaceholderText('Preset name')
        name.returnPressed.connect(self._save)
        self._widgets['name'] = name
        row.addWidget(name, 1)

        save_button = PrimaryButton('Save preset')
        save_button.clicked.connect(self._save)
        row.addWidget(save_button)
        save_card.add_layout(row)

        list_card = page.add_card('Saved presets')
        listing = ListWidget()
        listing.setMinimumHeight(200)
        listing.itemDoubleClicked.connect(lambda _item: self._load())
        self._widgets['list'] = listing
        list_card.add(listing)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        load_button = PrimaryButton('Load')
        load_button.clicked.connect(self._load)
        actions.addWidget(load_button)

        delete_button = DangerButton('Delete')
        delete_button.clicked.connect(self._delete)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        list_card.add_layout(actions)

        status = Banner('', 'info')
        status.setVisible(False)
        self._widgets['status'] = status
        page.add(status)

        self._refresh()

    def _say(self, message: str, ok: bool = True) -> None:
        status = self._widgets.get('status')
        if status is not None:
            status.set(message, 'ok' if ok else 'error')

    def _refresh(self) -> None:
        listing = self._widgets.get('list')
        if listing is None:
            return
        listing.clear()
        for name in store.list_presets():
            listing.addItem(name)

    def _selected(self) -> str | None:
        listing = self._widgets.get('list')
        if listing is None:
            return None
        item = listing.currentItem()
        return item.text() if item is not None else None

    def _save(self) -> None:
        name = self._widgets.get('name')
        if name is None:
            return
        ok, message = store.save_preset(name.text(), self.registry)
        self._say(message, ok)
        if ok:
            name.clear()
            self._refresh()

    def _load(self) -> None:
        selected = self._selected()
        if not selected:
            self._say('Select a preset first.', False)
            return
        ok, message = store.load_preset(selected, self.registry)
        self._say(message, ok)

    def _delete(self) -> None:
        selected = self._selected()
        if not selected:
            self._say('Select a preset first.', False)
            return
        ok, message = store.delete_preset(selected)
        self._say(message, ok)
        self._refresh()
