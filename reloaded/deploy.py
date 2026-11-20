"""Launch construction: turn a Layout into wt invocations."""
from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field, replace

from . import agents as agents_mod
from . import pwsh
from . import tabs as tabs_mod
from . import win32
from .discover import TranscriptInfo
from .layout import Layout, Monitor, Tab, clamp_rect, window_id
from .paths import (norm, parked_record, restart_marker, restart_marker_dir, restore_marker,
                    tab_title)
from .transcript import SIZE_WARN_BYTES, human_size

STAGGER_SECONDS = 4

# How long to let Windows Terminal settle its own startup layout before
# verifying/fixing a window's placement - see execute(). Waited once, after
# every window in the plan has already been placed, rather than once per
# window: the wait is about giving WT's own in-progress layout time to
# finish clobbering (or not) whatever we just applied, not about anything
# specific to a single window, so paying it N times for N windows would be
# pure added serial latency for the same one-time settling.
GEOMETRY_SETTLE_SECONDS = 0.3

# Suppresses the resume-return prompt ("compact or continue as-is") by pushing
# the token gate out of reach. Scoped to the launched process only — it is not a
# global env var, and it does not affect mid-session auto-compaction.
RESUME_SUPPRESSOR = "$env:CLAUDE_CODE_RESUME_TOKEN_THRESHOLD='999999999'"

# Claude Code marks the shells it spawns with CLAUDE_CODE_CHILD_SESSION. `up`
# and `restart` are normally run from inside a Claude Code session, so without
# this every tab we launch inherits the marker, decides it is a nested child,
# and silently disables transcript saving — the sessions come back looking
# healthy but stop recording history, so the next `--continue` finds nothing.
#
# Cleared per tab rather than by passing a sanitised env= to the Popen below:
# Windows Terminal hosts every window in one process, so a new tab inherits
# that long-lived process's environment, not the environment of the short-lived
# `wt` stub we spawn. Only a statement running inside the tab is reliable.
# In PowerShell assigning $null removes the variable outright rather than
# setting it empty, so children of this shell do not see it at all (verified).
CHILD_SESSION_CLEAR = "$env:CLAUDE_CODE_CHILD_SESSION=$null"

# How long a session must have run to count as started, rather than dying on
# startup - what relaunch's stale-loop handling still measures against.
STARTUP_GRACE_SECONDS = 10
CLAUDE_STARTED_AT = "$rlStart=Get-Date"


# How recently the agent's program must have been installed, when its session
# ends, for the tab to read the session as one that updated itself and quit.
UPDATED_WITHIN_SECONDS = 60


def _names_agent(binary: str) -> str:
    """A PowerShell regex, escaped for a single-quoted string, matching a
    command that runs `binary` - as the tab's invocation and markers' resume
    commands do, and as a command typed at the prompt usually does not."""
    return pwsh.escape(f"^\\s*{re.escape(binary)}(\\.exe)?(\\s|$)")


def park_write(cwd: str, kind: str) -> str:
    """Statements, in the tab, that record it as parked (parked.py) and set
    `$rlParked` - only once the record is there.

    The record holds its directory and kind, the agent command it takes back
    (`$rlAgent`, an exact resume a marker last handed the loop, else the tab's
    own invocation `$rlInv` - never a command typed at the prompt), and its
    shell, by pid and start time, so a record left by a shell since gone is
    told from the tab open now. Written to a temporary file and moved into
    place, so a reader never sees half of one; by .NET, for an absolute path
    and UTF-8 without a byte-order mark in both PowerShell versions. A
    failure says so and leaves the tab unparked: a tab that reads as parked
    with no record is one a restore would start, taking the conversation
    back."""
    path = pwsh.escape(str(parked_record(cwd, kind)))
    return ("try { $rlCmd=$rlInv; if ($rlAgent) { $rlCmd=$rlAgent }; "
            f"$rlRec=[ordered]@{{cwd='{pwsh.escape(cwd)}'; agent='{pwsh.escape(kind)}'; "
            "command=$rlCmd; shell_pid=$PID; "
            "shell_created=[DateTimeOffset]::new((Get-Process -Id $PID).StartTime)"
            ".ToUnixTimeMilliseconds()}; "
            f"[IO.File]::WriteAllText('{path}.tmp', ($rlRec | ConvertTo-Json -Compress)); "
            f"Move-Item -LiteralPath '{path}.tmp' -Destination '{path}' -Force -ErrorAction Stop; "
            "$rlParked=$true } catch { "
            "Write-Host \"[reloaded] could not park this tab: $_\" -ForegroundColor Red }")


def park_remove(cwd: str, kind: str) -> str:
    """Statements that remove the tab's record and clear `$rlParked` - only
    once it is gone: one left behind would keep the tab parked for every
    restore, while its session runs."""
    path = pwsh.escape(str(parked_record(cwd, kind)))
    return (f"Remove-Item -LiteralPath '{path}' -Force -ErrorAction SilentlyContinue; "
            f"if (Test-Path -LiteralPath '{path}') {{ "
            "Write-Host '[reloaded] could not unpark this tab: its record would not delete.' "
            "-ForegroundColor Red } else { $rlParked=$false }")


