"""Graceful shutdown: ask every live agent tab to quit, then close windows that
were entirely made up of sessions that exited cleanly.

How a tab is asked depends on which CLI is in it - `/exit` for Claude Code, an
interrupt for Codex. See agents.py; nothing else here differs by kind.

Mirrors capture.py's tab resolution (title -> live cwd) so `down` only ever
acts on tabs this tool already recognizes as agent sessions - the same scope as
capture and deploy. A window is only closed if every one of its tabs was a
recognized session and every one of them actually exited; a window with an
unrelated manual tab, or a holdout that didn't quit in time, is left open
rather than force-closed.

A tab launched by this package closes itself once its session ends
(deploy.CLOSE_TAB_IF_STARTED), which is what keeps a window with one holdout
from stranding the tabs that did exit - they used to sit at a bare shell prompt
until the whole window could be closed. It also means a window may be gone
before the close below runs; see is_wt_window.

A tab started by hand does not close itself, so close_dead_tabs invokes its
Close button. Both that and the window close are governed by close_emptied,
which `restart` turns off: it is about to put a session back into the very tab
and window that are being emptied.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import NamedTuple

from . import agents, discover, tabs, win32
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


class Target(NamedTuple):
    """One tab holding a live agent session, and how to reach it.

    A NamedTuple rather than a comment above a bare tuple. Every consumer used
    to unpack this positionally and throw away three of the four fields, which
    reads as `for _t, cwd, _p, _i in ...` and says nothing about what was
    discarded or why.
    """

    title: str
    cwd: str
    pid: int
    item: object  # the UIA TabItemControl; never touched outside tabs.py


class Sent(NamedTuple):
    """What one attempt at asking a session to quit actually did.

    Two facts, and they were briefly one return value where a count doubled as
    a failure signal. That is the same mistake as a flag named after half of
    what it governs: a stub returning None then meant "could not reach the
    tab", and a real zero meant "the session had already gone", and nothing
    could tell them apart.
    """

    reached: bool  # the tab was confirmed; False means nothing was typed
    keys: int      # how many quit keys actually went out


@dataclass
class WindowPlan:
    hwnd: int
    total_tabs: int
    targets: list[Target] = field(default_factory=list)


def plan_down(
    repos_root: str,
    *,
    live: dict[str, int] | None = None,
    title_map: dict[str, str] | None = None,
    only: list[str] | None = None,
    log=print,
) -> list[WindowPlan]:
    """Every WT window with at least one live agent tab, and which of its tabs
    those are. Read-only: selects, types, and closes nothing.

    ``live``/``title_map`` let a caller that already computed them (restart,
    right after its own capture) pass them straight through instead of
    paying for the psutil scan and the transcript-corpus walk again.

    ``only`` narrows the targets to those cwds — `restart <repo>` — and is
    compared through norm(), since it arrives from a typed argument while the
    cwds it matches came from psutil. It filters *targets*, never total_tabs:
    the untouched tabs still occupy their window, and execute_down relies on
    that count to decide the window is not empty enough to close.
    """
    if live is None:
        live = discover.live_sessions()
    if title_map is None:
        title_map = discover.title_to_cwd(discover.transcript_index())
    wanted = {norm(c) for c in only} if only is not None else None

    # One repo, one target, across the whole plan. Two tabs can carry the same
    # title and resolve to the same live session - seen for real, a window
    # holding two tabs both named `constructicon` behind one process. Undeduped
    # that types the quit keys twice for one session, and arms `restart`'s
    # marker twice with the second arming landing after the first was consumed.
    # build_layout dedups by cwd for exactly this reason.
    seen_cwds: set = set()

    plans: list[WindowPlan] = []
    for hwnd in win32.list_wt_windows():
        items = tabs.list_tab_items(hwnd)
        targets = []
        for title, item in items:
            # An unnameable tab still counts toward total_tabs below, so the
            # window is correctly treated as holding something we did not
            # start. It can never resolve, and an empty title would otherwise
            # make the basename guess in resolve_tab test repos_root itself.
            if not title:
                continue
            resolved = resolve_tab(title, title_map, live, repos_root)
            if resolved is None:
                continue
            cwd, _low_confidence = resolved
            if wanted is not None and norm(cwd) not in wanted:
                continue
            # Deliberately NOT counted against total_tabs: a duplicate tab still
            # occupies the window, and execute_down relies on that count to know
            # the window still holds something.
            if norm(cwd) in seen_cwds:
                log(f"    [warn] a second tab also resolves to {cwd} - acting "
                    "on the first only. Nothing connects a tab to a pid, so "
                    "which one this is cannot be established; if the wrong "
                    "session is left running, close the duplicate tab.")
                continue
            seen_cwds.add(norm(cwd))
            targets.append(Target(title, cwd, live[norm(cwd)], item))
        if targets:
            plans.append(WindowPlan(hwnd=hwnd, total_tabs=len(items), targets=targets))
    return plans


def _send_exit(hwnd: int, item, *, dismiss_overlay: bool = True, before_send=None,
               quit_keys=("/exit", "{Enter}"), pid: int | None = None) -> "Sent":
    """Foreground `item`'s tab and type its agent's quit keys into it.

    Returns a `Sent`. `reached` False means the tab could not be confirmed and
    nothing was typed, so the caller must not assume the session was asked to
    quit. `keys` is how many of the quit keys actually went out, and zero is a
    real answer: the target had already gone by the time its turn came.

    `quit_keys` defaults to Claude Code's, which is what every caller meant
    before a second agent kind existed. See tabs.send_quit_keystrokes for
    what dismiss_overlay controls.

    `pid` is what makes stopping possible. Aim is proved once, before the first
    key, and the keys are more than a second apart - ample time for the session
    to end and for something else to take the screen. Three conditions are
    re-asked before every key, and any of them failing means the rest of the
    sequence would land somewhere it was never aimed:

    the session is still running - Codex quits on a single interrupt, so the
    second one has nothing left to interrupt;

    the tab is still the selected one inside its terminal;

    that terminal still has OS focus - which the tab's own selection says
    nothing about. A notification, another application, or the user clicking
    away moves focus while the tab stays exactly as selected as it was, and
    SendKeys follows focus rather than the element anyone named.
    """
    if not tabs.select_tab(hwnd, item):
        return Sent(reached=False, keys=0)
    # After the tab is focused, before anything is typed. `restart` arms its
    # marker here so a tab that could not be foregrounded is never armed: it
    # will never read the marker, and one left on disk would fire on the
    # user's next manual exit instead. It is also disk I/O between the proof
    # and the typing, which is exactly why the proof is re-asked below rather
    # than trusted.
    if before_send is not None:
        before_send()

    def still_needed() -> bool:
        if pid is not None:
            import psutil

            if not psutil.pid_exists(pid):
                return False
        return tabs.tab_is_selected(item) and win32.is_foreground(hwnd)

    keys = tabs.send_quit_keystrokes(quit_keys, dismiss_overlay=dismiss_overlay,
                                     still_needed=still_needed)
    return Sent(reached=True, keys=keys or 0)


def _still_has_tabs(hwnd: int) -> bool:
    """Whether that window still holds any tab, asked right now.

    False when the window is gone, and false when it cannot be read: an
    unreadable window is not evidence that something is in it, and this only
    ever gates *not* closing.
    """
    try:
        return bool(tabs.list_tab_items(hwnd))
    except Exception:
        return False


def close_dead_tabs(plans, exited, still_open=None, log=print) -> int:
    """Close tabs whose session ended but whose shell did not close itself.

    A tab launched by reloaded closes itself once its session ends - see
    deploy.CLOSE_TAB_IF_STARTED. One started by hand sits in a plain
    interactive shell and does not, leaving a dead prompt in the strip where a
    session used to be. WM_CLOSE is the wrong tool for that: the window may
    hold other live sessions.

    Only tabs in `exited` are touched. A session that never exited is still
    running, and closing its tab would kill it - the one thing a graceful
    teardown must not do.

    `still_open` decides whether a tab is even there any more; a tab that
    already closed itself would otherwise mean invoking a button on a dead UIA
    element. Returns how many were closed, so the caller can subtract them
    from the window's remaining tab count.
    """
    from . import tabs as tabs_mod

    ended = {cwd for _title, cwd in exited}
    closed = 0
    for plan in plans:
        for t in plan.targets:
            if t.cwd not in ended:
                continue
            if still_open is not None and not still_open(t.item):
                continue
            if tabs_mod.close_tab(t.item):
                log(f"    closed the empty tab left by {t.title}")
                closed += 1
    return closed


def execute_down(
    plans: list[WindowPlan],
    log=print,
    *,
    close_emptied: bool = True,
    before_exit=None,
    kinds: dict | None = None,
) -> dict:
    """Send /exit to every planned tab, wait for each to actually end, then
    close each window whose tabs were all Claude sessions and all exited.

    ``close_emptied=False`` skips every part of that clean-up, for
    `restart <repo>`: a restarted session comes back inside the very shell that
    is exiting, so both its tab and its window have to outlive the exit. A
    single-session window otherwise satisfies ``len(targets) == total_tabs``
    and gets WM_CLOSE'd out from under the tab that is about to reappear in it
    - and the tab itself gets closed by close_dead_tabs before anything can be
    typed into it.

    One flag, not two, because it is one fact: whether a session is coming back
    into these tabs. Naming it after only the window is what let the tab
    clean-up run during a restart unnoticed - the parameter said windows, so
    nobody looked for what else it governed.

    ``before_exit(cwd)`` runs once per target, after its tab is focused and
    before /exit is typed into it. Targets are handled serially with a wait of
    up to EXIT_TIMEOUT_SECONDS each, so work done here for target N would
    otherwise have to stay valid across every earlier target's wait — which is
    how `restart`'s marker TTL used to be a function of batch size rather than
    of one tab's exit.
    """
    exited: list[tuple[str, str]] = []
    timed_out: list[tuple[str, str]] = []
    closed: list[int] = []
    left_open: list[int] = []

    for plan in plans:
        all_exited = True
        for title, cwd, pid, item in plan.targets:
            arm = None if before_exit is None else (lambda c=cwd: before_exit(c))
            # How this session is asked to quit depends on which CLI it is.
            # An unknown cwd is Claude Code, which is what every teardown
            # meant before a second kind existed.
            agent = agents.for_kind((kinds or {}).get(norm(cwd)))
            quit_keys = agent.quit_keys
            sent = _send_exit(plan.hwnd, item, before_send=arm,
                              quit_keys=quit_keys, pid=pid)
            if not sent.reached:
                log(
                    f"    [warn] could not bring window 0x{plan.hwnd:X} to the "
                    f"foreground - skipping {title!r} (exit it manually)"
                )
                timed_out.append((title, cwd))
                all_exited = False
                continue

            if sent.keys:
                log(f"    sent {agent.quit_label} -> {title}  [{cwd}]")
            else:
                # Targets are handled one at a time with a wait on each, so a
                # session can end on its own well before its turn. Saying
                # "sent /exit" here would describe something that did not
                # happen, in the log a user reads to decide whether to go
                # looking.
                log(f"    {title} had already gone  [{cwd}]")

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
                    # dismiss_overlay=False: Escape would cancel rather than
                    # answer Claude Code's background-agent /exit
                    # confirmation - the exact thing this resend exists to
                    # get past. See tabs.send_exit_keystrokes.
                    if _send_exit(plan.hwnd, item, dismiss_overlay=False,
                                  quit_keys=quit_keys, pid=pid).reached:
                        # A zero here is the session ending mid-resend, which
                        # the enclosing loop is about to notice anyway.
                        log(
                            f"    {title} still running after "
                            f"{EXIT_RETRY_AFTER_SECONDS:.0f}s - resending {agent.quit_label}"
                        )
                    else:
                        log(
                            f"    [warn] could not bring window 0x{plan.hwnd:X} "
                            f"to the foreground to resend {agent.quit_label} -> {title!r}"
                        )
                time.sleep(EXIT_POLL_SECONDS)
            else:
                exited.append((title, cwd))

        # A tab this package launched closes itself once its session ends. One
        # started by hand does not - its shell is a plain interactive prompt -
        # so `down` used to leave a dead tab where a session had been. Closed
        # through the tab's own button rather than by typing at it, so it
        # cannot reach a neighbour. `still_open` is left None: close_tab
        # already reports False for an element that has gone away, which makes
        # a separate liveness probe per tab pure cost.
        if close_emptied:
            close_dead_tabs([plan], exited, still_open=None, log=log)

        should_close = (
            close_emptied and all_exited and len(plan.targets) == plan.total_tabs
        )
        if should_close and _still_has_tabs(plan.hwnd):
            # total_tabs was counted when the plan was built, and teardown can
            # spend EXIT_TIMEOUT_SECONDS on every target since. A tab opened in
            # that window in the meantime - by the user, by `reloaded open`, by
            # a concurrent `up` - is in neither `targets` nor `total_tabs`, and
            # WM_CLOSE would take it and its session with the window. Every tab
            # this teardown accounted for has closed itself or been closed by
            # close_dead_tabs above, so anything still there is something else.
            should_close = False
            log(f"    [warn] window 0x{plan.hwnd:X} gained a tab during "
                "teardown - leaving it open rather than closing over it")

        if not should_close:
            left_open.append(plan.hwnd)
            if all_exited and close_emptied and len(plan.targets) != plan.total_tabs:
                log(f"    left window 0x{plan.hwnd:X} open - it has tabs this "
                    "teardown did not touch")
            continue

        if win32.close_window(plan.hwnd):
            closed.append(plan.hwnd)
            log(f"    closed window 0x{plan.hwnd:X}")
        elif not win32.is_wt_window(plan.hwnd):
            # Its tabs closed themselves as their sessions ended (see
            # deploy.CLOSE_TAB_IF_STARTED) and the last one took the window
            # with it, so there was nothing left for WM_CLOSE to reach. Racing
            # us to the same outcome is success, not a failure to report.
            closed.append(plan.hwnd)
            log(f"    window 0x{plan.hwnd:X} closed itself")
        else:
            left_open.append(plan.hwnd)
            log(f"    [warn] could not close window 0x{plan.hwnd:X}")

    return {
        "exited": exited,
        "timed_out": timed_out,
        "closed": closed,
        "left_open": left_open,
    }
