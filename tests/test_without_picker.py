"""A restored tab must not come up at a conversation picker.

`--resume` with no id opens Claude Code's picker. A logon restore left the
constructicon tab sitting at one: the session had been started by hand with a
bare `--resume`, capture recorded that, and the restore replayed it.
"""
from __future__ import annotations

import pytest

import reloaded.agents as agents_mod
from reloaded.deploy import launcher_command

SKIP = "claude --dangerously-skip-permissions"


@pytest.mark.parametrize("command, expected", [
    (f"{SKIP} --resume", f"{SKIP} --continue"),
    (f"{SKIP} -r", f"{SKIP} --continue"),
    (f"claude --resume {SKIP[7:]}", f"claude --continue {SKIP[7:]}"),
    # Named, it resumes exactly that conversation - left alone.
    (f"{SKIP} --resume 0673eda5-2873-425b", f"{SKIP} --resume 0673eda5-2873-425b"),
    (f"{SKIP} --resume=0673eda5", f"{SKIP} --resume=0673eda5"),
    (f"{SKIP} --continue", f"{SKIP} --continue"),
    (SKIP, SKIP),
    ("", ""),
])
def test_a_bare_resume_becomes_the_latest_conversation(command, expected):
    assert agents_mod.without_picker("claude", command) == expected


def test_a_kind_with_no_resume_by_id_is_left_alone():
    assert agents_mod.without_picker("codex", "codex resume") == "codex resume"


def test_the_launcher_and_its_restart_loop_never_open_the_picker():
    """The loop replays the same invocation on every restart, so the fix has
    to be in what the launcher runs, not only in what a restore starts."""
    cmd = launcher_command(r"C:\repos\app", 0, 0, "claude", f"{SKIP} --resume")

    assert "--resume" not in cmd
    assert f"{SKIP} --continue" in cmd
