"""What a restart reads off each session, and where it reads it from.

Three sources, each knowing strictly less than the one before: the live
process, then the saved layout, then the agent kind's own default.

The agent and the command are ONE fact about ONE process, so they are read
together and fall back together. Split apart they can be sourced from
different places and produce a pair that never existed - agent "codex"
carrying a command line that runs claude, or the reverse. Teardown picks its
quit keystrokes from the agent while launcher_command picks the invocation
from the command, so a mismatched pair interrupts one CLI and relaunches the
other. That is the exact failure this branch exists to prevent, arrived at
from a new direction.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
from reloaded.paths import norm

CWD = r"C:\repos\thing"


class _Item:
    pass


def _plans():
    return [main_mod.teardown_mod.WindowPlan(
        hwnd=7, total_tabs=1,
        targets=[main_mod.teardown_mod.Target("thing", CWD, 111, _Item())])]


@pytest.fixture
def sources(monkeypatch):
    """The three places a restart can learn what a session was running.

    `live` is the pair session_launch returns from one process - both or
    neither. `sweep` is the separate cwd-keyed process sweep, which can hold a
    kind the pid read did not. `saved` is the layout.
    """
    state = {"live": ("", ""), "sweep": None, "saved": None}

    monkeypatch.setattr(main_mod.discover_mod, "transcript_index",
                        lambda *a, **k: {})
    monkeypatch.setattr(main_mod, "_transcript_sizes", lambda index: {})
    monkeypatch.setattr(main_mod.discover_mod, "launcher_kind",
                        lambda pid: main_mod.discover_mod.RELOADED)
    monkeypatch.setattr(
        main_mod.discover_mod, "live_agents",
        lambda: {norm(CWD): state["sweep"]} if state["sweep"] else {})
    monkeypatch.setattr(main_mod.discover_mod, "session_launch",
                        lambda pid: state["live"])
    monkeypatch.setattr(
        main_mod, "_recorded_launches",
        lambda args: {norm(CWD): state["saved"]} if state["saved"] else {})
    return state


def _snapshot(sources) -> main_mod._Restarting:
    args = types.SimpleNamespace(layout="default")
    return main_mod._snapshot_restarts(_plans(), args)[0]


def _script(s) -> str:
    """What this record would actually run. An empty command reads as harmless
    right up until launcher_command turns it into something."""
    return main_mod.deploy_mod.relaunch_script(s.cwd, 0, s.agent, s.command)


def test_the_live_process_wins(sources):
    sources["live"] = ("codex", "codex resume --last")
    sources["saved"] = ("claude", "claude --continue")

    s = _snapshot(sources)

    assert (s.agent, s.command) == ("codex", "codex resume --last")


def test_the_layout_fills_in_an_unreadable_process(sources):
    """Nothing could be read off the pid, but the sweep still names the kind."""
    sources["sweep"] = "codex"
    sources["saved"] = ("codex", "codex resume --last")

    s = _snapshot(sources)

    assert (s.agent, s.command) == ("codex", "codex resume --last")


def test_a_layout_that_disagrees_about_the_kind_lends_nothing(sources):
    """The repo has changed CLI since the last capture. Its old command line
    describes a different agent, and launcher_command would run it."""
    sources["sweep"] = "codex"
    sources["saved"] = ("claude", "claude --dangerously-skip-permissions")

    s = _snapshot(sources)

    assert s.agent == "codex"
    assert s.command == "", "claude's command line was paired with codex"
    assert "claude --dangerously-skip-permissions" not in _script(s)
    assert "codex" in _script(s)


def test_a_read_command_brings_its_own_kind_with_it(sources):
    """The other direction, and the one a fallback-only fix leaves open: the
    cwd-keyed sweep missed this session and the layout calls it codex, but the
    pid itself reads as claude. Trusting the layout here would send Ctrl+C to
    a Claude session and then relaunch it from claude's own command line."""
    sources["live"] = ("claude", "claude --continue")
    sources["sweep"] = None
    sources["saved"] = ("codex", "codex resume --last")

    s = _snapshot(sources)

    assert s.agent == "claude", "the layout overrode what the process said"
    assert s.command == "claude --continue"


def test_nothing_known_falls_back_to_the_default_kind(sources):
    s = _snapshot(sources)

    assert (s.agent, s.command) == (main_mod.agents_mod.DEFAULT_KIND, "")


def test_a_missing_live_kind_still_uses_the_layout(sources):
    """live_agents() is a separate sweep from the one that produced these
    targets, so a session can be in the plan and absent from it."""
    sources["saved"] = ("codex", "codex resume --last")

    s = _snapshot(sources)

    assert (s.agent, s.command) == ("codex", "codex resume --last")


def test_everything_is_read_before_anything_is_asked_to_quit(sources):
    """The whole point of the record. Stated as a test so it cannot quietly
    stop being true: the snapshot is complete when it is returned."""
    sources["live"] = ("codex", "codex resume --last")

    s = _snapshot(sources)

    assert s.pid == 111 and s.hwnd == 7
    assert s.launcher == main_mod.discover_mod.RELOADED
    assert s.key == norm(CWD)
