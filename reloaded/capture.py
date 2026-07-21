"""Compose the OS readers into a saved Layout."""
from __future__ import annotations

import concurrent.futures
import datetime
import os
import sys

from . import discover, tabs, win32
from .layout import LAYOUT_VERSION, Layout, Monitor, Tab, Window
from .paths import norm

# capture_live runs on a 5-minute scheduler forever; one wedged window must
# degrade a single capture, not hang the whole reconcile task for however
# long the underlying COM call takes to give up (which can be minutes, or
# effectively indefinite for a truly unresponsive process).
TAB_TITLES_TIMEOUT_SECONDS = 5.0


def _tab_titles_bounded(hwnd: int, timeout: float = TAB_TITLES_TIMEOUT_SECONDS) -> list[str]:
    """Read one window's tab titles, bounded by a real timeout.

    tabs.tab_titles() makes raw UIA COM calls that do not consult
    uiautomation's own search-timeout setting — verified directly against the
    library source: ControlFromHandle, GetFirstChildControl/
    GetNextSiblingControl, and the .Name property getter are all
    unparameterized COM calls, so nothing in the library itself bounds them.
    Running the call in a worker thread is what makes an external timeout
    possible; a genuine failure (not a hang) still propagates normally via
    future.result(), matching tab_titles' documented "raises on UIA failure"
    contract. The pool is never joined: a thread stuck on a hung COM call
    cannot be killed from Python, so waiting for it would defeat the point of
    timing out. It is abandoned and dies with the process — each capture is a
    fresh, short-lived process, so this does not accumulate.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(tabs.tab_titles, hwnd)
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


def resolve_tab(
    title: str,
    title_map: dict[str, str],
    live: dict[str, int],
    repos_root: str,
) -> tuple[str, bool] | None:
    """Resolve a tab title to a repo cwd.

    Returns (cwd, low_confidence), or None when the tab is not a live Claude
    session. Requiring a live session is what enforces "Claude tabs only" —
    plain shells and dashboards resolve to nothing and are dropped.
    """
    candidate = title_map.get(title)
    if candidate and norm(candidate) in live:
        return candidate, False

    guess = os.path.join(repos_root, title)
    if norm(guess) in live:
        return guess, True

    return None


def build_layout(
    windows_data: list[dict],
    monitors: list[Monitor],
    title_map: dict[str, str],
    live: dict[str, int],
    repos_root: str,
    now_iso: str,
) -> Layout:
    windows: list[Window] = []
    for wd in windows_data:
        tab_objs: list[Tab] = []
        for title in wd.get("titles", []):
            resolved = resolve_tab(title, title_map, live, repos_root)
            if resolved is None:
                continue
            cwd, low = resolved
            tab_objs.append(Tab(cwd=cwd, title=title, low_confidence=low))
        if not tab_objs:
            continue
        windows.append(
            Window(
                monitor=wd.get("monitor", ""),
                rect=list(wd.get("rect", [0, 0, 1168, 624])),
                state=wd.get("state", "normal"),
                dpi=int(wd.get("dpi", 96)),
                tabs=tab_objs,
            )
        )
    return Layout(
        version=LAYOUT_VERSION,
        saved_ts=now_iso,
        monitors=list(monitors),
        windows=windows,
    )


def merge_pinned(fresh: Layout, previous: Layout | None) -> Layout:
    """Carry hand-added (pinned) tabs from the previous layout into a fresh capture.

    A capture only sees running sessions. Without this, the 5-minute reconcile
    would erase any repo the user added in the editor but has not launched yet.
    Fresh geometry and grouping always win — only the pinned tabs are rescued.
    """
    if previous is None:
        return fresh

    present = {norm(t.cwd) for w in fresh.windows for t in w.tabs}

    for position, old_window in enumerate(previous.windows):
        for tab in old_window.tabs:
            if not tab.pinned or norm(tab.cwd) in present:
                continue
            # Match by position: window identity is positional, and a stored id
            # would be stale for any window that shifted when another was removed.
            if position < len(fresh.windows):
                target = fresh.windows[position]
            elif fresh.windows:
                # KNOWN GAP (pre-existing, not yet decided): the window this tab
                # was pinned to no longer exists, so it lands in the first one —
                # possibly on a different monitor — with no warning. Dropping it
                # with a [warn] may be the better policy.
                target = fresh.windows[0]
            else:
                continue
            target.tabs.append(
                Tab(cwd=tab.cwd, title=tab.title, low_confidence=tab.low_confidence, pinned=True)
            )
            present.add(norm(tab.cwd))
    return fresh


def capture_live(repos_root: str, previous: Layout | None = None) -> Layout:
    """Snapshot the current arrangement. Raises if the UIA scan fails."""
    monitors = win32.list_monitors()
    live = discover.live_sessions()
    title_map = discover.title_to_cwd(discover.transcript_index())

    windows_data: list[dict] = []
    for hwnd in win32.list_wt_windows():
        rect, state, device, dpi = win32.get_geometry(hwnd)
        windows_data.append(
            {
                "rect": rect,
                "state": state,
                "monitor": device,
                "dpi": dpi,
                "titles": _tab_titles_bounded(hwnd),
            }
        )

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = build_layout(windows_data, monitors, title_map, live, repos_root, now_iso)
    return merge_pinned(fresh, previous)
