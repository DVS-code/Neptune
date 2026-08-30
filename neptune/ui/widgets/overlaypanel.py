"""A blurred, dimmed overlay that hosts one Page above the current shell content."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CardWidget, FluentIcon, TransparentToolButton

from neptune.ui import theme as T

BLUR_RADIUS = 24
VEIL_ALPHA = 130
PANEL_MAX_WIDTH = 820
PANEL_MARGIN = 48
CLOSE_BUTTON_SIZE = 32


def _blurred(source: QPixmap, radius: float) -> QPixmap:
    """A gaussian-blurred copy of `source`, rendered through a throwaway QGraphicsScene."""
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(source)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    item.setGraphicsEffect(effect)
    scene.addItem(item)

    result = QPixmap(source.size())
    result.fill(Qt.transparent)
    painter = QPainter(result)
    scene.render(painter, QRectF(result.rect()), QRectF(source.rect()))
    painter.end()
    return result


class _OpaqueCard(CardWidget):
    def _normalBackgroundColor(self) -> QColor:
        return QColor(T.BG)

    def _hoverBackgroundColor(self) -> QColor:
        return self._normalBackgroundColor()

    def _pressedBackgroundColor(self) -> QColor:
        return self._normalBackgroundColor()


class OverlayPanel(QWidget):
    """Covers its parent with a blurred backdrop and a centred card holding a Page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hide()
        self._backdrop_pixmap: QPixmap | None = None
        self._current: QWidget | None = None

        self._frame = _OpaqueCard(self)
        self._frame.setMaximumWidth(PANEL_MAX_WIDTH)

        frame_layout = QVBoxLayout(self._frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        close_row = QHBoxLayout()
        close_row.setContentsMargins(12, 12, 12, 0)
        close_row.addStretch(1)
        self._close_button = TransparentToolButton(FluentIcon.CLOSE)
        self._close_button.setFixedSize(CLOSE_BUTTON_SIZE, CLOSE_BUTTON_SIZE)
        self._close_button.setCursor(Qt.PointingHandCursor)
        self._close_button.clicked.connect(self.close_panel)
        close_row.addWidget(self._close_button)
        frame_layout.addLayout(close_row)

        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.addLayout(self._content_layout, 1)

        self._outer = QHBoxLayout(self)
        self._outer.setSpacing(0)
        self._outer.addWidget(self._frame)
        self._relayout()

    def _relayout(self) -> None:
        """Size the side margins so the frame fills the window, capped and centred."""
        side = max(PANEL_MARGIN, (self.width() - PANEL_MAX_WIDTH) // 2)
        self._outer.setContentsMargins(side, PANEL_MARGIN, side, PANEL_MARGIN)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def open(self, page: QWidget, backdrop: QPixmap) -> None:
        if self._current is not page:
            if self._current is not None:
                self._content_layout.removeWidget(self._current)
                self._current.setParent(None)
            self._content_layout.addWidget(page)
            self._current = page
        self._current.show()
        self._backdrop_pixmap = _blurred(backdrop, BLUR_RADIUS)
        self.show()
        self.raise_()
        self.setFocus()

    def close_panel(self) -> None:
        self.hide()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        if self._backdrop_pixmap is not None:
            painter.drawPixmap(self.rect(), self._backdrop_pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, VEIL_ALPHA))

    def mousePressEvent(self, event) -> None:
        if not self._frame.geometry().contains(event.position().toPoint()):
            self.close_panel()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close_panel()
            return
        super().keyPressEvent(event)
