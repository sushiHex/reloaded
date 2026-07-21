"""Interactive console editor for a saved layout.

Standard library only — no TUI dependency, so it works over RDP and in a bare
console. `capture` remains the fastest way to change geometry; this handles what
dragging windows cannot express.
"""
from __future__ import annotations

import os

from . import capture as capture_mod
from . import layout as layout_mod
from . import tabs as tabs_mod
from .layout import Layout, Tab, Window
from .paths import resolve_repo


def _render(lo: Layout) -> None:
    print("\n" + "=" * 64)
    print(f"layout saved {lo.saved_ts}   {len(lo.windows)} window(s)")
    print("=" * 64)
    for wi, w in enumerate(lo.windows, 1):
        print(f"\n[{wi}] {layout_mod.window_header(w)}")
        for ti, t in enumerate(w.tabs, 1):
            print(f"     {wi}.{ti}  {t.title}{layout_mod.tab_flags(t)}")
            print(f"           {t.cwd}")
    if not lo.windows:
        print("\n  (empty)")


MENU = """
commands:
  c            recapture from the live arrangement (replaces everything)
  m W.T W      move tab W.T into window W
  o W.T N      move tab W.T to position N within its window
  a W <repo>   add a repo as a new tab at the end of window W
  d W.T        delete tab W.T
  n            add a new empty window (geometry copied from the last one)
  s            save and exit
  q            quit without saving
"""


def _parse_ref(token: str) -> tuple[int, int] | None:
    try:
        w, t = token.split(".")
        return int(w) - 1, int(t) - 1
    except Exception:
        return None


def _valid(lo: Layout, wi: int, ti: int | None = None) -> bool:
    if wi < 0 or wi >= len(lo.windows):
        return False
    if ti is None:
        return True
    return 0 <= ti < len(lo.windows[wi].tabs)


def _parse_index(token: str, label: str) -> int | None:
    """Parse a 1-based argument into a 0-based index, or report and return None."""
    try:
        return int(token) - 1
    except ValueError:
        print(f"{label} must be a number")
        return None


def _drop_empty_windows(lo: Layout) -> None:
    """A window with no tabs has nothing to launch, so it stops being a window."""
    lo.windows = [w for w in lo.windows if w.tabs]


def run(lo: Layout, layout_file, repos_root: str) -> int:
    dirty = False
    while True:
        _render(lo)
        print(MENU)
        try:
            raw = input("reloaded> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()

        if cmd == "q":
            if dirty:
                confirm = input("unsaved changes — discard? [y/N] ").strip().lower()
                if confirm != "y":
                    continue
            return 1

        if cmd == "s":
            layout_mod.save(lo, layout_file)
            print(f"saved -> {layout_file}")
            return 0

        if cmd == "c":
            try:
                fresh = capture_mod.capture_live(repos_root, lo)
            except tabs_mod.UIAUnavailable as exc:
                print(f"[reloaded] {exc}")
                print(tabs_mod.install_hint())
                continue
            if not fresh.windows:
                print("no Claude tabs found — keeping the current layout")
                continue
            lo = fresh
            dirty = True
            continue

        if cmd == "n":
            template = lo.windows[-1] if lo.windows else None
            lo.windows.append(
                Window(
                    monitor=template.monitor if template else "",
                    rect=list(template.rect) if template else [100, 100, 1168, 624],
                    state="normal",
                    dpi=template.dpi if template else 96,
                    tabs=[],
                )
            )
            dirty = True
            continue

        if cmd == "d" and len(parts) == 2:
            ref = _parse_ref(parts[1])
            if not ref or not _valid(lo, *ref):
                print("no such tab")
                continue
            wi, ti = ref
            removed = lo.windows[wi].tabs.pop(ti)
            _drop_empty_windows(lo)
            print(f"removed {removed.title}")
            dirty = True
            continue

        if cmd == "m" and len(parts) == 3:
            ref = _parse_ref(parts[1])
            dest = _parse_index(parts[2], "destination window")
            if dest is None:
                continue
            if not ref or not _valid(lo, *ref) or not _valid(lo, dest):
                print("no such tab or window")
                continue
            wi, ti = ref
            tab = lo.windows[wi].tabs.pop(ti)
            lo.windows[dest].tabs.append(tab)
            _drop_empty_windows(lo)
            print(f"moved {tab.title}")
            dirty = True
            continue

        if cmd == "o" and len(parts) == 3:
            ref = _parse_ref(parts[1])
            pos = _parse_index(parts[2], "position")
            if pos is None:
                continue
            if not ref or not _valid(lo, *ref):
                print("no such tab")
                continue
            wi, ti = ref
            tabs_list = lo.windows[wi].tabs
            pos = max(0, min(pos, len(tabs_list) - 1))
            tab = tabs_list.pop(ti)
            tabs_list.insert(pos, tab)
            print(f"reordered {tab.title}")
            dirty = True
            continue

        if cmd == "a" and len(parts) == 3:
            wi = _parse_index(parts[1], "window")
            if wi is None:
                continue
            if not _valid(lo, wi):
                print("no such window")
                continue
            cwd = resolve_repo(parts[2], repos_root)
            if not os.path.isdir(cwd):
                print(f"not a directory: {cwd}")
                continue
            # Pinned: this repo may not be running, and a capture only sees live
            # sessions. Without the flag the next reconcile would erase it.
            lo.windows[wi].tabs.append(Tab(cwd=cwd, title=os.path.basename(cwd), pinned=True))
            print(f"added {cwd}  [pinned]")
            dirty = True
            continue

        print("unrecognized command")
