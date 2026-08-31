"""Each kind is quit with its own keystrokes.

Sent one at a time with a pause between, never joined: Claude Code's
slash-command menu will not accept an Enter arriving right behind the command,
and two interrupts sent together read as one.
"""
from __future__ import annotations

import sys
import types

import pytest

import reloaded.tabs as tabs_mod


@pytest.fixture
def keys(monkeypatch):
    log = []
    fake = types.ModuleType("uiautomation")
    fake.SendKeys = lambda text, **kw: log.append(("keys", text))
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    monkeypatch.setattr("time.sleep", lambda s: log.append(("sleep", s)))
    return log


def _sent(log):
    return [t for kind, t in log if kind == "keys"]


def test_claude_keys_are_typed_then_submitted(keys):
    tabs_mod.send_quit_keystrokes(("/exit", "{Enter}"))

    assert _sent(keys) == ["{Esc}", "/exit", "{Enter}"]


def test_codex_keys_are_two_interrupts(keys):
    """Measured on a disposable session: the target died, /quit did nothing."""
    tabs_mod.send_quit_keystrokes(("{Ctrl}c", "{Ctrl}c"))

    assert _sent(keys) == ["{Esc}", "{Ctrl}c", "{Ctrl}c"]


def test_a_pause_separates_every_send(keys):
    tabs_mod.send_quit_keystrokes(("/exit", "{Enter}"))

    typed = [i for i, (k, t) in enumerate(keys) if t == "/exit"][0]
    entered = [i for i, (k, t) in enumerate(keys) if t == "{Enter}"][0]
    waits = [s for k, s in keys[typed:entered] if k == "sleep"]
    assert waits and sum(waits) >= 0.5


def test_two_interrupts_are_not_sent_back_to_back(keys):
    """Joined, they read as one interrupt and the session survives."""
    tabs_mod.send_quit_keystrokes(("{Ctrl}c", "{Ctrl}c"))

    first = [i for i, (k, t) in enumerate(keys) if t == "{Ctrl}c"][0]
    second = [i for i, (k, t) in enumerate(keys) if t == "{Ctrl}c"][1]
    assert any(k == "sleep" for k, _t in keys[first:second])


def test_the_retry_skips_the_escape(keys):
    """Escape would cancel the very confirmation a retry exists to answer."""
    tabs_mod.send_quit_keystrokes(("{Ctrl}c",), dismiss_overlay=False)

    assert "{Esc}" not in _sent(keys)


def test_the_old_entry_point_still_sends_claude_keys(keys):
    """Callers predating agent kinds keep working."""
    tabs_mod.send_exit_keystrokes()

    assert _sent(keys) == ["{Esc}", "/exit", "{Enter}"]


# ── wired into teardown ──────────────────────────────────────────────────

import reloaded.teardown as teardown_mod  # noqa: E402
from reloaded.paths import norm  # noqa: E402

CWD = r"C:\repos\beta"


class _Item:
    def GetChildren(self):
        return []


def _run(monkeypatch, kinds):
    import psutil

    sent = []
    monkeypatch.setattr(teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(teardown_mod, "EXIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_quit_keystrokes",
                        lambda keys, **kw: sent.append(tuple(keys)))
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: True)

    plan = teardown_mod.WindowPlan(
        hwnd=1, total_tabs=1, targets=[teardown_mod.Target("beta", CWD, 222, _Item())]
    )
    teardown_mod.execute_down([plan], log=lambda *_: None, kinds=kinds)
    return sent


def test_a_codex_target_is_sent_two_interrupts(monkeypatch):
    assert _run(monkeypatch, {norm(CWD): "codex"}) == [("{Ctrl}c", "{Ctrl}c")]


def test_a_claude_target_is_sent_exit(monkeypatch):
    assert _run(monkeypatch, {norm(CWD): "claude"}) == [("/exit", "{Enter}")]


def test_an_unknown_target_is_treated_as_claude(monkeypatch):
    """Which is what every teardown before agent kinds assumed."""
    assert _run(monkeypatch, {}) == [("/exit", "{Enter}")]


def test_kinds_may_be_omitted_entirely(monkeypatch):
    assert _run(monkeypatch, None) == [("/exit", "{Enter}")]
