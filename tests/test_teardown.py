"""Tests for reloaded.teardown — the `down` command's plan/execute pair.

UIA (win32.list_wt_windows / tabs.list_tab_items) and the live-session set
are monkeypatched at the module boundary, matching test_capture.py's
convention for the same underlying dependencies.
"""
from __future__ import annotations

import psutil

import reloaded.teardown as teardown_mod
from reloaded.paths import norm

REPOS = r"C:\Users\k\repos"
CWD_A = r"C:\Users\k\repos\app-a"
CWD_B = r"C:\Users\k\repos\app-b"


def _stub_discovery(monkeypatch, *, live: dict, title_map: dict):
    monkeypatch.setattr(teardown_mod.discover, "live_sessions", lambda: dict(live))
    monkeypatch.setattr(teardown_mod.discover, "title_to_cwd", lambda index: dict(title_map))
    monkeypatch.setattr(teardown_mod.discover, "transcript_index", lambda: {})


class _Item:
    """Opaque stand-in for a TabItemControl — plan_down never inspects it,
    only threads it through to execute_down."""


# ── plan_down ────────────────────────────────────────────────────────────


def test_plan_down_groups_live_claude_tabs_by_window(monkeypatch):
    _stub_discovery(
        monkeypatch,
        live={norm(CWD_A): 111, norm(CWD_B): 222},
        title_map={},
    )
    monkeypatch.setattr(teardown_mod.win32, "list_wt_windows", lambda: [1, 2])
    monkeypatch.setattr(
        teardown_mod.tabs,
        "list_tab_items",
        lambda hwnd: {1: [("app-a", _Item())], 2: [("app-b", _Item())]}[hwnd],
    )

    plans = teardown_mod.plan_down(REPOS)

    assert [p.hwnd for p in plans] == [1, 2]
    assert plans[0].total_tabs == 1 and len(plans[0].targets) == 1
    title, cwd, pid, _item = plans[0].targets[0]
    assert title == "app-a"
    assert norm(cwd) == norm(CWD_A)
    assert pid == 111


def test_plan_down_reuses_a_precomputed_live_and_title_map(monkeypatch):
    """A caller that already has a discovery snapshot (restart, right after
    its own capture) can pass it straight through instead of paying for
    another psutil scan and transcript-corpus walk."""
    calls = []
    monkeypatch.setattr(
        teardown_mod.discover,
        "live_sessions",
        lambda: calls.append("live_sessions") or {},
    )
    monkeypatch.setattr(
        teardown_mod.discover,
        "transcript_index",
        lambda: calls.append("transcript_index") or {},
    )
    monkeypatch.setattr(teardown_mod.win32, "list_wt_windows", lambda: [1])
    monkeypatch.setattr(
        teardown_mod.tabs, "list_tab_items", lambda hwnd: [("app-a", _Item())]
    )

    plans = teardown_mod.plan_down(
        REPOS, live={norm(CWD_A): 111}, title_map={}
    )

    assert calls == []  # neither discovery call was made — the passthrough was used
    assert plans[0].targets[0][2] == 111  # pid came from the passed-in live dict


def test_plan_down_records_non_claude_tabs_as_extra_but_not_a_target(monkeypatch):
    _stub_discovery(monkeypatch, live={norm(CWD_A): 111}, title_map={})
    monkeypatch.setattr(teardown_mod.win32, "list_wt_windows", lambda: [1])
    monkeypatch.setattr(
        teardown_mod.tabs,
        "list_tab_items",
        lambda hwnd: [("app-a", _Item()), ("Windows PowerShell", _Item())],
    )

    plans = teardown_mod.plan_down(REPOS)

    assert len(plans) == 1
    assert plans[0].total_tabs == 2  # both tabs counted
    assert len(plans[0].targets) == 1  # only the Claude one is a target
    assert plans[0].targets[0][0] == "app-a"


def test_plan_down_skips_a_window_with_no_live_claude_tabs(monkeypatch):
    _stub_discovery(monkeypatch, live={}, title_map={})
    monkeypatch.setattr(teardown_mod.win32, "list_wt_windows", lambda: [1])
    monkeypatch.setattr(
        teardown_mod.tabs, "list_tab_items", lambda hwnd: [("Windows PowerShell", _Item())]
    )

    assert teardown_mod.plan_down(REPOS) == []


