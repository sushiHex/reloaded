"""plan_down's repo filter — the selection half of `restart <repo>`.

Follows test_teardown.py's convention: UIA and the live-session set are
monkeypatched at the module boundary.
"""
from __future__ import annotations

import reloaded.teardown as teardown_mod
from reloaded.paths import norm

REPOS = r"C:\Users\k\repos"
CWD_A = r"C:\Users\k\repos\app-a"
CWD_B = r"C:\Users\k\repos\app-b"
CWD_C = r"C:\Users\k\repos\app-c"


class _Item:
    """Opaque stand-in for a TabItemControl."""


def _stub(monkeypatch, windows: dict[int, list[str]], live: dict[str, int]):
    monkeypatch.setattr(teardown_mod.discover, "live_sessions", lambda: dict(live))
    monkeypatch.setattr(teardown_mod.discover, "title_to_cwd", lambda index: {})
    monkeypatch.setattr(teardown_mod.discover, "transcript_index", lambda: {})
    monkeypatch.setattr(teardown_mod.win32, "list_wt_windows", lambda: list(windows))
    monkeypatch.setattr(
        teardown_mod.tabs,
        "list_tab_items",
        lambda hwnd: [(t, _Item()) for t in windows[hwnd]],
    )


def _cwds(plans):
    return sorted(norm(cwd) for p in plans for _t, cwd, _p, _i in p.targets)


def test_no_filter_still_selects_everything(monkeypatch):
    """`restart` with no arguments must keep doing exactly what it did."""
    _stub(monkeypatch, {1: ["app-a", "app-b"]},
          {norm(CWD_A): 111, norm(CWD_B): 222})

    plans = teardown_mod.plan_down(REPOS)

    assert _cwds(plans) == sorted([norm(CWD_A), norm(CWD_B)])


def test_filter_keeps_only_the_named_repo(monkeypatch):
    _stub(monkeypatch, {1: ["app-a", "app-b"]},
          {norm(CWD_A): 111, norm(CWD_B): 222})

    plans = teardown_mod.plan_down(REPOS, only=[CWD_B])

    assert _cwds(plans) == [norm(CWD_B)]


def test_filter_accepts_several_repos(monkeypatch):
    """The user asked for "specific session(s)" — plural is the normal case."""
    _stub(monkeypatch, {1: ["app-a", "app-b", "app-c"]},
          {norm(CWD_A): 111, norm(CWD_B): 222, norm(CWD_C): 333})

    plans = teardown_mod.plan_down(REPOS, only=[CWD_A, CWD_C])

    assert _cwds(plans) == sorted([norm(CWD_A), norm(CWD_C)])


def test_a_window_with_nothing_selected_is_dropped_entirely(monkeypatch):
    """execute_down closes a window once every one of its tabs exited. A window
    that contributes no targets must not appear at all, or a filtered restart
    would close windows it was never asked to touch."""
    _stub(monkeypatch, {1: ["app-a"], 2: ["app-b"]},
          {norm(CWD_A): 111, norm(CWD_B): 222})

    plans = teardown_mod.plan_down(REPOS, only=[CWD_B])

    assert [p.hwnd for p in plans] == [2]


def test_a_partly_selected_window_still_reports_its_real_tab_count(monkeypatch):
    """total_tabs is what stops execute_down closing a window that still holds
    something. Filtering the targets must not shrink it, or restarting one tab
    of three would take the other two down with the window."""
    _stub(monkeypatch, {1: ["app-a", "app-b", "app-c"]},
          {norm(CWD_A): 111, norm(CWD_B): 222, norm(CWD_C): 333})

    plans = teardown_mod.plan_down(REPOS, only=[CWD_B])

    assert len(plans) == 1
    assert len(plans[0].targets) == 1
    assert plans[0].total_tabs == 3


def test_filter_matches_regardless_of_case_or_separator(monkeypatch):
    """The filter value comes from a typed argument via resolve_repo; the cwd
    it is compared against came from psutil."""
    _stub(monkeypatch, {1: ["app-a"]}, {norm(CWD_A): 111})

    plans = teardown_mod.plan_down(REPOS, only=[r"c:/users/k/repos/APP-A"])

    assert _cwds(plans) == [norm(CWD_A)]


def test_an_unknown_repo_selects_nothing(monkeypatch):
    """Rather than silently falling back to every session."""
    _stub(monkeypatch, {1: ["app-a"]}, {norm(CWD_A): 111})

    assert teardown_mod.plan_down(REPOS, only=[r"C:\Users\k\repos\nope"]) == []


# ── execute_down, close_windows ──────────────────────────────────────────


def _exit_cleanly(monkeypatch):
    """Every tab reports its pid gone on the first poll."""
    import psutil

    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_exit_keystrokes", lambda **k: None)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    closed = []
    monkeypatch.setattr(
        teardown_mod.win32, "close_window", lambda hwnd: closed.append(hwnd) or True
    )
    return closed


def _one_tab_window():
    return teardown_mod.WindowPlan(
        hwnd=1, total_tabs=1, targets=[("app-a", CWD_A, 111, _Item())]
    )


def test_execute_down_still_closes_an_emptied_window_by_default(monkeypatch):
    closed = _exit_cleanly(monkeypatch)

    result = teardown_mod.execute_down([_one_tab_window()], log=lambda *_: None)

    assert closed == [1]
    assert result["closed"] == [1]


def test_close_windows_false_leaves_the_window_standing(monkeypatch):
    """A restarted tab relaunches inside the shell it already had, so its
    window must survive the exit. Left to its default, execute_down sees
    len(targets) == total_tabs on a single-session window and sends WM_CLOSE
    to the window the tab is about to come back in."""
    closed = _exit_cleanly(monkeypatch)

    result = teardown_mod.execute_down(
        [_one_tab_window()], log=lambda *_: None, close_windows=False
    )

    assert closed == [], "WM_CLOSE was sent to a window that must stay open"
    assert result["closed"] == []


def test_close_windows_false_does_not_blame_non_claude_tabs(monkeypatch):
    """The default path explains a surviving window by saying it holds tabs we
    did not start. Under close_windows=False that reason is invented — the
    window survived because the caller asked it to."""
    _exit_cleanly(monkeypatch)
    lines = []

    teardown_mod.execute_down(
        [_one_tab_window()], log=lines.append, close_windows=False
    )

    assert not any("non-Claude tabs" in line for line in lines), lines


def test_close_windows_false_still_reports_which_sessions_exited(monkeypatch):
    """The caller needs the exit result to tell a relaunch from a timeout."""
    _exit_cleanly(monkeypatch)

    result = teardown_mod.execute_down(
        [_one_tab_window()], log=lambda *_: None, close_windows=False
    )

    assert result["exited"] == [("app-a", CWD_A)]
    assert result["timed_out"] == []
