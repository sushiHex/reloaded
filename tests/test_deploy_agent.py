"""Each tab relaunches with its own agent's command.

Only the invocation in the middle differs by kind. The environment hygiene
around it, the restart loop and the self-closing guard are properties of the
shell and apply to every tab.
"""
from __future__ import annotations

import pytest

import reloaded.deploy as deploy_mod
from reloaded.deploy import launcher_command

CWD = r"C:\repos\beta"


@pytest.fixture(autouse=True)
def _pin_shell(monkeypatch):
    monkeypatch.setattr(deploy_mod, "shell_executable", lambda: "pwsh")


def test_a_claude_tab_is_unchanged():
    assert "claude --dangerously-skip-permissions --continue" in \
        launcher_command(CWD, 0, 0)


def test_a_codex_tab_uses_the_codex_default():
    cmd = launcher_command(CWD, 0, 0, agent="codex")

    assert "codex resume --last --dangerously-bypass-approvals-and-sandbox" in cmd
    assert "claude --dangerously-skip-permissions" not in cmd


def test_a_captured_command_wins_over_the_default():
    """The whole point of capturing it: the flags are the user's choice."""
    cmd = launcher_command(CWD, 0, 0, agent="codex",
                           command="codex --profile fast")

    assert "codex --profile fast" in cmd
    assert "resume --last" not in cmd


def test_a_captured_command_is_used_for_claude_too():
    cmd = launcher_command(CWD, 0, 0, agent="claude",
                           command="claude --continue --model opus")

    assert "claude --continue --model opus" in cmd
    assert "--dangerously-skip-permissions" not in cmd


def test_an_unknown_kind_falls_back_to_claude():
    assert "claude" in launcher_command(CWD, 0, 0, agent="gemini")


def test_the_restart_loop_wraps_a_codex_tab_too():
    """In-place restart is a property of the shell, not of the agent."""
    assert "while ($true)" in launcher_command(CWD, 0, 0, agent="codex")


def test_the_environment_hygiene_applies_to_every_kind():
    """CLAUDE_CODE_CHILD_SESSION is inherited by any child of this session, so
    it must be cleared whatever ends up running in the tab."""
    assert "$env:CLAUDE_CODE_CHILD_SESSION=$null" in \
        launcher_command(CWD, 0, 0, agent="codex")


def test_the_self_closing_guard_applies_to_every_kind():
    assert "Stop-Process -Id $PID" in \
        deploy_mod.relaunch_script(CWD, 0, agent="codex")


def test_a_codex_tab_carries_its_own_restart_marker():
    """Keyed on cwd, not on kind - one repo, one marker."""
    cmd = launcher_command(CWD, 0, 0, agent="codex")

    assert ".marker" in cmd
