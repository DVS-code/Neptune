"""Design tokens and the global stylesheet."""

from __future__ import annotations

BG = "#0a0b0d"
BG_RAISED = "#101116"
SURFACE = "#131419"
SURFACE_HOVER = "#181a20"
SURFACE_SUNKEN = "#0d0e12"

BORDER = "#22242c"
BORDER_STRONG = "#2e313b"
BORDER_FOCUS = "#4a3d6b"

ACCENT = "#a855f7"
ACCENT_BRIGHT = "#c084fc"
ACCENT_DEEP = "#7e22ce"
ACCENT_MUTED = "#2a1d3d"
ACCENT_GLOW = "rgba(168, 85, 247, 0.16)"

TEXT = "#f2f3f5"
TEXT_MUTED = "#9ba1ad"
TEXT_FAINT = "#646b78"
TEXT_ON_ACCENT = "#0f0a17"

OK = "#4ade80"
WARN = "#fbbf24"
ERR = "#f87171"
INFO = "#60a5fa"

CURVE_STOCK = "#4b5563"
CURVE_LIVE = "#c084fc"
CURVE_FILL_TOP = "rgba(168, 85, 247, 0.22)"
CURVE_FILL_BOTTOM = "rgba(168, 85, 247, 0.02)"
GRID = "#1c1f27"

FONT_DISPLAY = '"Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", sans-serif'
FONT_SMALL = '"Segoe UI Variable Small", "Segoe UI", sans-serif'

FONT_NUM = '"Segoe UI", Tahoma, sans-serif'
NUM_WEIGHT = 650


SIZE_DISPLAY = 22
SIZE_TITLE = 15
SIZE_HEADING = 12
SIZE_BODY = 12
SIZE_LABEL = 11
SIZE_CAPTION = 10
SIZE_ICON = 15

SIDEBAR_WIDTH = 216
PAGE_PADDING = 28
CARD_GAP = 16
CARD_RADIUS = 10
CONTROL_RADIUS = 7

WINDOW_MIN = (985, 631)
WINDOW_DEFAULT = (1074, 689)


UI_FAMILIES = ("Segoe UI Variable Text", "Segoe UI", "Tahoma")
UI_POINT_SIZE = 9


def ui_font():
    """The application font: the best interface face this machine actually has.

    Qt resolves an unknown family silently, so naming one that is not installed leaves no
    trace and the app quietly renders in the default face. This walks a real preference
    order and picks the first family that exists, then turns on the typographic niceties
    the chosen face supports.
    """
    from PySide6.QtGui import QFont, QFontDatabase

    try:
        installed = set(QFontDatabase.families())
    except Exception:
        installed = set()

    family = next((name for name in UI_FAMILIES if name in installed), UI_FAMILIES[-1])
    font = QFont(family, UI_POINT_SIZE)

    font.setHintingPreference(QFont.PreferVerticalHinting)
    font.setStyleStrategy(QFont.PreferAntialias)
    return font


def stylesheet() -> str:
    return f"""

* {{
    color: {TEXT};
    outline: none;
}}

QWidget#Root, QMainWindow {{
    background: {BG};
}}

/* Tooltips now carry every control's explanation, so they are read rather than glanced
   at: roomier padding, and the muted colour the inline hint text used to have. */
QToolTip {{
    background: {BG_RAISED};
    color: {TEXT_MUTED};
    border: 1px solid {BORDER_STRONG};
    border-radius: {CONTROL_RADIUS}px;
    padding: 9px 12px;
    font-family: {FONT_SMALL};
    font-size: {SIZE_LABEL}px;
}}

QWidget#Sidebar {{
    background: {SURFACE_SUNKEN};
    border-right: 1px solid {BORDER};
}}


QPushButton#NavItem {{
    background: {SURFACE};
    border: none;
    border-radius: {CONTROL_RADIUS}px;
    text-align: left;
    padding: 0px;
    min-height: 44px;
}}
QPushButton#NavItem:hover {{
    background: {SURFACE_HOVER};
}}
QPushButton#NavItem:checked {{
    background: {ACCENT};
}}
QLabel#NavIcon {{
    background: transparent;
}}
QLabel#NavLabel {{
    font-size: {SIZE_BODY}px;
    font-weight: 700;
    letter-spacing: 0.4px;
    background: transparent;
}}

QLabel#RowLabel {{
    font-size: {SIZE_BODY}px;
    color: {TEXT};
}}
QLabel#RowHint {{
    font-family: {FONT_SMALL};
    font-size: {SIZE_LABEL}px;
    color: {TEXT_FAINT};
}}
QLabel#RowValue {{
    font-family: {FONT_NUM};
    font-size: {SIZE_BODY}px;
    font-weight: {NUM_WEIGHT};
    color: {TEXT};
}}
QLabel#RowValueMuted {{
    font-family: {FONT_NUM};
    font-size: {SIZE_BODY}px;
    font-weight: {NUM_WEIGHT};
    color: {TEXT_FAINT};
}}
QLineEdit#ValueEdit {{
    font-family: {FONT_NUM};
    font-weight: {NUM_WEIGHT};
    padding: 5px 8px;
}}

QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG};
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_FAINT};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    height: 0px;
    background: none;
    border: none;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_STRONG};
    border-radius: 4px;
    min-width: 32px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    width: 0px;
    background: none;
    border: none;
}}

QLabel#StatusText {{
    font-size: {SIZE_CAPTION}px;
    color: {TEXT_MUTED};
}}

QLabel#StatCaption {{
    font-family: {FONT_SMALL};
    font-size: {SIZE_CAPTION}px;
    font-weight: 600;
    letter-spacing: 0.8px;
    color: {TEXT_FAINT};
}}
QLabel#StatValue {{
    font-family: {FONT_NUM};
    font-size: 19px;
    font-weight: {NUM_WEIGHT};
    color: {TEXT};
}}
QLabel#StatUnit {{
    font-size: {SIZE_LABEL}px;
    color: {TEXT_FAINT};
}}
"""
