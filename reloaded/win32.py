"""Win32 geometry via ctypes. Reads and writes Windows Terminal window placement."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from .layout import Monitor

_u32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    _shcore = ctypes.WinDLL("shcore", use_last_error=True)
except OSError:  # pragma: no cover - shcore exists on Win8.1+
    _shcore = None

# Per-monitor DPI v2. Declared at import, before anything else can set it.
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def _declare_dpi_aware() -> None:
    """Pin the process to physical-pixel coordinates.

    Without this, Windows virtualizes coordinates for a DPI-unaware process and
    every rect comes back divided by the display scale. That alone would be
    survivable, but `uiautomation` sets DPI awareness when it is imported — so
    reading one window's geometry before importing it and another's after
    yields two different coordinate systems in a single capture. Observed on a
    150%-scaled display: the same window measured 1168x624 and then 1752x936.
    Declaring it here, at import of the lowest-level module, makes the
    coordinate system deterministic regardless of import order.
    """
    try:
        _u32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        return
    except Exception:
        pass
    try:  # Windows 8.1 fallback: PROCESS_PER_MONITOR_DPI_AWARE
        if _shcore is not None:
            _shcore.SetProcessDpiAwareness(2)
            return
    except Exception:
        pass
    try:  # Vista+ fallback: system-DPI aware
        _u32.SetProcessDPIAware()
    except Exception:
        pass


_declare_dpi_aware()

WT_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"

SW_NORMAL = 1
SW_MINIMIZED = 2
SW_MAXIMIZED = 3
_STATE_FROM_SHOWCMD = {SW_NORMAL: "normal", SW_MINIMIZED: "minimized", SW_MAXIMIZED: "maximized"}

MONITOR_DEFAULTTONEAREST = 2


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", POINT),
        ("ptMaxPosition", POINT),
        ("rcNormalPosition", RECT),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


_ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM
)


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _u32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
    return buf.value


def window_title(hwnd: int) -> str:
    n = _u32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(wintypes.HWND(hwnd), buf, n + 1)
    return buf.value


def list_wt_windows() -> list[int]:
    """Visible Windows Terminal top-level windows, in Z-order as EnumWindows yields them."""
    found: list[int] = []

    @_ENUMPROC
    def _cb(hwnd, _lparam):
        if _u32.IsWindowVisible(hwnd) and _class_name(hwnd).upper() == WT_CLASS:
            found.append(int(hwnd))
        return True

    _u32.EnumWindows(_cb, 0)
    return found


def _dpi_for_monitor(hmon) -> int:
    if _shcore is None:
        return 96
    dx, dy = wintypes.UINT(), wintypes.UINT()
    try:
        _shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))
        return int(dx.value) or 96
    except Exception:
        return 96


def list_monitors() -> list[Monitor]:
    out: list[Monitor] = []

    @_MONITORENUMPROC
    def _cb(hmon, _hdc, _rect, _lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if _u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            out.append(
                Monitor(
                    device=mi.szDevice,
                    primary=bool(mi.dwFlags & 1),
                    work=[mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom],
                    dpi=_dpi_for_monitor(hmon),
                )
            )
        return True

    _u32.EnumDisplayMonitors(None, None, _cb, 0)
    return out


def get_geometry(hwnd: int) -> tuple[list[int], str, str, int]:
    """Return (rect, state, monitor_device, dpi).

    Uses GetWindowPlacement rather than GetWindowRect so the *restored* rect is
    preserved even while the window is maximized — that is what makes it possible
    to restore both the maximized state and the size it would return to.
    """
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    _u32.GetWindowPlacement(wintypes.HWND(hwnd), ctypes.byref(wp))
    r = wp.rcNormalPosition
    rect = [r.left, r.top, r.right - r.left, r.bottom - r.top]
    state = _STATE_FROM_SHOWCMD.get(wp.showCmd, "normal")

    hmon = _u32.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
    mi = MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
    device = ""
    if _u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        device = mi.szDevice
    return rect, state, device, _dpi_for_monitor(hmon)


def set_geometry(hwnd: int, rect: list[int], state: str = "normal") -> bool:
    """Place a window at an exact pixel rect, then apply maximized/minimized state.

    Re-checks the window class before touching anything. A destroyed HWND
    fails `GetWindowPlacement` on its own, but a *reused* one — Windows can
    hand the same numeric HWND to an unrelated window once the original
    closes — would pass that call while no longer being a Windows Terminal
    window at all. The gap between identifying a just-launched window and
    this call being reached (up to ~2.5s when disambiguating between several
    candidates in deploy.launch_window) is enough for that to happen in
    principle, so this must not move/resize whatever the HWND now refers to.
    """
    if _class_name(hwnd).upper() != WT_CLASS:
        return False

    x, y, w, h = rect
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    if not _u32.GetWindowPlacement(wintypes.HWND(hwnd), ctypes.byref(wp)):
        return False
    wp.rcNormalPosition = RECT(x, y, x + w, y + h)
    # Always restore to normal first so rcNormalPosition takes effect, then
    # apply the target state — setting showCmd directly to maximized/minimized
    # in one call ignores the new rect.
    wp.showCmd = SW_NORMAL
    ok = bool(_u32.SetWindowPlacement(wintypes.HWND(hwnd), ctypes.byref(wp)))
    if ok and state == "maximized":
        _u32.ShowWindow(wintypes.HWND(hwnd), SW_MAXIMIZED)
    elif ok and state == "minimized":
        _u32.ShowWindow(wintypes.HWND(hwnd), SW_MINIMIZED)
    return ok
