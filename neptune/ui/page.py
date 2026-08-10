"""The scrollable page each feature builds into."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from neptune.ui import theme as T
from neptune.ui.widgets.card import Card
from neptune.ui.widgets.controls import SectionHeading

COLUMN_MAX_WIDTH = 760


class Page(QScrollArea):
    """A vertically scrolling column of cards."""

    def __init__(self, title: str, subtitle: str = '', parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.NoFrame)

        host = QWidget()
        host.setAttribute(Qt.WA_StyledBackground, False)
        outer = QHBoxLayout(host)
        outer.setContentsMargins(T.PAGE_PADDING, T.PAGE_PADDING,
                                 T.PAGE_PADDING, T.PAGE_PADDING)
        outer.setSpacing(0)

        column = QWidget()
        column.setMaximumWidth(COLUMN_MAX_WIDTH)
        self._column = QVBoxLayout(column)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(T.CARD_GAP)

        self._column.addWidget(SectionHeading(title, subtitle))
        self._column.addSpacing(4)

        outer.addWidget(column, 1)
        outer.addStretch(0)
        self.setWidget(host)

    def add_card(self, title: str = '', caption: str = '') -> Card:
        card = Card(title, caption)
        self._column.addWidget(card)
        return card

    def add(self, widget: QWidget) -> QWidget:
        self._column.addWidget(widget)
        return widget

    def finish(self) -> None:
        """Push everything to the top of the page."""
        self._column.addStretch(1)
