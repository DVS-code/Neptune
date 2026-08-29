"""Car: what car you are currently in.

Reads the car's identity out of the customization record on the entity — see
docs/CAR_CONFIG_RECORD.md for the layout. Read-only: nothing on this page writes to the
game, so it has no restore path and cannot leave anything behind.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel

from neptune.core import carnames
from neptune.core.module import FeatureModule
from neptune.memory import offsets as O
from neptune.ui.widgets.card import Banner, StatStrip
from neptune.vehicle import thumbnail

NOTE_OFFLINE = 'Attach to the game and get in a car to see it here.'
PREVIEW_WIDTH = 320


class _PreviewSignal(QObject):
    """Marshals a background thumbnail lookup back onto the GUI thread.
    """
    ready = Signal(object, object)


class CarModule(FeatureModule):
    name = 'car'
    title = 'Car'
    subtitle = 'What car you are in.'
    icon = 'car.png'
    group = 'Vehicle'
    order = 45

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._widgets: dict = {}
        self._last_car_id: int | None = None
        self._preview_token: int | None = None
        self._preview_signal = _PreviewSignal()
        self._preview_signal.ready.connect(self._apply_preview)

    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle

    def on_car_changed(self, vehicle) -> None:
        self.vehicle = vehicle
        self._last_car_id = None

    def on_detach(self) -> None:
        self.vehicle = None
        self._last_car_id = None

    def build_page(self, page) -> None:
        preview_card = page.add_card('Preview')
        preview_row = QHBoxLayout()
        preview_row.addStretch(1)
        preview = QLabel()
        preview.setAlignment(Qt.AlignCenter)
        preview.setFixedHeight(180)
        self._widgets['preview'] = preview
        preview_row.addWidget(preview)
        preview_row.addStretch(1)
        preview_card.add_layout(preview_row)
        self._widgets['preview_card'] = preview_card

        identity = page.add_card('This car')
        strip = StatStrip()
        strip.add('name', 'Name')
        strip.add('id', 'Car ID')
        strip.add('engine', 'Induction')
        self._widgets['identity'] = strip
        identity.add(strip)

        banner = Banner(NOTE_OFFLINE, 'info')
        self._widgets['banner'] = banner
        page.add(banner)

    def refresh(self, vehicle) -> None:
        strip = self._widgets.get('identity')
        banner = self._widgets.get('banner')

        if vehicle is None:
            if strip is not None:
                strip.reset()
            if banner is not None:
                banner.set(NOTE_OFFLINE, 'info')
            self._set_preview(None, None)
            return

        if banner is not None:
            banner.setVisible(False)

        if strip is None:
            return

        record = vehicle.car_config
        car_id = vehicle.process.i32(record + O.CarConfig.CAR_ID) if record else None
        strip.set('name', carnames.label(vehicle.media_name, car_id, '--'))
        strip.set('id', str(car_id) if car_id else '--')

        turbo = vehicle.turbo_block()
        strip.set('engine', O.aspiration_label(
            vehicle.aspiration, turbo.get('max_boost'), turbo.get('turbine_limit'),
            vehicle.boost_raw_blower, vehicle.blower_ceiling))

        if car_id != self._last_car_id:
            self._last_car_id = car_id
            self._set_preview(vehicle.process, car_id)

    def _set_preview(self, process, car_id: int | None) -> None:
        label = self._widgets.get('preview')
        card = self._widgets.get('preview_card')
        if label is None or card is None:
            return

        self._preview_token = car_id
        if car_id is None or process is None:
            self._apply_preview(car_id, None)
            return
        thumbnail.find_async(car_id, process, self._on_preview_found)

    def _on_preview_found(self, car_id, data) -> None:
        """Runs on a worker thread (see thumbnail.find_async) — hop to the GUI thread."""
        self._preview_signal.ready.emit(car_id, data)

    def _apply_preview(self, car_id, data: bytes | None) -> None:
        if car_id != self._preview_token:
            return  # superseded by a later car change; drop this stale result

        label = self._widgets.get('preview')
        card = self._widgets.get('preview_card')
        if label is None or card is None:
            return

        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            card.setVisible(False)
            label.clear()
            return

        card.setVisible(True)
        scaled = pixmap.scaledToWidth(PREVIEW_WIDTH, Qt.SmoothTransformation)
        label.setPixmap(scaled)
        label.setFixedWidth(scaled.width())
