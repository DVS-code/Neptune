"""Settings: units, display and startup behaviour."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton

from neptune import __version__
from neptune.core import paths
from neptune.core.module import FeatureModule
from neptune.core.settings import HEIGHT_UNITS, PRESSURE_UNITS, SPEED_UNITS
from neptune.memory import offsets as O
from neptune.ui.widgets.card import FieldRow, ToggleRow
from neptune.ui.widgets.controls import Segmented
from neptune.ui.widgets.sliderrow import SliderRow

HINT_ATMOSPHERIC = ('Where the boost gauge reads zero. Lower this for high altitude, '
                    'or set it to zero to show absolute pressure.')
HINT_AUTO_ATTACH = 'Connect to Forza Horizon 6 automatically when Neptune starts.'
HINT_RESTORE = 'Put every change back to stock when Neptune closes.'


class SettingsModule(FeatureModule):
    name = 'settings'
    title = 'Settings'
    subtitle = 'Units, display and startup behaviour.'
    icon = '○'
    group = 'Tool'
    order = 90
    ticks = False

    def __init__(self, registry, settings):
        super().__init__()
        self.registry = registry
        self.settings = settings
        self._widgets: dict = {}

    def build_page(self, page) -> None:
        units_card = page.add_card('Units', 'How Neptune shows numbers.')

        speed = Segmented(list(SPEED_UNITS), self.settings.get('speed_unit'))
        speed.changed.connect(lambda value: self.settings.set('speed_unit', value))
        units_card.add(FieldRow('Speed', speed))

        pressure = Segmented(list(PRESSURE_UNITS), self.settings.get('pressure_unit'))
        pressure.changed.connect(lambda value: self.settings.set('pressure_unit', value))
        units_card.add(FieldRow('Boost', pressure))

        height = Segmented(list(HEIGHT_UNITS), self.settings.get('height_unit'))
        height.changed.connect(lambda value: self.settings.set('height_unit', value))
        units_card.add(FieldRow('Ride height', height))

        gauge_card = page.add_card('Boost gauge')
        atmospheric = SliderRow(
            'Gauge zero', O.ATMOSPHERIC_PSI_MIN, O.ATMOSPHERIC_PSI_MAX,
            self.settings.get('atmospheric_psi'), step=0.1, decimals=1,
            unit='psi', hint=HINT_ATMOSPHERIC)
        atmospheric.changed.connect(
            lambda value: self.settings.set('atmospheric_psi', float(value)))
        self._widgets['atmospheric'] = atmospheric
        gauge_card.add(atmospheric)

        startup_card = page.add_card('Startup')

        auto_attach = ToggleRow('Attach automatically',
                                bool(self.settings.get('auto_attach')),
                                hint=HINT_AUTO_ATTACH)
        auto_attach.toggle.toggled_value.connect(
            lambda value: self.settings.set('auto_attach', bool(value)))
        startup_card.add(auto_attach)

        restore = ToggleRow('Restore on exit',
                            bool(self.settings.get('restore_on_exit')),
                            hint=HINT_RESTORE)
        restore.toggle.toggled_value.connect(
            lambda value: self.settings.set('restore_on_exit', bool(value)))
        startup_card.add(restore)

        data_card = page.add_card('Data', 'Where Neptune keeps your tunes and presets.')
        location = QLabel(paths.data_dir())
        location.setObjectName('RowHint')
        location.setWordWrap(True)
        location.setTextInteractionFlags(Qt.TextSelectableByMouse)
        data_card.add(location)

        open_button = QPushButton('Open this folder')
        open_button.setCursor(Qt.PointingHandCursor)
        open_button.clicked.connect(self._open_data_folder)
        data_card.add(open_button)

        about_card = page.add_card('About')
        about = QLabel(
            f'Neptune v{__version__} for Forza Horizon 6 · '
            f'built for game version {O.GAME_BUILD}\n\n'
            'Free software under the GNU General Public License v3.0, with no '
            'warranty of any kind. The source code is available at '
            'github.com/DVS-code/Neptune\n\n'
            'Run as administrator so Neptune can talk to the game.')
        about.setObjectName('RowHint')
        about.setWordWrap(True)
        about.setTextInteractionFlags(Qt.TextSelectableByMouse)
        about_card.add(about)

    def _open_data_folder(self) -> None:
        import os
        import subprocess
        try:
            os.startfile(paths.data_dir())
        except Exception:
            try:
                subprocess.Popen(['explorer', paths.data_dir()])
            except Exception:
                pass
