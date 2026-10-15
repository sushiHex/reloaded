"""Layout model: the saved arrangement of windows, tabs, and geometry."""
from __future__ import annotations

import json
import os
import pathlib
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from .agents import DEFAULT_KIND
from .paths import norm

LAYOUT_VERSION = 1


@dataclass
class Monitor:
    device: str
    primary: bool
    work: list[int]  # [left, top, right, bottom]
    dpi: int


@dataclass
class Tab:
    cwd: str
    title: str
    low_confidence: bool = False
    # A tab added by hand for a repo that is not currently open. The periodic
    # reconcile rebuilds the layout from live reality, so without this flag an
    # edit like "also launch `sample-repo` next time" would be erased within minutes.
    pinned: bool = False
    # Which agent CLI ran here. Absent from every layout written before a
    # second kind existed, so it defaults rather than being required - that is
    # what makes this a schema addition with no migration.
    agent: str = DEFAULT_KIND
    # The command this session was actually launched with, read off the live
    # process at capture time. Empty means "use the kind's default". Captured
    # rather than assumed because the flags are the user's choice, and this
    # package's whole premise is putting things back as they were.
    command: str = ""
    # The conversation the tab was in at capture, for a kind whose sessions
    # say so (discover.held_threads) - "" where unknown or ambiguous. The
    # launch resumes it by id (agents.resume_exactly): `--last`, the only
    # other choice, gives two tabs in one directory the same conversation.
    session_id: str = ""

    def __post_init__(self):
        # A key present but null in the JSON defeats the dataclass default and
        # would put None where every consumer expects a string.
        if not self.agent:
            self.agent = DEFAULT_KIND
        if not self.command:
            self.command = ""
        if not self.session_id:
            self.session_id = ""


@dataclass
class Window:
    monitor: str
    rect: list[int]  # [x, y, w, h]
    state: str  # normal | maximized | minimized
    dpi: int
    tabs: list[Tab] = field(default_factory=list)
    # How far the visible window sits inside `rect`, as [left, top, right,
    # bottom] - see win32.frame_inset. `rect` covers the invisible resize
    # border, so a flush-snapped window's rect legitimately extends past the
    # monitor's work area; clamp_rect needs this to tell that apart from a rect
    # that is genuinely off-screen. Defaults to zeros so a layout written
    # before this field behaves exactly as it used to until the next capture.
    inset: list[int] = field(default_factory=lambda: [0, 0, 0, 0])


@dataclass
class Layout:
    version: int
    saved_ts: str
    monitors: list[Monitor]
    windows: list[Window]

    def to_dict(self) -> dict[str, Any]:
        # asdict() recurses through the nested dataclasses and copies the lists,
        # so it stays correct when a field is added — a hand-written mapping
        # would silently drop it.
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Layout":
        # Deliberately not the mirror of asdict(): every field carries a default
        # so a layout written by an older version still loads.
        #
        # IF YOU ADD A FIELD to Tab/Window/Monitor, add it here too — asdict()
        # will write it automatically, but a field missing from this function
        # silently reverts to its default on every load. Guarded by
        # test_every_field_survives_a_roundtrip.
        return Layout(
            version=int(d.get("version", LAYOUT_VERSION)),
            saved_ts=str(d.get("saved_ts", "")),
            monitors=[
                Monitor(
                    device=m["device"],
                    primary=bool(m.get("primary", False)),
                    work=list(m.get("work", [0, 0, 0, 0])),
                    dpi=int(m.get("dpi", 96)),
                )
                for m in d.get("monitors", [])
            ],
            windows=[
                Window(
                    monitor=w.get("monitor", ""),
                    rect=list(w.get("rect", [0, 0, 1168, 624])),
                    state=w.get("state", "normal"),
                    dpi=int(w.get("dpi", 96)),
                    inset=list(w.get("inset", [0, 0, 0, 0])),
                    tabs=[
                        Tab(
                            cwd=t["cwd"],
                            title=t.get("title", ""),
                            low_confidence=bool(t.get("low_confidence", False)),
                            pinned=bool(t.get("pinned", False)),
                            # Both absent from any layout written before a
                            # second agent kind existed; Tab.__post_init__
                            # turns an absent-or-null value into the default.
                            agent=t.get("agent", DEFAULT_KIND),
                            command=t.get("command", ""),
                            session_id=t.get("session_id", ""),
                        )
                        for t in w.get("tabs", [])
                    ],
                )
                for w in d.get("windows", [])
            ],
        )


