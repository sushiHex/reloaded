"""Windows Terminal tab enumeration via UI Automation.

WT tabs are not Win32 windows — the whole terminal is a single XAML surface —
so the tab strip is only reachable through UIA. The window title reflects the
*active* tab alone, which is why this is required rather than optional.
"""
from __future__ import annotations

import sys
import threading

from . import win32
from .discover import strip_glyph

# How long to let Claude Code's slash-command menu settle after `/exit` is
# typed, before Enter is pressed. Measured, not guessed: reading the terminal
# buffer back mid-keystroke showed the menu still filtering with `/exit`
# unsent, and 1.2s was the interval at which the session exited first time.
MENU_SETTLE_SECONDS = 1.2

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
        # Every tab is returned, including one whose title cleans to "" (a tab
        # still rendering, or a control exposing no name — the case the
        # HeaderTextBlock fallback above exists for). Dropping those here made
        # them invisible to `WindowPlan.total_tabs`, so `down` counted fewer
        # tabs than the window held and closed it on top of one. Callers that
        # want only identifiable tabs filter; callers that need a true count
        # get one.
        items.append((strip_glyph(name), item))
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
    genuine failure (not a hang) still propagates normally, matching this
    function's documented "raises on UIA failure" contract.

    The thread is a *daemon*, and that detail is load-bearing. A thread stuck
    on a hung COM call cannot be killed from Python, so the timeout has to
    abandon it - but abandoning it is not what ThreadPoolExecutor does.
    `executor.shutdown(wait=False)` returns immediately and then the
    interpreter joins the worker anyway: `threading._shutdown()` runs before
    any atexit hook and joins every live non-daemon thread. Measured: a 5s
    timeout fired "as designed" and the process still sat for the full length
    of the hung call before exiting. So the timeout bounded the call but not
    the command, and one wedged window made `down` hang silently at exit and
    burned the reconcile task's whole kill limit every cycle. A daemon thread
    is exempt from that join, which is what the timeout always meant.
    """
    box: dict[str, object] = {}

    def _walk() -> None:
        try:
            box["value"] = _list_tab_items_uia(hwnd)
        except BaseException as exc:  # re-raised on the calling thread below
            box["error"] = exc

    worker = threading.Thread(
        target=_walk, name=f"reloaded-uia-0x{hwnd:X}", daemon=True
    )
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        print(
            f"[reloaded] tab read timed out after {timeout:.0f}s on window 0x{hwnd:X} — "
            "skipping it this cycle",
            file=sys.stderr,
        )
        return []
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("value", [])  # type: ignore[return-value]


def tab_titles(hwnd: int) -> list[str]:
    """Ordered, glyph-stripped tab titles for one Windows Terminal window.

    Unnameable tabs are omitted — they identify nothing. Use `list_tab_items`
    directly when you need the window's true tab count.
    """
    return [title for title, _item in list_tab_items(hwnd) if title]


def close_tab(tab_item) -> bool:
    """Close one tab by invoking its own Close Tab button.

    Needs no focus, no foreground and no synthesized keystroke, so unlike
    send_quit_keystrokes it cannot land on the wrong tab - the failure mode
    select_tab exists to guard against. Verified by hand against a wedged tab
    that Ctrl+D would not close, with three live sessions in the same window
    left untouched.

    For a tab that must be GONE and whose session has already ended. A live
    session is still asked to quit through its own UI, so that it gets the
    chance to shut down cleanly rather than have its tab pulled out from
    under it.
    """
    try:
        children = tab_item.GetChildren()
    except Exception:
        return False

    for child in children:
        # The control type matters as much as the name: a label or tooltip
        # reading "Close Tab" would otherwise be invoked instead.
        if getattr(child, "ControlTypeName", "") != "ButtonControl":
            continue
        if "close" not in (getattr(child, "Name", "") or "").lower():
            continue
        try:
            child.GetInvokePattern().Invoke()
            return True
        except Exception:
            return False
    return False


def select_tab(hwnd: int, tab_item, attempts: int = 10, settle: float = 0.3) -> bool:
    """Select `tab_item`, confirm it actually took, and foreground its window.

    Returns False unless BOTH happened, because SendKeys goes to whatever has
    OS focus rather than to whatever UIA element was asked for. A caller that
    cannot confirm which tab it is about to type into must not type.

    Confirming the tab is the whole point, and used to be the gap. Select() was
    best-effort and swallowed, with only the window's foreground state checked,
    so a silently failed Select() sent every keystroke to whichever tab was
    already active. That is not theoretical: a tab read back selected=False
    immediately after this returned True, and the same {Enter} that had done
    nothing three times running landed the moment selection was verified. It
    accounts for every unexplained /exit failure in this package - a target
    that was not the active tab never received anything, while the tab that
    *was* active silently did.
    """
    import time

    for _attempt in range(attempts):
        try:
            pattern = tab_item.GetSelectionItemPattern()
            if pattern.IsSelected:
                break
            pattern.Select()
        except Exception:
            # No selection pattern at all: nothing about this tab can be
            # confirmed, and typing blind is exactly what this prevents.
            return False
        time.sleep(settle)
    else:
        return False

    return win32.set_foreground(hwnd)


def send_exit_keystrokes(*, dismiss_overlay: bool = True) -> None:
    """Type '/exit' + Enter into whatever terminal currently has focus.

    ``dismiss_overlay=True`` (the default, meant for a tab's first exit
    attempt) sends Escape first to dismiss a transient overlay Claude Code
    may be showing on refocus - specifically its "away summary" recap,
    shown after returning to a session that's been idle long enough to
    trigger one. Without this, the very next keystroke (which is exactly
    what foregrounding the window here triggers) can get consumed
    dismissing that overlay instead of reaching the actual prompt, leaving
    /exit never typed at all - confirmed by a transcript ending in an
    away_summary system event with no trace of /exit ever being received.
    Escape is a safe no-op when there is nothing to dismiss.

    ``dismiss_overlay=False`` (used for teardown's mid-timeout retry) skips
    the Escape. Claude Code's OTHER documented /exit blocker - its
    confirmation when background agents are still running - is a dialog
    Escape would cancel rather than answer; sending it on a retry could
    dismiss exactly the confirmation the retry exists to get past instead
    of confirming it, undoing its own purpose. By retry time an
    away-summary overlay from regaining focus would already have been
    handled by the first attempt, so there is nothing left that Escape
    should still need to dismiss.
    """
    import time

    import uiautomation as auto

    if dismiss_overlay:
        auto.SendKeys("{Esc}")
        time.sleep(0.1)
    # Typed and submitted separately, with a pause. Typing `/` opens Claude
    # Code's slash-command menu, and sending "/exit{Enter}" as one string puts
    # Enter about ten milliseconds after the final character - while that menu
    # is still filtering, where it does not submit the command. Diagnosed by
    # reading the terminal buffer back between the two keystrokes: the menu was
    # on screen with `/exit` sitting unsent in the prompt. The same keys with a
    # pause exited the session first time. This is why `down` and `restart`
    # timed out on session after session while appearing to type correctly.
    auto.SendKeys("/exit")
    time.sleep(MENU_SETTLE_SECONDS)
    auto.SendKeys("{Enter}")
