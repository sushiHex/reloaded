"""`restart` needs the tab to survive the exit. `down` needs it gone.

These run the REAL execute_down. Every other restart test replaces it with a
fake, which is why the interaction below went unnoticed: close_dead_tabs is
called inside execute_down, so a test that substitutes the whole function can
never see it fire.

The two commands want opposite things from the same tab. `down` is finished
with it and a hand-launched shell would otherwise leave a dead prompt sitting
in the strip. `restart` is about to put a session back into that exact tab -
by typing the launcher into the idle shell, or by letting the marker loop
relaunch inside it. Closing it is the one thing that makes both impossible.
"""
from __future__ import annotations

import types

import pytest

import reloaded.tabs as tabs_mod
import reloaded.teardown as teardown_mod
import reloaded.win32 as win32_mod
from reloaded.paths import norm

CWD = r"C:\repos\demo"


class _Item:
    """Stands in for a UIA tab element. Never touched for real."""


@pytest.fixture
def desktop(monkeypatch):
    """Fakes for every desktop effect execute_down reaches for."""
    seen = {"selected": [], "typed": [], "closed_tabs": [], "closed_windows": []}

    monkeypatch.setattr(tabs_mod, "select_tab",
                        lambda hwnd, item, **kw: seen["selected"].append(hwnd) or True)
    monkeypatch.setattr(tabs_mod, "send_quit_keystrokes",
                        lambda keys, **kw: seen["typed"].append(tuple(keys)))
    monkeypatch.setattr(tabs_mod, "close_tab",
                        lambda item: seen["closed_tabs"].append(item) or True)
    monkeypatch.setattr(win32_mod, "close_window",
                        lambda hwnd: seen["closed_windows"].append(hwnd) or True)
    monkeypatch.setattr(win32_mod, "is_wt_window", lambda hwnd: True)

    # The session ends the moment it is asked to. No polling, no waiting.
    import psutil
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(teardown_mod.time, "sleep", lambda s: None)
    return seen


def _plan(total_tabs=1, item=None):
    return teardown_mod.WindowPlan(
        hwnd=0x1234, total_tabs=total_tabs,
        targets=[teardown_mod.Target("demo", CWD, 4321, item or _Item())],
    )


def test_a_restart_leaves_the_tab_open_for_the_session_coming_back(desktop):
    """close_emptied=False says this window has to outlive the exit. The tab
    inside it is the whole reason - a restarted session comes back into that
    slot. Closing it takes the slot away and, on a single-tab window, the
    window with it."""
    teardown_mod.execute_down([_plan()], log=lambda *a: None,
                              close_emptied=False)

    assert desktop["closed_tabs"] == []


def test_a_plain_down_still_closes_the_dead_tab(desktop):
    """The behaviour that made close_dead_tabs worth having: a hand-launched
    shell returns to a prompt and sits there. Nothing is coming back to it."""
    teardown_mod.execute_down([_plan(total_tabs=2)], log=lambda *a: None)

    assert len(desktop["closed_tabs"]) == 1


def test_a_session_that_never_exited_keeps_its_tab(desktop, monkeypatch):
    """Closing the tab of a session that is still running kills it. That is
    the one thing a graceful teardown must never do."""
    import psutil
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.0)

    teardown_mod.execute_down([_plan(total_tabs=2)], log=lambda *a: None)

    assert desktop["closed_tabs"] == []


def test_the_codex_keys_reach_a_codex_tab(desktop):
    teardown_mod.execute_down([_plan(total_tabs=2)], log=lambda *a: None,
                              kinds={norm(CWD): "codex"})

    assert desktop["typed"] == [("{Ctrl}c", "{Ctrl}c")]
