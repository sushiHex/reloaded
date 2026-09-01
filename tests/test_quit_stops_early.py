"""A quit sequence stops the moment it stops being aimed at anything.

Selection is proved once, before the first key. The keys after it are more than
a second apart, because two interrupts sent together read as one and an Enter
sent immediately behind `/exit` arrives while the slash-command menu is still
filtering. That pause is also long enough for the session to end: a tab this
package launched closes itself, the terminal moves to whatever tab is next, and
the remaining keys land on a neighbour that was busy working.

Codex makes this concrete. It quits on a single interrupt, so the second one in
its pair has nothing left to interrupt - it is a loose keystroke with a 1.2
second fuse. `/exit` typed into a live session is how six of them died. A stray
Ctrl+C is the same mistake with a smaller blast radius, and there is no reason
to keep making it.
"""
from __future__ import annotations

import sys
import types

import pytest

import reloaded.tabs as tabs_mod
import reloaded.teardown as teardown_mod


@pytest.fixture
def keys(monkeypatch):
    log = []
    fake = types.ModuleType("uiautomation")
    fake.SendKeys = lambda text, **kw: log.append(text)
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return log


def test_the_whole_sequence_is_sent_while_it_is_still_wanted(keys):
    tabs_mod.send_quit_keystrokes(("{Ctrl}c", "{Ctrl}c"), still_needed=lambda: True)

    assert keys == ["{Esc}", "{Ctrl}c", "{Ctrl}c"]


def test_the_second_interrupt_is_withheld_once_the_session_is_gone(keys):
    """The one that would land on the next tab."""
    alive = [True, False]
    tabs_mod.send_quit_keystrokes(("{Ctrl}c", "{Ctrl}c"),
                                  still_needed=lambda: alive.pop(0))

    assert keys == ["{Esc}", "{Ctrl}c"]


def test_nothing_at_all_is_sent_when_the_target_has_already_gone(keys):
    """The first key is withheld too, and this used to be the opposite rule.

    The old reasoning was that select_tab had just proved the target, so
    asking again before the first key only re-litigated it. That confused two
    questions. Selection proves WHERE a keystroke goes. It says nothing about
    whether there is still anything there to receive it, and teardown handles
    its targets one at a time with a twenty-second wait on each - so a session
    can end on its own long before its turn arrives.

    A terminal was found holding about fifty copies of the word `/exit`,
    typed into a shell whose session had already ended. Escape is withheld on
    the same grounds: it is a keystroke like any other.
    """
    tabs_mod.send_quit_keystrokes(("/exit", "{Enter}"), still_needed=lambda: False)

    assert keys == []


def test_no_predicate_means_send_everything(keys):
    """Every existing caller, unchanged."""
    tabs_mod.send_quit_keystrokes(("/exit", "{Enter}"))

    assert keys == ["{Esc}", "/exit", "{Enter}"]


# ── what _send_exit asks ──────────────────────────────────────────────────


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


def _capture(monkeypatch):
    """Run _send_exit against fakes and hand back the predicate it built."""
    asked = {}
    monkeypatch.setattr(tabs_mod, "select_tab", lambda hwnd, item, **kw: True)

    def send(keys, *, dismiss_overlay=True, still_needed=None):
        asked["predicate"] = still_needed

    monkeypatch.setattr(teardown_mod.tabs, "send_quit_keystrokes", send)
    return asked


def test_a_dead_session_needs_no_more_keys(monkeypatch):
    asked = _capture(monkeypatch)
    import psutil
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)

    teardown_mod._send_exit(1, _Tab(), pid=4321)

    assert asked["predicate"]() is False


def test_a_tab_that_lost_the_selection_needs_no_more_keys(monkeypatch):
    """Still running, but no longer the tab in front. Typing now reaches
    whatever took its place."""
    asked = _capture(monkeypatch)
    import psutil
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)

    teardown_mod._send_exit(1, _Tab(selected=False), pid=4321)

    assert asked["predicate"]() is False


def test_a_live_session_in_a_selected_tab_gets_the_rest(monkeypatch):
    asked = _capture(monkeypatch)
    import psutil
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)

    teardown_mod._send_exit(1, _Tab(), pid=4321)

    assert asked["predicate"]() is True


def test_claude_still_gets_its_enter(monkeypatch):
    """`/exit` alone quits nothing, so the process is necessarily still alive
    when the predicate is asked. A gate that swallowed Enter would break every
    Claude teardown."""
    asked = _capture(monkeypatch)
    import psutil
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)

    teardown_mod._send_exit(1, _Tab(), quit_keys=("/exit", "{Enter}"), pid=4321)

    assert asked["predicate"]() is True
