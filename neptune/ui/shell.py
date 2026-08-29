"""The application window: sidebar navigation and page host."""
from __future__ import annotations

import sys
from ctypes import byref, c_int, sizeof, windll

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
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
from neptune.ui.widgets.buttons import Button
from neptune.ui.widgets.iconbutton import IconButton, load_icon, tinted
from neptune.ui.widgets.overlaypanel import OverlayPanel
from neptune.ui.widgets.wordmark import Wordmark

REFRESH_MS = 90
GROUP_ORDER = ('Vehicle', 'World', 'Tool')
NAV_ICON_WIDTH = 24
EXCLUDED_FROM_NAV = {'presets', 'settings'}
ATTACH_ICON_SIZE = 22
TOPBAR_HEIGHT = 56

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

    ready = Signal()
    update_available = Signal(object)

    def __init__(self, registry: ModuleRegistry, runtime: Runtime, settings):
        super().__init__()
        self.registry = registry
        self.runtime = runtime
        self.settings = settings
        self._pages: dict[str, int] = {}
        self._overlay_pages: dict[str, Page] = {}
        self._nav: dict[str, QPushButton] = {}
        self._nav_parts: dict[str, tuple[QLabel, QLabel]] = {}
        self._nav_pixmaps: dict[str, tuple[QPixmap | None, QPixmap | None]] = {}
        self._last_attached_state: bool | None = None
        self.update_available.connect(self._offer_update)

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

        right.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        root.addLayout(right, 1)

        self._build_queue: list = []
        self._built: list = []
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)

        QTimer.singleShot(0, self._start_building_pages)

    def _start_building_pages(self) -> None:
        grouped: dict[str, list] = {}
        for module in self.registry:
            grouped.setdefault(module.group, []).append(module)
        for group in GROUP_ORDER:
            self._build_queue.extend(grouped.get(group, []))
        self._build_next_page()

    def _build_next_page(self) -> None:
        if not self._build_queue:
            self._reveal_pages()
            return
        module = self._build_queue.pop(0)
        self._built.append((module, self._build_page(module)))
        QTimer.singleShot(0, self._build_next_page)

    def _reveal_pages(self) -> None:
        """Wire every built page into the sidebar/stack in one pass, then start the app."""
        first = None
        for module, page in self._built:
            if module.name in EXCLUDED_FROM_NAV:
                self._overlay_pages[module.name] = page
            else:
                self._pages[module.name] = self.stack.addWidget(page)
                self._add_nav_item(module)
                if first is None:
                    first = module.name
        self._built = []

        if first:
            self.show_page(first)

        self._overlay = OverlayPanel(self)
        self._overlay.setGeometry(self.rect())

        self._timer.start()

        if self.settings.get('auto_attach'):
            QTimer.singleShot(250, self._auto_attach)

        self.ready.emit()

    @staticmethod
    def _build_page(module) -> Page:
        page = Page(module.title, module.subtitle)
        module.build_page(page)
        page.finish()
        return page

    def _apply_icon(self) -> None:
        icon_path = paths.asset('icons/neptune.ico')
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName('Sidebar')
        sidebar.setFixedWidth(T.SIDEBAR_WIDTH)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 20, 14, 16)
        layout.setSpacing(0)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_layout = QVBoxLayout()
        self._nav_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_layout.setSpacing(2)
        layout.addStretch(1)
        layout.addLayout(self._nav_layout)
        layout.addStretch(1)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(4, 0, 4, 0)
        status_row.setSpacing(8)

        self.status_dot = StatusDot()
        status_row.addWidget(self.status_dot)

        self.status_text = QLabel('Not attached')
        self.status_text.setObjectName('StatusText')
        status_row.addWidget(self.status_text, 1)

        self.attach_icon_button = IconButton('dettached.png', 'Attach to game')
        self.attach_icon_button.setFixedSize(ATTACH_ICON_SIZE, ATTACH_ICON_SIZE)
        self.attach_icon_button.setIconSize(QSize(14, 14))
        self.attach_icon_button.clicked.connect(self._toggle_attach)
        status_row.addWidget(self.attach_icon_button)

        layout.addLayout(status_row)
        layout.addSpacing(10)

        self.restore_button = Button('Restore everything')
        self.restore_button.clicked.connect(self._restore_all)
        layout.addWidget(self.restore_button)
        return sidebar

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('TopBar')
        bar.setFixedHeight(TOPBAR_HEIGHT)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 20, 0)
        layout.setSpacing(6)

        layout.addStretch(1)
        layout.addWidget(Wordmark())
        layout.addStretch(1)

        self.profile_button = IconButton('profile.png', 'Presets')
        self.profile_button.clicked.connect(lambda: self._open_overlay('presets'))
        layout.addWidget(self.profile_button)

        self.settings_button = IconButton('settings.png', 'Settings')
        self.settings_button.clicked.connect(lambda: self._open_overlay('settings'))
        layout.addWidget(self.settings_button)

        return bar

    def _add_nav_item(self, module) -> None:
        """One sidebar row: a tinted PNG icon in a fixed column, then the title.
        """
        button = QPushButton()
        button.setObjectName('NavItem')
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)

        row = QHBoxLayout(button)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(12)

        mark = QLabel()
        mark.setObjectName('NavIcon')
        mark.setFixedWidth(NAV_ICON_WIDTH)
        mark.setAlignment(Qt.AlignCenter)
        row.addWidget(mark)

        label = QLabel(module.title.upper())
        label.setObjectName('NavLabel')
        row.addWidget(label)
        row.addStretch(1)

        button.clicked.connect(lambda _checked, name=module.name: self.show_page(name))
        self._nav_group.addButton(button)
        self._nav_layout.addWidget(button)
        self._nav_layout.addSpacing(16)
        self._nav[module.name] = button
        self._nav_parts[module.name] = (mark, label)
        self._nav_pixmaps[module.name] = self._nav_icon_variants(module.icon)
        self._paint_nav(module.name, selected=False)

    @staticmethod
    def _nav_icon_variants(icon_file: str) -> tuple[QPixmap | None, QPixmap | None]:
        """Read, tint and scale one nav icon once, as (selected, unselected).
        """
        source = load_icon(icon_file) if icon_file else None
        if source is None:
            return (None, None)
        selected, unselected = (
            tinted(source, colour).scaled(
                T.SIZE_ICON, T.SIZE_ICON, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            for colour in (T.TEXT_ON_ACCENT, T.TEXT))
        return (selected, unselected)

    def _paint_nav(self, name: str, selected: bool) -> None:
        """Colour one nav row's icon and title for its state.
        """
        parts = self._nav_parts.get(name)
        if parts is None:
            return
        mark, label = parts
        on, off = self._nav_pixmaps.get(name, (None, None))
        pixmap = on if selected else off
        if pixmap is not None:
            mark.setPixmap(pixmap)
        colour = T.TEXT_ON_ACCENT if selected else T.TEXT
        label.setStyleSheet(f'color: {colour}; background: transparent;')

    def _paint_attach_icon(self, attached: bool) -> None:
        icon_name = 'attached.png' if attached else 'dettached.png'
        self.attach_icon_button.set_icon(icon_name)
        self.attach_icon_button.setToolTip('Detach' if attached else 'Attach to game')

    def _open_overlay(self, name: str) -> None:
        page = self._overlay_pages.get(name)
        if page is None:
            return
        module = self.registry.get(name)
        if module is not None:
            try:
                module.refresh(self.runtime.vehicle)
            except Exception:
                pass
        self._overlay.open(page, self.grab())

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
        else:
            self.runtime.attach()

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

        if self.runtime.attached != self._last_attached_state:
            self._last_attached_state = self.runtime.attached
            self._paint_attach_icon(self.runtime.attached)

        vehicle = self.runtime.vehicle
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
            # `landed` runs on the update-check's worker thread (see check_async's
            # docstring). QTimer.singleShot() from there never fires: it has no Qt event
            # loop to schedule on. update_available queues onto the GUI thread instead.
            self.update_available.emit(info)

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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, '_overlay'):
            self._overlay.setGeometry(self.rect())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.repaint()
        if getattr(self, '_faded', False):
            return
        self._faded = True
        QTimer.singleShot(0, self._enable_dark_titlebar)

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
