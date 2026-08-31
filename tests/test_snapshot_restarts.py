"""What a restart reads off each session, and where it reads it from.

Three sources, each knowing strictly less than the one before: the live
process, then the saved layout, then the agent kind's own default.

The agent and the command are ONE fact and fall back together. Split apart they
can be sourced from different places and produce a pair that never existed -
a live-read agent of "codex" carrying a layout-read command line that runs
claude. launcher_command trusts the command over the agent, so the tab comes
back as the wrong CLI, which is the exact failure this branch exists to
prevent, arrived at from a new direction.
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
    """The three places a restart can learn what a session was running."""
    state = {"live_agent": None, "live_command": "", "saved": None}

    monkeypatch.setattr(main_mod.discover_mod, "transcript_index",
                        lambda *a, **k: {})
    monkeypatch.setattr(main_mod, "_transcript_sizes", lambda index: {})
    monkeypatch.setattr(main_mod.discover_mod, "launcher_kind",
                        lambda pid: main_mod.discover_mod.RELOADED)
    monkeypatch.setattr(
        main_mod.discover_mod, "live_agents",
        lambda: {norm(CWD): state["live_agent"]} if state["live_agent"] else {})
    monkeypatch.setattr(main_mod.discover_mod, "session_command",
                        lambda pid: state["live_command"])
    monkeypatch.setattr(
        main_mod, "_recorded_launches",
        lambda args: {norm(CWD): state["saved"]} if state["saved"] else {})
    return state


def _snapshot(sources) -> main_mod._Restarting:
    args = types.SimpleNamespace(layout="default")
    return main_mod._snapshot_restarts(_plans(), args)[0]


def test_the_live_process_wins(sources):
    sources["live_agent"] = "codex"
    sources["live_command"] = "codex resume --last"
    sources["saved"] = ("claude", "claude --continue")

    s = _snapshot(sources)

    assert (s.agent, s.command) == ("codex", "codex resume --last")


def test_the_layout_fills_in_an_unreadable_process(sources):
    """The pid is alive enough to sweep but its cmdline cannot be read."""
    sources["live_agent"] = "codex"
    sources["live_command"] = ""
    sources["saved"] = ("codex", "codex resume --last")

    s = _snapshot(sources)

    assert (s.agent, s.command) == ("codex", "codex resume --last")


def test_a_layout_that_disagrees_about_the_kind_lends_nothing(sources):
    """The repo has changed CLI since the last capture. Its old command line
    describes a different agent, and launcher_command would run it."""
    sources["live_agent"] = "codex"
    sources["live_command"] = ""
    sources["saved"] = ("claude", "claude --dangerously-skip-permissions")

    s = _snapshot(sources)

    assert s.agent == "codex"
    assert s.command == "", "claude's command line was paired with codex"


def test_that_pair_launches_the_agent_that_was_actually_running(sources):
    """Asserted on the script, because an empty command reads as harmless
    right up until launcher_command turns it into something."""
    sources["live_agent"] = "codex"
    sources["live_command"] = ""
    sources["saved"] = ("claude", "claude --dangerously-skip-permissions")

    s = _snapshot(sources)
    script = main_mod.deploy_mod.relaunch_script(s.cwd, 0, s.agent, s.command)

    assert "codex" in script
    assert "claude --dangerously-skip-permissions" not in script


def test_nothing_known_falls_back_to_the_default_kind(sources):
    s = _snapshot(sources)

    assert (s.agent, s.command) == (main_mod.agents_mod.DEFAULT_KIND, "")


def test_a_missing_live_kind_still_uses_the_layout(sources):
    """live_agents() is a separate process sweep from the one that produced
    these targets, so a session can be in the plan and absent from it."""
    sources["saved"] = ("codex", "codex resume --last")

    s = _snapshot(sources)

    assert (s.agent, s.command) == ("codex", "codex resume --last")


def test_everything_is_read_before_anything_is_asked_to_quit(sources):
    """The whole point of the record. Stated as a test so it cannot quietly
    stop being true: the snapshot is complete when it is returned."""
    sources["live_agent"] = "codex"
    sources["live_command"] = "codex resume --last"

    s = _snapshot(sources)

    assert s.pid == 111 and s.hwnd == 7 and s.title == "thing"
    assert s.launcher == main_mod.discover_mod.RELOADED
    assert s.key == norm(CWD)
