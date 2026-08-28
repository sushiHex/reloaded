"""Closing a tab needs no keystroke, so it cannot hit the wrong tab.

Every tab exposes its own Close Tab button. Invoking it needs no focus, no
foreground and no synthesized input - which is the whole failure mode
select_tab exists to guard against. Verified by hand against a wedged tab that
Ctrl+D would not close, with three live sessions in the same window untouched.
"""
from __future__ import annotations

import reloaded.tabs as tabs_mod
import reloaded.teardown as teardown_mod


class _Invoke:
    def __init__(self):
        self.invoked = 0

    def Invoke(self):
        self.invoked += 1


class _Button:
    def __init__(self, name="Close Tab", raises=False):
        self.Name = name
        self.ControlTypeName = "ButtonControl"
        self._pattern = _Invoke()
        self._raises = raises

    def GetInvokePattern(self):
        if self._raises:
            raise RuntimeError("no invoke pattern")
        return self._pattern


class _Text:
    def __init__(self, name="some label"):
        self.Name = name
        self.ControlTypeName = "TextControl"


class _Item:
    def __init__(self, *children):
        self._children = list(children)

    def GetChildren(self):
        return list(self._children)


# ── the primitive ────────────────────────────────────────────────────────


def test_the_close_button_is_invoked():
    button = _Button()

    assert tabs_mod.close_tab(_Item(_Text(), button)) is True
    assert button._pattern.invoked == 1


def test_a_tab_with_no_close_button_reports_failure():
    assert tabs_mod.close_tab(_Item(_Text())) is False


def test_a_button_that_cannot_be_invoked_reports_failure():
    assert tabs_mod.close_tab(_Item(_Button(raises=True))) is False


def test_a_non_button_named_close_is_not_invoked():
    """Matching on the name alone would fire whatever happened to be called
    that - a label, a tooltip, a menu item."""
    assert tabs_mod.close_tab(_Item(_Text("Close Tab"))) is False


# ── its consumer ─────────────────────────────────────────────────────────


CWD_A = r"C:\repos\alpha"


def _plan(item, total_tabs=2):
    return teardown_mod.WindowPlan(
        hwnd=1, total_tabs=total_tabs,
        targets=[("alpha", CWD_A, 111, item)],
    )


def test_a_tab_whose_session_ended_but_did_not_close_is_closed():
    """A session started by hand leaves a plain interactive shell sitting at a
    prompt. The window survives, so WM_CLOSE is wrong; only this tab must go."""
    button = _Button()

    teardown_mod.close_dead_tabs(
        [_plan(_Item(button))],
        exited=[("alpha", CWD_A)],
        still_open=lambda item: True,
        log=lambda *_: None,
    )

    assert button._pattern.invoked == 1


def test_a_tab_that_closed_itself_is_left_alone():
    """Its shell ran `exit` and the tab is already gone. Invoking a button on
    a dead UIA element is a needless exception."""
    button = _Button()

    teardown_mod.close_dead_tabs(
        [_plan(_Item(button))],
        exited=[("alpha", CWD_A)],
        still_open=lambda item: False,
        log=lambda *_: None,
    )

    assert button._pattern.invoked == 0


def test_a_session_that_never_exited_keeps_its_tab():
    """It is still running. Closing its tab would kill it."""
    button = _Button()

    teardown_mod.close_dead_tabs(
        [_plan(_Item(button))],
        exited=[],
        still_open=lambda item: True,
        log=lambda *_: None,
    )

    assert button._pattern.invoked == 0


def test_the_number_closed_is_reported():
    """execute_down subtracts these from the window's remaining tab count."""
    assert teardown_mod.close_dead_tabs(
        [_plan(_Item(_Button()))],
        exited=[("alpha", CWD_A)],
        still_open=lambda item: True,
        log=lambda *_: None,
    ) == 1


# ── wired into the real teardown ─────────────────────────────────────────


def test_execute_down_closes_a_tab_the_shell_left_behind(monkeypatch):
    """Without this the primitive has no production caller, and a
    hand-launched session leaves a dead prompt in the strip after `down`."""
    import psutil

    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_exit_keystrokes", lambda **k: None)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: True)

    button = _Button()
    teardown_mod.execute_down([_plan(_Item(button), total_tabs=2)],
                              log=lambda *_: None)

    assert button._pattern.invoked == 1


def test_execute_down_leaves_the_tab_of_a_session_that_would_not_exit(monkeypatch):
    """It is still running behind that tab."""
    import psutil

    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(teardown_mod, "EXIT_RETRY_AFTER_SECONDS", 999)
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_exit_keystrokes", lambda **k: None)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: True)

    button = _Button()
    teardown_mod.execute_down([_plan(_Item(button), total_tabs=2)],
                              log=lambda *_: None)

    assert button._pattern.invoked == 0
