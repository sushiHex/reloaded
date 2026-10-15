"""A directory running Claude in one tab and Codex in another keeps both.

Measured after a logon: retro, hermes-realtime and constructicon each ran a
Claude and a Codex tab in one window, and each came back with one. Capture had
keyed everything on the directory, so the second session was never saved -
the restore only replayed the loss. Both tabs carry the repo's name, so no
title can tell them apart; each session is placed by its tab's shell instead.
"""
from __future__ import annotations

import os
import sys
import types
from collections import Counter

import pytest

from conftest import MONITORS, make_layout, make_window

import reloaded.agents as agents
import reloaded.capture as capture_mod
import reloaded.discover as discover_mod
import reloaded.win32 as win32_mod
from reloaded.capture import build_layout
from reloaded.deploy import plan_deploy
from reloaded.discover import Session
from reloaded.layout import Tab
from reloaded.paths import norm
from reloaded.win32 import Proc

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


HERMES = REPOS + r"\hermes-realtime"
FORGE = REPOS + r"\constructicon"


def _layout(*kinds_at):
    return make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=cwd, title=os.path.basename(cwd), agent=kind) for cwd, kind in kinds_at])])


def test_a_directorys_tabs_keep_the_kind_order_the_layout_has():
    """The #77 window: the restore's stagger made each directory's Codex or
    Claude agent the older one, so start order put every pair back the way
    the restore had launched it, over the order the user had saved."""
    started = [(RETRO, "codex"), (RETRO, "claude"), (HERMES, "claude"),
               (FORGE, "claude"), (HERMES, "codex"), (FORGE, "codex")]
    sessions = [Session(pid, cwd, kind, float(pid), 100)
                for pid, (cwd, kind) in enumerate(started, 1)]
    wanted = [(RETRO, "codex"), (HERMES, "codex"), (FORGE, "codex"),
              (RETRO, "claude"), (HERMES, "claude"), (FORGE, "claude")]
    titles = ["retro", "hermes-realtime", "constructicon"] * 2
    live = {norm(s.cwd): s.pid for s in sessions}

    lo = build_layout([_window(100, titles)], MONITORS, {}, live, REPOS, "t",
                      sessions=sessions, previous=_layout(*wanted))

    assert [(t.cwd, t.agent) for t in lo.windows[0].tabs] == wanted
    # A moved tab is its session's, command and all, not only its kind:
    # Codex hermes-realtime started fifth.
    assert lo.windows[0].tabs[1].command == "command-of-5"


def test_with_no_saved_order_a_directorys_tabs_are_in_start_order():
    sessions = [Session(1, RETRO, "codex", 10.0, 100),
                Session(2, RETRO, "claude", 20.0, 100)]

    lo = _capture([_window(100, ["retro", "retro"])], sessions)

    assert [t.agent for t in lo.windows[0].tabs] == ["codex", "claude"]


def test_two_saved_windows_equally_like_this_one_lend_it_no_order():
    """Both hold the pair, in opposite orders: either could be this window."""
    sessions = [Session(1, RETRO, "codex", 10.0, 100),
                Session(2, RETRO, "claude", 20.0, 100)]
    live = {norm(RETRO): 2}
    previous = make_layout([
        make_window([0, 0, 800, 600], [Tab(cwd=RETRO, title="retro", agent="claude"),
                                       Tab(cwd=RETRO, title="retro", agent="codex")]),
        make_window([0, 0, 800, 600], [Tab(cwd=RETRO, title="retro", agent="codex"),
                                       Tab(cwd=RETRO, title="retro", agent="claude")])])

    lo = build_layout([_window(100, ["retro", "retro"])], MONITORS, {}, live, REPOS,
                      "t", sessions=sessions, previous=previous)

    assert [t.agent for t in lo.windows[0].tabs] == ["codex", "claude"]


