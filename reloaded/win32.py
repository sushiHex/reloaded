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
WM_CLOSE = 0x0010


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

    For a "normal"-state window, rect is its actual current on-screen bounds
    (GetWindowRect). This matters specifically for a window Aero-Snapped to
    half a monitor: Windows reports showCmd as SW_SHOWNORMAL for a snapped
    window, indistinguishable from a genuinely floating one, but
    GetWindowPlacement's rcNormalPosition for it is the size the window
    would return to if *un*-snapped - not its current half-screen bounds.
    Capturing rcNormalPosition here would silently drop a snap-managed
    layout: the window comes back at some unrelated earlier float size and
    position instead of back in its snapped half, restored via
    SetWindowPlacement to a rect nobody currently sees the window at.
    Verified directly: on a real snapped 2x2 layout (two monitors, one
    window snapped to each half), rcNormalPosition reported the same
    generic non-half-monitor size for every window regardless of which
    half it was actually snapped to, while GetWindowRect correctly reported
    each one's real half-monitor bounds.

    For a maximized/minimized window, rect is still rcNormalPosition (via
    GetWindowPlacement) - the opposite problem applies there instead:
    GetWindowRect for a maximized window returns the full-screen covering
    rect, not the size it should restore to. rcNormalPosition is what makes
    it possible to restore both the maximized state and the size the window
    would return to if un-maximized.
    """
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    _u32.GetWindowPlacement(wintypes.HWND(hwnd), ctypes.byref(wp))
    state = _STATE_FROM_SHOWCMD.get(wp.showCmd, "normal")

    if state == "normal":
        r = RECT()
        _u32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    else:
        r = wp.rcNormalPosition
    rect = [r.left, r.top, r.right - r.left, r.bottom - r.top]

    hmon = _u32.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
    mi = MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
    device = ""
    if _u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        device = mi.szDevice
    return rect, state, device, _dpi_for_monitor(hmon)


_u32.GetForegroundWindow.restype = wintypes.HWND


def set_foreground(hwnd: int) -> bool:
    """Bring a window to the foreground and verify it actually happened.

    Re-checks the window class first, the same HWND-reuse guard
    set_geometry already applies: teardown's per-tab loop can take up to
    EXIT_TIMEOUT_SECONDS per holdout, real time for a just-closed window's
    HWND to be handed to an unrelated app - foregrounding, let alone typing
    into, that window would be wrong no matter what GetForegroundWindow
    reports.

    SetForegroundWindow can also be silently refused by Windows depending on
    which process last had input focus - the call succeeding is not proof
    the window is now frontmost. A caller about to send real keystrokes
    (which go to whatever has OS focus, not to whatever HWND was asked for)
    must know whether this actually worked before typing into what might be
    the wrong window entirely.
    """
    if _class_name(hwnd).upper() != WT_CLASS:
        return False
    _u32.SetForegroundWindow(wintypes.HWND(hwnd))
    # GetForegroundWindow.restype is set to HWND above so this compares
    # correctly regardless of the handle's sign bit - left as plain c_int
    # (ctypes' default), a HWND with bit 31 set would read back negative and
    # never equal the positive hwnd, always reporting failure.
    return _u32.GetForegroundWindow() == hwnd


def close_window(hwnd: int) -> bool:
    """Politely ask a window to close (WM_CLOSE) - the same signal its own
    title-bar X button sends. Does not force it: a window that ignores this
    (e.g. Windows Terminal's own "close all tabs?" prompt) stays open.

    Re-checks the window class first, same HWND-reuse rationale as
    set_foreground/set_geometry.
    """
    if _class_name(hwnd).upper() != WT_CLASS:
        return False
    return bool(_u32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0))


def _apply_geometry(hwnd: int, rect: list[int], state: str) -> bool:
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

    This is the immediate, one-shot placement only - it does not wait for or
    verify against Windows Terminal's own startup layout settling, which can
    still be in progress right when a freshly launched window is first
    detected and can clobber a placement applied into it shortly after. See
    `verify_and_fix_geometry`, a separate call a caller makes after its own
    wait (batched across every window in a deploy in `deploy.execute`,
    rather than paid here per window).
    """
    if _class_name(hwnd).upper() != WT_CLASS:
        return False
    return _apply_geometry(hwnd, rect, state)


def verify_and_fix_geometry(hwnd: int, rect: list[int], state: str) -> bool:
    """Re-check a placement `set_geometry` already applied, reapplying once
    if Windows Terminal's own startup layout clobbered it.

    Callers are expected to have already waited out WT's startup-settling
    window (see deploy.GEOMETRY_SETTLE_SECONDS) before calling this. Re-checks
    the window class again first: real time has passed since the original
    `set_geometry` call, so the same HWND-reuse hazard applies here too - a
    blind reapply with no re-check would defeat the guard `set_geometry`
    itself uses, and could move/resize a since-unrelated window.

    Only reapplies if the placement actually drifted (compares both the
    restored-position rect and the min/normal/maximized state read back from
    `get_geometry`), so a window that already stuck - the common case - is
    never touched again: a maximized/minimized window is never visibly
    re-transitioned, and a transient failure reading back an already-correct
    placement can never be reported as if the placement itself had failed.
    """
    if _class_name(hwnd).upper() != WT_CLASS:
        return False
    actual_rect, actual_state, _device, _dpi = get_geometry(hwnd)
    if actual_rect == rect and actual_state == state:
        return True
    return _apply_geometry(hwnd, rect, state)