def updated_test(binary: str) -> str:
    """The test, in the tab, that the session just ended was its agent
    updating itself: `binary`'s file on PATH was installed while the session
    ran (`$rlStart`), within UPDATED_WITHIN_SECONDS of it ending.

    Codex asks at startup to update, installs, and exits ("Please restart
    Codex"). Installed is the later of the file's two times, since the two
    installers keep different ones (measured): Codex's standalone installer
    extracts a new release whose files keep the build's write time and get a
    new creation time; Claude Code's overwrites `~/.local/bin/claude.exe`, which
    keeps its creation time and gets a new write time.

    Recent, because Claude Code updates itself in the background without
    exiting: one updated an hour into its session was not quit by the update.

    Once per install (`$rlDone`), and never for a time ahead of the clock: a
    file dated in the future would otherwise pass on every exit until the
    clock caught up. Only when what ran was the agent - the tab's own
    invocation, or a command naming `binary` first, as markers' resume
    commands do - so a command typed at the prompt is not replayed because
    the agent updated. UTC, so a DST change cannot shift the window.

    Not proof that this session did the updating: another tab's update, or a
    background one, landing in the minute before a deliberate quit restarts it
    too, and the tab says so; Ctrl+C or a second quit leaves it.
    """
    return (f"$rlUp=$false; if ((-not $rlRan) -or ($rlRan -match '{_names_agent(binary)}')) {{ "
            f"$rlBin=Get-Command '{pwsh.escape(binary)}' -CommandType Application "
            "-ErrorAction SilentlyContinue | Select-Object -First 1; "
            "if ($rlBin) { $rlI=Get-Item -LiteralPath $rlBin.Source -ErrorAction SilentlyContinue; "
            "if ($rlI) { $rlT=$rlI.LastWriteTimeUtc; "
            "if ($rlI.CreationTimeUtc -gt $rlT) { $rlT=$rlI.CreationTimeUtc }; "
            "$rlAge=([DateTime]::UtcNow-$rlT).TotalSeconds; "
            "$rlUp=($rlT -gt $rlStart.ToUniversalTime()) -and ($rlAge -ge 0) -and "
            f"($rlAge -lt {UPDATED_WITHIN_SECONDS}) -and ($rlT -ne $rlDone); "
            "if ($rlUp) { $rlDone=$rlT } } } }")


# The key, typed alone, that parks an ended tab. PowerShell's -eq ignores
# case, so `p` and `P` both do.
PARK_KEY = "P"


def ended_prompt(name: str, binary: str, cwd: str = "", kind: str = "") -> str:
    """What a tab does once its session ends with no restart asked for: it
    stays, and a bare Enter starts the session again.

    Tabs used to close themselves here, once their session had run 10s. A
    closed tab's place in its window is gone - Windows Terminal cannot put a
    tab back at an index - and quitting to restart quickly is common. `down`
    does not need the self-close: teardown closes a tab whose session it saw
    exit through the tab's own button (teardown.close_dead_tabs).

    `down` is the exception: it leaves a stop file for the shell
    (paths.shell_stop) before quitting the session, and the loop closes the
    tab when it finds one (restart_loop), as tabs always used to. Otherwise an
    Enter landing between the session's exit and teardown closing the tab
    would start it again, and the close would end what it had just started.

    Only an empty answer starts the session again: the command the last pass
    ran (`$rlRan`). Anything typed runs in its place, inside the loop - so
    restart markers reach it as they reach the session, and a resume command
    relaunch's helper types when no loop took its marker works. Ctrl+C leaves
    for the shell. End of input reads as $null, not "", so a shell with no
    console never loops (measured, pwsh 7 and Windows PowerShell 5.1).

    A session that ended by updating its agent (updated_test) is started
    again without asking: the update said to restart, and Enter was all the
    prompt would have been given.

    `P` (PARK_KEY) is not run: it records the tab as parked (parked.py,
    park_write) and asks again, under a prompt that says so. Parked, only a
    bare Enter takes the tab back: its record is removed, and the agent
    command it was parked with starts (`$rlAgent`, else the tab's own) - not
    a command typed earlier, which a restored tab would not have. Anything
    else typed is ignored, so keystrokes meant for another window - a
    teardown's `/exit`, or typing that lands in a window that took the focus
    - cannot start a session and take the conversation back. End of input
    leaves it parked. A tab opened parked (`$rlParked`, from launcher_command)
    starts here, without a session.
    """
    say = (f"[reloaded] {name} ended. Enter starts it again; [{PARK_KEY}] parks it so "
           "its conversation can continue elsewhere; a typed command runs in its "
           "place; Ctrl+C leaves you at the shell.")
    parked = (f"[reloaded] {name} is parked: its conversation is free to continue "
              "elsewhere, such as Codex's remote control. Enter takes it back here.")
    again = f"[reloaded] {binary} was updated; starting {name} again."
    ask = ("$rlGo=$false; $rlQuit=$false; while (-not $rlGo) { "
           f"if ($rlParked) {{ Write-Host '{pwsh.escape(parked)}' -ForegroundColor Cyan }} "
           f"else {{ Write-Host '{pwsh.escape(say)}' -ForegroundColor DarkGray }}; "
           "$rlAns=Read-Host; "
           "if ($null -eq $rlAns) { $rlQuit=$true; $rlGo=$true } "
           f"elseif ($rlParked) {{ if ($rlAns -eq '') {{ {park_remove(cwd, kind)}; "
           "if (-not $rlParked) { $rlNext=$rlAgent; $rlGo=$true } } } "
           f"elseif ($rlAns.Trim() -eq '{PARK_KEY}') {{ {park_write(cwd, kind)} }} "
           "else { if ($rlAns -ne '') { $rlNext=$rlAns } else { $rlNext=$rlRan }; "
           "$rlGo=$true } }; "
           "if ($rlQuit) { break }")
    return (f"if ($rlParked) {{ $rlUp=$false }} else {{ {updated_test(binary)} }}; "
            f"if ($rlUp) {{ Write-Host '{pwsh.escape(again)}' -ForegroundColor DarkGray; "
            f"$rlNext=$rlRan }} else {{ {ask} }}; "
            "$rlStart=Get-Date")

