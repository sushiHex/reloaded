"""Where a quit keystroke can actually land, and when it is refused.

`SendKeys` follows the OS focus, not the element anyone named. Proving aim once
and then typing a sequence with second-long pauses in it is not proof at all -
it is proof about the moment before the pauses.

Three things have to hold, and they are three different questions:

  the session is still running   - or there is nothing to quit
  the tab is still selected      - inside its terminal
  the terminal still has focus   - which the tab's selection does not imply

The third is the one that hid. A notification, another application, or the user
clicking away moves focus while the tab stays exactly as selected as it was.
"""
from __future__ import annotations

import pytest

import reloaded.tabs as tabs_mod
import reloaded.teardown as teardown_mod
import reloaded.win32 as win32_mod


class _Tab:
    def __init__(self, selected=True):
        self.selected = selected

    def GetSelectionItemPattern(self):
        tab = self

        class _P:
            IsSelected = property(lambda _self: tab.selected)

            def Select(_self):
                tab.selected = True

        return _P()


@pytest.fixture
def predicate(monkeypatch):
    """Run _send_exit against fakes and hand back the test it built."""
    asked = {}
    monkeypatch.setattr(tabs_mod, "select_tab", lambda hwnd, item, **kw: True)

    def send(keys, *, dismiss_overlay=True, still_needed=None):
        asked["ask"] = still_needed
        return len(keys)

    monkeypatch.setattr(teardown_mod.tabs, "send_quit_keystrokes", send)
    return asked


def test_a_terminal_that_lost_focus_gets_no_more_keys(predicate, monkeypatch):
    """The gap a selection-only check leaves wide open. The tab is selected,
    the session is alive, and the keystroke goes to whatever is in front."""
    import psutil
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(win32_mod, "is_foreground", lambda hwnd: False)

    teardown_mod._send_exit(1, _Tab(), pid=4321)

    assert predicate["ask"]() is False


def test_a_focused_terminal_showing_the_right_tab_gets_the_rest(predicate, monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(win32_mod, "is_foreground", lambda hwnd: True)

    teardown_mod._send_exit(1, _Tab(), pid=4321)

    assert predicate["ask"]() is True


def test_the_foreground_is_checked_against_this_window(predicate, monkeypatch):
    """Not "is any terminal in front" - is THIS one."""
    asked_for = []
    import psutil
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(win32_mod, "is_foreground",
                        lambda hwnd: asked_for.append(hwnd) or True)

    teardown_mod._send_exit(0xABC, _Tab(), pid=4321)
    predicate["ask"]()

    assert asked_for == [0xABC]


# ── what the caller is told ──────────────────────────────────────────────


def test_an_unreachable_tab_reports_that_nothing_was_typed(monkeypatch):
    monkeypatch.setattr(tabs_mod, "select_tab", lambda hwnd, item, **kw: False)

    sent = teardown_mod._send_exit(1, _Tab(), pid=4321)

    assert sent.reached is False
    assert sent.keys == 0


def test_a_target_that_had_already_gone_reports_zero_keys(monkeypatch):
    """Reached, and correctly typed nothing. Reporting "sent /exit" here would
    describe something that did not happen, in the log a user reads to decide
    whether to go looking."""
    monkeypatch.setattr(tabs_mod, "select_tab", lambda hwnd, item, **kw: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_quit_keystrokes",
                        lambda keys, **kw: 0)

    sent = teardown_mod._send_exit(1, _Tab(), pid=4321)

    assert sent.reached is True
    assert sent.keys == 0


def test_a_partial_send_is_not_reported_as_a_whole_one(monkeypatch):
    monkeypatch.setattr(tabs_mod, "select_tab", lambda hwnd, item, **kw: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_quit_keystrokes",
                        lambda keys, **kw: 1)

    sent = teardown_mod._send_exit(1, _Tab(), quit_keys=("a", "b"), pid=4321)

    assert sent.keys == 1


def test_the_log_says_already_gone_rather_than_sent(monkeypatch):
    """End to end through execute_down, because the log line is the only
    consumer of the count and a test of the count alone proves nothing."""
    import psutil
    monkeypatch.setattr(tabs_mod, "select_tab", lambda hwnd, item, **kw: True)
    monkeypatch.setattr(tabs_mod, "send_quit_keystrokes", lambda keys, **kw: 0)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(win32_mod, "close_window", lambda hwnd: True)
    monkeypatch.setattr(win32_mod, "is_wt_window", lambda hwnd: True)
    monkeypatch.setattr(teardown_mod.time, "sleep", lambda s: None)

    lines = []
    plan = teardown_mod.WindowPlan(
        hwnd=1, total_tabs=2,
        targets=[teardown_mod.Target("app", r"C:\repos\app", 9, _Tab())])
    result = teardown_mod.execute_down([plan], log=lines.append)

    joined = "\n".join(lines)
    assert "had already gone" in joined
    assert "sent /exit" not in joined
    assert result["exited"] == [("app", r"C:\repos\app")]