def test_a_shell_placed_session_helps_find_the_saved_window():
    """Its title names no repo, but what it is still says which saved window
    this one was: only the second holds meta beside the retro pair."""
    sessions = [Session(1, RETRO, "codex", 10.0, 100),
                Session(2, RETRO, "claude", 20.0, 100),
                Session(3, META, "claude", 30.0, 100)]
    live = {norm(RETRO): 2, norm(META): 3}
    previous = make_layout([
        make_window([0, 0, 800, 600], [Tab(cwd=RETRO, title="retro", agent="codex"),
                                       Tab(cwd=RETRO, title="retro", agent="claude")]),
        make_window([0, 0, 800, 600], [Tab(cwd=RETRO, title="retro", agent="claude"),
                                       Tab(cwd=RETRO, title="retro", agent="codex"),
                                       Tab(cwd=META, title="meta", agent="claude")])])

    lo = build_layout([_window(100, ["retro", "retro", "renamed"])], MONITORS, {}, live,
                      REPOS, "t", sessions=sessions, previous=previous)

    assert [t.agent for t in lo.windows[0].tabs] == ["claude", "codex", "claude"]


def test_a_window_whose_tab_read_timed_out_is_left_out(monkeypatch):
    """Its sessions would come back by shell, in start order, and be saved
    over the order they had; missing instead, the save is refused."""
    import reloaded.tabs as tabs_mod
    monkeypatch.setattr(win32_mod, "list_monitors", lambda: MONITORS)
    monkeypatch.setattr(win32_mod, "list_wt_windows", lambda: [100, 200])
    monkeypatch.setattr(win32_mod, "get_geometry",
                        lambda hwnd: ([0, 0, 800, 600], "normal", r"\\.\DISPLAY1", 96))
    monkeypatch.setattr(win32_mod, "frame_inset", lambda hwnd: [0, 0, 0, 0])
    monkeypatch.setattr(tabs_mod, "list_tab_items",
                        lambda hwnd: [("meta", object())] if hwnd == 200 else [])
    monkeypatch.setattr(discover_mod, "sessions", lambda: [
        Session(1, RETRO, "codex", 10.0, 100), Session(2, RETRO, "claude", 20.0, 100),
        Session(3, META, "claude", 30.0, 200)])

    lo = capture_mod.capture_live(REPOS, None, live={norm(RETRO): 2, norm(META): 3},
                                  title_map={})

    assert [[t.cwd for t in w.tabs] for w in lo.windows] == [[META]]


def test_capture_live_hands_the_saved_layout_to_build_layout(monkeypatch):
    seen = {}
    monkeypatch.setattr(win32_mod, "list_monitors", lambda: MONITORS)
    monkeypatch.setattr(win32_mod, "list_wt_windows", lambda: [])
    monkeypatch.setattr(discover_mod, "sessions", lambda: [])
    monkeypatch.setattr(capture_mod, "build_layout",
                        lambda *a, **k: seen.update(k) or make_layout([]))
    previous = _layout((RETRO, "claude"))

    capture_mod.capture_live(REPOS, previous, live={}, title_map={})

    assert seen["previous"] is previous


def test_a_saved_order_for_other_kinds_is_not_followed():
    """Two Claude tabs saved where a Claude and a Codex now run: the layout
    says nothing about which of these is where."""
    sessions = [Session(1, RETRO, "codex", 10.0, 100),
                Session(2, RETRO, "claude", 20.0, 100)]
    live = {norm(RETRO): 2}

    lo = build_layout([_window(100, ["retro", "retro"])], MONITORS, {}, live, REPOS,
                      "t", sessions=sessions,
                      previous=_layout((RETRO, "claude"), (RETRO, "claude")))

    assert [t.agent for t in lo.windows[0].tabs] == ["codex", "claude"]


def _marked(kind, cwd=RETRO):
    return agents.tab_label(kind, cwd)


def test_a_tabs_mark_says_which_session_it_holds():
    """Older Codex, but the strip says Claude comes first."""
    sessions = [Session(1, RETRO, "codex", 10.0, 100),
                Session(2, RETRO, "claude", 20.0, 100)]

    lo = _capture([_window(100, [_marked("claude"), _marked("codex")])], sessions)

    assert [(t.title, t.agent) for t in lo.windows[0].tabs] == [
        ("retro", "claude"), ("retro", "codex")]


