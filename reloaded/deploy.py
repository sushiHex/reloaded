"""Launch construction: turn a Layout into wt invocations."""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import time
from dataclasses import dataclass

from . import tabs as tabs_mod
from . import win32
from .discover import TranscriptInfo
from .layout import Layout, Monitor, Tab, clamp_rect, window_id
from .paths import norm, restart_marker
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

CLAUDE_COMMAND = "claude --dangerously-skip-permissions --continue"

# A tab exists to host one Claude Code session, so once that session ends the
# tab is dead weight. `-NoExit` (see new_tab_args) keeps the shell alive on
# purpose though: a launch that fails immediately has to stay on screen with
# its error rather than vanishing, which is the "silently dead sessions" case
# shell_executable() guards against. Exiting only after claude has run long
# enough to have started successfully satisfies both.
#
# This is what lets `down`/`restart` leave a clean desktop. A window is closed
# only when every one of its Claude tabs exited (teardown.execute_down), so one
# holdout used to strand the whole window - including tabs whose session had
# already ended, left sitting at a bare shell prompt. Now those tabs close
# themselves and the window shrinks to just the holdout.
#
# Gated on elapsed time rather than $LASTEXITCODE because what `claude` returns
# on /exit is not something this package verifies; guessing wrong would either
# strand dead tabs or swallow startup errors. Elapsed time separates the two
# cases directly. Verified: an explicit `exit` inside -Command terminates the
# shell even under -NoExit.
STARTUP_GRACE_SECONDS = 10
CLAUDE_STARTED_AT = "$rlStart=Get-Date"
CLOSE_TAB_IF_STARTED = (
    f"if (((Get-Date)-$rlStart).TotalSeconds -gt {STARTUP_GRACE_SECONDS}) {{ exit }}"
)

# How long a restart marker stays good for. `restart` writes one, then has at
# most teardown.EXIT_TIMEOUT_SECONDS to get the session to end, so anything
# beyond a couple of minutes means the restart never reached its tab - the
# window would not come to the foreground, or reloaded itself died. The marker
# outlives the attempt either way, and obeying it later would relaunch a
# session the user had just deliberately exited by hand.
RESTART_MARKER_TTL_SECONDS = 120


def restart_loop(cwd: str) -> str:
    """Wrap the claude invocation so `restart <repo>` can relaunch a session
    without the tab ever closing.

    A tab's slot in the window is lost the moment it closes, and Windows
    Terminal ships no keybinding for moving a tab to an index - so the only way
    to keep a restarted session where it was is to not let the tab go. The
    shell that already owns the tab runs claude again instead.

    `$rlStart` is reset per iteration so CLOSE_TAB_IF_STARTED still measures
    the *relaunched* session's lifetime. Measured from the original launch it
    would always look successful, and a relaunch that died on startup would
    close its tab and take the error with it.
    """
    m = _ps_quote(str(restart_marker(cwd)))
    body = "; ".join((
        CLAUDE_COMMAND,
        f"if (-not (Test-Path '{m}')) {{ break }}",
        # Read the age before deleting, but delete either way - a stale marker
        # left on disk would be found again by the next exit.
        f"$rlAge=((Get-Date)-(Get-Item '{m}').LastWriteTime).TotalSeconds",
        f"Remove-Item '{m}' -Force -ErrorAction SilentlyContinue",
        # Consuming the marker is what makes one request produce one restart,
        # so a delete that failed must stop the loop rather than be ignored.
        # Remove-Item is silenced, and a marker that survives is still there
        # and still fresh on the next pass: claude relaunches, exits, finds it
        # again, forever. Verified - a directory at the marker path (which
        # -Force cannot remove without -Recurse) spun until the test's 60s
        # subprocess timeout.
        f"if (Test-Path '{m}') {{ break }}",
        f"if ($rlAge -gt {RESTART_MARKER_TTL_SECONDS}) {{ break }}",
        "$rlStart=Get-Date",
    ))
    return f"while ($true) {{ {body} }}"


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


def _ps_quote(s: str) -> str:
    """Escape for a PowerShell single-quoted string."""
    return s.replace("'", "''")


def _wt_escape(s: str) -> str:
    """Escape a value being passed as one wt argument.

    wt treats `;` as a subcommand separator *inside* arguments, not just between
    them. Left raw, a multi-statement PowerShell command is torn apart: a single
    launcher became seven tabs in testing, one per statement. The documented
    escape is a backslash. This applies to directory paths too — a repo path
    containing a semicolon would split the command just as readily.
    """
    return s.replace(";", "\\;")


def launcher_command(cwd: str, delay: int, size_bytes: int) -> str:
    """The PowerShell command run inside one tab."""
    name = os.path.basename(cwd.rstrip("\\/")) or cwd
    parts = [CHILD_SESSION_CLEAR, RESUME_SUPPRESSOR]

    if size_bytes >= SIZE_WARN_BYTES:
        warn = f"[reloaded] {name} - transcript {human_size(size_bytes)}, consider /compact"
        parts.append(f"Write-Host '{_ps_quote(warn)}' -ForegroundColor Yellow")
    else:
        parts.append(f"Write-Host '{_ps_quote('[reloaded] ' + name)}' -ForegroundColor DarkGray")

    if delay > 0:
        parts.append(f"Start-Sleep {delay}")

    parts.append(CLAUDE_STARTED_AT)
    parts.append(restart_loop(cwd))
    parts.append(CLOSE_TAB_IF_STARTED)
    return "; ".join(parts)


