"""Named icons, drawn from the Windows 11 icon font with a safe fallback.

Every feature asks for an icon by NAME (`icons.get('engine')`) rather than pasting a
codepoint into its module, so the whole set can be seen and changed in one place.

⚠️ **The icon font is not everywhere.** `Segoe Fluent Icons` ships with Windows 11; on
Windows 10 it is absent and the codepoints below land in the Private Use Area, which
renders as a tofu box — nine identical empty rectangles down the sidebar. `available()`
checks for the font once at startup and `get()` returns the old geometric shape instead
when it is missing. That check is why this module exists at all.
"""
from __future__ import annotations

from neptune.ui import theme as T

# name -> (Fluent Icons codepoint, fallback glyph for machines without the font)
#
# Codepoints were chosen by rendering the candidates at real sidebar size and picking by
# eye, not by trusting the published names — several "obvious" names are the wrong
# picture. The fallbacks are the shapes Neptune shipped with before the icon font.
ICONS: dict[str, tuple[str, str]] = {
    'engine': ('', '◆'),       # pulse in a frame — the torque curve
    'turbo': ('', '●'),        # speedometer needle — boost
    'suspension': ('', '▬'),   # sliders — ride height
    'dragy': ('', '⏱'),        # stopwatch
    'boostgauge': ('', '◒'),   # filled dial — a gauge face
    'car': ('', '▤'),          # car
    'tunes': ('', '▲'),        # saved chart — a tune
    'presets': ('', '▣'),      # folder
    'settings': ('', '○'),     # gear
}

_available: bool | None = None


def available() -> bool:
    """True when the Windows 11 icon font is installed.

    Cached: this is asked once per nav item at build time, and a font-database lookup is
    not free. Any failure answers False, so a machine that cannot be queried gets the
    fallback shapes rather than an exception during window construction.
    """
    global _available
    if _available is None:
        try:
            from PySide6.QtGui import QFontDatabase
            _available = T.FONT_ICON in QFontDatabase.families()
        except Exception:
            _available = False
    return _available


def get(name: str) -> str:
    """The icon for a feature, or its fallback shape when the icon font is missing."""
    entry = ICONS.get(name)
    if entry is None:
        return ''
    glyph, fallback = entry
    return glyph if available() else fallback


# A single concrete family, not a CSS stack — this is handed to QFont, which takes one
# family name and does its own substitution if it is missing.
FALLBACK_FAMILY = 'Segoe UI'


def font_family(name: str = '') -> str:
    """The family an icon must be drawn in.

    A fallback shape is an ordinary Unicode character and has to be drawn in a text font;
    asking for it in the icon font would produce a box on the very machines the fallback
    exists to serve.
    """
    return T.FONT_ICON if available() else FALLBACK_FAMILY
