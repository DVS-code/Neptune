"""Design tokens and the global stylesheet."""
from __future__ import annotations

BG = '#0a0b0d'
BG_RAISED = '#101116'
SURFACE = '#131419'
SURFACE_HOVER = '#181a20'
SURFACE_SUNKEN = '#0d0e12'

BORDER = '#22242c'
BORDER_STRONG = '#2e313b'
BORDER_FOCUS = '#4a3d6b'

ACCENT = '#a855f7'
ACCENT_BRIGHT = '#c084fc'
ACCENT_DEEP = '#7e22ce'
ACCENT_MUTED = '#2a1d3d'
ACCENT_GLOW = 'rgba(168, 85, 247, 0.16)'

WORDMARK_GRADIENT = ('#e040d6', '#c084fc', '#8b5cf6', '#6366f1',
                     '#8b5cf6', '#c084fc')

TEXT = '#f2f3f5'
TEXT_MUTED = '#9ba1ad'
TEXT_FAINT = '#646b78'
TEXT_ON_ACCENT = '#0f0a17'

OK = '#4ade80'
WARN = '#fbbf24'
ERR = '#f87171'
INFO = '#60a5fa'

CURVE_STOCK = '#4b5563'
CURVE_LIVE = '#c084fc'
CURVE_FILL_TOP = 'rgba(168, 85, 247, 0.22)'
CURVE_FILL_BOTTOM = 'rgba(168, 85, 247, 0.02)'
GRID = '#1c1f27'

FONT_UI = 'Inter, Segoe UI Variable Display, Segoe UI, sans-serif'
FONT_NUM = 'Segoe UI Variable Display, Segoe UI, sans-serif'

SIZE_DISPLAY = 22
SIZE_TITLE = 15
SIZE_HEADING = 12
SIZE_BODY = 12
SIZE_LABEL = 11
SIZE_CAPTION = 10

SIDEBAR_WIDTH = 216
PAGE_PADDING = 28
CARD_GAP = 16
CARD_RADIUS = 10
CONTROL_RADIUS = 7

WINDOW_MIN = (1060, 720)
WINDOW_DEFAULT = (1180, 820)


