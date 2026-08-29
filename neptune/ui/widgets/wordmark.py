"""The product wordmark: Neptune's own logo image, not a redrawn approximation of it."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel

from neptune.core import paths

HEIGHT = 34


class Wordmark(QLabel):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedHeight(HEIGHT)

        path = paths.asset('icons/neptune.png')
        pixmap = QPixmap(path) if path else QPixmap()
        if not pixmap.isNull():
            pixmap = pixmap.scaledToHeight(HEIGHT, Qt.SmoothTransformation)
        self.setPixmap(pixmap)