# ── execute_down ─────────────────────────────────────────────────────────


def _plan(hwnd, total_tabs, targets):
    return teardown_mod.WindowPlan(hwnd=hwnd, total_tabs=total_tabs, targets=targets)


def test_execute_down_closes_a_window_once_all_its_tabs_exit(monkeypatch):
    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_exit_keystrokes", lambda **k: None)
    # The pid no longer exists on the first poll, simulating a clean exit.
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    closed_hwnds = []
    monkeypatch.setattr(
        teardown_mod.win32, "close_window", lambda hwnd: closed_hwnds.append(hwnd) or True
    )

    plan = _plan(1, total_tabs=1, targets=[("app-a", CWD_A, 111, _Item())])
    result = teardown_mod.execute_down([plan], log=lambda *_: None)

    assert result["exited"] == [("app-a", CWD_A)]
    assert result["timed_out"] == []
    assert result["closed"] == [1]
    assert closed_hwnds == [1]


def test_execute_down_resends_exit_after_a_partial_wait(monkeypatch):
    """If a session hasn't exited by EXIT_RETRY_AFTER_SECONDS - the concrete
    case being Claude Code's own /exit confirmation when background agents
    are still running, which the first /exit alone does not satisfy -
    resending it once should give it another chance before the full
    timeout. The resend must NOT dismiss an overlay first: Escape would
    cancel that very confirmation instead of answering it."""
    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.02)
    monkeypatch.setattr(teardown_mod, "EXIT_RETRY_AFTER_SECONDS", 0.1)

    select_calls = []
    monkeypatch.setattr(
        teardown_mod.tabs, "select_tab", lambda hwnd, item: select_calls.append(1) or True
    )
    send_calls = []
    monkeypatch.setattr(
        teardown_mod.tabs,
        "send_exit_keystrokes",
        lambda *, dismiss_overlay=True: send_calls.append(dismiss_overlay),
    )
    # Still "running" until the retry (2nd send) has happened.
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: len(send_calls) < 2)
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: True)

    plan = _plan(1, total_tabs=1, targets=[("app-a", CWD_A, 111, _Item())])
    result = teardown_mod.execute_down([plan], log=lambda *_: None)

    # Initial send dismisses an overlay; the resend must not.
    assert send_calls == [True, False]
    assert select_calls == [1, 1]  # re-foregrounded before resending
    assert result["exited"] == [("app-a", CWD_A)]
    assert result["timed_out"] == []


def test_execute_down_only_resends_once(monkeypatch):
    """A session that still hasn't exited after the retry just times out
    normally - the resend is a single extra attempt, not a repeated poke."""
    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.02)
    monkeypatch.setattr(teardown_mod, "EXIT_RETRY_AFTER_SECONDS", 0.05)
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    send_calls = []
    monkeypatch.setattr(
        teardown_mod.tabs,
        "send_exit_keystrokes",
        lambda *, dismiss_overlay=True: send_calls.append(dismiss_overlay),
    )
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)  # never exits
    monkeypatch.setattr(
        teardown_mod.win32,
        "close_window",
        lambda hwnd: (_ for _ in ()).throw(AssertionError("must not close a holdout window")),
    )

    plan = _plan(1, total_tabs=1, targets=[("app-a", CWD_A, 111, _Item())])
    result = teardown_mod.execute_down([plan], log=lambda *_: None)

    assert send_calls == [True, False]  # initial + exactly one resend, never more
    assert result["timed_out"] == [("app-a", CWD_A)]


def test_execute_down_logs_when_the_resend_cannot_foreground(monkeypatch):
    """The retry's foreground failure must be logged like the initial send's
    is - silently discarding it left operators with no clue the resend
    never actually happened."""
    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.02)
    monkeypatch.setattr(teardown_mod, "EXIT_RETRY_AFTER_SECONDS", 0.05)
    select_calls = []

    def fake_select(hwnd, item):
        select_calls.append(1)
        return len(select_calls) == 1  # succeeds initially, fails on the retry

    monkeypatch.setattr(teardown_mod.tabs, "select_tab", fake_select)
    monkeypatch.setattr(teardown_mod.tabs, "send_exit_keystrokes", lambda **k: None)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)

    logs = []
    plan = _plan(1, total_tabs=1, targets=[("app-a", CWD_A, 111, _Item())])
    teardown_mod.execute_down([plan], log=logs.append)

    assert any("resend" in line and "app-a" in line for line in logs)