def window_id(index: int) -> str:
    """Display name for a window, derived from its position.

    Deliberately not stored: an `id` field is only ever assigned as
    "position + 1", but nothing renumbers it when a window is removed, so a
    stored value drifts from the position it claims to describe.
    """
    return f"w{index + 1}"


def tab_flags(t: Tab) -> str:
    """Bracketed suffix describing a tab's flags, or "" when it has none.

    The agent kind shows only when it is not the default. A Codex tab and a
    Claude Code tab are otherwise indistinguishable everywhere a layout is
    displayed - `status`, the editor, the dry runs - which is a poor property
    for the one field that decides how a tab is launched and how it is quit.
    """
    flags = [
        label
        for present, label in ((t.low_confidence, "basename guess"), (t.pinned, "pinned"))
        if present
    ]
    if t.agent and t.agent != DEFAULT_KIND:
        flags.insert(0, t.agent)
    return f"  [{', '.join(flags)}]" if flags else ""


def window_header(w: Window) -> str:
    """Monitor and geometry, formatted identically everywhere it is shown."""
    x, y, width, height = w.rect
    return f"{w.monitor}  ({x},{y} {width}x{height}, {w.state})"


def tab_changes(before: Layout | None, after: Layout):
    """What saving `after` over `before` changes about what comes back:
    (lost, gained, changed).

    Per tab, as (normalized cwd, kind), not per directory: a directory's Codex
    tab lost beside its surviving Claude one is a loss, and a set of
    directories cannot show it. `lost` and `gained` hold one key per tab.
    `changed` holds (key, command before, command after) for a tab that is
    still there but now launches differently - a command that went empty
    because its process could not be read included. Geometry and window
    grouping change constantly and are none of these; order is `reordered`'s.
    """
    # str() throughout: from_dict keeps whatever the file held, and this runs
    # after the save has replaced it, where raising would abort the writer.
    def tabs(lo) -> Counter:
        return Counter((norm(str(t.cwd)), str(t.agent or DEFAULT_KIND),
                        str(t.command or ""))
                       for w in (lo.windows if lo is not None else ()) for t in w.tabs)

    was, now = tabs(before), tabs(after)
    lost, gained = sorted((was - now).elements()), sorted((now - was).elements())
    # A tab whose command differs is one of each under the same key: pair them.
    changed = []
    for key in sorted({t[:2] for t in lost} & {t[:2] for t in gained}):
        for old, new in zip([t for t in lost if t[:2] == key],
                            [t for t in gained if t[:2] == key]):
            changed.append((key, old[2], new[2]))
            lost.remove(old)
            gained.remove(new)
    return [t[:2] for t in lost], [t[:2] for t in gained], changed


def session_key(t: Tab) -> tuple[str, str]:
    """A tab's session as (normalized cwd, kind). str() for the same reason as
    in tab_changes: a loaded layout holds whatever the file held."""
    return norm(str(t.cwd)), str(t.agent or DEFAULT_KIND)


def matching_window(tabs: list[Tab], windows: list[Window]) -> Window | None:
    """The one window of `windows` holding the most of `tabs`' sessions.

    Windows are saved in z-order, which shifts, so a window is found again by
    what it holds - and not at all where none holds any of it, or two hold
    equally much.
    """
    here = Counter(session_key(t) for t in tabs)
    scored = sorted(((sum((here & Counter(session_key(t) for t in w.tabs)).values()), i)
                     for i, w in enumerate(windows)), reverse=True)
    if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return windows[scored[0][1]]


