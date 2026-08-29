"""The application window: sidebar navigation, page host and status bar."""
from __future__ import annotations

import sys
from ctypes import byref, c_int, sizeof, windll

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from neptune.core import paths
from neptune.core.module import ModuleRegistry
from neptune.core.runtime import STATE_DETACHED, STATE_READY, STATE_WAITING, Runtime
from neptune.ui import icons
from neptune.ui import theme as T
from neptune.ui.page import Page
from neptune.ui.widgets.wordmark import Wordmark

REFRESH_MS = 90
GROUP_ORDER = ('Vehicle', 'World', 'Tool')
NAV_ICON_WIDTH = 18

STATE_COLOURS = {
    STATE_DETACHED: T.TEXT_FAINT,
    STATE_WAITING: T.WARN,
    STATE_READY: T.OK,
}


class StatusDot(QWidget):
    """A small filled circle showing connection state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(9, 9)
        self._colour = T.TEXT_FAINT

    def set_colour(self, colour: str) -> None:
        if colour != self._colour:
            self._colour = colour
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._colour))
        painter.drawEllipse(0, 0, 9, 9)


class Shell(QWidget):
    """The main window."""

    def __init__(self, registry: ModuleRegistry, runtime: Runtime, settings):
        super().__init__()
        self.registry = registry
        self.runtime = runtime
        self.settings = settings
        self._pages: dict[str, int] = {}
        self._nav: dict[str, QPushButton] = {}
        self._nav_parts: dict[str, tuple[QLabel, QLabel]] = {}

        self.setObjectName('Root')
        self.setWindowTitle('Neptune')
        self.setMinimumSize(*T.WINDOW_MIN)
        self.resize(*T.WINDOW_DEFAULT)
        self._apply_icon()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        right.addWidget(self._build_status_bar())
        root.addLayout(right, 1)

        self._build_pages()

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        QTimer.singleShot(0, self._enable_dark_titlebar)
        if self.settings.get('auto_attach'):
            QTimer.singleShot(250, self._auto_attach)

    def _apply_icon(self) -> None:
        icon_path = paths.asset('neptune.ico')
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName('Sidebar')
        sidebar.setFixedWidth(T.SIDEBAR_WIDTH)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 22, 14, 16)
        layout.setSpacing(0)

        wordmark = Wordmark()
        wordmark.setContentsMargins(10, 0, 0, 0)
        layout.addWidget(wordmark)
        layout.addSpacing(24)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_layout = QVBoxLayout()
        self._nav_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_layout.setSpacing(2)
        layout.addLayout(self._nav_layout)
        layout.addStretch(1)

        self.attach_button = QPushButton('Attach to game')
        self.attach_button.setObjectName('Primary')
        self.attach_button.setCursor(Qt.PointingHandCursor)
        self.attach_button.clicked.connect(self._toggle_attach)
        layout.addWidget(self.attach_button)
        layout.addSpacing(6)

        self.restore_button = QPushButton('Restore everything')
        self.restore_button.setCursor(Qt.PointingHandCursor)
        self.restore_button.clicked.connect(self._restore_all)
        layout.addWidget(self.restore_button)
        return sidebar

    def _add_nav_group(self, title: str) -> None:
        label = QLabel(title.upper())
        label.setObjectName('NavGroup')
        label.setContentsMargins(0, 14, 0, 6)
        self._nav_layout.addWidget(label)

    def _add_nav_item(self, module) -> None:
        """One sidebar row: an icon in a fixed column, then the title.

        The icon is a child label rather than part of the button's own text. Padding the
        text with spaces cannot line the icons up — the icon font and the interface font
        have different advance widths, so every row started at a slightly different x and
        the column visibly wandered. A fixed-width label pins them to a grid, and it also
        lets the icon carry its own font and colour without restyling the label.
        """
        button = QPushButton()
        button.setObjectName('NavItem')
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)

        row = QHBoxLayout(button)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(10)

        mark = QLabel(icons.get(module.name))
        mark.setObjectName('NavIcon')
        mark.setFixedWidth(NAV_ICON_WIDTH)
        mark.setAlignment(Qt.AlignCenter)



        icon_font = QFont(icons.font_family())
        icon_font.setPixelSize(T.SIZE_ICON)
        mark.setFont(icon_font)
        row.addWidget(mark)

        label = QLabel(module.title)
        label.setObjectName('NavLabel')
        row.addWidget(label)
        row.addStretch(1)

        button.clicked.connect(lambda _checked, name=module.name: self.show_page(name))
        self._nav_group.addButton(button)
        self._nav_layout.addWidget(button)
        self._nav[module.name] = button
        self._nav_parts[module.name] = (mark, label)
        self._paint_nav(module.name, selected=False)

    def _paint_nav(self, name: str, selected: bool) -> None:
        """Colour one nav row's icon and title for its state.

        Done here rather than in the stylesheet because Qt's descendant state selectors
        (`QPushButton:checked QLabel`) match children whatever the parent's real state is,
        so every row would render as selected.
        """
        parts = self._nav_parts.get(name)
        if parts is None:
            return
        mark, label = parts
        mark.setStyleSheet(
            f'color: {T.ACCENT_BRIGHT if selected else T.TEXT_FAINT}; background: transparent;')
        label.setStyleSheet(
            f'color: {T.TEXT if selected else T.TEXT_MUTED}; background: transparent;'
            f' font-weight: {600 if selected else 500};')

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('StatusBar')
        bar.setFixedHeight(38)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(10)

        self.status_dot = StatusDot()
        layout.addWidget(self.status_dot)

        self.status_text = QLabel('Not attached')
        self.status_text.setObjectName('StatusText')
        layout.addWidget(self.status_text)
        layout.addStretch(1)



        self._metrics: list[QLabel] = []
        self._metric_seps: list[QLabel] = []
        for index, name in enumerate(('rpm', 'speed', 'gear', 'boost')):
            if index:
                separator = QLabel('·')
                separator.setObjectName('StatDivider')
                layout.addWidget(separator)
                self._metric_seps.append(separator)
            label = QLabel('')
            label.setObjectName('StatMetric')
            setattr(self, f'metric_{name}', label)
            layout.addWidget(label)
            self._metrics.append(label)
        return bar

    def _show_metrics(self, visible: bool) -> None:
        """Hide the dividers along with the readings, so no stray dots are left behind."""
        for separator in self._metric_seps:
            separator.setVisible(visible)

    def _build_pages(self) -> None:
        grouped: dict[str, list] = {}
        for module in self.registry:
            grouped.setdefault(module.group, []).append(module)

        first = None
        for group in GROUP_ORDER:
            modules = grouped.get(group)
            if not modules:
                continue
            self._add_nav_group(group)
            for module in modules:
                page = Page(module.title, module.subtitle)
                module.build_page(page)
                page.finish()
                self._pages[module.name] = self.stack.addWidget(page)
                self._add_nav_item(module)
                if first is None:
                    first = module.name

        if first:
            self.show_page(first)

    def show_page(self, name: str) -> None:
        index = self._pages.get(name)
        if index is None:
            return
        self.stack.setCurrentIndex(index)
        button = self._nav.get(name)
        if button is not None:
            button.setChecked(True)
        for other in self._nav_parts:
            self._paint_nav(other, selected=other == name)

        module = self.registry.get(name)
        if module is not None:
            try:
                module.refresh(self.runtime.vehicle)
            except Exception:
                pass

    def _auto_attach(self) -> None:
        from neptune.memory import offsets as O
        from neptune.memory.process import game_is_running
        if game_is_running(O.GAME_EXE):
            self._toggle_attach()

    def _toggle_attach(self) -> None:
        if self.runtime.attached:
            self.runtime.detach()
            self.attach_button.setText('Attach to game')
            return
        ok, _message = self.runtime.attach()
        if ok:
            self.attach_button.setText('Detach')

    def _restore_all(self) -> None:
        self.runtime.restore_all()
        self._sync_all_controls()

    def _sync_all_controls(self) -> None:
        """Bring every page's controls back in step, including hidden ones."""
        vehicle = self.runtime.vehicle
        for module in self.registry:
            try:
                module.refresh(vehicle)
            except Exception:
                continue

    @staticmethod
    def _set_text(label, text: str) -> None:
        """Set a label only when the text actually changed.

        This runs ~11 times a second on every status field. `setText` with an identical
        string still costs a relayout, and the status text is unchanged on the large
        majority of refreshes.
        """
        if label.text() != text:
            label.setText(text)

    def _refresh(self) -> None:
        state, message = self.runtime.status
        errors = self.registry.take_errors()
        if errors:
            self._set_text(self.status_text, errors[-1])
            self.status_dot.set_colour(T.ERR)
        else:
            self._set_text(self.status_text, message)
            self.status_dot.set_colour(STATE_COLOURS.get(state, T.TEXT_FAINT))

        if not self.runtime.attached:
            self._set_text(self.attach_button, 'Attach to game')

        vehicle = self.runtime.vehicle
        if vehicle is None:
            for label in self._metrics:
                self._set_text(label, '')
            self._show_metrics(False)
        else:
            self._show_metrics(True)
            rpm = vehicle.rpm
            self._set_text(self.metric_rpm, f'{rpm:.0f} rpm' if rpm is not None else '--')
            self._set_text(self.metric_speed, self.settings.format_speed(vehicle.speed_ms))
            gear = vehicle.gear
            self._set_text(self.metric_gear,
                           f'Gear {gear}' if gear and 0 < gear < 11 else '--')
            boost = vehicle.boost_gauge
            self._set_text(self.metric_boost,
                           self.settings.format_pressure(boost)
                           if boost is not None else '--')

        current = self.stack.currentIndex()
        for module in self.registry:
            if not (self._pages.get(module.name) == current or module.always_refresh):
                continue
            try:
                module.refresh(vehicle)
            except Exception:
                continue

    def _enable_dark_titlebar(self) -> None:
        if sys.platform != 'win32':
            return
        try:
            handle = int(self.winId())
            value = c_int(1)
            for attribute in (20, 19):
                try:
                    windll.dwmapi.DwmSetWindowAttribute(
                        handle, attribute, byref(value), sizeof(value))
                except Exception:
                    continue
            colour = T.BG.lstrip('#')
            packed = c_int(int(colour[4:6] + colour[2:4] + colour[0:2], 16))
            windll.dwmapi.DwmSetWindowAttribute(handle, 35, byref(packed), sizeof(packed))
        except Exception:
            pass


    def _start_update_check(self) -> None:
        """Ask GitHub for a newer release, quietly, a moment after the window opens."""
        if not self.settings.get('check_for_updates', True):
            return
        from neptune.core import updater

        def landed(status, info):


            if status != updater.UPDATE_AVAILABLE or info is None:
                return
            if info.version == self.settings.get('skip_update_version'):
                return
            QTimer.singleShot(0, lambda: self._offer_update(info))

        updater.check_async(landed)

    def _offer_update(self, info) -> None:
        from PySide6.QtWidgets import QMessageBox

        from neptune import __version__
        from neptune.core import updater

        notes = (info.notes or '').strip()
        if len(notes) > 700:
            notes = notes[:700].rstrip() + '…'

        box = QMessageBox(self)
        box.setWindowTitle('Update available')
        box.setIcon(QMessageBox.Information)
        box.setText(f'Neptune {info.version} is available.')
        box.setInformativeText(
            f'You are running {__version__}.\n\n'
            + (notes if notes else 'Would you like to update now?'))
        install = box.addButton('Update now', QMessageBox.AcceptRole)
        box.addButton('Not now', QMessageBox.RejectRole)
        skip = box.addButton('Skip this version', QMessageBox.DestructiveRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked is skip:

            self.settings.set('skip_update_version', info.version)
            return
        if clicked is not install:
            return

        if not updater.frozen():

            QMessageBox.information(
                self, 'Update',
                'Neptune is running from source, so it cannot replace itself.\n'
                'The releases page has been opened in your browser.')
            updater.open_releases_page()
            return

        if updater.download_and_apply(info):
            QMessageBox.information(
                self, 'Update',
                'Neptune will close and reopen on the new version.')
            self.close()
        else:
            QMessageBox.warning(
                self, 'Update',
                'The update could not be installed automatically.\n'
                'The releases page has been opened so you can download it.')
            updater.open_releases_page()

    def showEvent(self, event) -> None:
        """Ease the window in on first paint.

        ⚠️ Degrades to a plain, FULLY VISIBLE window if the animation cannot run — never leave the
        shell stuck transparent. The fade is armed once and only on the first show, so restoring
        from the taskbar does not replay it.
        """
        super().showEvent(event)
        if getattr(self, '_faded', False):
            return
        self._faded = True
        try:
            from PySide6.QtCore import QEasingCurve, QPropertyAnimation
            self.setWindowOpacity(0.0)
            fade = QPropertyAnimation(self, b'windowOpacity', self)
            fade.setDuration(260)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QEasingCurve.OutCubic)
            fade.finished.connect(lambda: self.setWindowOpacity(1.0))
            self._fade = fade
            fade.start()
        except Exception:
            self.setWindowOpacity(1.0)


        QTimer.singleShot(1500, self._start_update_check)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        try:
            if self.settings.get('restore_on_exit'):
                self.runtime.restore_all()
            self.runtime.detach()
        except Exception:
            pass
        try:
            self.registry.dispatch('shutdown')
        except Exception:
            pass
        event.accept()
