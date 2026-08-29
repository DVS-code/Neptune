"""A small question mark that reveals its explanation on hover.

Replaces the paragraph of grey text that used to sit under every control. The text is
identical — it just stays out of the way until asked for, which keeps a page of ten
settings readable instead of doubling its height in explanations nobody is reading yet.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from neptune.ui import theme as T

SIZE = 15
WRAP_CHARS = 62


class HelpMark(QWidget):
    """A circled question mark whose tooltip carries the explanation."""

    def __init__(self, text: str = '', parent=None):
        super().__init__(parent)
        self.setFixedSize(SIZE, SIZE)
        self.setCursor(Qt.WhatsThisCursor)


        self.setAttribute(Qt.WA_Hover, True)
        self._hover = False
        self.set_text(text)

    def set_text(self, text: str) -> None:
        """Set the explanation. Also drives whether the mark shows at all."""
        self._text = (text or '').strip()



        self.setToolTip(f'<div style="width:{WRAP_CHARS}em">{self._text}</div>'
                        if self._text else '')
        self.setVisible(bool(self._text))

    def text(self) -> str:
        return self._text

    def enterEvent(self, _event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _event) -> None:
        self._hover = False
        self.update()

    def mousePressEvent(self, event) -> None:
        """Show the tooltip on click too.

        Hover alone is not enough on a touch screen, and a user who clicks a question
        mark and gets nothing reasonably concludes it is broken.
        """
        if event.button() == Qt.LeftButton and self._text:
            QToolTip.showText(self.mapToGlobal(QPoint(0, self.height())),
                              self.toolTip(), self)

    def paintEvent(self, _event) -> None:
        """A ringed question mark.

        The ring is what makes it read as something to point at; an unringed '?' next to a
        label just looks like part of the label. It brightens to the accent on hover so the
        control being explained is unambiguous when several sit close together.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if self._hover:
            ring, glyph, fill = T.ACCENT, T.ACCENT_BRIGHT, QColor(T.ACCENT_MUTED)
        else:
            ring, glyph, fill = T.BORDER_STRONG, T.TEXT_FAINT, QColor(T.SURFACE_SUNKEN)

        painter.setPen(QPen(QColor(ring), 1.2))
        painter.setBrush(fill)

        painter.drawEllipse(1, 1, SIZE - 3, SIZE - 3)

        font = QFont(self.font())
        font.setPixelSize(SIZE - 7)
        font.setWeight(QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor(glyph))
        painter.drawText(self.rect(), Qt.AlignCenter, '?')
