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
import re
from dataclasses import dataclass

from . import pwsh
from .paths import tab_title


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
    # Claude Code's are read, by discovery and by `conversations_dir`. Codex's
    # are not, and they are not the interface that should be. A Codex
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
    # Options that take no value, that take every value after them, and that
    # take one only if it does not look like an option. Any other option takes
    # the next argument whatever it looks like. See `_roles`, which tells an
    # opening prompt from an option's value by them.
    bare_flags: tuple = ()
    variadic_flags: tuple = ()
    optional_flags: tuple = ()
    # What resumes the directory's latest conversation, standing in for a
    # `resume_by_id` flag given no id - which opens a picker. See
    # `resuming`, and `fresh` for a directory with none.
    resume_latest: str = ""
    # Whether transcript.py's torn-tail guard has been verified against this
    # kind's session records. Repair ends in a truncation, so running it on an
    # unverified format is a guess with a destructive edit on the end.
    repairs_transcripts: bool = False
    # One zero-width character ending the title of every tab reloaded opens
    # for this kind - the only thing that tells a directory's Claude tab from
    # its Codex one, which otherwise carry the same name (#77). Measured on
    # Terminal 1.24: `wt --title` keeps it, UIA returns it, and the tab strip
    # draws nothing for it. See `tab_label` and `title_kind`.
    title_mark: str = ""
    # Subcommands that run a background server rather than a session. Such a
    # process has this kind's image name and a directory of its own, and is
    # nobody's tab: discovery skips one whose first argument is listed.
    services: tuple = ()


def _codex_home() -> str:
    """Codex's state directory, which is not always ~/.codex.

    CODEX_HOME moves it, and a session started under one has a different
    thread store and a different name index entirely.
    """
    return os.environ.get("CODEX_HOME") or os.path.join(
        os.path.expanduser("~"), ".codex")


def _claude_home() -> str:
    """Claude Code's configuration home, which is not always ~/.claude.

    CLAUDE_CONFIG_DIR moves it ("the configuration home (CLAUDE_CONFIG_DIR)",
    2.1.296), and its conversations with it. Read past, the folder that
    deploy.nothing_to_resume looks in would be empty beside a real
    conversation - the one case its fallback must never start a new one in.
    """
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude")


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
    sessions_dir=os.path.join(_claude_home(), "projects"),
    repairs_transcripts=True,
    resume_by_id=("--resume", "-r"),
    # From `claude --help`, 2.1.296: every option with no <value> or [value],
    # every one whose value is <values...>, and every one whose value is
    # [value]. A flag added later reads as taking the next argument, which at
    # worst keeps a prompt or hides a resume flag - never drops a value.
    bare_flags=(
        "--allow-dangerously-skip-permissions", "--ax-screen-reader",
        "--background", "--bare", "--bg", "--brief", "--chrome", "--continue",
        "--dangerously-skip-permissions", "--desktop", "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections", "--fork-session",
        "--forward-subagent-text", "--help", "--ide", "--include-hook-events",
        "--include-partial-messages", "--no-chrome", "--no-session-persistence",
        "--print", "--replay-user-messages", "--restricted", "--safe-mode",
        "--strict-mcp-config", "--tmux", "--verbose", "--version",
        "-c", "-h", "-p", "-v"),
    variadic_flags=(
        "--add-dir", "--allowed-tools", "--allowedTools", "--betas",
        "--disallowed-tools", "--disallowedTools", "--file", "--mcp-config",
        "--tools"),
    optional_flags=(
        "--cloud", "--debug", "--from-pr", "--prompt-suggestions",
        "--remote-control", "--resume", "--teleport", "--worktree",
        "-d", "-r", "-w"),
    resume_latest="--continue",
    title_mark="​",  # ZERO WIDTH SPACE
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
    title_mark="⁠",  # WORD JOINER
    # 0.162.1 keeps a managed daemon running with no parent at all, in
    # ~/.codex/app-server-daemon/workdir: `codex.exe app-server --listen
    # unix:// ... --managed-daemon` and `codex.exe app-server daemon
    # pid-update-loop`. Counted as sessions, they were two Codex tabs no
    # capture could find (measured 2026-10-15).
    services=("app-server",),
)

AGENTS = {a.kind: a for a in (CLAUDE, CODEX)}

# Kind by image name (lowercase, as psutil reports it) and by launch binary.
BY_PROCESS = {a.process: a.kind for a in AGENTS.values()}
BY_BINARY = {a.binary: a.kind for a in AGENTS.values()}


