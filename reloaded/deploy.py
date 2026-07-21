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
from .paths import norm
from .transcript import SIZE_WARN_BYTES, human_size

STAGGER_SECONDS = 4

# Suppresses the resume-return prompt ("compact or continue as-is") by pushing
# the token gate out of reach. Scoped to the launched process only — it is not a
# global env var, and it does not affect mid-session auto-compaction.
RESUME_SUPPRESSOR = "$env:CLAUDE_CODE_RESUME_TOKEN_THRESHOLD='999999999'"

CLAUDE_COMMAND = "claude --dangerously-skip-permissions --continue"


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
    parts = [RESUME_SUPPRESSOR]

    if size_bytes >= SIZE_WARN_BYTES:
        warn = f"[reloaded] {name} - transcript {human_size(size_bytes)}, consider /compact"
        parts.append(f"Write-Host '{_ps_quote(warn)}' -ForegroundColor Yellow")
    else:
        parts.append(f"Write-Host '{_ps_quote('[reloaded] ' + name)}' -ForegroundColor DarkGray")

    if delay > 0:
        parts.append(f"Start-Sleep {delay}")

    parts.append(CLAUDE_COMMAND)
    return "; ".join(parts)


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

        rect = clamp_rect(window.rect, window.dpi, window.monitor, monitors)
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
    for entry in plan:
        hwnd = launch_window(entry.argv, entry.tabs)
        placed = False
        if hwnd is not None:
            # wt --pos got it close; this makes the rect exact and restores
            # maximized state, which --size (character cells) cannot express.
            placed = win32.set_geometry(hwnd, entry.rect, entry.state)
        results.append(LaunchResult(window_id=entry.id, hwnd=hwnd, placed=placed))
    return results
