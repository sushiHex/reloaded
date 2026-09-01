"""Read-only session discovery: live processes and transcript metadata.

Reloaded requires no cooperation from the sessions it manages. Everything here
reads what Claude Code and Windows already record.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import unicodedata
from dataclasses import dataclass

from .paths import norm

# Bounded reads: a transcript can be hundreds of megabytes, and this runs on a
# timer. cwd appears on nearly every record so the head is enough; customTitle
# must come from the tail because a rename appends a newer one.
_HEAD_BYTES = 64 * 1024
_TAIL_BYTES = 256 * 1024


@dataclass
class TranscriptInfo:
    cwd: str
    # Empty when the session has no custom title *or* when it was read with
    # need_title=False — the two are indistinguishable, so do not use an index
    # built that way for title lookups.
    title: str
    path: str
    mtime: float
    size: int


# The spinner families Claude Code has shipped, as ranges rather than single
# codepoints so a new frame *within* a family still matches without a code
# change. That is the whole point: an exact frame list went stale once already
# (Braille dots -> circled halves) and failed silently.
#
# Deliberately NOT "any symbol". Matching every category-So character also ate
# user decoration: a hand-renamed shell tab "▶ build" (U+25B6) cleaned to
# "build", resolved against a live repo of that name, and `down` typed /exit
# into the user's shell and could close its window. A spinner and a decoration
# are textually identical - symbol, space, name - so breadth cannot be traded
# for safety here. Prefer the recoverable failure: an unrecognised frame leaves
# the title alone and the tab is skipped, which a re-capture fixes.
#
# To add a family: append its range, add a case to
# test_strip_glyph_removes_every_shipped_spinner_family.
_SPINNER_RANGES = (
    (0x2733, 0x2733),  # ✳   ready marker
    (0x2800, 0x28FF),  # ⠐⠂⠁ Braille patterns
    (0x25D0, 0x25D3),  # ◐◑◒◓ circled halves
    (0x25F0, 0x25F7),  # ◰◱◲◳ quadrant squares/circles, same visual family
)

# Characters that only ever modify the glyph before them: variation selectors
# (Mn), skin-tone modifiers (Sk), zero-width joiner (Cf). They are not So, so a
# category match left them behind as invisible debris - "⚙️ x" kept an orphan
# U+FE0F, which then never matched any path.
_GLYPH_MODIFIERS = frozenset({"Mn", "Sk", "Cf"})


def _is_spinner_char(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _SPINNER_RANGES)


def strip_glyph(title: str) -> str:
    """Drop Claude Code's leading status glyph, whichever frame it is showing.

    Strips a leading run of whitespace and known spinner frames, plus any
    modifier that trails one. Anything else - including an emoji the user chose
    as their title - is left alone, so "🚀" and the busy "✳ 🚀" both clean to
    "🚀" and a tab keeps matching its own key while it works.
    """
    s = title if isinstance(title, str) else ""
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isspace() or _is_spinner_char(ch):
            i += 1
        elif i > 0 and unicodedata.category(ch) in _GLYPH_MODIFIERS:
            i += 1
        else:
            break
    return s[i:].strip()


HAND = "hand"
RELOADED = "reloaded"

# The launcher's own variable, not the word "reloaded". The banner it prints
# contains that word for every repo, and a repo literally named `reloaded` puts
# it in a hand-opened shell's title too - matching on it would classify by repo
# name. `$rlStart` appears only because deploy put it there.
_LAUNCHER_MARK = "$rlStart"

_psutil = None


def _ps():
    """psutil, imported on first use. A module attribute so tests can replace
    it, matching how live_sessions imports it lazily rather than at module
    load - psutil is optional and this package degrades without it."""
    global _psutil
    if _psutil is None:
        import psutil

        _psutil = psutil
    return _psutil


def owning_session(pid: int | None = None, depth: int = 12) -> tuple | None:
    """The agent session this process is running INSIDE: (pid, cwd, kind).

    None when there is no agent above us - reloaded run from an ordinary
    terminal rather than from inside a session's tool call.

    Found by walking up the parent chain, not by matching the current
    directory against live_sessions(). A shell a session spawns can be
    anywhere: a subdirectory of the repo, a sibling, or somewhere else
    entirely, and a session can be running with a cwd that no longer exists.
    Matching on directory would then answer with the wrong session, or with
    none, and be indistinguishable from a correct answer either way. The
    parent chain is the only thing that actually says "I am inside this one".

    `depth` bounds the walk. A parent chain should be three or four processes
    here; anything longer is a pid loop or a surprise, and neither is worth
    hanging on.
    """
    from . import agents as agents_mod

    by_process = {a.process: a.kind for a in agents_mod.AGENTS.values()}
    try:
        proc = _ps().Process(os.getpid() if pid is None else pid)
    except Exception:
        return None

    for _step in range(depth):
        try:
            proc = proc.parent()
        except Exception:
            return None
        if proc is None:
            return None
        try:
            kind = by_process.get((proc.name() or "").lower())
        except Exception:
            return None
        if kind is None:
            continue
        try:
            cwd = proc.cwd()
        except Exception:
            # Its identity is the part that matters, and losing the cwd here
            # would be reported as "not inside a session" - a confidently
            # wrong answer, when the truthful one is "inside one I cannot
            # fully read".
            cwd = ""
        return proc.pid, cwd, kind
    return None


def launcher_kind(pid: int) -> str:
    """Whether the session at `pid` was launched by reloaded or started by hand.

    They need opposite handling on restart. A reloaded tab's shell runs the
    launcher, so /exit either relaunches in place or closes the tab. A
    hand-launched tab's shell is a plain interactive prompt: /exit leaves it
    sitting there with no session and nothing to close it.

    Read from the parent shell's command line, where the launcher is visible
    verbatim. Anything unreadable counts as HAND, which is the recoverable
    guess: treating a hand-launched session as reloaded means waiting for a
    relaunch no loop will perform and then reporting failure, while the reverse
    means typing into a shell that a reloaded tab, having closed itself, is not
    around to receive.
    """
    try:
        parent = _ps().Process(_ps().Process(pid).ppid())
        return RELOADED if _LAUNCHER_MARK in " ".join(parent.cmdline()) else HAND
    except Exception:
        return HAND


# Codex also runs as a child of its desktop app, where it is not a terminal tab
# at all - both are live on this machine at once. Excluding by image name would
# lose the real tab along with it, so the parent process is what distinguishes
# them.
DESKTOP_HOSTS = ("chatgpt.exe",)


def _sessions() -> dict[str, tuple]:
    """Normalized cwd -> (pid, kind) for every live agent session.

    Keyed by cwd, so two sessions in the same directory collapse to whichever
    psutil reports last. That is a real limit and not a fixable one here:
    nothing connects a Windows Terminal tab to the process running inside it,
    so even a complete list would not say which pid belongs to which tab. What
    it costs is that teardown can type into one session while waiting on the
    other's pid, and then report a timeout for a session that did quit, or
    success for one that did not. teardown.plan_down warns when it sees the
    matching symptom - two tabs resolving to one cwd - rather than leaving the
    ambiguity silent.
    """
    try:
        import psutil
    except ImportError:
        return {}

    from . import agents as agents_mod

    by_process = {a.process: a.kind for a in agents_mod.AGENTS.values()}
    out: dict[str, tuple] = {}
    for proc in psutil.process_iter(["pid", "name", "ppid"]):
        kind = by_process.get((proc.info.get("name") or "").lower())
        if kind is None:
            continue
        try:
            parent = psutil.Process(proc.info.get("ppid"))
            if (parent.name() or "").lower() in DESKTOP_HOSTS:
                continue
        except Exception:
            # An unreadable parent is not grounds to drop a session that is
            # otherwise a normal match - the common case for that is a
            # permissions error, not a desktop-hosted process.
            pass
        try:
            cwd = proc.cwd()
        except Exception:
            continue
        if cwd:
            out[norm(cwd)] = (int(proc.info["pid"]), kind)
    return out


def live_sessions() -> dict[str, int]:
    """Map normalized cwd -> pid for every running agent session.

    Ground truth for liveness, and correct for an idle session, which is why no
    heartbeat is needed.

    The signature is unchanged from when this only knew about Claude Code: nine
    call sites depend on dict[str, int], and which kind a session is travels
    beside it in live_agents() rather than through here.
    """
    return {cwd: pid for cwd, (pid, _kind) in _sessions().items()}


def live_agents() -> dict[str, str]:
    """Map normalized cwd -> agent kind, alongside live_sessions()."""
    return {cwd: kind for cwd, (_pid, kind) in _sessions().items()}


_SAFE_EXE = re.compile(r"[A-Za-z0-9._+-]+")

# Everything PowerShell will not read as syntax: no space, no metacharacter.
# An allowlist rather than a list of things to escape, because the cost of
# forgetting one is a captured argument being executed as a command.
_SAFE_ARG = re.compile(r"[A-Za-z0-9._+=:/\@%-]+")


def _ps_arg(arg: str) -> str:
    """One captured argument, safe to paste into a PowerShell command line.

    A plain token is left alone, which keeps the common case (`resume`,
    `--last`) readable in a saved layout. Anything else is single-quoted, and a
    single-quoted PowerShell string is literal all the way through - no
    variable expansion, no subexpressions, no escapes - so the only thing left
    to handle inside one is the quote character itself, doubled.

    Double quotes would not do: PowerShell expands `$` inside them, so a
    captured argument holding `$(...)` would have run rather than been passed.
    """
    if arg and _SAFE_ARG.fullmatch(arg):
        return arg
    return "'" + arg.replace("'", "''") + "'"


def session_launch(pid: int) -> tuple[str, str]:
    """One live session's (agent kind, command line), read from one process.

    Both facts or neither. Read from separate sweeps they drift apart: a
    process that vanishes between them gives its kind to one call and nothing
    to the other, the gap gets filled from a saved layout that may describe a
    different CLI entirely, and the result is a pair that never existed -
    agent "codex" carrying claude's command line, or the reverse. That is not
    cosmetic. Teardown picks its quit keystrokes from the agent while
    launcher_command picks the invocation from the command, so a mismatched
    pair interrupts one CLI and relaunches the other.

    The kind comes from the image name, and falls back to the command's own
    argv[0] - a command line that names `claude` is evidence about which agent
    it is. That fallback is what keeps the promise above: anything that yields
    a command also yields a kind.

    ("", "") when the process cannot be read, which callers treat as "fall
    back" rather than as a failure.
    """
    from . import agents as agents_mod

    try:
        import psutil

        proc = psutil.Process(pid)
    except Exception:
        return "", ""

    # Guarded separately: on Windows the image name is usually readable when
    # the command line is not, and losing both to one AccessDenied would throw
    # away the more reliable of the two.
    try:
        name = (proc.name() or "").lower()
    except Exception:
        name = ""
    try:
        argv = list(proc.cmdline())
    except Exception:
        argv = []

    command = _format_command(argv)
    # Both lookups fold case. `name` is already lowered; argv[0] is whatever
    # the process was launched with, and `CODEX.EXE` on the command line is
    # the same program as `codex.exe`. A case-sensitive miss here does not
    # produce a wrong answer - it produces no answer, and the caller then
    # relaunches from a default instead of from what was actually running.
    kind = ({a.process: a.kind for a in agents_mod.AGENTS.values()}.get(name)
            or {a.binary: a.kind for a in agents_mod.AGENTS.values()}.get(
                command.split(" ", 1)[0].lower())
            or "")
    if not kind:
        # A command line that belongs to no agent is not a command line worth
        # keeping. Windows reuses pids, so this pid may have been the session
        # when the plan was built and be something else entirely by now - and
        # the caller's only guard against a stale command is that it is empty.
        # Handing back `node server.js` here gets it typed into a terminal.
        return "", ""
    return kind, command


def session_command(pid: int) -> str:
    """The command line a live session is running, for a captured tab.

    Capture wants only this half; restart wants the pair, and takes it from
    session_launch rather than from here.
    """
    return session_launch(pid)[1]


def _format_command(argv: list) -> str:
    """One process's argv as a command line safe to paste into PowerShell.

    The executable is reduced to its bare name: an absolute path would pin the
    saved layout to one install location, and every agent binary is on PATH by
    the time readiness lets a deploy start.

    Returns "" when there is nothing usable, which the caller treats as "use
    the kind's default" rather than as a failure. A capture that dropped a tab
    because one cmdline was unreadable would be worse than one that relaunches
    it with default flags.

    The result is spliced into a PowerShell command by deploy.launcher_command,
    so every argument is quoted for PowerShell rather than only the ones with
    spaces in them. Single quotes, not double: PowerShell expands `$` inside
    double quotes, so a captured argument holding `$(...)` would have run.
    """
    if not argv:
        return ""

    exe = os.path.splitext(os.path.basename(argv[0]))[0]
    if not exe or not _SAFE_EXE.fullmatch(exe):
        # argv[0] is the command itself, so it cannot be quoted - quoting it
        # would make PowerShell print the name rather than run it. An
        # executable whose name is not a plain word is not something to invoke
        # on the strength of a guess; the kind's default is the safer answer.
        return ""
    return " ".join([exe] + [_ps_arg(a) for a in argv[1:]])


def _str_field(obj: dict, key: str) -> bool:
    """Whether `obj[key]` is a non-empty string.

    A transcript record is untrusted input: these fields are whatever the JSON
    held, and nothing downstream re-checks them. `cwd` reaches os.path via
    paths.norm and `customTitle` reaches strip_glyph, both of which raise on a
    non-string. A single record with a numeric title - a format change, or a
    corrupt line that still parses - would otherwise take down capture and
    every reconcile run with a traceback until that transcript rotates away.

    Screening at the read boundary keeps every consumer safe without each one
    repeating the check.
    """
    value = obj.get(key)
    return isinstance(value, str) and bool(value)


def _iter_json_lines(blob: str):
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue  # torn or partial line — skip it
        if isinstance(obj, dict):
            yield obj


def read_transcript_info(jsonl_path, stat_result=None, need_title: bool = True) -> TranscriptInfo | None:
    """Read cwd (and optionally the session title) from a transcript.

    `stat_result` lets a caller that already stat'ed the file avoid a second
    syscall. `need_title=False` skips the tail read entirely — most transcripts
    here exceed the tail threshold, so callers that only want the size or path
    would otherwise pay a 256 KB read and parse for a field they discard.

    Note that `need_title=False` does *not* help the periodic reconcile: that
    path resolves tab titles, so it needs the tail. The flag benefits the
    logon deploy and single-repo launch, which only read cwd and size.
    """
    p = pathlib.Path(jsonl_path)
    st = stat_result
    if st is None:
        try:
            st = p.stat()
        except OSError:
            return None

    cwd = ""
    title = ""
    try:
        with open(p, "rb") as fh:
            head = fh.read(_HEAD_BYTES).decode("utf-8", errors="replace")
            for obj in _iter_json_lines(head):
                if _str_field(obj, "cwd"):
                    cwd = obj["cwd"]
                    break

            # The tail is also where cwd hides when the head scan missed it, so
            # a size-only caller still reads it in that (rare) case.
            must_read_tail = need_title or not cwd
            if must_read_tail:
                # Size from the open handle, never the cached scandir stat. A
                # transcript can rotate between enumeration and open, and a
                # stale larger size makes seek(-_TAIL_BYTES, SEEK_END) land
                # before the start of the file: OSError [Errno 22], swallowed
                # below as "unreadable", so the project silently disappeared
                # from the index and every session in it from the layout.
                size = os.fstat(fh.fileno()).st_size
                if size > _TAIL_BYTES:
                    fh.seek(-_TAIL_BYTES, os.SEEK_END)
                    tail = fh.read().decode("utf-8", errors="replace")
                    tail = tail.split("\n", 1)[1] if "\n" in tail else ""
                else:
                    fh.seek(0)
                    tail = fh.read().decode("utf-8", errors="replace")
                for obj in _iter_json_lines(tail):
                    if _str_field(obj, "customTitle"):
                        title = obj["customTitle"]  # last one wins
                    if not cwd and _str_field(obj, "cwd"):
                        cwd = obj["cwd"]
    except OSError:
        return None

    if not cwd:
        return None
    return TranscriptInfo(
        cwd=cwd, title=title, path=str(p), mtime=st.st_mtime, size=st.st_size
    )


def default_projects_dir() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~")) / ".claude" / "projects"


def transcript_index(projects_dir=None, need_title: bool = True) -> dict[str, TranscriptInfo]:
    """Newest transcript per project, keyed by normalized cwd.

    Uses scandir rather than glob+stat over a corpus of >1,000 files: on Windows
    a DirEntry's stat is served from the directory enumeration itself, so
    selecting the newest costs no syscalls, and the winner's stat is reused
    instead of being taken again.
    """
    root = pathlib.Path(projects_dir) if projects_dir else default_projects_dir()
    index: dict[str, TranscriptInfo] = {}
    if not root.is_dir():
        return index

    try:
        with os.scandir(root) as it:
            children = sorted(it, key=lambda e: e.name)
    except OSError:
        return index

    for child in children:
        if not child.is_dir():
            continue
        # Claude Code actively creates and rotates transcripts in these
        # directories, so a file can disappear between being listed and being
        # stat'ed — cover the whole selection, not just the listing.
        try:
            with os.scandir(child.path) as entries:
                transcripts = [
                    e for e in entries if e.name.endswith(".jsonl") and e.is_file()
                ]
            newest = max(transcripts, key=lambda e: e.stat().st_mtime, default=None)
            if newest is None:
                continue
            newest_stat = newest.stat()
        except OSError:
            continue

        info = read_transcript_info(
            newest.path, stat_result=newest_stat, need_title=need_title
        )
        if info is None:
            continue
        key = norm(info.cwd)
        prior = index.get(key)
        if prior is None or info.mtime > prior.mtime:
            index[key] = info
    return index


def title_to_cwd(index: dict[str, TranscriptInfo]) -> dict[str, str]:
    """Reverse map for resolving a tab title to a repo. Newest wins on collision.

    Keyed on the *raw* customTitle, deliberately, and that is sound because
    strip_glyph only removes spinner frames. A customTitle is what the user set
    via /title; the spinner decorates the terminal title, not the transcript.
    So the key and the stripped tab title agree, including for an emoji title -
    "🚀 svc" keys and resolves, idle and busy alike.

    Keying on the stripped title instead looks like a repair and is worse:
    stripping is not injective, so two repos titled "✳ api" and "🔧 api"
    collapse to one key and the loser's cwd is *discarded*, erasing a live repo
    from the layout, binding a tab to another session's pid, and letting
    teardown close a window whose session is still working. Measured, not
    theorised.

    Residual miss: a customTitle that itself begins with a spinner character
    ("✳ odd") keeps it here but loses it on the lookup side, so it falls
    through to the repos-root basename guess.
    """
    best: dict[str, TranscriptInfo] = {}
    for info in index.values():
        if not info.title:
            continue
        prior = best.get(info.title)
        if prior is None or info.mtime > prior.mtime:
            best[info.title] = info
    return {title: info.cwd for title, info in best.items()}