def resumes(kind, command: str) -> bool:
    """Whether `command` brings a conversation back, or starts an empty one.

    An empty command means the kind's own default, and both defaults resume.

    This matters because capture records what a session was actually running
    and the relaunch replays it verbatim. Someone who started a tab by typing
    plain `codex` gets plain `codex` back - a new thread, at the same
    directory, with none of the conversation in it. `restart` then reports
    success, having thrown the thing away that it exists to preserve.

    Not fixed by rewriting the command for a kind without `resume_latest`
    (Codex): there the warning before anything is exited is the answer. For
    Claude Code, `resuming` does rewrite it, at launch - a plain `claude`
    restored empty every time, and relaunches through its loop did too.
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

    The opening prompt goes too - after a `--`, or as a bare word no option
    claims - because resuming would send it into the conversation again.

    With no `argv` - it could not be read - the kind's default launch is
    rewritten instead, so a known id is not lost to it.

    Unchanged for a kind whose sessions cannot be named, or with no id.
    """
    agent = for_kind(kind)
    if not session_id or not agent.resume_by_id:
        return list(argv)
    argv = list(argv) or agent.launch.split()
    # A resume option goes with the conversation it named.
    kept = [arg for arg, (role, option) in zip(argv[1:], _roles(agent, argv[1:]))
            if role != "word" and option not in agent.resume_tokens]
    return [argv[0]] + kept + [agent.resume_by_id[0], session_id]


def resuming(kind, command: str) -> str:
    """`command`, made to bring a conversation back when Reloaded launches it.

    Two ways a captured command came back as no conversation at all, both
    measured on the user's tabs:

    - `--resume` with no id opens Claude Code's picker, and a restored tab sat
      at it: the session had been started by hand that way, and capture
      replays what it read. The bare flag becomes `resume_latest`.
    - A command with no resume flag starts an empty conversation. Captured
      from a session started as plain `claude`, every restore - and every
      relaunch through its tab's loop - came back empty, the long-running
      conversation out of reach. `resume_latest` is added.

    `resume_latest` is the directory's latest conversation, which is what the
    picker had at the top. This reverses `resumes`' rule against rewriting the
    user's flags, for a kind that has `resume_latest`: what Reloaded launches
    is a restore, and a restore that brings nothing back is not one. A command
    naming its conversation keeps that name.

    The opening prompt goes, for the reason `resume_exactly` drops it: into a
    resumed conversation it is sent again, and its work done twice. Options
    are read the same way - a bare word no option claims is the prompt.
    """
    agent = for_kind(kind)
    if not command or not agent.resume_latest or not agent.resume_by_id:
        return command
    # Judged on what PowerShell passes, kept as written: `_format_command`
    # quotes a whole `--system-prompt=has spaces` argument, and read raw that
    # option looked like prompt text and was dropped. The raw form is what is
    # rejoined, so the result is still the same valid PowerShell.
    tokens = _ps_tokens(command)
    roles = _roles(agent, [value for *_x, value in tokens[1:]])
    kept, resumes_any = [tokens[0][2]], False
    for i, ((_s, _e, text, _q, value), (role, option)) in enumerate(zip(tokens[1:], roles)):
        if role == "word":
            continue  # the opening prompt: resuming would send it again
        if (role == "option" and option in agent.resume_by_id and "=" not in value
                and roles[i + 1:i + 2] != [("value", option)]):
            kept.append(agent.resume_latest)  # bare: the picker
            resumes_any = True
            continue
        kept.append(text)
        # `--fork-session` only qualifies a resume; alone it resumes nothing.
        resumes_any = resumes_any or (role == "option" and option in agent.resume_tokens
                                      and option != "--fork-session")
    if not resumes_any:
        kept.append(agent.resume_latest)
    return " ".join(kept)


def fresh(kind, invocation: str) -> str:
    """`invocation` starting a new conversation instead of the directory's
    latest, for when it has none - or "" where nothing needs one.

    Measured, Claude Code 2.1.296: `--continue` in a directory with no
    conversation prints "No conversation found to continue" and exits 1 after
    about two seconds, so a repo first opened through Reloaded never started.
    Codex's `resume --last` (0.162.1) starts a new conversation instead, and
    a kind without `resume_latest` gets "".

    Only for an invocation whose one resume is the latest conversation. One
    naming its conversation fails for another reason, which a new
    conversation would hide.
    """
    agent = for_kind(kind)
    if not agent.resume_latest:
        return ""
    tokens = _ps_tokens(invocation)
    roles = _roles(agent, [value for *_x, value in tokens[1:]])
    resumes = {option for role, option in roles
               if role == "option" and option in agent.resume_tokens}
    # `--fork-session` only qualifies a resume; alone it resumes nothing.
    if resumes & set(agent.resume_by_id) or not resumes - {"--fork-session"}:
        return ""
    # What is left takes no id: the latest conversation, its short form, and
    # the qualifier.
    return " ".join([tokens[0][2]] + [text for (_s, _e, text, _q, _v), (role, option)
                                      in zip(tokens[1:], roles)
                                      if not (role == "option"
                                              and option in agent.resume_tokens)])