def test_a_marked_title_takes_its_session_before_an_unmarked_one_can():
    """An unmarked tab first in the strip would otherwise take the older
    session - the Codex one its marked neighbour names."""
    sessions = [Session(1, RETRO, "codex", 10.0, 100),
                Session(2, RETRO, "claude", 20.0, 100)]

    lo = _capture([_window(100, ["retro", _marked("codex")])], sessions)

    assert [t.agent for t in lo.windows[0].tabs] == ["claude", "codex"]


def test_a_marked_title_never_takes_the_other_kinds_session():
    sessions = [Session(1, RETRO, "claude", 10.0, 100)]

    lo = _capture([_window(100, [_marked("codex"), "meta"])], sessions)

    # Still saved - its shell places it - but not as the tab the mark named.
    assert [(t.agent, t.low_confidence) for t in lo.windows[0].tabs] == [("claude", False)]


def test_a_saved_order_never_moves_a_marked_tab():
    sessions = [Session(1, RETRO, "codex", 10.0, 100),
                Session(2, RETRO, "claude", 20.0, 100)]
    live = {norm(RETRO): 2}

    lo = build_layout([_window(100, [_marked("codex"), _marked("claude")])], MONITORS,
                      {}, live, REPOS, "t", sessions=sessions,
                      previous=_layout((RETRO, "claude"), (RETRO, "codex")))

    assert [t.agent for t in lo.windows[0].tabs] == ["codex", "claude"]


def test_a_mark_names_its_kind_and_no_mark_names_none():
    assert agents.title_kind(_marked("codex")) == ("retro", "codex")
    assert agents.title_kind(_marked("claude")) == ("retro", "claude")
    assert agents.title_kind("retro") == ("retro", None)
    assert capture_mod.resolve_tab(_marked("codex"), {}, {norm(RETRO): 1}, REPOS) == (
        os.path.join(REPOS, "retro"), True)


def test_a_launched_tab_is_titled_with_its_kinds_mark():
    import reloaded.deploy as deploy_mod
    args = deploy_mod.new_tab_args(RETRO, "Write-Host hi", "codex")

    title = args[args.index("--title") + 1]
    assert title == "retro⁠" and title != _marked("claude")


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
    monkeypatch.setattr(main_mod.discover_mod, "census", lambda: discover_mod.Census(
        {norm(RETRO): [(1, "claude"), (2, "codex")]}))
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
    monkeypatch.setattr(main_mod.discover_mod, "census", lambda: discover_mod.Census(
        {norm(RETRO): [(1, "claude")]}))
    monkeypatch.setattr(main_mod.os.path, "isdir", lambda p: True)
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
    # Each agent's shell, then the terminal both shells run under.
    _machine(monkeypatch, {1: Proc(10, "claude.exe", 3.0), 2: Proc(20, "codex.exe", 3.0),
                           3: Proc(7, "codex.exe", 3.0), 10: Proc(5, "pwsh.exe", 2.0),
                           20: Proc(5, "pwsh.exe", 2.0), 7: Proc(0, "pwsh.exe", 2.0),
                           5: Proc(0, "WindowsTerminal.exe", 1.0)},
             cwds={1: RETRO, 2: RETRO, 3: META}, shells={10: 100, 20: 100})

    found = {(s.pid, s.hwnd) for s in real_sessions()}

    assert found == {(1, 100), (2, 100), (3, None)}


def test_a_tab_shell_far_up_the_line_still_places_its_session(monkeypatch):
    """The host search stops at eight ancestors; finding the tab does not."""
    line = {1: Proc(101, "claude.exe", 0.0)}
    line.update({101 + hop: Proc(102 + hop, "cmd.exe", -1.0 - hop) for hop in range(12)})
    _machine(monkeypatch, line, cwds={1: RETRO}, shells={112: 100})

    assert [(s.pid, s.hwnd) for s in real_sessions()] == [(1, 100)]


def _machine(monkeypatch, table, *, cwds, shells):
    monkeypatch.setattr(win32_mod, "process_table", lambda: table)
    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(
        Process=lambda pid: types.SimpleNamespace(
            cwd=lambda: cwds[pid], create_time=lambda: table[pid].created)))
    monkeypatch.setattr(win32_mod, "tab_shells", lambda: shells)
