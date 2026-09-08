"""execute_down's before_exit hook — arming a restart marker just in time.

`restart <repo>` used to write every marker up front. Each marker then had to
stay valid for the whole batch, because targets are exited serially with a
20s wait each: the sixth marker's age reached the 120s TTL before its tab was
ever asked to exit, and a wedged UIA selection made the wait unbounded. Writing
each marker immediately before its own /exit bounds every marker's age by one
tab's exit, not by the batch.
"""
from __future__ import annotations

import psutil
import pytest

import reloaded.teardown as teardown_mod

CWD_A = r"C:\repos\app-a"
CWD_B = r"C:\repos\app-b"


class _Item:
    """Opaque stand-in for a TabItemControl."""


@pytest.fixture
def clean_exit(monkeypatch):
    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_quit_keystrokes", lambda *a, **k: None)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: True)


def _plan(targets):
    """Bare tuples in, real Targets out - a fixture that cannot be told apart
    from the real thing is how a whole integration went untested."""
    targets = [teardown_mod.Target(*t) for t in targets]
    return teardown_mod.WindowPlan(hwnd=1, total_tabs=len(targets), targets=targets)


def test_before_exit_runs_for_each_target(clean_exit):
    armed = []
    plan = _plan([("app-a", CWD_A, 111, _Item()), ("app-b", CWD_B, 222, _Item())])

    teardown_mod.execute_down([plan], log=lambda *_: None, before_exit=armed.append)

    assert armed == [CWD_A, CWD_B]


def test_before_exit_runs_before_that_target_is_asked_to_exit(clean_exit, monkeypatch):
    """Armed afterwards, the shell would already have decided to close."""
    order = []
    monkeypatch.setattr(
        teardown_mod.tabs, "send_quit_keystrokes",
        lambda *a, **k: order.append("exit"),
    )
    plan = _plan([("app-a", CWD_A, 111, _Item())])

    teardown_mod.execute_down(
        [plan], log=lambda *_: None, before_exit=lambda cwd: order.append("arm")
    )

    assert order == ["arm", "exit"]


def test_each_target_is_armed_only_once_its_turn_comes(clean_exit, monkeypatch):
    """The whole point: app-b must not be armed while app-a is still exiting,
    or its marker ages through app-a's wait for nothing."""
    order = []
    monkeypatch.setattr(
        teardown_mod.tabs, "send_quit_keystrokes",
        lambda *a, **k: order.append("exit"),
    )
    plan = _plan([("app-a", CWD_A, 111, _Item()), ("app-b", CWD_B, 222, _Item())])

    teardown_mod.execute_down(
        [plan], log=lambda *_: None, before_exit=lambda cwd: order.append(f"arm:{cwd}")
    )

    assert order == [f"arm:{CWD_A}", "exit", f"arm:{CWD_B}", "exit"]


def test_a_target_that_cannot_be_foregrounded_is_never_armed(clean_exit, monkeypatch):
    """Nothing is typed into it, so it will not read a marker - and an armed
    one left on disk is a booby trap for the user's next manual exit."""
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: False)
    armed = []
    plan = _plan([("app-a", CWD_A, 111, _Item())])

    teardown_mod.execute_down([plan], log=lambda *_: None, before_exit=armed.append)

    assert armed == []


def test_execute_down_still_works_with_no_hook(clean_exit):
    plan = _plan([("app-a", CWD_A, 111, _Item())])

    result = teardown_mod.execute_down([plan], log=lambda *_: None)

    assert result["exited"] == [("app-a", CWD_A)]
