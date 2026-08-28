"""Layout model: the saved arrangement of windows, tabs, and geometry."""
from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass, field
from typing import Any

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
    agent: str = "claude"
    # The command this session was actually launched with, read off the live
    # process at capture time. Empty means "use the kind's default". Captured
    # rather than assumed because the flags are the user's choice, and this
    # package's whole premise is putting things back as they were.
    command: str = ""

    def __post_init__(self):
        # A key present but null in the JSON defeats the dataclass default and
        # would put None where every consumer expects a string.
        if not self.agent:
            self.agent = "claude"
        if not self.command:
            self.command = ""


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
                            agent=t.get("agent", "claude"),
                            command=t.get("command", ""),
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
    """Bracketed suffix describing a tab's flags, or "" when it has none."""
    flags = [
        label
        for present, label in ((t.low_confidence, "basename guess"), (t.pinned, "pinned"))
        if present
    ]
    return f"  [{', '.join(flags)}]" if flags else ""


def window_header(w: Window) -> str:
    """Monitor and geometry, formatted identically everywhere it is shown."""
    x, y, width, height = w.rect
    return f"{w.monitor}  ({x},{y} {width}x{height}, {w.state})"


def save(lo: Layout, path) -> None:
    """Write atomically — a torn layout file would break unattended deploy."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(lo.to_dict(), indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


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
