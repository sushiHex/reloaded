"""Graceful shutdown: send /exit to every live Claude Code tab, then close
windows that were entirely made up of sessions that exited cleanly.

Mirrors capture.py's tab resolution (title -> live cwd) so `down` only ever
acts on tabs this tool already recognizes as Claude Code sessions - the same
"Claude tabs only" scope as capture and deploy. A window is only closed if
every one of its tabs was a recognized Claude session and every one of them
actually exited; a window with an unrelated manual tab, or a holdout that
didn't respond to /exit in time, is left open rather than force-closed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import discover, tabs, win32
from .capture import resolve_tab
from .paths import norm

EXIT_TIMEOUT_SECONDS = 20.0
EXIT_POLL_SECONDS = 0.5
# If a session hasn't exited by this far into EXIT_TIMEOUT_SECONDS, resend
# the exit keystrokes once. Covers Claude Code's own /exit confirmation when
# background agents are still running for that session - a warning the
# first /exit alone does not satisfy, verified against the changelog
# ("Fixed /exit incorrectly warning about running background agents...").
EXIT_RETRY_AFTER_SECONDS = 6.0


@dataclass
class WindowPlan:
    hwnd: int
    total_tabs: int
    # (title, cwd, pid, TabItemControl) for the tabs recognized as live Claude sessions.
    targets: list[tuple] = field(default_factory=list)


def plan_down(
    repos_root: str,
    *,
    live: dict[str, int] | None = None,
    title_map: dict[str, str] | None = None,
) -> list[WindowPlan]:
    """Every WT window with at least one live Claude Code tab, and which of
    its tabs those are. Read-only: selects, types, and closes nothing.

    ``live``/``title_map`` let a caller that already computed them (restart,
    right after its own capture) pass them straight through instead of
    paying for the psutil scan and the transcript-corpus walk again.
    """
    if live is None:
        live = discover.live_sessions()
    if title_map is None:
        title_map = discover.title_to_cwd(discover.transcript_index())

    plans: list[WindowPlan] = []
    for hwnd in win32.list_wt_windows():
        items = tabs.list_tab_items(hwnd)
        targets = []
        for title, item in items:
            resolved = resolve_tab(title, title_map, live, repos_root)
            if resolved is None:
                continue
            cwd, _low_confidence = resolved
            targets.append((title, cwd, live[norm(cwd)], item))
        if targets:
            plans.append(WindowPlan(hwnd=hwnd, total_tabs=len(items), targets=targets))
    return plans


def _send_exit(hwnd: int, item) -> bool:
    """Foreground `item`'s tab and type /exit into it. Returns whether the
    foreground actually happened (see tabs.select_tab) - a False return
    means nothing was typed, so the caller must not assume /exit was sent."""
    if not tabs.select_tab(hwnd, item):
        return False
    tabs.send_exit_keystrokes()
    return True


def execute_down(plans: list[WindowPlan], log=print) -> dict:
    """Send /exit to every planned tab, wait for each to actually end, then
    close each window whose tabs were all Claude sessions and all exited."""
    exited: list[tuple[str, str]] = []
    timed_out: list[tuple[str, str]] = []
    closed: list[int] = []
    left_open: list[int] = []

    for plan in plans:
        all_exited = True
        for title, cwd, pid, item in plan.targets:
            if not _send_exit(plan.hwnd, item):
                log(
                    f"    [warn] could not bring window 0x{plan.hwnd:X} to the "
                    f"foreground - skipping {title!r} (exit it manually)"
                )
                timed_out.append((title, cwd))
                all_exited = False
                continue

            log(f"    sent /exit -> {title}  [{cwd}]")

            # Polls the specific pid rather than re-scanning every process via
            # discover.live_sessions() - a full psutil.process_iter() sweep on
            # every tick is real, avoidable cost across dozens of tabs. Trade-
            # off: this no longer re-confirms the pid is still claude.exe at
            # this cwd on each tick, only that it's still running - fine at
            # this timescale (Windows does not aggressively recycle pids).
            # Only reached with a real pid, which plan_down could only have
            # produced via discover.live_sessions() - psutil is therefore
            # already imported; this is a cached re-import, not a fresh load.
            import psutil

            start = time.time()
            deadline = start + EXIT_TIMEOUT_SECONDS
            retry_at = start + EXIT_RETRY_AFTER_SECONDS
            retried = False
            while psutil.pid_exists(pid):
                now = time.time()
                if now >= deadline:
                    timed_out.append((title, cwd))
                    log(f"    [warn] {title} did not exit within {EXIT_TIMEOUT_SECONDS:.0f}s")
                    all_exited = False
                    break
                if not retried and now >= retry_at:
                    retried = True
                    if _send_exit(plan.hwnd, item):
                        log(
                            f"    {title} still running after "
                            f"{EXIT_RETRY_AFTER_SECONDS:.0f}s - resending /exit"
                        )
                time.sleep(EXIT_POLL_SECONDS)
            else:
                exited.append((title, cwd))

        should_close = all_exited and len(plan.targets) == plan.total_tabs
        if not should_close:
            left_open.append(plan.hwnd)
            if all_exited:
                log(f"    left window 0x{plan.hwnd:X} open - it has non-Claude tabs too")
            continue

        if win32.close_window(plan.hwnd):
            closed.append(plan.hwnd)
            log(f"    closed window 0x{plan.hwnd:X}")
        else:
            left_open.append(plan.hwnd)
            log(f"    [warn] could not close window 0x{plan.hwnd:X}")

    return {
        "exited": exited,
        "timed_out": timed_out,
        "closed": closed,
        "left_open": left_open,
    }
