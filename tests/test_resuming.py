"""A restored tab must come back with a conversation.

Two ways it did not, both on the user's tabs. `--resume` with no id opens
Claude Code's picker, and a logon restore left constructicon sitting at one:
the session had been started by hand that way and capture replayed it. And a
command with no resume flag at all - hardline-mcp's plain `claude` - started an
empty conversation on every restore and every relaunch through its loop.
"""
from __future__ import annotations

import pathlib

import pytest

import reloaded.agents as agents_mod
from reloaded.deploy import launcher_command

SKIP = "claude --dangerously-skip-permissions"


@pytest.mark.parametrize("command, expected", [
    # The picker: a bare resume flag becomes the latest conversation.
    (f"{SKIP} --resume", f"{SKIP} --continue"),
    (f"{SKIP} -r", f"{SKIP} --continue"),
    (f"claude --resume {SKIP[7:]}", f"claude --continue {SKIP[7:]}"),
    # Nothing resumed: the latest conversation is added.
    (SKIP, f"{SKIP} --continue"),
    ("claude", "claude --continue"),
    ("claude --model opus", "claude --model opus --continue"),
    # Only qualifies a resume; alone it brings nothing back.
    ("claude --fork-session", "claude --fork-session --continue"),
    # Named, it resumes exactly that conversation - left alone.
    (f"{SKIP} --resume 0673eda5-2873-425b", f"{SKIP} --resume 0673eda5-2873-425b"),
    (f"{SKIP} --resume=0673eda5", f"{SKIP} --resume=0673eda5"),
    (f"{SKIP} --continue", f"{SKIP} --continue"),
    ("claude --resume 'my session'", "claude --resume 'my session'"),
    ("", ""),
    # The opening prompt goes: into a resumed conversation it would be sent
    # again, and its work done twice. Quoted or after `--`, it is never an
    # option, whatever it says.
    ("claude 'apply the migration'", "claude --continue"),
    ("claude -- 'apply the migration'", "claude --continue"),
    ("claude --continue 'apply the migration'", "claude --continue"),
    ("claude --model opus 'apply it'", "claude --model opus --continue"),
    ("claude 'fix --resume --verbose'", "claude --continue"),
    ("claude --continue -- --resume --verbose", "claude --continue"),
    ("claude 'it''s --resume' --resume", "claude --continue"),
    ('claude "a --resume" -r', "claude --continue"),
])
def test_a_launched_claude_command_always_resumes_something(command, expected):
    assert agents_mod.resuming("claude", command) == expected


def test_a_kind_with_no_resume_latest_is_left_alone():
    """Codex is warned about before a restart instead; see `resumes`."""
    assert agents_mod.resuming("codex", "codex") == "codex"
    assert agents_mod.resuming("codex", "codex resume") == "codex resume"


def test_the_launcher_and_its_restart_loop_never_open_the_picker():
    """The loop replays the same invocation on every restart, so the fix has
    to be in what the launcher runs, not only in what a restore starts."""
    cmd = launcher_command(r"C:\repos\app", 0, 0, "claude", f"{SKIP} --resume")

    assert "--resume" not in cmd
    assert f"{SKIP} --continue" in cmd


def test_the_launcher_never_starts_an_empty_conversation():
    cmd = launcher_command(r"C:\repos\app", 0, 0, "claude", SKIP)

    assert f"{SKIP} --continue" in cmd


@pytest.mark.parametrize("invocation, expected", [
    # #59: where there is no conversation, the latest one cannot be resumed.
    (f"{SKIP} --continue", SKIP),
    ("claude -c --model opus", "claude --model opus"),
    ("claude --fork-session --continue", "claude"),
    ("claude --continue --append-system-prompt 'be brief'",
     "claude --append-system-prompt 'be brief'"),
    # A named conversation that is gone is another failure; a new one would
    # hide it.
    (f"{SKIP} --resume 0673eda5", ""),
    (f"{SKIP} --resume=0673eda5", ""),
    (f"{SKIP} --resume=0673eda5 --fork-session", ""),
    # Nothing resumed, nothing to fall back from.
    (SKIP, ""),
    ("claude --fork-session", ""),
])
def test_a_claude_resume_of_the_latest_falls_back_to_a_new_conversation(
        invocation, expected):
    assert agents_mod.fresh("claude", invocation) == expected


@pytest.mark.parametrize("cwd, folder", [
    # Folders Claude Code made on the machine this was measured on.
    (r"C:\Users\kfaim\repos\reloaded", "C--Users-kfaim-repos-reloaded"),
    (r"C:\Users\kfaim\.claude\jobs\dc5c72cf\tmp",
     "C--Users-kfaim--claude-jobs-dc5c72cf-tmp"),
    # The same rule for a space, and for a trailing separator, which the
    # directory itself does not have.
    (r"C:\Users\kfaim\repos\with space", "C--Users-kfaim-repos-with-space"),
    ("C:\\repos\\app\\", "C--repos-app"),
])
def test_claude_files_a_directorys_conversations_under_its_path_spelled_out(
        cwd, folder):
    assert agents_mod.conversations_dir("claude", cwd) == str(
        pathlib.Path(agents_mod.CLAUDE.sessions_dir) / folder)


def test_a_folder_name_that_claude_would_hash_is_not_guessed():
    """Over 200 characters Claude Code cuts the name and appends a hash; a
    wrong guess would find no conversation where there is one."""
    assert agents_mod.conversations_dir("claude", "C:\\" + "a" * 200) == ""


def test_codex_needs_no_fallback():
    """`codex resume --last` with nothing to resume starts a new conversation
    (measured, 0.162.1). Nor does Codex file its sessions by directory."""
    assert agents_mod.fresh("codex", agents_mod.CODEX.launch) == ""
    assert agents_mod.conversations_dir("codex", r"C:\repos\app") == ""
    assert "$LASTEXITCODE" not in launcher_command(r"C:\repos\app", 0, 0, "codex")
