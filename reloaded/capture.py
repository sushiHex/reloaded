"""Compose the OS readers into a saved Layout."""
from __future__ import annotations

import datetime
import os
import re
from collections import Counter, deque

from . import agents, discover, tabs, win32
from .layout import LAYOUT_VERSION, Layout, Monitor, Tab, Window
from .paths import norm, tab_title


def resolve_tab(
    title: str,
    title_map: dict[str, str],
    live: dict[str, int],
    repos_root: str,
) -> tuple[str, bool] | None:
    """Resolve a tab title to a repo cwd.

    Returns (cwd, low_confidence), or None when the tab is not a live Claude
    session. Requiring a live session is what enforces "Claude tabs only" —
    plain shells and dashboards resolve to nothing and are dropped. A title's
    kind mark (agents.title_kind) is not part of the name it resolves by - but
    a recorded title is tried as it stands first, since a custom title could
    end in the same character, and one that is nothing but a mark names
    nothing.
    """
    bare = agents.title_kind(title)[0]
    if not bare:
        return None
    for name in dict.fromkeys((title, bare)):
        candidate = title_map.get(name)
        if candidate and norm(candidate) in live:
            return candidate, False

    guess = os.path.join(repos_root, bare)
    if norm(guess) in live:
        return guess, True

    return None


