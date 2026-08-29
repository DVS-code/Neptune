"""Keybind capture, segmented selector and section heading."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    PushButton,
    SegmentedWidget,
    TitleLabel,
    setCustomStyleSheet,
)

from neptune.core import input as inp
from neptune.ui import theme as T


class BindButton(PushButton):

    bound = Signal(object)

    def __init__(self, binding: dict | None = None, parent=None,
                 settings=None, key: str = ''):
        super().__init__(parent)
        self._binding = binding
        self._listening = False



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
        # setCustomStyleSheet, not setStyleSheet as PushButton.__init__ already applied
        if self._listening:
            self.setText('Press any key or button')
            listening = f'BindButton {{ color: {T.ACCENT_BRIGHT}; border-color: {T.ACCENT}; }}'
            setCustomStyleSheet(self, listening, listening)
            return
        setCustomStyleSheet(self, '', '')
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

        self._widget = SegmentedWidget(self)
        row.addWidget(self._widget)
        self._options = list(options)

        for option in self._options:
            self._widget.addItem(
                routeKey=option, text=option,
                onClick=lambda _checked, o=option: self.changed.emit(o))

        selected = value if value in self._options else (
            self._options[0] if self._options else None)
        if selected:
            self._widget.setCurrentItem(selected)

    def value(self) -> str | None:
        return self._widget.currentRouteKey()

    def set_value(self, value: str, notify: bool = False) -> None:
        if value not in self._options:
            return
        self._widget.setCurrentItem(value)
        if notify:
            self.changed.emit(value)


class SectionHeading(QWidget):

    def __init__(self, title: str, subtitle: str = '', parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        outer.addWidget(TitleLabel(title))

        if subtitle:
            caption = CaptionLabel(subtitle)
            caption.setWordWrap(True)
            outer.addWidget(caption)
