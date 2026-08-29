"""A small icon-only button, and the pixmap loading/tinting the sidebar nav shares."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from qfluentwidgets import TransparentToolButton

from neptune.core import paths
from neptune.ui import theme as T

SIZE = 32
ICON_SIZE = 17


def load_icon(name: str) -> QPixmap | None:
    path = paths.asset(f"icons/ui/{name}")
    if not path:
        return None
    pixmap = QPixmap(path)
    return pixmap if not pixmap.isNull() else None


def tinted(pixmap: QPixmap, colour: str) -> QPixmap:
    """Recolour a flat silhouette PNG to `colour`, keeping its alpha shape."""
    result = QPixmap(pixmap.size())
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(result.rect(), QColor(colour))
    painter.end()
    return result


class IconButton(TransparentToolButton):
    """A round, icon-only button built from a bundled PNG."""

    def __init__(self, icon_name: str = "", tooltip: str = "", parent=None):
        super().__init__(parent)
        self.setFixedSize(SIZE, SIZE)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        if icon_name:
            self.set_icon(icon_name)

    def set_icon(self, icon_name: str, colour: str = T.TEXT_MUTED) -> None:
        pixmap = load_icon(icon_name)
        if pixmap is not None:
            self.setIcon(QIcon(tinted(pixmap, colour)))
