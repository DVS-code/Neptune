"""Keeps an overlay window positioned over, and stacked above, the game."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

WINDOWS = sys.platform == 'win32'

GWL_EXSTYLE = -20
GWLP_HWNDPARENT = -8
GA_ROOT = 2
GW_HWNDPREV = 3

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOSENDCHANGING = 0x0400
HWND_TOP = 0

TITLE_HINT = 'forza horizon'

if WINDOWS:
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.UINT]
    user32.GetForegroundWindow.restype = wintypes.HWND

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _set_window_long = user32.SetWindowLongPtrW
        _get_window_long = user32.GetWindowLongPtrW
        _set_window_long.restype = ctypes.c_longlong
        _set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
        _get_window_long.restype = ctypes.c_longlong
        _get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
    else:
        _set_window_long = user32.SetWindowLongW
        _get_window_long = user32.GetWindowLongW
else:
    user32 = None


class Rect:
    """A client area in screen coordinates."""

    __slots__ = ('x', 'y', 'width', 'height')

    def __init__(self, x: int = 0, y: int = 0, width: int = 0, height: int = 0):
        self.x = x
        self.y = y
        self.width = width
        self.height = height

    @property
    def valid(self) -> bool:
        return self.width > 0 and self.height > 0

    def __eq__(self, other) -> bool:
        return (isinstance(other, Rect) and self.x == other.x and self.y == other.y
                and self.width == other.width and self.height == other.height)


def client_rect(hwnd) -> Rect:
    """The window's drawable area, in screen coordinates."""
    if not (WINDOWS and hwnd):
        return Rect()
    bounds = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(bounds)):
        return Rect()
    top_left = wintypes.POINT(bounds.left, bounds.top)
    bottom_right = wintypes.POINT(bounds.right, bounds.bottom)
    if not user32.ClientToScreen(hwnd, ctypes.byref(top_left)):
        return Rect()
    if not user32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
        return Rect()
    return Rect(top_left.x, top_left.y,
                bottom_right.x - top_left.x, bottom_right.y - top_left.y)


def find_game_window(pid: int | None = None, exclude=None):
    """The game's main window, preferring a match on its process id."""
    if not WINDOWS:
        return None

    best = [None, 0]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _param):
        if exclude and hwnd == exclude:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetAncestor(hwnd, GA_ROOT) != hwnd:
            return True

        matched = False
        if pid:
            owner = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            matched = owner.value == pid
        else:
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            matched = TITLE_HINT in (title.value or '').lower()

        if matched:
            bounds = client_rect(hwnd)
            area = bounds.width * bounds.height
            if bounds.valid and area > best[1]:
                best[0] = hwnd
                best[1] = area
        return True

    try:
        user32.EnumWindows(visit, 0)
    except Exception:
        return None
    return best[0]


class GameWindowTracker:
    """Ties one overlay window to the game window."""

    def __init__(self, overlay_hwnd: int, pid: int | None = None):
        self.overlay_hwnd = overlay_hwnd
        self.pid = pid
        self.game_hwnd = None
        self.rect = Rect()
        self._active = False

    def make_overlay(self, click_through: bool = True) -> None:
        """Keep the overlay out of the taskbar and set whether clicks pass through."""
        if not (WINDOWS and self.overlay_hwnd):
            return
        try:
            style = _get_window_long(self.overlay_hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            if click_through:
                style |= WS_EX_TRANSPARENT
            else:
                style &= ~WS_EX_TRANSPARENT
            _set_window_long(self.overlay_hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    def set_click_through(self, enabled: bool) -> None:
        """Let clicks pass to the game, or capture them so the gauge can be dragged."""
        self.make_overlay(click_through=enabled)

    def attach(self) -> bool:
        """Find the game window and take ownership of it."""
        if not WINDOWS:
            return False
        self.game_hwnd = find_game_window(self.pid, exclude=self.overlay_hwnd)
        if not self.game_hwnd:
            return False
        self._active = True
        self._take_ownership(self.game_hwnd)
        self.sync()
        return True

    def detach(self) -> None:
        self._active = False
        self.game_hwnd = None

    def _take_ownership(self, game_hwnd) -> None:
        """Owned windows float above their owner and follow it when minimised.

        Setting the owner briefly activates the overlay, so the foreground is
        handed straight back or the game would treat it as losing focus.
        """
        if not game_hwnd or game_hwnd == self.overlay_hwnd:
            return

        previous = None
        try:
            previous = user32.GetForegroundWindow()
        except Exception:
            pass

        try:
            _set_window_long(self.overlay_hwnd, GWLP_HWNDPARENT, game_hwnd)
        except Exception:
            pass

        self._restore_foreground(previous)

    def _restore_foreground(self, previous) -> None:
        if not (WINDOWS and previous) or previous == self.overlay_hwnd:
            return
        try:
            if user32.GetForegroundWindow() != self.overlay_hwnd:
                return
            this_thread = user32.GetCurrentThreadId()
            other_thread = user32.GetWindowThreadProcessId(previous, None)
            attached = user32.AttachThreadInput(this_thread, other_thread, True)
            try:
                user32.SetForegroundWindow(previous)
            finally:
                if attached:
                    user32.AttachThreadInput(this_thread, other_thread, False)
        except Exception:
            pass

    def sync(self) -> Rect:
        """Track the game's area and keep the overlay stacked above it."""
        if not (WINDOWS and self.overlay_hwnd):
            return self.rect

        if not self.game_hwnd or not user32.IsWindow(self.game_hwnd):
            found = find_game_window(self.pid, exclude=self.overlay_hwnd)
            if not found:
                self.game_hwnd = None
                return self.rect
            self.game_hwnd = found
            self._take_ownership(found)

        bounds = client_rect(self.game_hwnd)
        if not bounds.valid:
            return self.rect

        self.rect = bounds
        self.restack()
        return bounds

    def restack(self) -> None:
        """Insert the overlay directly above the game."""
        if not (WINDOWS and self._active and self.overlay_hwnd and self.game_hwnd):
            return
        try:
            above = user32.GetWindow(self.game_hwnd, GW_HWNDPREV)
            if above == self.overlay_hwnd:
                return
            insert_after = HWND_TOP if (not above or above == self.overlay_hwnd) else above
            user32.SetWindowPos(self.overlay_hwnd, insert_after, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                                | SWP_NOSENDCHANGING)
        except Exception:
            pass

    def anchor(self, relative_x: float, relative_y: float,
               width: int, height: int) -> tuple[int, int] | None:
        """Map a position inside the game's area to screen coordinates.

        ⚠️ The fraction is measured against the DRAGGABLE SPAN (`width - overlay_width`), not the
        full width, because that is what `GaugeOverlay._store_position` divides by. Using the full
        width here scaled every saved position up, so the clamp below pinned the gauge to the right
        and bottom edges — the "gauge sticks to the side of the screen" bug. The two functions must
        use the same units or the round-trip drifts.
        """
        bounds = self.rect
        if not bounds.valid:
            return None
        span_x = max(0, bounds.width - width)
        span_y = max(0, bounds.height - height)
        x = int(bounds.x + relative_x * span_x)
        y = int(bounds.y + relative_y * span_y)
        x = max(bounds.x, min(x, bounds.x + bounds.width - width))
        y = max(bounds.y, min(y, bounds.y + bounds.height - height))
        return x, y

    def game_is_foreground(self) -> bool:
        if not (WINDOWS and self.game_hwnd):
            return False
        try:
            foreground = user32.GetForegroundWindow()
            return foreground in (self.game_hwnd, self.overlay_hwnd)
        except Exception:
            return False
