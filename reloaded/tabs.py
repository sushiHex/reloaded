"""Windows Terminal tab enumeration via UI Automation.

WT tabs are not Win32 windows — the whole terminal is a single XAML surface —
so the tab strip is only reachable through UIA. The window title reflects the
*active* tab alone, which is why this is required rather than optional.
"""
from __future__ import annotations

import concurrent.futures
import sys

from . import win32
from .discover import strip_glyph

# list_tab_items runs on a 5-minute scheduler forever via the reconcile task
# (through capture.py's tab_titles) and is also on down/restart's interactive
# path - either way, one wedged window must be skipped, not hang the caller
# for however long the underlying COM call takes to give up (which can be
# minutes, or effectively indefinite for a truly unresponsive process).
TAB_ITEMS_TIMEOUT_SECONDS = 5.0


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


def _list_tab_items_uia(hwnd: int) -> list[tuple[str, object]]:
    """The actual UIA walk - see list_tab_items for the timeout wrapper.

    Tree shape (verified against WT 1.24):
        CASCADIA_HOSTING_WINDOW_CLASS
          -> TabView       (AutomationId="TabView")
            -> TabListView (AutomationId="TabListView")
              -> TabItemControl[]  (one per tab, in left-to-right order)

    Keeps the control reference alongside the title so a caller that wants to
    act on a specific tab (select it, ...) does not need a second UIA query
    racing a tab order that may have changed in between.
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

    items: list[tuple[str, object]] = []
    for item in strip.GetChildren():
        name = item.Name or ""
        if not name:
            # Fall back to the header text block when the item exposes no name.
            header = item.Control(searchDepth=8, AutomationId="HeaderTextBlock")
            if header.Exists(maxSearchSeconds=0):
                name = header.Name or ""
        cleaned = strip_glyph(name)
        if cleaned:
            items.append((cleaned, item))
    return items


def list_tab_items(
    hwnd: int, timeout: float = TAB_ITEMS_TIMEOUT_SECONDS
) -> list[tuple[str, object]]:
    """Ordered (cleaned_title, TabItemControl) pairs for one WT window,
    bounded by a real timeout.

    The underlying UIA COM calls (ControlFromHandle, GetFirstChildControl/
    GetNextSiblingControl, the .Name property getter) do not consult
    uiautomation's own search-timeout setting - verified directly against
    the library source - so nothing bounds them without this. Running the
    walk in a worker thread is what makes an external timeout possible; a
    genuine failure (not a hang) still propagates normally via
    future.result(), matching this function's documented "raises on UIA
    failure" contract. The pool is never joined: a thread stuck on a hung
    COM call cannot be killed from Python, so waiting for it would defeat
    the point of timing out. It is abandoned and dies with the process -
    every caller here is a short-lived CLI invocation, not a long-running
    server, so this does not accumulate.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_list_tab_items_uia, hwnd)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        print(
            f"[reloaded] tab read timed out after {timeout:.0f}s on window 0x{hwnd:X} — "
            "skipping it this cycle",
            file=sys.stderr,
        )
        return []
    finally:
        executor.shutdown(wait=False)


def tab_titles(hwnd: int) -> list[str]:
    """Ordered, glyph-stripped tab titles for one Windows Terminal window."""
    return [title for title, _item in list_tab_items(hwnd)]


def select_tab(hwnd: int, tab_item) -> bool:
    """Select `tab_item` and bring its window to the foreground so it can
    receive real keyboard input. Returns whether the window actually ended up
    foreground (see win32.set_foreground) - SendKeys goes to whatever has OS
    focus, not to whatever UIA element was merely selected, so a caller must
    not send keystrokes on a False return.

    Known gap: this verifies which *window* has focus, not which *tab* is
    active within it. Select() is best-effort (swallowed on failure below);
    if it silently fails while a different tab in the same window is
    already active, keystrokes go to that tab instead. The failure mode
    stays bounded either way - the caller polls for the intended session's
    pid and reports a timeout if it never reacts - but this does not
    guarantee the keystrokes landed on the intended tab specifically.
    """
    try:
        tab_item.GetSelectionItemPattern().Select()
    except Exception:
        pass
    return win32.set_foreground(hwnd)


def send_exit_keystrokes() -> None:
    """Type '/exit' + Enter into whatever terminal currently has focus."""
    import uiautomation as auto

    auto.SendKeys("/exit{Enter}")
