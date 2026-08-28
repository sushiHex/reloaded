"""_type_relaunch: what it writes, and when it is allowed to type.

Two failures this pins, both found reviewing the branch before merge rather
than by a test failing.
"""
from __future__ import annotations

import sys
import types

import pytest

import reloaded.__main__ as main_mod

CWD = r"C:\repos\by-hand"


@pytest.fixture
def typed(monkeypatch, tmp_path):
    """Record what gets typed, and the order of select vs type."""
    events = []

    fake = types.ModuleType("uiautomation")
    fake.SendKeys = lambda text, **kw: events.append(("type", text))
    monkeypatch.setitem(sys.modules, "uiautomation", fake)

    monkeypatch.setattr(main_mod, "relaunch_script_path",
                        lambda cwd: tmp_path / "relaunch.ps1")
    monkeypatch.setattr(main_mod.tabs_mod, "select_tab",
                        lambda hwnd, item: events.append(("select", None)) or True)
    monkeypatch.setattr("time.sleep", lambda s: events.append(("sleep", s)))
    return events, tmp_path / "relaunch.ps1"


def test_a_codex_tab_gets_the_codex_launcher(typed):
    """The bug: it called relaunch_script with defaults, so a hand-launched
    Codex session was handed the CLAUDE launcher and came back as Claude."""
    _events, script = typed

    main_mod._type_relaunch(1, object(), CWD, 0, agent="codex")

    body = script.read_text(encoding="utf-8")
    assert "codex resume --last" in body
    assert "claude --dangerously-skip-permissions" not in body


def test_a_captured_command_is_used(typed):
    _events, script = typed

    main_mod._type_relaunch(1, object(), CWD, 0, agent="codex",
                            command="codex --profile fast")

    assert "codex --profile fast" in script.read_text(encoding="utf-8")


def test_a_claude_tab_is_unchanged(typed):
    _events, script = typed

    main_mod._type_relaunch(1, object(), CWD, 0)

    assert "claude --dangerously-skip-permissions" in script.read_text(encoding="utf-8")


def test_nothing_is_typed_between_confirming_the_tab_and_typing(typed):
    """It used to confirm the tab, then sleep a whole second, then type. A
    second is ample for focus to move, and typing at an unconfirmed target is
    exactly how a test suite once put /exit into six live sessions.
    """
    events, _script = typed

    main_mod._type_relaunch(1, object(), CWD, 0)

    kinds = [k for k, _v in events]
    assert kinds.index("select") == kinds.index("type") - 1, kinds


def test_the_shell_is_still_given_time_to_settle(typed):
    """The wait is still needed - the session has only just ended - it just
    happens before the tab is claimed rather than after."""
    events, _script = typed

    main_mod._type_relaunch(1, object(), CWD, 0)

    kinds = [k for k, _v in events]
    assert "sleep" in kinds
    assert kinds.index("sleep") < kinds.index("select")


def test_a_tab_that_cannot_be_confirmed_is_never_typed_into(monkeypatch, typed):
    events, _script = typed
    monkeypatch.setattr(main_mod.tabs_mod, "select_tab", lambda hwnd, item: False)

    assert main_mod._type_relaunch(1, object(), CWD, 0) is False
    assert not any(k == "type" for k, _v in events)
