"""Card container and the row primitives that live inside one."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from neptune.ui import theme as T
from neptune.ui.widgets.toggle import Toggle


class Card(QFrame):

    def __init__(self, title: str = '', caption: str = '', parent=None):
        super().__init__(parent)
        self.setObjectName('Card')
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(0)

        if title:
            head = QVBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(2)

            title_label = QLabel(title)
            title_label.setObjectName('CardTitle')
            head.addWidget(title_label)

            if caption:
                caption_label = QLabel(caption)
                caption_label.setObjectName('CardCaption')
                caption_label.setWordWrap(True)
                head.addWidget(caption_label)

            outer.addLayout(head)
            outer.addSpacing(14)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(12)
        outer.addLayout(self.body)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)

    def add_divider(self) -> None:
        line = QFrame()
        line.setObjectName('CardDivider')
        line.setFrameShape(QFrame.HLine)
        self.body.addWidget(line)


class ToggleRow(QWidget):

    def __init__(self, label: str, checked: bool = False, hint: str = '', parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self._label = QLabel(label)
        self._label.setObjectName('RowLabel')
        row.addWidget(self._label)
        row.addStretch(1)

        self.toggle = Toggle(checked)
        row.addWidget(self.toggle)
        outer.addLayout(row)

        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName('RowHint')
            hint_label.setWordWrap(True)
            outer.addWidget(hint_label)

    def value(self) -> bool:
        return self.toggle.isChecked()

    def set_value(self, checked: bool, notify: bool = False) -> None:
        self.toggle.set_value(checked, notify)


class FieldRow(QWidget):

    def __init__(self, label: str, widget: QWidget, hint: str = '', parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self._label = QLabel(label)
        self._label.setObjectName('RowLabel')
        row.addWidget(self._label)
        row.addStretch(1)
        row.addWidget(widget)
        outer.addLayout(row)

        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName('RowHint')
            hint_label.setWordWrap(True)
            outer.addWidget(hint_label)


class Stat(QWidget):

    def __init__(self, caption: str, value: str = '--', unit: str = '', parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        caption_label = QLabel(caption.upper())
        caption_label.setObjectName('StatCaption')
        outer.addWidget(caption_label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)

        self._value = QLabel(value)
        self._value.setObjectName('StatValue')
        row.addWidget(self._value)

        self._unit = QLabel(unit)
        self._unit.setObjectName('StatUnit')
        self._unit.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        row.addWidget(self._unit)
        row.addStretch(1)
        outer.addLayout(row)

    def set(self, value: str, colour: str | None = None, unit: str | None = None) -> None:
        self._value.setText(value)
        self._value.setStyleSheet(f'color: {colour};' if colour else '')
        if unit is not None:
            self._unit.setText(unit)


class StatStrip(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(28)
        self._stats: dict[str, Stat] = {}

    def add(self, key: str, caption: str, value: str = '--', unit: str = '') -> Stat:
        stat = Stat(caption, value, unit)
        self._stats[key] = stat
        self._row.addWidget(stat, 1)
        return stat

    def set(self, key: str, value: str, colour: str | None = None,
            unit: str | None = None) -> None:
        stat = self._stats.get(key)
        if stat is not None:
            stat.set(value, colour, unit)

    def reset(self, value: str = '--') -> None:
        for stat in self._stats.values():
            stat.set(value, T.TEXT_FAINT)


class Banner(QFrame):

    COLOURS = {'info': T.INFO, 'warn': T.WARN, 'error': T.ERR, 'ok': T.OK}

    def __init__(self, text: str = '', kind: str = 'info', parent=None):
        super().__init__(parent)
        self.setObjectName('Banner')
        self._kind = kind

        row = QHBoxLayout(self)
        row.setContentsMargins(13, 11, 13, 11)
        row.setSpacing(11)

        self._text = QLabel(text)
        self._text.setObjectName('BannerText')
        self._text.setWordWrap(True)
        row.addWidget(self._text, 1)

        self._apply(kind)

    def _apply(self, kind: str) -> None:
        colour = self.COLOURS.get(kind, T.INFO)
        self.setStyleSheet(
            f'QFrame#Banner {{ background: {T.SURFACE_SUNKEN};'
            f' border: 1px solid {T.BORDER};'
            f' border-left: 3px solid {colour};'
            f' border-radius: {T.CONTROL_RADIUS}px; }}')

    def set(self, text: str, kind: str | None = None) -> None:
        self._text.setText(text)
        if kind and kind != self._kind:
            self._kind = kind
            self._apply(kind)
        self.setVisible(bool(text))
