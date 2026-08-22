"""Settings: units, display and startup behaviour."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QPushButton

from neptune import __version__
from neptune.core import paths
from neptune.core.module import FeatureModule
from neptune.core.settings import HEIGHT_UNITS, PRESSURE_UNITS, SPEED_UNITS
from neptune.memory import offsets as O
from neptune.ui.widgets.card import FieldRow, ToggleRow
from neptune.ui.widgets.controls import Segmented
from neptune.ui.widgets.sliderrow import SliderRow

HINT_ATMOSPHERIC = ('Where the boost gauge reads zero. Lower for altitude, '
                    'or zero to show absolute pressure.')
HINT_AIRRIDE_VOLUME = 'The air-release hiss when the car drops.'
HINT_MAYBACH_VOLUME = 'The track that loops while the Maybach bounce runs.'
HINT_AUTO_ATTACH = 'Connect to Forza Horizon 6 automatically when Neptune starts.'
HINT_RESTORE = 'Put every change back to stock when Neptune closes.'
HINT_UPDATES = 'Look for a newer Neptune when the tool starts.'

ISSUES_URL = 'https://github.com/DVS-code/Neptune/issues'


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
        self._page = None
        self._widgets: dict = {}

    def build_page(self, page) -> None:
        self._page = page
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

        sound_card = page.add_card('Sound', 'How loud Neptune\'s own sounds play.')

        airride_volume = SliderRow('Air ride', 0, 100,
                                   self.settings.get('airride_volume'),
                                   step=1, decimals=0, unit='%',
                                   hint=HINT_AIRRIDE_VOLUME)
        airride_volume.changed.connect(
            lambda value: self.settings.set('airride_volume', int(value)))
        self._widgets['airride_volume'] = airride_volume
        sound_card.add(airride_volume)

        maybach_volume = SliderRow('Maybach bounce', 0, 100,
                                   self.settings.get('maybach_volume'),
                                   step=1, decimals=0, unit='%',
                                   hint=HINT_MAYBACH_VOLUME)
        maybach_volume.changed.connect(
            lambda value: self.settings.set('maybach_volume', int(value)))
        self._widgets['maybach_volume'] = maybach_volume
        sound_card.add(maybach_volume)

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

        updates = ToggleRow('Check for updates',
                            bool(self.settings.get('check_for_updates')),
                            hint=HINT_UPDATES)
        updates.toggle.toggled_value.connect(self._set_check_for_updates)
        startup_card.add(updates)

        check_button = QPushButton('Check for updates now')
        check_button.setCursor(Qt.PointingHandCursor)
        check_button.clicked.connect(self._check_now)
        startup_card.add(check_button)

        input_card = page.add_card(
            'Controls',
            'Keyboard, controller and racing wheel. Bind keys on each feature\'s own page.')
        self._widgets['devices'] = QLabel('Looking for controllers…')
        self._widgets['devices'].setObjectName('RowHint')
        self._widgets['devices'].setWordWrap(True)
        input_card.add(self._widgets['devices'])

        refresh_devices = QPushButton('Rescan for wheels')
        refresh_devices.setCursor(Qt.PointingHandCursor)
        refresh_devices.clicked.connect(self._rescan_devices)
        input_card.add(refresh_devices)

        # ⚠️ Deferred, NOT called inline. Enumerating racing wheels asks Windows for the
        # capabilities of all 16 joystick slots, which measured **314 ms** of blocking
        # hardware I/O — a third of a second added to Neptune's startup, on a card most
        # users never look at. Running it after the window is up keeps the launch snappy
        # and the label fills itself in a moment later.
        QTimer.singleShot(0, self._rescan_devices)

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

        feedback_card = page.add_card(
            'Report bugs / feedback',
            'Found something broken, or want a feature? Open an issue on GitHub.')
        report_button = QPushButton('Report bugs / feedback')
        report_button.setCursor(Qt.PointingHandCursor)
        report_button.clicked.connect(self._open_issues)
        feedback_card.add(report_button)

        issues_label = QLabel(ISSUES_URL)
        issues_label.setObjectName('RowHint')
        issues_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        feedback_card.add(issues_label)

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

    def _rescan_devices(self) -> None:
        """List what Neptune can bind right now.

        Worth showing: a wheel that Windows and the game both see is still invisible to XInput, so
        without this the user has no way to tell whether Neptune found it before trying to bind.
        """
        from neptune.core import input as inp

        label = self._widgets.get('devices')
        if label is None:
            return

        lines = ['Keyboard  ·  connected']
        lines.append('Controller  ·  '
                     + ('connected' if inp.controller_connected() else 'not detected'))

        names = inp.wheel_names()
        if names:
            for name in names:
                lines.append(f'{name}  ·  connected')
        else:
            lines.append('Racing wheel  ·  not detected')
        label.setText('\n'.join(lines))

    def _set_check_for_updates(self, enabled: bool) -> None:
        self.settings.set('check_for_updates', bool(enabled))
        if enabled:


            self.settings.set('skip_update_version', '')

    def _check_now(self) -> None:
        """Manual check. Unlike the startup check this always reports, including 'up to date'."""
        from PySide6.QtWidgets import QMessageBox

        from neptune import __version__
        from neptune.core import updater

        status, info = updater.check()
        window = self._page.window() if getattr(self, '_page', None) else None

        if status == updater.UPDATE_AVAILABLE and info is not None:

            self.settings.set('skip_update_version', '')
            shell = window
            if shell is not None and hasattr(shell, '_offer_update'):
                shell._offer_update(info)
            else:
                updater.open_releases_page()
            return

        if status == updater.UP_TO_DATE:
            message = f'Neptune {__version__} is the latest version.'
        elif status == updater.NO_RELEASES:
            message = 'There are no published releases to update to yet.'
        else:
            message = ('Could not reach GitHub to check for updates.\n'
                       'Check your connection and try again.')
        QMessageBox.information(window, 'Check for updates', message)

    def _open_issues(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(ISSUES_URL))

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