def _roles(agent: Agent, values: list) -> list:
    """What each argument after the program is, up to a `--`: ("option",
    its name), ("value", the option it belongs to), or ("word", "") - an
    opening prompt, which no option claims.

    Read as Claude Code's parser reads them, measured on 2.1.296. An option
    with a required value takes the next argument whatever it looks like:
    `--model --version` set the model to "--version". One with an optional
    value (`optional_flags`) takes it only if it does not look like an
    option: `--debug --version` printed the version. A variadic one takes
    each argument up to the next option. Judged by look alone, a value spelled
    like a resume flag - `--append-system-prompt -c` - was taken for one (#68).
    """
    roles, option, takes = [], "", "none"
    for value in values:
        if takes == "one" or (takes in ("maybe", "every") and not value.startswith("-")):
            roles.append(("value", option))
            takes = "every" if takes == "every" else "none"
        elif value == "--":
            break
        elif value.startswith("-"):
            option = value.split("=", 1)[0]
            roles.append(("option", option))
            takes = ("none" if "=" in value or option in agent.bare_flags
                     else "every" if option in agent.variadic_flags
                     else "maybe" if option in agent.optional_flags else "one")
        else:
            roles.append(("word", ""))
    return roles


# Claude Code's name for a directory's folder of conversations, from its
# source (2.1.296): every character but an ASCII letter or digit becomes `-`,
# and a name longer than this is cut and given a hash not reproduced here.
_CLAUDE_FOLDER_LIMIT = 200


def conversations_dir(kind, cwd: str) -> str:
    """The folder where `kind` keeps the conversations held in `cwd`, or ""
    where that is not known.

    Only Claude Code files them by directory. Codex files its sessions by
    date (see `sessions_dir`), so no folder answers the question for it.
    """
    if for_kind(kind) is not CLAUDE:
        return ""
    name = re.sub(r"[^a-zA-Z0-9]", "-", os.path.normpath(cwd))
    if len(name) > _CLAUDE_FOLDER_LIMIT:
        return ""
    return os.path.join(CLAUDE.sessions_dir, name)


_SINGLE = pwsh.SINGLE_QUOTES


def _ps_tokens(command: str) -> list:
    """(start, end, text, quoted, value) for each argument of a PowerShell
    command line, in the form discover._format_command writes: separated by
    whitespace outside quotes; inside single quotes - any of the five
    characters PowerShell reads as one - a doubled quote is a literal one.
    `text` is the raw token, `value` what PowerShell passes."""
    tokens, i, n = [], 0, len(command)
    while i < n:
        if command[i].isspace():
            i += 1
            continue
        start, quoted, value = i, False, []
        while i < n and not command[i].isspace():
            ch = command[i]
            if ch in _SINGLE or ch == '"':
                quoted, closers = True, _SINGLE if ch in _SINGLE else '"'
                i += 1
                while i < n:
                    if command[i] in closers:
                        if closers is _SINGLE and command[i + 1:i + 2] in tuple(_SINGLE):
                            value.append(command[i])  # doubled: a literal quote
                            i += 2
                            continue
                        break
                    value.append(command[i])
                    i += 1
            else:
                value.append(ch)
            i += 1
        tokens.append((start, i, command[start:i], quoted, "".join(value)))
    return tokens


def tab_label(kind, cwd: str) -> str:
    """The title reloaded gives a tab it opens: the directory's name, which
    is what the user sees, and the kind's invisible mark."""
    return tab_title(cwd) + for_kind(kind).title_mark


def title_kind(title: str) -> tuple[str, str | None]:
    """A tab title without its mark, and the kind the mark names - None for
    a title with none, which is a tab opened before marks existed, or by
    hand, and could hold either kind."""
    for agent in AGENTS.values():
        if agent.title_mark and title.endswith(agent.title_mark):
            return title[:-len(agent.title_mark)], agent.kind
    return title, None


def for_kind(kind) -> Agent:
    """The agent for `kind`, defaulting to Claude Code.

    Defaulting rather than raising is what lets every layout written before a
    second kind existed keep working with no migration: an absent `agent` field
    reads as the only kind there used to be.
    """
    return AGENTS.get(str(kind or "").strip().lower(), CLAUDE)
