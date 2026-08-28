"""The only things that differ between agent kinds.

A managed tab may hold a Claude Code session or a Codex CLI one. Almost
everything this package does is the same either way - window placement,
geometry, the restart-marker loop, the staggered launch - so only what
genuinely differs belongs here.

Every fact below was measured on a live machine rather than assumed. The
history of this package is that inference about how these CLIs behave produces
confident wrong answers.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Agent:
    """One kind of agent CLI, and how this package has to treat it.

    Frozen because the registry is read from every module; a mutated field
    would be spooky action at a distance.
    """

    kind: str
    # The running image name, for discovery.
    process: str
    # What readiness waits for on PATH. Not the same thing as `process`:
    # `codex.exe` is what runs, `codex` is what shutil.which finds.
    binary: str
    # Used only when a tab carries no captured command of its own.
    launch: str
    # Sent one keystroke at a time, with a pause between - never joined.
    # Claude Code's slash-command menu does not accept an Enter arriving right
    # behind the command, and two interrupts sent together read as one.
    quit_keys: tuple
    # Where this kind keeps its session records.
    sessions_dir: str


DEFAULT_KIND = "claude"

CLAUDE = Agent(
    kind="claude",
    process="claude.exe",
    binary="claude",
    launch="claude --dangerously-skip-permissions --continue",
    quit_keys=("/exit", "{Enter}"),
    sessions_dir=os.path.join(os.path.expanduser("~"), ".claude", "projects"),
)

CODEX = Agent(
    kind="codex",
    process="codex.exe",
    binary="codex",
    launch="codex resume --last --dangerously-bypass-approvals-and-sandbox",
    # Verified on a disposable session: the target process died and every
    # other session was untouched. `/quit` did nothing at all.
    quit_keys=("{Ctrl}c", "{Ctrl}c"),
    sessions_dir=os.path.join(os.path.expanduser("~"), ".codex", "sessions"),
)

AGENTS = {a.kind: a for a in (CLAUDE, CODEX)}


def for_kind(kind) -> Agent:
    """The agent for `kind`, defaulting to Claude Code.

    Defaulting rather than raising is what lets every layout written before a
    second kind existed keep working with no migration: an absent `agent` field
    reads as the only kind there used to be.
    """
    return AGENTS.get(str(kind or "").strip().lower(), CLAUDE)
