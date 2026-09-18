"""Card container and the row primitives that live inside one."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    InfoBar,
    InfoBarIcon,
    InfoBarPosition,
    SimpleCardWidget,
    StrongBodyLabel,
)

from neptune.ui import theme as T
from neptune.ui.widgets.helpmark import HelpMark
from neptune.ui.widgets.toggle import Toggle


class Card(SimpleCardWidget):
    """A flat container. `SimpleCardWidget`, not `CardWidget`: the latter is Fluent's
    *clickable* card and brings a hover-brighten animation with it, which these cards —
    plain grouping panels, never navigation targets — must not have."""

    def __init__(self, title: str = "", caption: str = "", parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(0)

        if title:
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(7)

            title_label = StrongBodyLabel(title)
            head.addWidget(title_label)

            self._help = HelpMark(caption)
            head.addWidget(self._help)
            head.addStretch(1)

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
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(
            f"background: {T.BORDER}; max-height: 1px; min-height: 1px; border: none;"
        )
        self.body.addWidget(line)


class ToggleRow(QWidget):
    def __init__(self, label: str, checked: bool = False, hint: str = "", parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self._label = QLabel(label)
        self._label.setObjectName("RowLabel")
        row.addWidget(self._label)

        self._help = HelpMark(hint)
        row.addWidget(self._help)
        row.addStretch(1)

        self.toggle = Toggle(checked)
        row.addWidget(self.toggle)
        outer.addLayout(row)

    def value(self) -> bool:
        return self.toggle.isChecked()

    def set_value(self, checked: bool, notify: bool = False) -> None:
        self.toggle.set_value(checked, notify)


def bind_progressive(toggle_row: ToggleRow, *details: QWidget) -> None:
    """Show detailed controls only while their feature's toggle is on.

    Follows the shown state, so a preset or tune that sets the toggle silently updates the page
    too, and no page needs its own visibility bookkeeping.
    """

    def sync(enabled: bool) -> None:
        for widget in details:
            widget.setVisible(bool(enabled))

    toggle_row.toggle.state_changed.connect(sync)
    sync(toggle_row.value())


class FieldRow(QWidget):
    def __init__(self, label: str, widget: QWidget, hint: str = "", parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self._label = QLabel(label)
        self._label.setObjectName("RowLabel")
        row.addWidget(self._label)

        self._help = HelpMark(hint)
        row.addWidget(self._help)
        row.addStretch(1)
        row.addWidget(widget)
        outer.addLayout(row)


class Stat(QWidget):
    def __init__(self, caption: str, value: str = "--", unit: str = "", parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        caption_label = QLabel(caption.upper())
        caption_label.setObjectName("StatCaption")
        outer.addWidget(caption_label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)

        self._value = QLabel(value)
        self._value.setObjectName("StatValue")
        row.addWidget(self._value)

        self._unit = QLabel(unit)
        self._unit.setObjectName("StatUnit")
        self._unit.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        row.addWidget(self._unit)
        row.addStretch(1)
        outer.addLayout(row)

    def set(self, value: str, colour: str | None = None, unit: str | None = None) -> None:
        self._value.setText(value)
        self._value.setStyleSheet(f"color: {colour};" if colour else "")
        if unit is not None:
            self._unit.setText(unit)


STAT_MIN_WIDTH = 104
"""Narrowest a stat may be laid out before the strip wraps to another row.

A stat holds a caption plus a value like a full car name, so squeezing eight of them
into one row clipped the text. Wrapping keeps every value readable instead."""

STAT_SPACING = 28


class StatStrip(QWidget):
    """A row of stats that wraps onto further rows when the card is too narrow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = QVBoxLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(14)
        self._stats: dict[str, Stat] = {}
        self._order: list[Stat] = []
        self._columns = 0
        # Without this the strip's minimum is the sum of every stat's minimum, so it
        # refuses to narrow and the wrap never triggers - it just clips instead.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def add(self, key: str, caption: str, value: str = "--", unit: str = "") -> Stat:
        stat = Stat(caption, value, unit)
        stat.setMinimumWidth(STAT_MIN_WIDTH)
        self._stats[key] = stat
        self._order.append(stat)
        self._relayout(self._columns or len(self._order))
        return stat

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        usable = max(1, self.width())
        columns = max(1, (usable + STAT_SPACING) // (STAT_MIN_WIDTH + STAT_SPACING))
        columns = min(columns, len(self._order)) or 1
        if columns != self._columns:
            self._relayout(columns)

    def _relayout(self, columns: int) -> None:
        """Rebuild the rows at `columns` per row, keeping the order stats were added in."""
        columns = max(1, int(columns))
        self._columns = columns
        while self._grid.count():
            item = self._grid.takeAt(0)
            layout = item.layout()
            if layout is not None:
                while layout.count():
                    child = layout.takeAt(0)
                    if child.widget() is not None:
                        child.widget().setParent(self)
                layout.deleteLater()

        for start in range(0, len(self._order), columns):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(STAT_SPACING)
            chunk = self._order[start : start + columns]
            for stat in chunk:
                row.addWidget(stat, 1)
            # Pad the last row so a lone stat does not stretch across the whole card.
            for _ in range(columns - len(chunk)):
                row.addStretch(1)
            self._grid.addLayout(row)

    def set(self, key: str, value: str, colour: str | None = None, unit: str | None = None) -> None:
        stat = self._stats.get(key)
        if stat is not None:
            stat.set(value, colour, unit)

    def reset(self, value: str = "--") -> None:
        for stat in self._stats.values():
            stat.set(value, T.TEXT_FAINT)


class Banner(QWidget):
    ICONS = {
        "info": InfoBarIcon.INFORMATION,
        "warn": InfoBarIcon.WARNING,
        "error": InfoBarIcon.ERROR,
        "ok": InfoBarIcon.SUCCESS,
    }

    def __init__(self, text: str = "", kind: str = "info", parent=None):
        super().__init__(parent)
        self._kind = kind
        self._text = text
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._bar = None
        self._rebuild()
        self.setVisible(bool(text))

    def _rebuild(self) -> None:
        if self._bar is not None:
            self._layout.removeWidget(self._bar)
            self._bar.deleteLater()
        self._bar = InfoBar(
            icon=self.ICONS.get(self._kind, InfoBarIcon.INFORMATION),
            title="",
            content=self._text,
            orient=Qt.Horizontal,
            isClosable=False,
            duration=-1,
            position=InfoBarPosition.NONE,
            parent=self,
        )
        self._layout.addWidget(self._bar)
        self._bar.show()

    def _retext(self) -> None:
        """Repoint the existing InfoBar at `self._text`, no reconstruction.

        Goes through qfluentwidgets' undocumented internals (no public API exists for
        this). If a library update renames or removes any of them, fall back to a full
        rebuild instead of raising.
        """
        try:
            self._bar.content = self._text
            self._bar.contentLabel.setVisible(bool(self._text))
            self._bar._adjustText()
        except AttributeError:
            self._rebuild()

    def set(self, text: str, kind: str | None = None) -> None:
        """Update the banner, rebuilding only when it genuinely has to."""
        kind = kind or self._kind
        if kind != self._kind:
            self._kind = kind
            self._text = text
            self._rebuild()
        elif text != self._text:
            self._text = text
            self._retext()
        self.setVisible(bool(text))
