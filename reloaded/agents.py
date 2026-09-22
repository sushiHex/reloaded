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
    # What to call `quit_keys` in a message to the user. "Sending /exit" is a
    # lie about a Codex tab, and a teardown that misreports what it did is
    # worse than a quiet one - the user reads it to decide whether to go
    # looking.
    quit_label: str
    # Substrings that mean an invocation picks its conversation back up rather
    # than starting a fresh one. See `resumes`.
    resume_tokens: tuple
    # Where this kind keeps its session records.
    #
    # Nothing reads this yet, and it is not the interface that should. A Codex
    # rollout's first line records how a thread was CREATED, not what hosts it
    # now - a terminal can resume a thread `codex exec` or the desktop app
    # started, and that line is never rewritten - so no field in this directory
    # answers "is this a tab?". Codex's own answer is the app-server
    # `thread/list` API. Kept because it is true, and marked because a plausible
    # index built on it would be confidently wrong.
    sessions_dir: str
    # The flag that resumes one named conversation, and its short form, for a
    # kind whose sessions can be named. Empty for one that cannot, which is
    # left resuming however it already does. See `resume_exactly`.
    resume_by_id: tuple = ()


def _codex_home() -> str:
    """Codex's state directory, which is not always ~/.codex.

    CODEX_HOME moves it, and a session started under one has a different
    thread store and a different name index entirely.
    """
    return os.environ.get("CODEX_HOME") or os.path.join(
        os.path.expanduser("~"), ".codex")


DEFAULT_KIND = "claude"

CLAUDE = Agent(
    kind="claude",
    process="claude.exe",
    binary="claude",
    launch="claude --dangerously-skip-permissions --continue",
    quit_keys=("/exit", "{Enter}"),
    quit_label="/exit",
    # `--fork-session` only means anything beside a resume, where it makes it
    # a new conversation: `resume_exactly` has to drop it with the rest.
    resume_tokens=("--continue", "-c", "--resume", "-r", "--fork-session"),
    sessions_dir=os.path.join(os.path.expanduser("~"), ".claude", "projects"),
    resume_by_id=("--resume", "-r"),
)

CODEX = Agent(
    kind="codex",
    process="codex.exe",
    binary="codex",
    launch="codex resume --last --dangerously-bypass-approvals-and-sandbox",
    # Verified on a disposable session: the target process died and every
    # other session was untouched. `/quit` did nothing at all. The binary
    # carries "again to quit" (tui/src/bottom_pane/textarea.rs), which is the
    # chord these two presses answer.
    #
    # Ctrl+C is context-sensitive, though, and not every context is quitting.
    # The same binary carries "Press Ctrl+C now to cancel the review" and
    # "Press Ctrl+C to return to the main thread first" - so in a review or a
    # side conversation the first press does that instead, and the second only
    # raises the quit chord rather than answering it. The session survives, and
    # teardown's single resend six seconds later is what completes the quit.
    # If that is also eaten, it times out and says so. Nothing is forced.
    quit_keys=("{Ctrl}c", "{Ctrl}c"),
    quit_label="Ctrl+C",
    resume_tokens=("resume",),
    sessions_dir=os.path.join(_codex_home(), "sessions"),
)

AGENTS = {a.kind: a for a in (CLAUDE, CODEX)}


def resumes(kind, command: str) -> bool:
    """Whether `command` brings a conversation back, or starts an empty one.

    An empty command means the kind's own default, and both defaults resume.

    This matters because capture records what a session was actually running
    and the relaunch replays it verbatim. Someone who started a tab by typing
    plain `codex` gets plain `codex` back - a new thread, at the same
    directory, with none of the conversation in it. `restart` then reports
    success, having thrown the thing away that it exists to preserve.

    Deliberately not fixed by rewriting the command. The flags are the user's,
    and a launcher that quietly appends `resume` to what someone typed is
    guessing at intent. Saying so before anything is exited is the honest
    version, and leaves the choice where it belongs.
    """
    if not command:
        return True
    lowered = f" {command.lower()} "
    return any(f" {t} " in lowered or lowered.rstrip().endswith(f" {t}")
               for t in for_kind(kind).resume_tokens)


def resume_exactly(kind, argv: list, session_id: str | None) -> list:
    """`argv` rewritten to resume conversation `session_id` and no other.

    Only for `/relaunch`, and the exception to `resumes`' rule against
    rewriting the user's command: there the intent is a guess, here it is not -
    "restart this session" means this conversation. Whatever resume flag was
    there goes, with its value, and `--resume <id>` takes its place: bare
    `--resume` opens a picker and plain `claude` starts empty, and neither is a
    restart.

    Everything from a `--` on goes too: it is the opening prompt, and resuming
    would send it into the conversation again.

    Unchanged for a kind whose sessions cannot be named, or with no id.
    """
    agent = for_kind(kind)
    if not session_id or not agent.resume_by_id or not argv:
        return list(argv)
    if "--" in argv[1:]:
        argv = argv[:argv.index("--", 1)]
    kept, i = [argv[0]], 1
    while i < len(argv):
        flag, has_value = argv[i].split("=", 1)[0], "=" in argv[i]
        i += 1
        if flag not in agent.resume_tokens:
            kept.append(argv[i - 1])
        elif (flag in agent.resume_by_id and not has_value
              and i < len(argv) and not argv[i].startswith("-")):
            i += 1  # the conversation it named
    return kept + [agent.resume_by_id[0], session_id]


def for_kind(kind) -> Agent:
    """The agent for `kind`, defaulting to Claude Code.

    Defaulting rather than raising is what lets every layout written before a
    second kind existed keep working with no migration: an absent `agent` field
    reads as the only kind there used to be.
    """
    return AGENTS.get(str(kind or "").strip().lower(), CLAUDE)