def nothing_to_resume(folder: str) -> str:
    """The test, in the tab, that a resume of the latest conversation failed
    for want of one (agents.fresh): it exited 1, and `folder`
    (agents.conversations_dir) holds no conversation.

    Both, because neither alone is that failure. /exit returns 0, and so does
    declining Claude Code's folder-trust dialog (measured, 2.1.296). An exit 1
    where a conversation exists is a resume that failed some other way: a new
    conversation there would become the latest, the one every later restart
    continues. The folder is looked at on every pass, since a restart can come
    before the first conversation exists or after.

    Not bounded by the startup grace. In a directory not yet trusted, Claude
    asks first and looks for a conversation only once answered - measured,
    exit 1 at 35s - which is how a brand-new repo first opens. A session that
    was worked in has a conversation to show for it.
    """
    return (f"$LASTEXITCODE -eq 1 -and "
            f"-not (Get-ChildItem -LiteralPath '{pwsh.escape(folder)}' -Filter *.jsonl "
            f"-ErrorAction SilentlyContinue)")

# How long a restart marker stays good for. `restart` writes one, then has at
# most teardown.EXIT_TIMEOUT_SECONDS to get the session to end, so anything
# beyond a couple of minutes means the restart never reached its tab - the
# window would not come to the foreground, or reloaded itself died. The marker
# outlives the attempt either way, and obeying it later would relaunch a
# session the user had just deliberately exited by hand.
RESTART_MARKER_TTL_SECONDS = 120


def restart_loop(cwd: str, invocation: str) -> str:
    """Wrap `invocation` so `restart <repo>` can relaunch a session without the
    tab ever closing.

    `invocation` is whatever this tab runs - the agent kind's default, or the
    command capture read off the live process. Everything around it is a
    property of the shell rather than of the agent, so it is identical for
    every kind.

    A tab's slot in the window is lost the moment it closes, and Windows
    Terminal ships no keybinding for moving a tab to an index - so the only way
    to keep a restarted session where it was is to not let the tab go. The
    shell that already owns the tab runs claude again instead.

    `$rlStart` is reset per iteration, so it is the start of the session now
    running - what relaunch's old-loop handling measures against
    STARTUP_GRACE_SECONDS.

    A marker holding a command rather than the word `restart` runs that
    command, once, in place of `invocation`. `restart --self` writes its exact
    resume command there: `invocation` is how the tab was first launched, and
    replayed it brought back the directory's latest conversation, or an
    empty one, or Claude Code's picker - not the session that asked.

    The shell's own marker (paths.shell_marker, named by its `$PID`) is
    checked before the directory's. Only this shell reads it, so a sibling
    tab in the same directory cannot take a request meant for this one.
    """
    return "".join(_restart_loop_parts(cwd, invocation))


