"""What differs between agent kinds, and nothing else.

Window placement, geometry, the restart-marker loop and the staggered launch
are all kind-agnostic and live elsewhere. Anything that lands in the registry
should be something a second CLI genuinely does differently.
"""
from __future__ import annotations

import dataclasses

import pytest

import reloaded.agents as agents


def test_both_kinds_are_registered():
    assert set(agents.AGENTS) == {"claude", "codex"}


def test_claude_is_the_default_for_an_unknown_kind():
    """A layout written before kinds existed carries no `agent` field, and
    must keep working untouched rather than being migrated."""
    assert agents.for_kind("").kind == "claude"
    assert agents.for_kind("gemini").kind == "claude"
    assert agents.for_kind(None).kind == "claude"


def test_the_kind_lookup_ignores_case_and_padding():
    assert agents.for_kind("  CODEX ").kind == "codex"


def test_each_kind_names_its_own_process():
    assert agents.CLAUDE.process == "claude.exe"
    assert agents.CODEX.process == "codex.exe"


def test_each_kind_names_the_binary_readiness_waits_for():
    """Separate from `process`: readiness looks this up on PATH, while
    discovery matches a running image name. `codex.exe` runs; `codex` is what
    shutil.which finds."""
    assert agents.CLAUDE.binary == "claude"
    assert agents.CODEX.binary == "codex"


def test_quit_keys_are_a_sequence_of_separate_sends():
    """A tuple, not a string. Claude Code's slash menu will not accept an
    Enter that arrives ten milliseconds behind the command, and two interrupts
    sent together read as one."""
    assert agents.CLAUDE.quit_keys == ("/exit", "{Enter}")
    assert agents.CODEX.quit_keys == ("{Ctrl}c", "{Ctrl}c")


def test_codex_quits_on_two_interrupts():
    """Measured on a disposable session: the target process died and other
    sessions were untouched. `/quit` did nothing."""
    assert agents.CODEX.quit_keys.count("{Ctrl}c") == 2


def test_each_kind_knows_where_its_sessions_live():
    assert ".claude" in agents.CLAUDE.sessions_dir
    assert ".codex" in agents.CODEX.sessions_dir


def test_each_kind_has_a_default_launch_command():
    assert agents.CLAUDE.launch.startswith("claude ")
    assert agents.CODEX.launch.startswith("codex ")


def test_an_agent_is_immutable():
    """The registry is read by every module; a mutated field would be spooky
    action at a distance."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        agents.CODEX.launch = "something else"


def test_every_registered_kind_agrees_with_its_own_key():
    for key, agent in agents.AGENTS.items():
        assert agent.kind == key
