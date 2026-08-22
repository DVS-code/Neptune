"""Car: what car you are currently in.

Reads the car's identity out of the customization record on the entity — see
docs/CAR_CONFIG_RECORD.md for the layout. Read-only: nothing on this page writes to the
game, so it has no restore path and cannot leave anything behind.
"""
from __future__ import annotations

from neptune.core import carnames
from neptune.core.module import FeatureModule
from neptune.memory import offsets as O
from neptune.ui.widgets.card import Banner, StatStrip

NOTE_OFFLINE = 'Attach to the game and get in a car to see it here.'


class CarModule(FeatureModule):
    name = 'car'
    title = 'Car'
    subtitle = 'What car you are in.'
    icon = '▤'
    group = 'Vehicle'
    order = 45

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._widgets: dict = {}

    def on_attach(self, vehicle) -> None:
        self.vehicle = vehicle

    def on_car_changed(self, vehicle) -> None:
        self.vehicle = vehicle

    def on_detach(self) -> None:
        self.vehicle = None

    def build_page(self, page) -> None:
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
            return

        if banner is not None:
            banner.setVisible(False)

        if strip is None:
            return

        record = vehicle.car_config
        car_id = vehicle.process.i32(record + O.CarConfig.CAR_ID) if record else None
        strip.set('name', carnames.label(vehicle.media_name, '--'))
        strip.set('id', str(car_id) if car_id else '--')

        turbo = vehicle.turbo_block()
        strip.set('engine', O.aspiration_label(
            vehicle.aspiration, turbo.get('max_boost'), turbo.get('turbine_limit'),
            vehicle.boost_raw_blower, vehicle.blower_ceiling))