def _restart_loop_parts(cwd: str, invocation: str) -> tuple[str, str]:
    """restart_loop as its setup and its `while`, so that a tab can run the
    `while` again (deploy.ended_prompt) without the setup clearing `$rlNext`.

    `$rlRan` is the command the pass ran - what Enter brings back - kept apart
    from `$rlNext`, which a marker the loop then refuses as stale has already
    overwritten.

    `down`'s stop file (paths.shell_stop) is read straight after the session
    returns, before any restart marker: a restart left armed by a timed-out
    `restart` must not bring a session back under the teardown closing it.
    """
    m = pwsh.escape(str(restart_marker(cwd)))
    own = pwsh.escape(str(restart_marker_dir() / "shell-"))
    body = "; ".join((
        f"$rlRan=$rlNext; if ($rlNext) {{ Invoke-Expression $rlNext }} else {{ {invocation} }}",
        "$rlStop=$rlOwn -replace '\\.marker$','.stop'",
        "if (Test-Path -LiteralPath $rlStop) { "
        "$rlAge=((Get-Date)-(Get-Item -LiteralPath $rlStop).LastWriteTime).TotalSeconds; "
        "Remove-Item -LiteralPath $rlStop -Force -ErrorAction SilentlyContinue; "
        f"if ($rlAge -lt {RESTART_MARKER_TTL_SECONDS}) {{ exit }} }}",
        f"$rlM=$null; if (Test-Path -LiteralPath $rlOwn) {{ $rlM=$rlOwn }} "
        f"elseif (Test-Path -LiteralPath '{m}') {{ $rlM='{m}' }}",
        "if (-not $rlM) { break }",
        # Read the age before deleting, but delete either way - a stale marker
        # left on disk would be found again by the next exit.
        "$rlAge=((Get-Date)-(Get-Item -LiteralPath $rlM).LastWriteTime).TotalSeconds",
        # UTF-8 explicitly: Windows PowerShell 5.1 reads a BOM-less file - what
        # Python writes - in the ANSI code page, and would mangle a non-ASCII
        # path or name in the command before running it.
        "$rlNext=(Get-Content -Raw -Encoding UTF8 -LiteralPath $rlM -ErrorAction SilentlyContinue)",
        "if ($rlNext) { $rlNext=$rlNext.Trim() }",
        "if ($rlNext -eq 'restart') { $rlNext=$null }",
        "Remove-Item -LiteralPath $rlM -Force -ErrorAction SilentlyContinue",
        # Consuming the marker is what makes one request produce one restart,
        # so a delete that failed must stop the loop rather than be ignored.
        # Remove-Item is silenced, and a marker that survives is still there
        # and still fresh on the next pass: claude relaunches, exits, finds it
        # again, forever. Verified - a directory at the marker path (which
        # -Force cannot remove without -Recurse) spun until the test's 60s
        # subprocess timeout.
        "if (Test-Path -LiteralPath $rlM) { break }",
        f"if ($rlAge -gt {RESTART_MARKER_TTL_SECONDS}) {{ break }}",
        # The agent command the tab now runs, for a park to record
        # (park_write): a marker's exact resume, or none for the tab's own.
        "$rlAgent=$rlNext",
        "$rlStart=Get-Date",
    ))
    return (f"$rlNext=$null; $rlOwn='{own}' + $PID + '.marker'; ",
            f"while ($true) {{ {body} }}")


@functools.lru_cache(maxsize=1)
def shell_executable() -> str:
    """PowerShell 7 (pwsh) if installed, else the Windows PowerShell 5.1 that
    ships with every supported Windows version.

    pwsh is a separate install (winget/Store) that most machines do not have
    by default. Assuming it exists breaks every tab on day one for anyone
    without it: the shell fails to start, `claude` never launches, but `up`
    still reports the window "launched" since wt itself opened successfully —
    silently dead sessions, not a visible error. Both shells accept the same
    `$env:X='...'; Write-Host ...` syntax used here (verified), so no other
    change is needed for the fallback. Cached: this is one filesystem PATH
    probe, not something that changes mid-process.
    """
    return "pwsh" if shutil.which("pwsh") else "powershell"


def _wt_escape(s: str) -> str:
    """Escape a value being passed as one wt argument.

    wt treats `;` as a subcommand separator *inside* arguments, not just between
    them. Left raw, a multi-statement PowerShell command is torn apart: a single
    launcher became seven tabs in testing, one per statement. The documented
    escape is a backslash. This applies to directory paths too — a repo path
    containing a semicolon would split the command just as readily.
    """
    return s.replace(";", "\\;")


