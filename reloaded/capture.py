"""Compose the OS readers into a saved Layout."""
from __future__ import annotations

import datetime
import os

from . import discover, tabs, win32
from .layout import LAYOUT_VERSION, Layout, Monitor, Tab, Window
from .paths import norm


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
                # The window this tab was pinned to no longer exists, so it
                # lands in the first remaining window instead — possibly on
                # a different monitor, silently.
                target = fresh.windows[0]
            else:
                continue
            target.tabs.append(
                Tab(cwd=tab.cwd, title=tab.title, low_confidence=tab.low_confidence, pinned=True)
            )
            present.add(norm(tab.cwd))
    return fresh


def capture_live(
    repos_root: str,
    previous: Layout | None = None,
    *,
    live: dict[str, int] | None = None,
    title_map: dict[str, str] | None = None,
) -> Layout:
    """Snapshot the current arrangement. Raises if the UIA scan fails.

    ``live``/``title_map`` let a caller that already computed them (restart
    reuses the same snapshot for both the capture and the teardown plan
    right after it) pass them straight through instead of paying for the
    psutil scan and the transcript-corpus walk a second time.
    """
    monitors = win32.list_monitors()
    if live is None:
        live = discover.live_sessions()
    if title_map is None:
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
                "titles": tabs.tab_titles(hwnd),
            }
        )

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = build_layout(windows_data, monitors, title_map, live, repos_root, now_iso)
    return merge_pinned(fresh, previous)