def relaunch_script(cwd: str, size_bytes: int) -> str:
    """The launcher, as a script to run inside a shell that already exists.

    `restart <repo>` uses this on a session started by hand rather than by
    reloaded: /exit leaves its plain interactive shell sitting at a prompt in a
    tab nothing will close, so the launcher is put into that shell instead and
    the tab comes out behaving like any reloaded-launched one.

    It is a file rather than typed text because SendKeys reads `{` and `(` as
    syntax; escaped, the launcher is 838 keystrokes, and they did not arrive
    intact when tried. One short line runs the file instead.

    The one thing that cannot survive the move is `exit`. Inside a called
    script it ends the script, and the tab would be left open where a reloaded
    tab closes - so the shell is stopped by pid, which works from any scope. A
    PowerShell script runs in the calling process, so $PID is that shell.
    """
    body = launcher_command(cwd, 0, size_bytes)
    return body.replace(
        CLOSE_TAB_IF_STARTED,
        f"if (((Get-Date)-$rlStart).TotalSeconds -gt {STARTUP_GRACE_SECONDS}) "
        f"{{ Stop-Process -Id $PID }}",
    ) + "\n"


def new_tab_args(cwd: str, command: str) -> list[str]:
    """The `new-tab` fragment of a wt command line, with escaping applied.

    Every wt argv in the package is assembled from this, so escaping is a
    property of the boundary rather than something each call site has to
    remember. Callers never need `_wt_escape` themselves.
    """
    return [
        "new-tab", "-d", _wt_escape(cwd),
        shell_executable(), "-NoExit", "-Command", _wt_escape(command),
    ]


def wt_argv_single_tab(cwd: str, size_bytes: int = 0) -> list[str]:
    """Open one repo as a tab in the current window (`-w 0`)."""
    return ["wt", "-w", "0"] + new_tab_args(cwd, launcher_command(cwd, 0, size_bytes))


def wt_argv(rect: list[int], tabs: list[Tab], delays: list[int], sizes: list[int]) -> list[str]:
    """Build one wt invocation creating a new window with tabs in order.

    `--pos` gets the window born in the right place so it does not visibly jump.
    Size is deliberately not passed: wt's `--size` is in character cells, not
    pixels, so an exact rect is applied afterwards via SetWindowPlacement.
    """
    x, y = rect[0], rect[1]
    # `--pos=x,y` rather than `--pos x,y`: a monitor left of the primary yields
    # a negative x, and a bare "-1920,..." argument is parsed as an option, not
    # a value, for the two-token form.
    argv = ["wt", "-w", "-1", f"--pos={x},{y}"]
    for i, tab in enumerate(tabs):
        if i:
            argv.append(";")  # structural separator — must stay unescaped
        argv += new_tab_args(tab.cwd, launcher_command(tab.cwd, delays[i], sizes[i]))
    return argv


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


@dataclass
class LaunchResult:
    window_id: str
    hwnd: int | None
    placed: bool


def plan_deploy(
    lo: Layout,
    live: dict[str, int],
    monitors: list[Monitor],
    sizes: dict[str, int],
) -> list[PlanEntry]:
    """Compute exactly what would be launched, without launching it.

    Tabs whose session is already running are skipped, which makes deploy
    idempotent and therefore safe to re-run after a partial failure.
    """
    plan: list[PlanEntry] = []
    index = 0

    for position, window in enumerate(lo.windows):
        to_launch: list[Tab] = []
        skipped: list[Tab] = []
        missing: list[Tab] = []
        for tab in window.tabs:
            if norm(tab.cwd) in live:
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
            delays.append(index * STAGGER_SECONDS)
            tab_sizes.append(int(sizes.get(norm(tab.cwd), 0)))
            index += 1

        rect = clamp_rect(
            window.rect, window.dpi, window.monitor, monitors, window.inset
        )
        plan.append(
            PlanEntry(
                id=window_id(position),
                state=window.state,
                rect=rect,
                tabs=to_launch,
                skipped=skipped,
                missing=missing,
                delays=delays,
                argv=wt_argv(rect, to_launch, delays, tab_sizes),
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

    wanted_names = {os.path.basename(t.cwd.rstrip("\\/")) for t in tabs}
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
    """Transcript paths for the sessions this plan will launch."""
    wanted = {norm(t.cwd) for entry in plan for t in entry.tabs}
    return [info.path for key, info in index.items() if key in wanted]


def execute(plan: list[PlanEntry]) -> list[LaunchResult]:
    """Launch every window in the plan and apply its exact geometry."""
    results: list[LaunchResult] = []
    # (result, target rect, target state) for every window placed below, so
    # the settle-and-verify pass after the loop knows what each one should
    # look like without re-deriving it from the plan.
    to_verify: list[tuple[LaunchResult, list[int], str]] = []

    for entry in plan:
        hwnd = launch_window(entry.argv, entry.tabs)
        placed = False
        if hwnd is not None:
            # wt --pos got it close; this makes the rect exact and restores
            # maximized state, which --size (character cells) cannot express.
            placed = win32.set_geometry(hwnd, entry.rect, entry.state)
        result = LaunchResult(window_id=entry.id, hwnd=hwnd, placed=placed)
        results.append(result)
        if placed:
            to_verify.append((result, entry.rect, entry.state))

    if to_verify:
        # One shared wait for Windows Terminal's own startup layout to
        # settle, not one per window - every placement above already
        # happened; this only decides whether any of them need correcting.
        time.sleep(GEOMETRY_SETTLE_SECONDS)
        for result, rect, state in to_verify:
            result.placed = win32.verify_and_fix_geometry(result.hwnd, rect, state)

    return results