def launcher_command(cwd: str, delay: int, size_bytes: int,
                     agent: str = agents_mod.DEFAULT_KIND, command: str = "",
                     session_id: str = "", parked: bool = False) -> str:
    """The PowerShell command run inside one tab.

    `command` is what capture read off the live process; empty falls back to
    the kind's default. `session_id` is the conversation capture saw it in,
    which it resumes by name where known. Only that invocation differs
    between agent kinds - the environment hygiene around it, the restart loop
    and the ended prompt are properties of the shell and apply to
    every tab.

    A conversation named and gone is not swapped for another: its resume
    fails on screen, where `--last` would open some other conversation and a
    fresh start an empty one.
    """

    invocation = agents_mod.resuming(agent, command) or agents_mod.for_kind(agent).launch
    invocation = agents_mod.own_server(
        agent, agents_mod.resuming_exactly(agent, invocation, session_id))
    # The agent command alone, for a park to record (park_write).
    agent_command = invocation
    fresh = agents_mod.fresh(agent, invocation)
    folder = agents_mod.conversations_dir(agent, cwd)
    if fresh and folder:
        invocation += (f"; if ({nothing_to_resume(folder)}) "
                       f"{{ $rlStart=Get-Date; {fresh} }}")
    name = tab_title(cwd)
    parts = [CHILD_SESSION_CLEAR, RESUME_SUPPRESSOR]

    if size_bytes >= SIZE_WARN_BYTES:
        warn = f"[reloaded] {name} - transcript {human_size(size_bytes)}, consider /compact"
        parts.append(f"Write-Host '{pwsh.escape(warn)}' -ForegroundColor Yellow")
    else:
        parts.append(f"Write-Host '{pwsh.escape('[reloaded] ' + name)}' -ForegroundColor DarkGray")

    if delay > 0:
        parts.append(f"Start-Sleep {delay}")

    parts.append(CLAUDE_STARTED_AT)
    # The restart loop runs again after each session the user starts with
    # Enter, markers and all; `break` in ended_prompt leaves for the shell.
    # Its setup runs once: `$rlNext` keeps the command that last ran - an
    # exact resume `--self` handed the loop - so Enter brings that back.
    setup, loop = _restart_loop_parts(cwd, invocation)
    binary = agents_mod.for_kind(agent).binary
    # A parked tab opens at the prompt, its session not started, and records
    # itself again: the shell that parked it is gone, and this one is it now.
    setup += (f"$rlInv='{pwsh.escape(agent_command)}'; $rlRan=$null; $rlAgent=$null; "
              f"$rlParked=$false; $rlSkip=${str(parked).lower()}; ")
    if parked:
        setup += f"{park_write(cwd, agent)}; "
    prompt = ended_prompt(name, binary, cwd, agent)
    parts.append(f"{setup}while ($true) {{ if ($rlSkip) {{ $rlSkip=$false }} "
                 f"else {{ {loop} }}; {prompt} }}")
    return "; ".join(parts)


def relaunch_script(cwd: str, size_bytes: int,
                    agent: str = agents_mod.DEFAULT_KIND, command: str = "") -> str:
    """The launcher, as a script to run inside a shell that already exists.

    `restart <repo>` uses this on a session started by hand rather than by
    reloaded: /exit leaves its plain interactive shell sitting at a prompt in a
    tab nothing will close, so the launcher is put into that shell instead and
    the tab comes out behaving like any reloaded-launched one.

    It is a file rather than typed text because SendKeys reads `{` and `(` as
    syntax; escaped, the launcher is 838 keystrokes, and they did not arrive
    intact when tried. One short line runs the file instead.

    It is the launcher as it is: leaving the outer loop ends the script, and
    the shell that ran it is back at its prompt - what a reloaded tab does too.
    """
    return launcher_command(cwd, 0, size_bytes, agent, command) + "\n"


def new_tab_args(cwd: str, command: str,
                 agent: str = agents_mod.DEFAULT_KIND) -> list[str]:
    """The `new-tab` fragment of a wt command line, with escaping applied.

    Every wt argv in the package is assembled from this, so escaping is a
    property of the boundary rather than something each call site has to
    remember. Callers never need `_wt_escape` themselves.

    The tab is NAMED here, and that is what makes it findable. Reloaded
    resolves a tab by its title, and a tab it had not named showed the running
    program instead - `claude` - which matches no repo and no recorded title.
    Measured: a freshly opened session stayed unresolvable for the full two
    minutes it was watched, so `open` handed back a tab that `down` and
    `restart` could not touch until the user had talked to it and Claude Code
    had written a transcript to take a title from.

    `--suppressApplicationTitle` is required, not belt-and-braces. With
    `--title` alone the running program overwrote it inside five seconds and it
    never came back - watched at 5, 15, 30, 60 and 90 seconds, `claude` every
    time.

    The cost is real and worth naming: a session the user renames will not show
    that name in the tab strip, because reloaded is holding the title. On the
    machine this was measured on, every recorded title was already its repo's
    directory name, so nothing changed visually - but a person who renames
    sessions loses that, and the fix is to drop this one flag.

    The title also ends in the kind's invisible mark (agents.tab_label), which
    is how capture tells a directory's Claude tab from its Codex one.
    """
    return [
        "new-tab",
        "--title", _wt_escape(agents_mod.tab_label(agent, cwd)),
        "--suppressApplicationTitle",
        "-d", _wt_escape(cwd),
        shell_executable(), "-NoExit", "-Command", _wt_escape(command),
    ]


def wt_argv_single_tab(cwd: str, size_bytes: int = 0,
                       agent: str = agents_mod.DEFAULT_KIND, command: str = "") -> list[str]:
    """Open one repo as a tab in the current window (`-w 0`)."""
    return ["wt", "-w", "0"] + new_tab_args(
        cwd, launcher_command(cwd, 0, size_bytes, agent, command), agent)