def reordered(before: Layout | None, after: Layout) -> list[list[tuple[str, str]]]:
    """The new tab order, as session keys, of each window of `after` whose
    tabs run in a different order than in its window of `before` (#80).

    A window is reordered when the tabs both windows hold cannot all keep
    their order - when their longest common subsequence is shorter than what
    they share. So a tab gained, lost or moved to another window is not also
    a reorder, nor is closing one of two alike, nor the order of the windows
    themselves. Tabs are told apart by command too, so two conversations of
    one kind in one directory trading places is one.
    """
    def keys(ts):
        return [session_key(t) + (str(t.command or ""),) for t in ts]

    def in_order(a, b) -> int:
        row = [0] * (len(b) + 1)
        for x in a:
            diagonal = 0
            for j, y in enumerate(b, 1):
                diagonal, row[j] = row[j], (diagonal + 1 if x == y
                                            else max(row[j], row[j - 1]))
        return row[-1]

    moved = []
    for w in after.windows:
        old = matching_window(w.tabs, before.windows) if before is not None else None
        if old is None:
            continue
        now, was = keys(w.tabs), keys(old.tabs)
        if in_order(now, was) < sum((Counter(now) & Counter(was)).values()):
            moved.append([k[:2] for k in now])
    return moved


def save(lo: Layout, path) -> None:
    """Write atomically — a torn layout file would break unattended deploy.

    The temp file is per-process. Every writer used to share one
    `default.json.tmp`, and there are several: the reconcile task saves every
    five minutes, forever, while `capture`, `restart` and the interactive
    editor all save on demand. Two of them overlapping wrote into the same
    handle, or one replaced the file the other was about to move, and the
    "atomic" rename then made a half-written layout the real one. A pid in the
    name costs nothing and makes the two writes independent.

    It does not make them ordered. Two writers still race to be last, and the
    loser's changes are gone - an editor session open for ten minutes saves an
    in-memory layout that the reconcile has rewritten twice underneath it. This
    stops corruption, not lost updates. Fixing that needs a lock, and a lock
    needs a stale-lock story on a machine that crashes; not attempted here.
    """
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f"{p.suffix}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(lo.to_dict(), indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)
    finally:
        # A crash between write and replace would otherwise leave one temp file
        # per run, forever, in a directory nobody looks at.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def load(path) -> Layout:
    p = pathlib.Path(path)
    return Layout.from_dict(json.loads(p.read_text(encoding="utf-8")))


def clamp_rect(
    rect: list[int],
    saved_dpi: int,
    saved_monitor: str,
    monitors: list[Monitor],
    saved_inset: list[int] | None = None,
) -> list[int]:
    """Return a rect guaranteed to be visible on an available monitor.

    Handles the three ways a saved rect goes bad between capture and deploy:
    the monitor disappeared, the DPI changed, or the resolution shrank.

    `rect` is a window rect, which includes the invisible resize border, while
    a monitor's work area is measured in visible pixels. `saved_inset` (see
    win32.frame_inset) reconciles the two: the allowed bounds are widened by
    the border so a window snapped flush to a screen edge - whose rect really
    does start at x=-9 and end 9px past the screen - is left exactly where it
    was. Without it every snapped window got pulled inward by the border width,
    leaving a gap at the outer screen edges and an overlap down the middle.
    Omitted or zero reproduces the old behaviour.
    """
    if not monitors:
        return list(rect)

    x, y, w, h = rect
    inset_left, inset_top, inset_right, inset_bottom = saved_inset or [0, 0, 0, 0]
    target = next((m for m in monitors if m.device == saved_monitor), None)
    if target is None:
        # Monitor is gone (undocked, powered off, re-enumerated). Re-anchor near
        # the primary's origin rather than leaving coordinates that point at a
        # screen which no longer exists.
        target = next((m for m in monitors if m.primary), monitors[0])
        x = target.work[0] + 40
        y = target.work[1] + 40

    if saved_dpi and target.dpi and target.dpi != saved_dpi:
        scale = target.dpi / float(saved_dpi)
        w = int(round(w * scale))
        h = int(round(h * scale))
        # The border is part of the frame, so it scales with the window.
        inset_left = int(round(inset_left * scale))
        inset_top = int(round(inset_top * scale))
        inset_right = int(round(inset_right * scale))
        inset_bottom = int(round(inset_bottom * scale))

    left = target.work[0] - inset_left
    top = target.work[1] - inset_top
    right = target.work[2] + inset_right
    bottom = target.work[3] + inset_bottom
    w = max(1, min(w, right - left))
    h = max(1, min(h, bottom - top))
    x = max(left, min(x, right - w))
    y = max(top, min(y, bottom - h))
    return [x, y, w, h]