def stylesheet() -> str:
    return f"""
* {{
    font-family: {FONT_UI};
    font-size: {SIZE_BODY}px;
    color: {TEXT};
    outline: none;
}}

QWidget#Root, QMainWindow {{
    background: {BG};
}}

QToolTip {{
    background: {BG_RAISED};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 7px 10px;
    font-size: {SIZE_LABEL}px;
}}

QWidget#Sidebar {{
    background: {SURFACE_SUNKEN};
    border-right: 1px solid {BORDER};
}}

QLabel#NavGroup {{
    font-size: {SIZE_CAPTION}px;
    font-weight: 700;
    letter-spacing: 1.4px;
    color: {TEXT_FAINT};
    padding: 0px 12px;
}}

QPushButton#NavItem {{
    background: transparent;
    border: none;
    border-radius: {CONTROL_RADIUS}px;
    color: {TEXT_MUTED};
    text-align: left;
    padding: 9px 12px;
    font-size: {SIZE_BODY}px;
    font-weight: 500;
}}
QPushButton#NavItem:hover {{
    background: {SURFACE};
    color: {TEXT};
}}
QPushButton#NavItem:checked {{
    background: {ACCENT_MUTED};
    color: {TEXT};
    font-weight: 600;
}}

QLabel#PageTitle {{
    font-size: {SIZE_TITLE}px;
    font-weight: 650;
    color: {TEXT};
}}
QLabel#PageSubtitle {{
    font-size: {SIZE_LABEL}px;
    color: {TEXT_FAINT};
}}

QFrame#Card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {CARD_RADIUS}px;
}}
QLabel#CardTitle {{
    font-size: {SIZE_HEADING}px;
    font-weight: 650;
    color: {TEXT};
}}
QLabel#CardCaption {{
    font-size: {SIZE_LABEL}px;
    color: {TEXT_FAINT};
}}
QFrame#CardDivider {{
    background: {BORDER};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

QLabel#RowLabel {{
    font-size: {SIZE_BODY}px;
    color: {TEXT};
}}
QLabel#RowHint {{
    font-size: {SIZE_LABEL}px;
    color: {TEXT_FAINT};
}}
QLabel#RowValue {{
    font-family: {FONT_NUM};
    font-size: {SIZE_BODY}px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#RowValueMuted {{
    font-family: {FONT_NUM};
    font-size: {SIZE_BODY}px;
    font-weight: 600;
    color: {TEXT_FAINT};
}}

QPushButton {{
    background: {BG_RAISED};
    border: 1px solid {BORDER_STRONG};
    border-radius: {CONTROL_RADIUS}px;
    padding: 8px 16px;
    font-size: {SIZE_BODY}px;
    font-weight: 550;
    color: {TEXT};
}}
QPushButton:hover {{
    background: {SURFACE_HOVER};
    border-color: {BORDER_FOCUS};
}}
QPushButton:pressed {{
    background: {SURFACE_SUNKEN};
}}
QPushButton:disabled {{
    background: {SURFACE_SUNKEN};
    border-color: {BORDER};
    color: {TEXT_FAINT};
}}

QPushButton#Primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: {TEXT_ON_ACCENT};
    font-weight: 650;
}}
QPushButton#Primary:hover {{
    background: {ACCENT_BRIGHT};
    border-color: {ACCENT_BRIGHT};
}}
QPushButton#Primary:pressed {{
    background: {ACCENT_DEEP};
    border-color: {ACCENT_DEEP};
}}
QPushButton#Primary:disabled {{
    background: {ACCENT_MUTED};
    border-color: {ACCENT_MUTED};
    color: {TEXT_FAINT};
}}

QPushButton#Danger:hover {{
    border-color: {ERR};
    color: {ERR};
}}

QPushButton#Ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {TEXT_MUTED};
    padding: 6px 10px;
}}
QPushButton#Ghost:hover {{
    background: {SURFACE_HOVER};
    color: {TEXT};
}}

QLineEdit {{
    background: {SURFACE_SUNKEN};
    border: 1px solid {BORDER};
    border-radius: {CONTROL_RADIUS}px;
    padding: 8px 11px;
    selection-background-color: {ACCENT};
    selection-color: {TEXT_ON_ACCENT};
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit:disabled {{
    color: {TEXT_FAINT};
}}
QLineEdit#ValueEdit {{
    font-family: {FONT_NUM};
    font-weight: 600;
    padding: 5px 8px;
}}

QComboBox {{
    background: {BG_RAISED};
    border: 1px solid {BORDER_STRONG};
    border-radius: {CONTROL_RADIUS}px;
    padding: 7px 12px;
    min-width: 130px;
}}
QComboBox:hover {{
    border-color: {BORDER_FOCUS};
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
QComboBox QAbstractItemView {{
    background: {BG_RAISED};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 5px;
    selection-background-color: {ACCENT_MUTED};
    selection-color: {TEXT};
    outline: none;
}}

QListWidget {{
    background: {SURFACE_SUNKEN};
    border: 1px solid {BORDER};
    border-radius: {CONTROL_RADIUS}px;
    padding: 5px;
    outline: none;
}}
QListWidget::item {{
    padding: 9px 11px;
    border-radius: 6px;
    color: {TEXT_MUTED};
}}
QListWidget::item:hover {{
    background: {SURFACE_HOVER};
    color: {TEXT};
}}
QListWidget::item:selected {{
    background: {ACCENT_MUTED};
    color: {TEXT};
}}

QPlainTextEdit {{
    background: {SURFACE_SUNKEN};
    border: 1px solid {BORDER};
    border-radius: {CONTROL_RADIUS}px;
    padding: 10px;
    font-size: {SIZE_LABEL}px;
    color: {TEXT_MUTED};
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

QWidget#StatusBar {{
    background: {SURFACE_SUNKEN};
    border-top: 1px solid {BORDER};
}}
QLabel#StatusText {{
    font-size: {SIZE_LABEL}px;
    color: {TEXT_MUTED};
}}
QLabel#StatMetric {{
    font-family: {FONT_NUM};
    font-size: {SIZE_LABEL}px;
    font-weight: 600;
    color: {TEXT_MUTED};
}}

QLabel#StatCaption {{
    font-size: {SIZE_CAPTION}px;
    font-weight: 600;
    letter-spacing: 0.8px;
    color: {TEXT_FAINT};
}}
QLabel#StatValue {{
    font-family: {FONT_NUM};
    font-size: 19px;
    font-weight: 620;
    color: {TEXT};
}}
QLabel#StatUnit {{
    font-size: {SIZE_LABEL}px;
    color: {TEXT_FAINT};
}}

QLabel#BannerText {{
    font-size: {SIZE_LABEL}px;
    color: {TEXT_MUTED};
}}

QCheckBox {{
    spacing: 9px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {BORDER_STRONG};
    background: {SURFACE_SUNKEN};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
"""