def wt_argvs(name: str, rect: list[int], tabs: list[Tab], delays: list[int],
             sizes: list[int]) -> tuple[list[str], list[str]]:
    """Two wt invocations for one window: one creating it, named `name`, with
    its first tab; and one adding the rest of its tabs to it, or [] if none.

    Split because a tab only gets the window's size if it exists after the
    window has it. Windows Terminal resizes the tab in front when the window
    is resized, and a background tab not until it is first shown - by which
    time its agent has drawn for 120x30. Measured on a real logon restore: 11
    of 15 tabs still read 120x30 in windows holding 133x71, and every one of
    them was jumbled when switched to, until the window was resized by hand.

    `--pos` gets the window born in the right place so it does not visibly jump.
    Size is deliberately not passed: wt's `--size` is in character cells, not
    pixels, so an exact rect is applied afterwards via SetWindowPlacement -
    between the two invocations.
    """
    def tab_args(i: int) -> list[str]:
        tab = tabs[i]
        return new_tab_args(tab.cwd, launcher_command(
            tab.cwd, delays[i], sizes[i], tab.agent, tab.command, tab.session_id,
            tab.parked), tab.agent)

    x, y = rect[0], rect[1]
    # `--pos=x,y` rather than `--pos x,y`: a monitor left of the primary yields
    # a negative x, and a bare "-1920,..." argument is parsed as an option, not
    # a value, for the two-token form.
    window = ["wt", "-w", name, f"--pos={x},{y}"] + tab_args(0)
    rest: list[str] = []
    for i in range(1, len(tabs)):
        rest += [";"] if rest else ["wt", "-w", name]  # `;` must stay unescaped
        rest += tab_args(i)
    return window, rest


@dataclass
class PlanEntry:
    """One window's worth of work: what to launch, what to skip, and why.

    A dataclass rather than a dict — the plan is threaded through three
    modules and this file's own tests; a typo in a string key would surface
    as a runtime KeyError instead of a static/IDE-catchable error, which is
    exactly the class of mistake that made the last two refactors require
    careful manual find-and-fix across every call site.
    """

    id: str
    state: str
    rect: list[int]
    tabs: list[Tab]
    skipped: list[Tab]
    missing: list[Tab]
    delays: list[int]
    argv: list[str]
    # The rest of the window's tabs, added once `argv`'s window is sized. See
    # wt_argvs.
    rest_argv: list[str] = field(default_factory=list)


@dataclass
class LaunchResult:
    window_id: str
    hwnd: int | None
    placed: bool
    # Seconds into `execute` at which this window's `wt` was spawned, or None
    # if it never was. A tab's staggered delay is a sleep inside a shell that
    # does not exist until then, so this is the origin its turn is measured
    # from - `execute`'s own start can be a minute earlier.
    spawned_at: float | None = None
    # Why the placement failed, "" when it did not. Carried so the warning can
    # say which of three different failures this was; see win32.Placed.
    why: str = ""
    # The same pair for the call that added the window's other tabs (see
    # wt_argvs): when it was spawned, and why it could not be, "" if it was.
    rest_spawned_at: float | None = None
    rest_error: str = ""


def plan_deploy(
    lo: Layout,
    live: dict[str, int],
    monitors: list[Monitor],
    sizes: dict[str, int],
    running=None,
    records=None,
) -> list[PlanEntry]:
    """Compute exactly what would be launched, without launching it.

    Tabs whose session is already running are skipped, which makes deploy
    idempotent and therefore safe to re-run after a partial failure.
    `running` (discover.running) makes that per session: a directory's Codex
    tab is not skipped because its Claude one is up, and two saved Claude tabs
    there are not both answered by one live one. Without it, a live directory
    counts as running every kind.
    """
    from . import discover, parked as parked_mod

    plan: list[PlanEntry] = []
    index = 0
    remaining = None if running is None else running.copy()
    if records is None:
        records = parked_mod.load_all()
    held = parked_mod.by_key(records)
    table = win32.process_table() if held else {}
    # One record is one tab: of a directory's saved tabs of its kind, the one
    # saved parked, else the first. Its siblings are ordinary sessions.
    owner: dict = {}
    for prefer_parked in (True, False):
        for window in lo.windows:
            for tab in window.tabs:
                key = (norm(tab.cwd), tab.agent or agents_mod.DEFAULT_KIND)
                if key in held and key not in owner and (tab.parked or not prefer_parked):
                    owner[key] = id(tab)

    for position, window in enumerate(lo.windows):
        to_launch: list[Tab] = []
        skipped: list[Tab] = []
        missing: list[Tab] = []
        for tab in window.tabs:
            # Parked: what decides is its record, not the layout's flag, which
            # is only as fresh as the last capture. Open already, it is
            # skipped like a running session; closed, it opens waiting, with
            # the command the record says it takes back.
            key = (norm(tab.cwd), tab.agent or agents_mod.DEFAULT_KIND)
            record = held.get(key) if owner.get(key) == id(tab) else None
            if record is None and tab.parked:
                # Saved parked, and taken back or unparked since: a session.
                tab = replace(tab, parked=False)
            if record is not None:
                if parked_mod.is_open(record, table):
                    skipped.append(tab)
                elif os.path.isdir(tab.cwd):
                    to_launch.append(replace(
                        tab, parked=True, command=record.command or tab.command,
                        session_id="" if record.command else tab.session_id))
                else:
                    missing.append(tab)
                continue
            if remaining is not None:
                up = discover.claim_running(remaining, live, tab.cwd, tab.agent)
            else:
                up = norm(tab.cwd) in live
            if up:
                skipped.append(tab)
            elif not os.path.isdir(tab.cwd):
                # readiness.wait_for_ready deliberately never blocks on this —
                # a deleted repo's directory would never come back, so this is
                # the actual point where a stale pinned tab is dropped.
                missing.append(tab)
            else:
                to_launch.append(tab)

        if not to_launch:
            continue

        delays = []
        tab_sizes = []
        for tab in to_launch:
            # A parked tab starts nothing, so it takes no turn in the stagger.
            delays.append(0 if tab.parked else index * STAGGER_SECONDS)
            tab_sizes.append(int(sizes.get(norm(tab.cwd), 0)))
            index += 0 if tab.parked else 1

        rect = clamp_rect(
            window.rect, window.dpi, window.monitor, monitors, window.inset
        )
        # Unique per launch: a name wt already knows adds tabs to that window.
        name = f"reloaded-{window_id(position)}-{uuid.uuid4().hex[:8]}"
        argv, rest_argv = wt_argvs(name, rect, to_launch, delays, tab_sizes)
        plan.append(
            PlanEntry(
                id=window_id(position),
                state=window.state,
                rect=rect,
                tabs=to_launch,
                skipped=skipped,
                missing=missing,
                delays=delays,
                argv=argv,
                rest_argv=rest_argv,
            )
        )
    return plan


