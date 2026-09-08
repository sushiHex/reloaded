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
    kinds: dict[str, str] | None = None,
) -> Layout:
    windows: list[Window] = []
    # One repo, one tab, across the whole layout. Two titles can resolve to the
    # same cwd, and `deploy` launches a tab per entry: the duplicates become two
    # `claude --continue` in one directory, seconds apart, against a single
    # transcript. Nothing downstream catches it — merge_pinned only compares
    # against the previous layout, never a capture against itself.
    seen_cwds: set[str] = set()
    for wd in windows_data:
        tab_objs: list[Tab] = []
        for title in wd.get("titles", []):
            resolved = resolve_tab(title, title_map, live, repos_root)
            if resolved is None:
                continue
            cwd, low = resolved
            if norm(cwd) in seen_cwds:
                continue
            seen_cwds.add(norm(cwd))
            # A cwd absent from `kinds` captures as Claude Code, which is what
            # every layout written before a second kind existed meant. The
            # command is read off the live process so the tab comes back with
            # the flags it was actually running.
            tab_objs.append(Tab(
                cwd=cwd,
                title=title,
                low_confidence=low,
                agent=(kinds or {}).get(norm(cwd), "claude"),
                command=discover.session_command(live[norm(cwd)]),
            ))
        if not tab_objs:
            continue
        windows.append(
            Window(
                monitor=wd.get("monitor", ""),
                rect=list(wd.get("rect", [0, 0, 1168, 624])),
                state=wd.get("state", "normal"),
                dpi=int(wd.get("dpi", 96)),
                inset=list(wd.get("inset", [0, 0, 0, 0])),
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

    # A pin survives the session it was launched into. A capture taken while a
    # pinned repo is running sees an ordinary running tab, and skipping it here
    # dropped `pinned` on the floor: the pin lasted exactly until its first
    # successful launch, and the repo vanished for good the next time the
    # session was closed. Re-mark instead of skip.
    fresh_by_cwd = {norm(t.cwd): t for w in fresh.windows for t in w.tabs}
    for old_window in previous.windows:
        for tab in old_window.tabs:
            if tab.pinned:
                running = fresh_by_cwd.get(norm(tab.cwd))
                if running is not None:
                    running.pinned = True

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
                Tab(
                    cwd=tab.cwd,
                    title=tab.title,
                    low_confidence=tab.low_confidence,
                    pinned=True,
                    # Carried across, not rebuilt. A pinned tab is by
                    # definition not running, so nothing can re-derive these -
                    # dropping them would silently revert a pinned Codex tab to
                    # Claude, and the reconcile that rebuilds this runs every
                    # five minutes.
                    agent=tab.agent,
                    command=tab.command,
                )
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
    # Which kind each live session is. A separate scan rather than a richer
    # live_sessions() because nine call sites depend on that returning
    # cwd -> pid.
    kinds = discover.live_agents()

    windows_data: list[dict] = []
    for hwnd in win32.list_wt_windows():
        rect, state, device, dpi = win32.get_geometry(hwnd)
        windows_data.append(
            {
                "rect": rect,
                "state": state,
                "monitor": device,
                "dpi": dpi,
                # Measured per window: the invisible border is the window's own
                # frame, not a system constant derivable from dpi. clamp_rect
                # needs it to leave a flush-snapped window alone.
                "inset": win32.frame_inset(hwnd),
                "titles": tabs.tab_titles(hwnd),
            }
        )

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = build_layout(windows_data, monitors, title_map, live, repos_root,
                         now_iso, kinds=kinds)
    return merge_pinned(fresh, previous)