def test_execute_down_leaves_window_open_when_a_tab_times_out(monkeypatch):
    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_exit_keystrokes", lambda **k: None)
    # The pid never goes away -> the tab never "exits".
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(
        teardown_mod.win32,
        "close_window",
        lambda hwnd: (_ for _ in ()).throw(AssertionError("must not close a window with a holdout")),
    )

    plan = _plan(1, total_tabs=1, targets=[("app-a", CWD_A, 111, _Item())])
    result = teardown_mod.execute_down([plan], log=lambda *_: None)

    assert result["timed_out"] == [("app-a", CWD_A)]
    assert result["exited"] == []
    assert result["closed"] == []
    assert result["left_open"] == [1]


def test_execute_down_never_closes_a_window_with_a_non_claude_tab(monkeypatch):
    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_exit_keystrokes", lambda **k: None)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)  # the Claude tab exits
    monkeypatch.setattr(
        teardown_mod.win32,
        "close_window",
        lambda hwnd: (_ for _ in ()).throw(AssertionError("must not close a mixed window")),
    )

    # total_tabs=2 but only 1 target: a manual/plain tab shares this window.
    plan = _plan(1, total_tabs=2, targets=[("app-a", CWD_A, 111, _Item())])
    result = teardown_mod.execute_down([plan], log=lambda *_: None)

    assert result["exited"] == [("app-a", CWD_A)]
    assert result["closed"] == []
    assert result["left_open"] == [1]


def test_execute_down_never_types_into_a_window_that_did_not_actually_foreground(monkeypatch):
    """select_tab returning False means SetForegroundWindow was refused by
    the OS — sending keystrokes anyway could type '/exit' into whatever
    unrelated window actually has focus. Must skip, not guess."""
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: False)
    sent = []
    monkeypatch.setattr(teardown_mod.tabs, "send_exit_keystrokes", lambda **k: sent.append(1))
    monkeypatch.setattr(
        psutil,
        "pid_exists",
        lambda pid: (_ for _ in ()).throw(AssertionError("must not poll without sending /exit")),
    )
    monkeypatch.setattr(
        teardown_mod.win32,
        "close_window",
        lambda hwnd: (_ for _ in ()).throw(AssertionError("must not close on a foreground failure")),
    )

    plan = _plan(1, total_tabs=1, targets=[("app-a", CWD_A, 111, _Item())])
    result = teardown_mod.execute_down([plan], log=lambda *_: None)

    assert sent == []
    assert result["timed_out"] == [("app-a", CWD_A)]
    assert result["left_open"] == [1]


def test_execute_down_reports_close_failure_without_crashing(monkeypatch):
    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_exit_keystrokes", lambda **k: None)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: False)
    # Still a live window - it refused WM_CLOSE rather than having vanished,
    # which is what makes this a reportable failure instead of a self-close.
    monkeypatch.setattr(teardown_mod.win32, "is_wt_window", lambda hwnd: True)

    plan = _plan(1, total_tabs=1, targets=[("app-a", CWD_A, 111, _Item())])
    result = teardown_mod.execute_down([plan], log=lambda *_: None)

    assert result["closed"] == []
    assert result["left_open"] == [1]


def test_execute_down_counts_a_self_closed_window_as_closed(monkeypatch):
    """A window whose last tab closed itself is gone before WM_CLOSE lands.

    close_window returns False for a destroyed HWND for the same reason it does
    for a foreign one - the class check - so without is_wt_window this would be
    reported as a failure to close a window that is already exactly where the
    caller wanted it.
    """
    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_exit_keystrokes", lambda **k: None)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: False)
    monkeypatch.setattr(teardown_mod.win32, "is_wt_window", lambda hwnd: False)

    plan = _plan(1, total_tabs=1, targets=[("app-a", CWD_A, 111, _Item())])
    result = teardown_mod.execute_down([plan], log=lambda *_: None)

    assert result["closed"] == [1]
    assert result["left_open"] == []