def _pick_window(candidates: set[int], wanted_names: set[str], get_titles) -> int | None:
    """Choose which candidate HWND is the one this entry just launched.

    With one candidate there is nothing to decide. With more than one — another
    WT window opened by the user, or restored by WT itself, during the same
    poll window — pick whichever's visible tabs overlap the most with the repo
    names this entry launched. `get_titles` may raise (a window can close mid
    check, or UIA can be transiently unavailable); such a candidate scores 0
    rather than aborting the pick. Ties fall back to the lowest HWND, matching
    prior behavior when no candidate can be distinguished at all.
    """
    if len(candidates) <= 1:
        return next(iter(candidates), None)

    def score(hwnd: int) -> int:
        try:
            return len(set(get_titles(hwnd)) & wanted_names)
        except Exception:
            return 0

    return max(sorted(candidates), key=score)


def launch_window(argv: list[str], tabs: list[Tab], timeout: float = 20.0, settle: float = 0.5) -> int | None:
    """Spawn one wt window and return its HWND.

    Takes just the two fields it needs (the command and the tabs it launches,
    for disambiguation) rather than a whole PlanEntry — the narrower
    signature is what makes it obvious this function has no business reading
    an entry's rect or state.

    Windows Terminal hosts every window in a single process, so a PID cannot
    identify the new window. Diffing the top-level window list before and
    after is the primary signal; see _pick_window for how ties are broken.
    """
    before = set(win32.list_wt_windows())
    try:
        subprocess.Popen(argv, close_fds=True)
    except OSError:
        # wt.exe missing or unlaunchable despite readiness having passed
        # earlier. Report as unidentified rather than aborting callers still
        # mid-loop over the rest of the plan.
        return None

    wanted_names = {agents_mod.tab_label(t.agent, t.cwd) for t in tabs}
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.25)
        new = set(win32.list_wt_windows()) - before
        if not new:
            continue
        if len(new) > 1:
            time.sleep(settle)  # let tabs render before reading titles to disambiguate
            new = set(win32.list_wt_windows()) - before  # a sibling may have closed meanwhile
        return _pick_window(new, wanted_names, tabs_mod.tab_titles)
    return None


def transcripts_to_repair(plan: list[PlanEntry], index: dict[str, TranscriptInfo]) -> list[str]:
    """Transcript paths for the sessions this plan will launch.

    Only for kinds with a verified torn-tail guard. `index` is Claude Code's
    transcript corpus, so a Codex tab is mostly safe by accident - but a repo
    that used to run Claude and now runs Codex has an entry in there, and
    repairing it on that tab's behalf would truncate a file for a session that
    is not going to read it.
    """
    from .transcript import guards_for

    wanted = {
        norm(t.cwd)
        for entry in plan for t in entry.tabs
        # A parked tab opens with no session: its transcript is not read now,
        # and may be in use elsewhere.
        if guards_for(t.agent) and not t.parked
    }
    return [info.path for key, info in index.items() if key in wanted]


# How long after its staggered turn a session should have appeared as a
# process. Below this it has not failed to start; it has not been asked.
SESSION_START_ALLOWANCE = 10

# How long after its last staggered launch a restore is still starting.
RESTORE_GRACE_SECONDS = 30


def restore_window(plan: list[PlanEntry]) -> float:
    """Seconds from now until this plan's last session should have started.

    The stagger is a `Start-Sleep` inside each launched shell, not something
    `execute` waits out, so a thirteen-session restore is still starting its
    last session forty-eight seconds after `execute` returned.
    """
    last = max((d for entry in plan for d in entry.delays), default=0)
    return last + RESTORE_GRACE_SECONDS


