"""Adding a repo by hand records which agent it runs.

`a <window> <repo>` is how a repo that is not currently open gets into a
layout. It built a Tab with no agent and no command, so every repo added this
way was recorded as Claude Code - including a Codex repo running in front of
the user at the moment they typed the command. Nothing showed it either: the
listing had no way to display an agent kind, so the wrong value stayed
invisible until the next deploy launched the wrong CLI.
"""
from __future__ import annotations

import pytest

import reloaded.discover as discover_mod
import reloaded.editor as editor_mod
import reloaded.layout as layout_mod
from conftest import make_layout, make_window
from reloaded.layout import Tab
from reloaded.paths import norm

CWD = r"C:\repos\thing"


@pytest.fixture
def live(monkeypatch):
    """What discovery reports for the repo about to be added."""
    state = {"agents": {}, "commands": {}}

    monkeypatch.setattr(discover_mod, "live_agents",
                        lambda: {norm(c): k for c, k in state["agents"].items()})
    monkeypatch.setattr(discover_mod, "live_sessions",
                        lambda: {norm(c): 99 for c in state["agents"]})
    monkeypatch.setattr(discover_mod, "session_command",
                        lambda pid: state["commands"].get(pid, ""))
    monkeypatch.setattr(editor_mod, "resolve_repo", lambda ref, root: CWD)
    monkeypatch.setattr(editor_mod.os.path, "isdir", lambda p: True)
    return state


def _run(monkeypatch, tmp_path, *commands) -> layout_mod.Layout:
    lo = make_layout([make_window([0, 0, 800, 600],
                                  [Tab(cwd=r"C:\repos\other", title="other")])])
    typed = iter(list(commands) + ["q", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))
    editor_mod.run(lo, tmp_path / "default.json", r"C:\repos")
    return lo


def test_a_live_codex_repo_is_added_as_codex(live, monkeypatch, tmp_path):
    live["agents"][CWD] = "codex"
    live["commands"][99] = "codex resume --last"

    lo = _run(monkeypatch, tmp_path, "a 1 thing")

    added = lo.windows[0].tabs[-1]
    assert added.agent == "codex"
    assert added.command == "codex resume --last"


def test_a_repo_that_is_not_running_falls_back_to_the_default(live, monkeypatch, tmp_path):
    """The ordinary use of `a`: pinning a repo precisely because it is closed.
    There is nothing to read, so the default kind is the honest answer."""
    lo = _run(monkeypatch, tmp_path, "a 1 thing")

    added = lo.windows[0].tabs[-1]
    assert added.agent == "claude"
    assert added.command == ""


def test_discovery_failing_does_not_take_the_editor_down(live, monkeypatch, tmp_path):
    """psutil is not needed to edit a layout. An editor that cannot add a repo
    because a process sweep raised would be a poor trade."""
    monkeypatch.setattr(discover_mod, "live_agents",
                        lambda: (_ for _ in ()).throw(OSError("no psutil")))

    lo = _run(monkeypatch, tmp_path, "a 1 thing")

    assert lo.windows[0].tabs[-1].agent == "claude"


def test_the_listing_shows_a_non_default_agent():
    """A tab that launches a different CLI and looks identical to one that
    does not is the whole problem restated."""
    assert "codex" in layout_mod.tab_flags(Tab(cwd=CWD, title="t", agent="codex"))


def test_the_listing_stays_quiet_for_the_default_agent():
    assert layout_mod.tab_flags(Tab(cwd=CWD, title="t")) == ""
