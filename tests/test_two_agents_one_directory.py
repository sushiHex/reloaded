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
from collections import Counter

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
                       running=Counter({(norm(RETRO), "claude"): 1}))

    assert [t.agent for t in plan[0].tabs] == ["codex"]
    assert [t.agent for t in plan[0].skipped] == ["claude"]


def test_two_saved_tabs_of_one_kind_are_not_both_answered_by_one_session(monkeypatch):
    import reloaded.deploy as deploy_mod
    monkeypatch.setattr(deploy_mod.os.path, "isdir", lambda p: True)
    lo = make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=RETRO, title="retro", agent="claude"),
        Tab(cwd=RETRO, title="retro", agent="claude")])])

    plan = plan_deploy(lo, {norm(RETRO): 1}, MONITORS, {},
                       running=Counter({(norm(RETRO), "claude"): 1}))

    assert len(plan[0].skipped) == 1 and len(plan[0].tabs) == 1


def test_a_directory_whose_sessions_are_unknown_falls_back_to_the_directory():
    """What every caller did before kinds were counted - and what a test
    that supplies only `live_sessions` still gets."""
    assert discover_mod.claim_running(Counter(), {norm(RETRO): 1}, RETRO, "codex")
    assert not discover_mod.claim_running(
        Counter({(norm(RETRO), "claude"): 1}), {norm(RETRO): 1}, RETRO, "codex")


def test_titles_keep_their_order_when_one_repo_appears_twice():
    """One session per title: `retro, meta, retro` is not `retro, retro, meta`."""
    sessions = [Session(1, RETRO, "claude", 10.0, 100),
                Session(2, META, "claude", 15.0, 100),
                Session(3, RETRO, "codex", 20.0, 100)]

    lo = _capture([_window(100, ["retro", "meta", "retro"])], sessions)

    assert [(t.title, t.agent) for t in lo.windows[0].tabs] == [
        ("retro", "claude"), ("meta", "claude"), ("retro", "codex")]


def _saved(tmp_path, *tabs):
    from reloaded.layout import save
    path = tmp_path / "layout.json"
    save(make_layout([make_window([0, 0, 800, 600], list(tabs))]), path)
    return path


def test_a_capture_that_lost_one_of_a_directorys_sessions_is_refused(tmp_path, monkeypatch):
    """Guarded by directory, keeping the Claude tab made the lost Codex one
    look fine, and the reconcile wrote the loss over a good layout."""
    from reloaded.__main__ import _capture_shrinkage
    monkeypatch.setattr(discover_mod, "running", lambda: Counter(
        {(norm(RETRO), "claude"): 1, (norm(RETRO), "codex"): 1}))
    path = _saved(tmp_path, Tab(cwd=RETRO, title="retro", agent="claude"),
                  Tab(cwd=RETRO, title="retro", agent="codex"))
    fresh = make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=RETRO, title="retro", agent="claude")])])

    assert "retro" in _capture_shrinkage(fresh, path, {norm(RETRO): 1})


def test_losing_one_of_two_same_kind_sessions_is_refused(tmp_path, monkeypatch):
    """Matched as a set, the surviving Claude tab stood in for both."""
    from reloaded.__main__ import _capture_shrinkage
    monkeypatch.setattr(discover_mod, "running", lambda: Counter(
        {(norm(RETRO), "claude"): 2}))
    path = _saved(tmp_path, Tab(cwd=RETRO, title="retro", agent="claude"),
                  Tab(cwd=RETRO, title="retro", agent="claude"))
    fresh = make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=RETRO, title="retro", agent="claude")])])

    assert "retro" in _capture_shrinkage(fresh, path, {norm(RETRO): 1})


def test_status_names_a_live_session_the_saved_layout_lacks(tmp_path, monkeypatch, capsys):
    """A layout written before this change holds one tab for a directory
    running two; status must not read that as all saved."""
    import reloaded.__main__ as main_mod
    path = _saved(tmp_path, Tab(cwd=RETRO, title="retro", agent="claude"))
    monkeypatch.setattr(main_mod, "layout_path", lambda name: path)
    monkeypatch.setattr(main_mod.discover_mod, "sweep",
                        lambda: ({norm(RETRO): 1}, {norm(RETRO): ["claude", "codex"]}))
    monkeypatch.setattr(discover_mod, "running", lambda: Counter(
        {(norm(RETRO), "claude"): 1, (norm(RETRO), "codex"): 1}))
    monkeypatch.setattr(main_mod.readiness_mod, "wait_for_ready",
                        lambda lo, timeout: (True, "ready"))
    monkeypatch.setattr(main_mod.readiness_mod, "check_wt_version",
                        lambda: (True, "ok"))

    main_mod.cmd_status(types.SimpleNamespace(layout="default", repos_root=REPOS))

    out = capsys.readouterr().out
    assert "[drift] 1 live session(s) are not in the layout" in out
    assert "(codex)" in out


def test_a_directorys_session_the_user_closed_is_let_go(tmp_path, monkeypatch):
    from reloaded.__main__ import _capture_shrinkage
    monkeypatch.setattr(discover_mod, "running", lambda: Counter(
        {(norm(RETRO), "claude"): 1}))
    path = _saved(tmp_path, Tab(cwd=RETRO, title="retro", agent="claude"),
                  Tab(cwd=RETRO, title="retro", agent="codex"))
    fresh = make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=RETRO, title="retro", agent="claude")])])

    assert _capture_shrinkage(fresh, path, {norm(RETRO): 1}) == ""


def test_status_counts_a_directorys_missing_codex_tab(tmp_path, monkeypatch, capsys):
    """`up` launches it; status said both were running and `up` would launch
    nothing."""
    import reloaded.__main__ as main_mod
    path = _saved(tmp_path, Tab(cwd=RETRO, title="retro", agent="claude"),
                  Tab(cwd=RETRO, title="retro", agent="codex"))
    monkeypatch.setattr(main_mod, "layout_path", lambda name: path)
    monkeypatch.setattr(main_mod.discover_mod, "sweep",
                        lambda: ({norm(RETRO): 1}, {}))
    monkeypatch.setattr(discover_mod, "running", lambda: Counter(
        {(norm(RETRO), "claude"): 1}))
    monkeypatch.setattr(main_mod.readiness_mod, "wait_for_ready",
                        lambda lo, timeout: (True, "ready"))
    monkeypatch.setattr(main_mod.readiness_mod, "check_wt_version",
                        lambda: (True, "ok"))

    main_mod.cmd_status(types.SimpleNamespace(layout="default", repos_root=REPOS))

    assert "`up` would launch 1 session(s)." in capsys.readouterr().out


def test_a_pinned_codex_tab_survives_beside_a_running_claude_one():
    """Pins merged by directory handed the Codex tab's pin to the Claude tab
    and dropped the Codex tab - erased by the next reconcile."""
    fresh = make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=RETRO, title="retro", agent="claude")])])
    previous = make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=RETRO, title="retro", agent="codex", pinned=True)])])

    merged = capture_mod.merge_pinned(fresh, previous)

    tabs = merged.windows[0].tabs
    assert [(t.agent, t.pinned) for t in tabs] == [("claude", False), ("codex", True)]


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