def _names(title: str, cwd: str) -> bool:
    """Whether `title` holds the name of directory `cwd` as a word of its own -
    not as part of a longer name, so "hermes-realtime" does not name `hermes`."""
    name = tab_title(cwd)
    return bool(name) and re.search(
        rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", title, re.IGNORECASE) is not None


def build_layout(
    windows_data: list[dict],
    monitors: list[Monitor],
    title_map: dict[str, str],
    live: dict[str, int],
    repos_root: str,
    now_iso: str,
    kinds: dict[str, str] | None = None,
    sessions: list | None = None,
    previous: Layout | None = None,
) -> Layout:
    """One tab per live session, in the window its tab is in.

    `sessions` (discover.sessions) places each session by its tab's shell, so
    a directory running Claude in one tab and Codex in another saves both.
    Keyed on the directory instead, the second was dropped - measured after a
    logon, three such directories each came back with one tab. Titles now only
    order a window's tabs. Without `sessions`, each live directory is one
    session of the kind `kinds` names, placed by title alone, as before.
    `previous` orders what the titles cannot (keep_saved_order).
    """
    pool = sorted(sessions if sessions is not None else [
        discover.Session(pid, cwd, (kinds or {}).get(cwd, agents.DEFAULT_KIND), 0.0, None)
        for cwd, pid in live.items()], key=lambda s: s.started)
    # Once each, across the whole layout: two titles can resolve to one
    # session, and a duplicate would launch it twice against one transcript.
    taken: set[int] = set()
    # Each window's titles, in strip order: (bare title, kind or None, cwd,
    # low_confidence), cwd None where the title names no live repo.
    placed: list[list[tuple]] = []
    for wd in windows_data:
        placed.append([])
        for title in wd.get("titles", []):
            resolved = resolve_tab(title, title_map, live, repos_root)
            placed[-1].append(agents.title_kind(title) + (resolved or (None, False)))
    # One session per title, so a window titled `retro, meta, retro` keeps
    # that order. A session whose window is known belongs to that window only.
    # A title whose mark names its kind takes a session of that kind or none,
    # and before any unmarked title in any window can take one: a session
    # whose window is unknown can answer titles in several.
    matches: dict[tuple[int, int], object] = {}
    for marked in (True, False):
        for w, wd in enumerate(windows_data):
            for i, (_bare, kind, cwd, _low) in enumerate(placed[w]):
                if cwd is None or (kind is not None) != marked:
                    continue
                fits = [s for s in pool if s.pid not in taken
                        and norm(s.cwd) == norm(cwd)
                        and s.hwnd in (None, wd.get("hwnd"))
                        and kind in (None, s.kind)]
                # One known to be in this window before one that could be in
                # any: taken first, the latter left this window's own session
                # to no title, and two windows were saved as one.
                match = min(fits, key=lambda s: s.hwnd is None, default=None)
                if match is not None:
                    taken.add(match.pid)
                    matches[w, i] = match
    # A tab opened by hand shows its agent's own title - Codex's reads
    # "retro | retro" - which names no repo, so its session went to the end of
    # the window, in start order (#77). Such a title keeps its place if it
    # names exactly one directory among the sessions in this window that no
    # title took. Unmarked titles only: a marked one names a kind, and is
    # that kind's or nothing. Not in resolve_tab, which teardown aims by: this
    # orders tabs, and is not good enough to act on.
    for w, wd in enumerate(windows_data):
        hwnd = wd.get("hwnd")
        left = [s for s in pool if s.pid not in taken and hwnd is not None and s.hwnd == hwnd]
        for i, (bare, kind, cwd, _low) in enumerate(placed[w]):
            if cwd is not None or kind is not None:
                continue
            named = {norm(s.cwd) for s in left if _names(bare, s.cwd)}
            match = next((s for s in left if s.pid not in taken and norm(s.cwd) in named),
                         None) if len(named) == 1 else None
            if match is not None:
                taken.add(match.pid)
                matches[w, i] = match
                placed[w][i] = (tab_title(match.cwd), None, match.cwd, False)
    windows: list[Window] = []
    for w, wd in enumerate(windows_data):
        hwnd = wd.get("hwnd")
        tab_objs: list[Tab] = []

        def add(session, cwd, title, low):
            taken.add(session.pid)
            # The command is read off the live process so the tab comes back
            # with the flags it was actually running.
            tab_objs.append(Tab(cwd=cwd, title=title, low_confidence=low,
                                agent=session.kind,
                                command=discover.session_command(session.pid)))

        # Placed by a title that does not say which kind it holds.
        unmarked = set()
        for i, (bare, kind, cwd, low) in enumerate(placed[w]):
            match = matches.get((w, i))
            if match is not None:
                if kind is None:
                    unmarked.add(len(tab_objs))
                add(match, cwd, bare, low)
        # In this window by its shell, under a title that names no repo.
        for s in pool:
            if s.pid not in taken and hwnd is not None and s.hwnd == hwnd:
                add(s, s.cwd, tab_title(s.cwd), False)
        keep_saved_order(tab_objs, previous, unmarked)
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


def keep_saved_order(tabs: list[Tab], previous: Layout | None,
                     movable: set[int]) -> None:
    """Put a window's same-titled tabs of one directory - those at `movable`,
    the places an unmarked title gave them - in the kind order the saved
    layout has for them, in place.

    Two unmarked tabs with one title cannot be told apart, so build_layout fills them
    in the order their agents started - and a capture every five minutes
    wrote that guess over the layout, undoing any correction made in the
    editor (#77). The saved order is no better evidence of where each tab is
    now, but it is the one a person can set and see kept. It is followed only
    from the one saved window most like this one, and only where that window
    holds the same title and directory with exactly these kinds; anywhere else
    start order stands.
    """
    def held(ts) -> Counter:
        return Counter((norm(t.cwd), t.agent) for t in ts)

    groups: dict[tuple[str, str], list[int]] = {}
    for i in sorted(movable):
        groups.setdefault((tabs[i].title, norm(tabs[i].cwd)), []).append(i)
    mixed = {key: places for key, places in groups.items()
             if len({tabs[i].agent for i in places}) > 1}
    if not mixed or previous is None:
        return
    # Windows are saved by position, which shifts, so the saved one is found
    # by what it holds - and not at all where two hold this much of it.
    here = held(tabs)
    scored = sorted(((sum((here & held(w.tabs)).values()), i)
                     for i, w in enumerate(previous.windows)), reverse=True)
    if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return
    window = previous.windows[scored[0][1]]
    for (title, cwd), places in mixed.items():
        saved = [t.agent for t in window.tabs if t.title == title and norm(t.cwd) == cwd]
        queues: dict[str, deque] = {}
        for i in places:
            queues.setdefault(tabs[i].agent, deque()).append(tabs[i])
        if Counter(saved) != Counter({k: len(q) for k, q in queues.items()}):
            continue
        for i, kind in zip(places, saved):
            tabs[i] = queues[kind].popleft()


def merge_pinned(fresh: Layout, previous: Layout | None) -> Layout:
    """Carry hand-added (pinned) tabs from the previous layout into a fresh capture.

    A capture only sees running sessions. Without this, the 5-minute reconcile
    would erase any repo the user added in the editor but has not launched yet.
    Fresh geometry and grouping always win — only the pinned tabs are rescued.
    """
    if previous is None:
        return fresh

    # By session - directory and kind - not by directory: a directory can hold
    # a Claude and a Codex tab, and a pinned Codex tab is not present because
    # its Claude neighbour is.
    def identity(t):
        return norm(t.cwd), t.agent or agents.DEFAULT_KIND

    present = {identity(t) for w in fresh.windows for t in w.tabs}

    # A pin survives the session it was launched into. A capture taken while a
    # pinned repo is running sees an ordinary running tab, and skipping it here
    # dropped `pinned` on the floor: the pin lasted exactly until its first
    # successful launch, and the repo vanished for good the next time the
    # session was closed. Re-mark instead of skip.
    fresh_by_identity = {identity(t): t for w in fresh.windows for t in w.tabs}
    for old_window in previous.windows:
        for tab in old_window.tabs:
            if tab.pinned:
                running = fresh_by_identity.get(identity(tab))
                if running is not None:
                    running.pinned = True

    for position, old_window in enumerate(previous.windows):
        for tab in old_window.tabs:
            if not tab.pinned or identity(tab) in present:
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
            present.add(identity(tab))
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
    # Every session, each placed in its window - not one per directory.
    found = discover.sessions()

    windows_data: list[dict] = []
    for hwnd in win32.list_wt_windows():
        items = tabs.list_tab_items(hwnd)
        if not items:
            # A tab read that timed out: no window has no tabs. Its sessions
            # would still be found by their shells, in start order, and saved
            # over the order they had (#77). Left out, they are missing, and
            # the save that would lose them is refused (_capture_shrinkage).
            continue
        rect, state, device, dpi = win32.get_geometry(hwnd)
        windows_data.append(
            {
                "hwnd": hwnd,
                "rect": rect,
                "state": state,
                "monitor": device,
                "dpi": dpi,
                # Measured per window: the invisible border is the window's own
                # frame, not a system constant derivable from dpi. clamp_rect
                # needs it to leave a flush-snapped window alone.
                "inset": win32.frame_inset(hwnd),
                "titles": [title for title, _item in items if title],
            }
        )

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = build_layout(windows_data, monitors, title_map, live, repos_root,
                         now_iso, sessions=found, previous=previous)
    return merge_pinned(fresh, previous)