def mark_restoring(plan: list[PlanEntry]) -> None:
    """Say that sessions are coming up, so a capture does not read the gap.

    A reconcile firing inside this window sees tabs whose agents have not
    started, finds them absent from `live`, and cannot tell that from the user
    having closed them - so it writes a layout with them missing. Measured on a
    real logon: a capture 43 seconds into a 48-second stagger took four
    repositories out of the saved layout.

    Expiry rather than a clearing call, for the same reason the restart marker
    uses one: `execute` returns long before the sessions it started have
    finished starting, so there is no moment at which this process could
    honestly remove it.
    """
    window = restore_window(plan)
    # Never shorten a restore already in flight. The marker is one file for the
    # machine, so a three-session restore of one layout would otherwise replace
    # a thirteen-session restore's deadline with its own - and the reconcile
    # would then capture the longer one's half-finished desktop.
    #
    # Read-then-write, not a lock: `save`'s docstring is right that a lock here
    # needs a stale-lock story on a machine that crashes. The race this leaves
    # is two marks landing together and the shorter winning, which is the
    # behaviour of every mark before this one.
    if restoring_for() > window:
        return
    try:
        restore_marker().write_text(f"{window:.0f}", encoding="utf-8")
    except OSError:
        # Best effort. Failing a restore because a hint could not be written
        # would trade a recoverable layout for an unrecoverable desktop.
        pass


def restoring_for() -> float:
    """Seconds left of a restore still starting, or 0 when none is.

    Anything unreadable reads as "no restore": a capture that could never save
    again because of a corrupt marker is a worse failure than the one this
    prevents.
    """
    marker = restore_marker()
    try:
        window = float(marker.read_text(encoding="utf-8").strip())
        left = window - (time.time() - marker.stat().st_mtime)
    except (OSError, ValueError):
        return 0.0
    # Clamped to the window that was written. An mtime ahead of the clock -
    # a logon is exactly when w32time resyncs, when a VM resumes, and when a
    # dual-boot machine corrects an RTC written as local time - would
    # otherwise make `left` arbitrarily large and stop every capture for
    # hours, through a reconcile with no console to say so.
    return min(left, window) if left > 0 else 0.0


def execute(plan: list[PlanEntry]) -> list[LaunchResult]:
    """Launch every window in the plan and apply its exact geometry."""
    mark_restoring(plan)
    started_at = time.monotonic()
    results: list[LaunchResult] = []
    # (result, target rect, target state) for every window placed below, so
    # the settle-and-verify pass after the loop knows what each one should
    # look like without re-deriving it from the plan.
    to_verify: list[tuple[LaunchResult, list[int], str]] = []

    for entry in plan:
        # Before the call, not after. `launch_window` runs Popen and then polls
        # for the window for up to twenty seconds, so timing from its return
        # pushes a delay-zero tab's turn twenty seconds into the future - and a
        # session stuck at a trust prompt is then reported as "still starting"
        # rather than failed, with nothing checking again later.
        spawned_at = time.monotonic() - started_at
        # Identified by its first tab alone: the others are not in it yet, and
        # scoring on them could pick another window that shows their names.
        hwnd = launch_window(entry.argv, entry.tabs[:1])
        # Refreshed per spawn, not once at the top. `launch_window` polls for
        # up to twenty seconds before giving up on an HWND, so four slow
        # windows outlast a deadline computed from in-shell delays alone - and
        # a reconcile arriving in that gap overwrites the layout mid-restore,
        # which is the failure this marker exists to prevent.
        mark_restoring(plan)
        placed = win32.Placed(False, "")
        if hwnd is not None:
            # wt --pos got it close; this makes the rect exact and restores
            # maximized state, which --size (character cells) cannot express.
            placed = win32.set_geometry(hwnd, entry.rect, entry.state)
        result = LaunchResult(window_id=entry.id, hwnd=hwnd,
                              placed=placed.ok, why=placed.why,
                              spawned_at=None if hwnd is None else spawned_at)
        results.append(result)
        if placed.ok:
            to_verify.append((result, entry.rect, entry.state))

    if to_verify:
        # One shared wait for Windows Terminal's own startup layout to
        # settle, not one per window - every placement above already
        # happened; this only decides whether any of them need correcting.
        time.sleep(GEOMETRY_SETTLE_SECONDS)
        for result, rect, state in to_verify:
            verified = win32.verify_and_fix_geometry(result.hwnd, rect, state)
            result.placed, result.why = verified.ok, verified.why

    # Only now, with every window at its final size, do the rest of the tabs
    # go in - see wt_argvs. By name, so a window that could not be found or
    # placed still gets its sessions; they just start at whatever size it has.
    for entry, result in zip(plan, results):
        if not entry.rest_argv:
            continue
        result.rest_spawned_at = time.monotonic() - started_at
        try:
            subprocess.Popen(entry.rest_argv, close_fds=True)
        except OSError as exc:
            result.rest_spawned_at = None
            result.rest_error = str(exc) or type(exc).__name__
    mark_restoring(plan)

    return results
