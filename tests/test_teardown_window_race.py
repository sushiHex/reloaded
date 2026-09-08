"""A window is not closed on a tab count taken minutes ago.

`total_tabs` is counted when the plan is built. Teardown then spends up to
EXIT_TIMEOUT_SECONDS on every target in turn, so a batch of six holdouts is two
minutes of real time in which the desktop is not frozen. The user opens a tab.
`reloaded open` opens one. A concurrent `up` opens one.

That tab is in neither `targets` nor `total_tabs`, so `len(targets) ==
total_tabs` still reads true and WM_CLOSE takes the whole window - the new
session with it. Every tab this teardown accounted for has closed itself or
been closed by close_dead_tabs, so anything still standing is something else,
and the count is re-asked rather than remembered.
"""
from __future__ import annotations

import pytest

import reloaded.tabs as tabs_mod
import reloaded.teardown as teardown_mod
import reloaded.win32 as win32_mod

CWD = r"C:\repos\app"


class _Item:
    pass


@pytest.fixture
def desktop(monkeypatch):
    seen = {"closed_windows": [], "tabs_now": []}

    import psutil
    monkeypatch.setattr(tabs_mod, "select_tab", lambda hwnd, item, **kw: True)
    monkeypatch.setattr(tabs_mod, "send_quit_keystrokes", lambda keys, **kw: len(keys))
    monkeypatch.setattr(tabs_mod, "close_tab", lambda item: True)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(teardown_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(win32_mod, "is_wt_window", lambda hwnd: True)
    monkeypatch.setattr(win32_mod, "close_window",
                        lambda hwnd: seen["closed_windows"].append(hwnd) or True)
    monkeypatch.setattr(tabs_mod, "list_tab_items", lambda hwnd: seen["tabs_now"])
    return seen


def _plan():
    return teardown_mod.WindowPlan(
        hwnd=0x10, total_tabs=1,
        targets=[teardown_mod.Target("app", CWD, 111, _Item())])


def test_an_emptied_window_is_still_closed(desktop):
    """The behaviour the re-count must not cost: nothing left, so close it."""
    desktop["tabs_now"] = []

    teardown_mod.execute_down([_plan()], log=lambda *a: None)

    assert desktop["closed_windows"] == [0x10]


def test_a_tab_opened_during_teardown_is_not_closed_over(desktop):
    desktop["tabs_now"] = [("new-repo", _Item())]

    teardown_mod.execute_down([_plan()], log=lambda *a: None)

    assert desktop["closed_windows"] == []


def test_the_user_is_told_why_the_window_was_left(desktop):
    """Silence here reads as "it refused to close", which is a different
    problem with a different answer."""
    desktop["tabs_now"] = [("new-repo", _Item())]
    lines = []

    teardown_mod.execute_down([_plan()], log=lines.append)

    joined = "\n".join(lines).lower()
    assert "gained a tab" in joined


def test_that_window_is_reported_as_left_open(desktop):
    desktop["tabs_now"] = [("new-repo", _Item())]

    result = teardown_mod.execute_down([_plan()], log=lambda *a: None)

    assert result["left_open"] == [0x10]
    assert result["closed"] == []


def test_an_unreadable_window_does_not_block_the_close(desktop, monkeypatch):
    """A window that cannot be read is not evidence that something is in it,
    and this check only ever gates NOT closing. Failing closed here would
    strand a window on every UIA hiccup."""
    monkeypatch.setattr(tabs_mod, "list_tab_items",
                        lambda hwnd: (_ for _ in ()).throw(OSError("uia")))

    teardown_mod.execute_down([_plan()], log=lambda *a: None)

    assert desktop["closed_windows"] == [0x10]


def test_a_window_with_untouched_tabs_still_says_so(desktop):
    """The pre-existing reason for leaving a window open, which must not get
    reworded into the new one."""
    desktop["tabs_now"] = []
    plan = teardown_mod.WindowPlan(
        hwnd=0x10, total_tabs=3,
        targets=[teardown_mod.Target("app", CWD, 111, _Item())])
    lines = []

    teardown_mod.execute_down([plan], log=lines.append)

    joined = "\n".join(lines).lower()
    assert "did not touch" in joined
    assert "gained a tab" not in joined
