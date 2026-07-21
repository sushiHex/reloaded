"""Windows Terminal tab enumeration via UI Automation.

WT tabs are not Win32 windows — the whole terminal is a single XAML surface —
so the tab strip is only reachable through UIA. The window title reflects the
*active* tab alone, which is why this is required rather than optional.
"""
from __future__ import annotations

from .discover import strip_glyph


class UIAUnavailable(RuntimeError):
    """Raised when the uiautomation package or the WT tab tree is unreachable."""


def install_hint() -> str:
    """User-facing guidance for a UIAUnavailable failure.

    Shared by every capture_live() call site so a fresh install without the
    uiautomation package gets a clear one-line fix instead of a raw
    traceback — a missing psutil already degrades gracefully (0 live
    sessions); this is the other required dependency and previously did not.
    """
    return "Fix: python -m pip install uiautomation"


def tab_titles(hwnd: int) -> list[str]:
    """Ordered, glyph-stripped tab titles for one Windows Terminal window.

    Tree shape (verified against WT 1.24):
        CASCADIA_HOSTING_WINDOW_CLASS
          -> TabView       (AutomationId="TabView")
            -> TabListView (AutomationId="TabListView")
              -> TabItemControl[]  (one per tab, in left-to-right order)
    """
    try:
        import uiautomation as auto
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise UIAUnavailable("uiautomation is not installed") from exc

    root = auto.ControlFromHandle(hwnd)
    if root is None:
        raise UIAUnavailable(f"no UIA control for hwnd 0x{hwnd:X}")

    strip = root.Control(searchDepth=20, AutomationId="TabListView")
    if not strip.Exists(maxSearchSeconds=2):
        raise UIAUnavailable(f"TabListView not found for hwnd 0x{hwnd:X}")

    titles: list[str] = []
    for item in strip.GetChildren():
        name = item.Name or ""
        if not name:
            # Fall back to the header text block when the item exposes no name.
            header = item.Control(searchDepth=8, AutomationId="HeaderTextBlock")
            if header.Exists(maxSearchSeconds=0):
                name = header.Name or ""
        cleaned = strip_glyph(name)
        if cleaned:
            titles.append(cleaned)
    return titles
