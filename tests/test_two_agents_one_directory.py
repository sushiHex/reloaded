"""A directory running Claude in one tab and Codex in another keeps both.

Measured after a logon: retro, hermes-realtime and constructicon each ran a
Claude and a Codex tab in one window, and each came back with one. Capture had
keyed everything on the directory, so the second session was never saved -
the restore only replayed the loss. Both tabs carry the repo's name, so no
title can tell them apart; each session is placed by its tab's shell instead.
"""
from __future__ import annotations

import sys
import types

import pytest

from conftest import MONITORS, make_layout, make_window

import reloaded.capture as capture_mod
import reloaded.discover as discover_mod
from reloaded.capture import build_layout
from reloaded.deploy import plan_deploy
from reloaded.discover import Session
from reloaded.layout import Tab
from reloaded.paths import norm

# Bound at import, before the autouse fixture swaps in its empty answer.
real_sessions = discover_mod.sessions

REPOS = r"C:\Users\k\repos"
RETRO = REPOS + r"\retro"
META = REPOS + r"\meta"


@pytest.fixture(autouse=True)
def _commands(monkeypatch):
    monkeypatch.setattr(capture_mod.discover, "session_command",
                        lambda pid: f"command-of-{pid}")


def _window(hwnd, titles):
    return {"hwnd": hwnd, "rect": [0, 0, 800, 600], "state": "normal",
            "monitor": r"\\.\DISPLAY1", "dpi": 96, "titles": titles}


def _capture(windows_data, sessions):
    live = {norm(s.cwd): s.pid for s in sessions}
    return build_layout(windows_data, MONITORS, {}, live, REPOS, "t",
                        sessions=sessions)


def test_both_agents_in_one_directory_are_saved():
    sessions = [Session(1, RETRO, "claude", 10.0, 100),
                Session(2, RETRO, "codex", 20.0, 100)]

    lo = _capture([_window(100, ["retro", "retro"])], sessions)

    tabs = lo.windows[0].tabs
    assert [(t.agent, t.command) for t in tabs] == [
        ("claude", "command-of-1"), ("codex", "command-of-2")]
    assert {norm(t.cwd) for t in tabs} == {norm(RETRO)}


def test_a_session_is_saved_in_the_window_its_tab_is_in():
    """Its title appears in two windows; only its shell says which."""
    sessions = [Session(1, RETRO, "claude", 10.0, 100),
                Session(2, RETRO, "codex", 20.0, 200)]

    lo = _capture([_window(100, ["retro"]), _window(200, ["retro"])], sessions)

    assert [t.agent for t in lo.windows[0].tabs] == ["claude"]
    assert [t.agent for t in lo.windows[1].tabs] == ["codex"]


def test_titles_still_order_a_window():
    sessions = [Session(1, RETRO, "claude", 10.0, 100),
                Session(2, META, "claude", 5.0, 100)]

    lo = _capture([_window(100, ["retro", "meta"])], sessions)

    assert [t.title for t in lo.windows[0].tabs] == ["retro", "meta"]


def test_a_session_under_a_title_naming_no_repo_is_still_saved():
    """A retitled tab resolves to nothing, but its shell still places it."""
    sessions = [Session(1, RETRO, "claude", 10.0, 100)]

    lo = _capture([_window(100, ["my renamed tab"])], sessions)

    assert [(t.cwd, t.title) for t in lo.windows[0].tabs] == [(RETRO, "retro")]


def test_one_session_is_saved_once_however_many_titles_name_it():
    sessions = [Session(1, RETRO, "claude", 10.0, 100)]

    lo = _capture([_window(100, ["retro", "retro"])], sessions)

    assert len(lo.windows[0].tabs) == 1


def test_a_directorys_codex_tab_is_launched_while_its_claude_one_is_up(monkeypatch):
    import reloaded.deploy as deploy_mod
    monkeypatch.setattr(deploy_mod.os.path, "isdir", lambda p: True)
    lo = make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=RETRO, title="retro", agent="claude"),
        Tab(cwd=RETRO, title="retro", agent="codex")])])

    plan = plan_deploy(lo, {norm(RETRO): 1}, MONITORS, {},
                       running={(norm(RETRO), "claude")})

    assert [t.agent for t in plan[0].tabs] == ["codex"]
    assert [t.agent for t in plan[0].skipped] == ["claude"]


def test_each_session_is_placed_through_its_tabs_shell(monkeypatch):
    """Agent -> its shell -> the window that shell's tab is in. A session
    outside any tab has no window."""
    procs = [
        types.SimpleNamespace(pid=1, name="claude.exe", cwd=RETRO, line=[10, 5]),
        types.SimpleNamespace(pid=2, name="codex.exe", cwd=RETRO, line=[20, 5]),
        types.SimpleNamespace(pid=3, name="codex.exe", cwd=META, line=[7]),
    ]

    class _Proc:
        def __init__(self, p):
            self._p = p
            self.info = {"pid": p.pid, "name": p.name, "create_time": float(p.pid)}

        def cwd(self):
            return self._p.cwd

        def parents(self):
            return [types.SimpleNamespace(pid=pid) for pid in self._p.line]

    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(
        process_iter=lambda attrs: [_Proc(p) for p in procs]))
    monkeypatch.setattr(discover_mod, "_hosted_elsewhere", lambda proc, images: False)
    import reloaded.win32 as win32_mod
    monkeypatch.setattr(win32_mod, "tab_shells", lambda: {10: 100, 20: 100})

    found = {(s.pid, s.hwnd) for s in real_sessions()}

    assert found == {(1, 100), (2, 100), (3, None)}
