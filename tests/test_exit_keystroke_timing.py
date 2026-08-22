"""/exit must not be submitted the instant it is typed.

Diagnosed by reading the terminal buffer back mid-keystroke. Typing `/` opens
Claude Code's slash-command menu, and the buffer showed that menu still
filtering while `/exit` sat in the prompt. Sent as one `"/exit{Enter}"` string,
Enter arrives about ten milliseconds after the final `t` - too soon, and the
session does not exit. The same keystrokes with a pause before Enter exited it
first time.

That is why `down` and `restart` timed out on session after session while
appearing to have typed correctly.
"""
from __future__ import annotations

import sys
import types

import pytest

import reloaded.tabs as tabs_mod


@pytest.fixture
def keys(monkeypatch):
    """Record every SendKeys call and the sleeps between them."""
    log = []

    fake = types.ModuleType("uiautomation")
    fake.SendKeys = lambda text, **kw: log.append(("keys", text))
    monkeypatch.setitem(sys.modules, "uiautomation", fake)

    real_sleep = __import__("time").sleep
    monkeypatch.setattr(
        tabs_mod.time if hasattr(tabs_mod, "time") else __import__("time"),
        "sleep",
        lambda s: log.append(("sleep", s)),
        raising=False,
    )
    monkeypatch.setattr("time.sleep", lambda s: log.append(("sleep", s)))
    return log


def _sent(log):
    return [t for kind, t in log if kind == "keys"]


def test_enter_is_not_attached_to_the_typed_command(keys):
    """`"/exit{Enter}"` in one call is the bug: Enter lands ~10ms after the
    final character, while the slash-command menu is still filtering."""
    tabs_mod.send_exit_keystrokes()

    assert not any("/exit{Enter}" in s for s in _sent(keys)), _sent(keys)


def test_exit_is_typed_then_submitted_separately(keys):
    tabs_mod.send_exit_keystrokes()

    sent = _sent(keys)
    assert "/exit" in sent, sent
    assert "{Enter}" in sent, sent
    assert sent.index("/exit") < sent.index("{Enter}")


def test_something_waits_between_typing_and_submitting(keys):
    """The menu needs time to settle on /exit before Enter chooses it."""
    tabs_mod.send_exit_keystrokes()

    typed = next(i for i, (k, t) in enumerate(keys) if t == "/exit")
    entered = next(i for i, (k, t) in enumerate(keys) if t == "{Enter}")
    waits = [s for k, s in keys[typed:entered] if k == "sleep"]
    assert waits, "Enter follows immediately, which is the failure"
    assert sum(waits) >= 0.5, f"only {sum(waits)}s - measured 1.2s working"


def test_the_overlay_escape_still_comes_first(keys):
    """Unchanged behaviour: Escape dismisses an away-summary overlay that
    would otherwise eat the first keystroke."""
    tabs_mod.send_exit_keystrokes()

    assert _sent(keys)[0] == "{Esc}"


def test_the_retry_still_skips_the_escape(monkeypatch):
    log = []
    fake = types.ModuleType("uiautomation")
    fake.SendKeys = lambda text, **kw: log.append(text)
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    monkeypatch.setattr("time.sleep", lambda s: None)

    tabs_mod.send_exit_keystrokes(dismiss_overlay=False)

    assert "{Esc}" not in log
    assert "/exit" in log and "{Enter}" in log
