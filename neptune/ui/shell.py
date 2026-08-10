"""The application window: sidebar navigation, page host and status bar."""
from __future__ import annotations

import sys
from ctypes import byref, c_int, sizeof, windll

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter
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
from neptune.ui import theme as T
from neptune.ui.page import Page
from neptune.ui.widgets.wordmark import Wordmark

REFRESH_MS = 90
GROUP_ORDER = ('Vehicle', 'World', 'Tool')

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
        button = QPushButton(f'  {module.icon}   {module.title}'
                             if module.icon else f'  {module.title}')
        button.setObjectName('NavItem')
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda _checked, name=module.name: self.show_page(name))
        self._nav_group.addButton(button)
        self._nav_layout.addWidget(button)
        self._nav[module.name] = button

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

        self.metric_rpm = QLabel('')
        self.metric_rpm.setObjectName('StatMetric')
        layout.addWidget(self.metric_rpm)

        self.metric_speed = QLabel('')
        self.metric_speed.setObjectName('StatMetric')
        layout.addWidget(self.metric_speed)

        self.metric_gear = QLabel('')
        self.metric_gear.setObjectName('StatMetric')
        layout.addWidget(self.metric_gear)
        return bar

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

    def _refresh(self) -> None:
        state, message = self.runtime.status
        errors = self.registry.take_errors()
        if errors:
            self.status_text.setText(errors[-1])
            self.status_dot.set_colour(T.ERR)
        else:
            self.status_text.setText(message)
            self.status_dot.set_colour(STATE_COLOURS.get(state, T.TEXT_FAINT))

        if not self.runtime.attached:
            self.attach_button.setText('Attach to game')

        vehicle = self.runtime.vehicle
        if vehicle is None:
            self.metric_rpm.setText('')
            self.metric_speed.setText('')
            self.metric_gear.setText('')
        else:
            rpm = vehicle.rpm
            self.metric_rpm.setText(f'{rpm:.0f} rpm' if rpm is not None else '')
            self.metric_speed.setText(self.settings.format_speed(vehicle.speed_ms))
            gear = vehicle.gear
            self.metric_gear.setText(f'Gear {gear}' if gear and 0 < gear < 11 else '')

        current = self.stack.currentWidget()
        for module in self.registry:
            index = self._pages.get(module.name)
            visible = index is not None and self.stack.widget(index) is current
            if not (visible or module.always_refresh):
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
