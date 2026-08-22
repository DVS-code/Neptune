"""Keybind capture, segmented selector and section heading."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from neptune.core import input as inp
from neptune.ui import theme as T


class BindButton(QPushButton):

    bound = Signal(object)

    def __init__(self, binding: dict | None = None, parent=None,
                 settings=None, key: str = ''):
        super().__init__(parent)
        self._binding = binding
        self._listening = False
        # Given the settings and its own key, the button keeps itself honest: when a
        # control is reassigned to another feature this one is unassigned there, and
        # without this the losing page would still show the binding it no longer owns.
        self._settings = settings
        self._key = key
        if settings is not None and key:
            settings.subscribe(self._on_settings_changed)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(190)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._poll)
        self.clicked.connect(self._start)
        self._render()

    def _render(self) -> None:
        if self._listening:
            self.setText('Press any key or button')
            self.setStyleSheet(f'color: {T.ACCENT_BRIGHT}; border-color: {T.ACCENT};')
            return
        self.setStyleSheet('')
        if not self._binding or not self._binding.get('kind'):
            self.setText('Click to bind')
            return
        source = inp.source_label(self._binding)
        self.setText(f'{source}  ·  {self._binding.get("label", "?")}')

    def _start(self) -> None:
        if self._listening:
            self._stop()
            return
        self._listening = True
        self._render()
        self._timer.start()

    def _stop(self) -> None:
        self._listening = False
        self._timer.stop()
        self._render()

    def _poll(self) -> None:
        if not self._listening:
            self._timer.stop()
            return
        captured = inp.poll_any_input()
        if not captured:
            return
        if captured.get('clear'):
            self._binding = None
        else:
            self._binding = captured
        self._stop()
        self.bound.emit(self._binding)

    def _on_settings_changed(self, key: str) -> None:
        if key != 'bindings' or self._settings is None or not self._key:
            return
        current = self._settings.binding(self._key)
        if current != self._binding:
            self._binding = current
            self._render()

    def binding(self) -> dict | None:
        return self._binding

    def set_binding(self, binding: dict | None) -> None:
        self._binding = binding
        self._render()


class Segmented(QWidget):

    changed = Signal(str)

    def __init__(self, options: list[str], value: str | None = None, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        for index, option in enumerate(options):
            button = QPushButton(option)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(self._style(index, len(options)))
            self._group.addButton(button)
            self._buttons[option] = button
            row.addWidget(button)
            button.clicked.connect(lambda _checked, o=option: self.changed.emit(o))

        selected = value if value in self._buttons else (options[0] if options else None)
        if selected:
            self._buttons[selected].setChecked(True)

    @staticmethod
    def _style(index: int, total: int) -> str:
        radius = T.CONTROL_RADIUS
        left = radius if index == 0 else 0
        right = radius if index == total - 1 else 0
        return f"""
        QPushButton {{
            background: {T.SURFACE_SUNKEN};
            border: 1px solid {T.BORDER};
            border-left-width: {1 if index == 0 else 0}px;
            border-top-left-radius: {left}px;
            border-bottom-left-radius: {left}px;
            border-top-right-radius: {right}px;
            border-bottom-right-radius: {right}px;
            padding: 7px 15px;
            color: {T.TEXT_MUTED};
            font-weight: 550;
        }}
        QPushButton:hover {{ background: {T.SURFACE_HOVER}; color: {T.TEXT}; }}
        QPushButton:checked {{
            background: {T.ACCENT_MUTED};
            color: {T.TEXT};
            border-color: {T.ACCENT_DEEP};
        }}
        """

    def value(self) -> str | None:
        for option, button in self._buttons.items():
            if button.isChecked():
                return option
        return None

    def set_value(self, value: str, notify: bool = False) -> None:
        button = self._buttons.get(value)
        if button is None:
            return
        button.setChecked(True)
        if notify:
            self.changed.emit(value)


class SectionHeading(QWidget):

    def __init__(self, title: str, subtitle: str = '', parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        title_label = QLabel(title)
        title_label.setObjectName('PageTitle')
        outer.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName('PageSubtitle')
            subtitle_label.setWordWrap(True)
            outer.addWidget(subtitle_label)
