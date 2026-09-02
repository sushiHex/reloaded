"""Reloaded CLI."""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from . import agents as agents_mod
from . import capture as capture_mod
from . import deploy as deploy_mod
from . import discover as discover_mod
from . import editor as editor_mod
from . import layout as layout_mod
from . import readiness as readiness_mod
from . import tabs as tabs_mod
from . import tasks as tasks_mod
from . import teardown as teardown_mod
from . import transcript as transcript_mod
from . import win32 as win32_mod
from .paths import (
    layout_path,
    log_path,
    norm,
    relaunch_script_path,
    resolve_repo,
    restart_marker,
    restart_marker_dir,
)

DEFAULT_REPOS_ROOT = os.path.join(os.path.expanduser("~"), "repos")


def _configure_stdout() -> None:
    # Windows consoles default to a legacy codepage that cannot encode the
    # glyphs in session titles.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _report_uia_unavailable(exc: tabs_mod.UIAUnavailable) -> int:
    print(f"[reloaded] {exc}")
    print(tabs_mod.install_hint())
    return 1


def _print_window_tree(lo) -> None:
    for w in lo.windows:
        print(f"  {layout_mod.window_header(w)}")
        for t in w.tabs:
            print(f"    - {t.title}{layout_mod.tab_flags(t)}")


def _print_capture_summary(lo, path) -> None:
    total = sum(len(w.tabs) for w in lo.windows)
    print(f"Captured {len(lo.windows)} window(s), {total} session(s) -> {path}")
    _print_window_tree(lo)


def _print_down_result(result: dict, *, timed_out_note: str = "") -> None:
    print(
        f"Exited {len(result['exited'])} session(s); "
        f"closed {len(result['closed'])} window(s)."
    )
    if result["timed_out"]:
        print(f"[warn] {len(result['timed_out'])} session(s) did not exit in time{timed_out_note}:")
        for title, cwd in result["timed_out"]:
            print(f"    - {title}  [{cwd}]")
    if result["left_open"]:
        print(f"{len(result['left_open'])} window(s) left open (see warnings above).")


def _capture_layout(args):
    """Load the previous layout (if any) and capture the current live
    arrangement. Returns (Layout, path, live, title_map) - the last two are
    the raw discovery snapshot capture_live used, so a caller doing more
    work against the same live state (restart's teardown plan) can reuse it
    instead of re-scanning. Raises UIAUnavailable; callers report it via
    _report_uia_unavailable.
    """
    path = layout_path(args.layout)
    # Load the existing layout first so pinned tabs survive the reconcile.
    previous = layout_mod.load(path) if path.exists() else None
    live = discover_mod.live_sessions()
    title_map = discover_mod.title_to_cwd(discover_mod.transcript_index())
    lo = capture_mod.capture_live(args.repos_root, previous, live=live, title_map=title_map)
    return lo, path, live, title_map


def cmd_capture(args) -> int:
    try:
        lo, path, live, _title_map = _capture_layout(args)
    except tabs_mod.UIAUnavailable as exc:
        return _report_uia_unavailable(exc)

    # capture runs every 5 minutes forever via the reconcile task — cheap,
    # regular hook to keep stale .torn backups from accumulating on a machine
    # that crashes periodically.
    pruned = transcript_mod.prune_torn_backups(discover_mod.default_projects_dir())
    if pruned:
        print(f"[reloaded] pruned {len(pruned)} stale .torn backup(s)")

    if not lo.windows:
        print("No Claude Code tabs found — nothing captured, existing layout untouched.")
        return 1

    unresolved = _unresolved_live_repos(lo, live)
    if unresolved:
        print(
            f"[reloaded] {len(unresolved)} running session(s) did not match any tab: "
            + ", ".join(unresolved[:6])
        )
        print(
            "    Usually a title this build does not recognise — if Claude Code "
            "changed its spinner, add the range to discover._SPINNER_RANGES."
        )

    shrink = _capture_shrinkage(lo, path, live)
    if shrink and not getattr(args, "force", False):
        print(f"[reloaded] refusing to overwrite the layout: {shrink}")
        print(
            "    Those sessions are still running, so this capture failed to "
            "read them (a UIA timeout, or a window still starting) rather than "
            "them having gone away. Saving now would drop them from `up`."
        )
        print("    Re-run once they read cleanly, or pass --force to accept it.")
        return 1

    layout_mod.save(lo, path)
    _print_capture_summary(lo, path)
    return 0


def _unresolved_live_repos(fresh, live) -> list[str]:
    """Running sessions that no captured tab accounts for.

    strip_glyph only removes spinner frames it recognises, so an unfamiliar one
    stays attached, the title matches neither the transcript map nor a repo
    basename, and the tab is dropped. That is the safe failure - far better
    than stripping any symbol and acting on someone else's tab - but it is
    invisible, which is how the previous stale frame list went unnoticed for
    long enough to overwrite a good layout. Naming the sessions turns a silent
    drop into something a person can act on.
    """
    captured = {norm(t.cwd) for w in fresh.windows for t in w.tabs}
    return sorted(
        os.path.basename(cwd.rstrip("\\/")) or cwd
        for cwd in live
        if norm(cwd) not in captured
    )


def _capture_shrinkage(fresh, path, live) -> str:
    """Describe sessions the saved layout has that this capture lost *while
    they were still running*. Returns "" when there are none.

    Only total loss was guarded before, but partial loss is the common case and
    the damaging one: `tabs.list_tab_items` degrades a per-window UIA timeout to
    an empty list, `build_layout` drops that window entirely, and the 5-minute
    reconcile writes the result over a layout that was correct. Nothing says so
    on screen - that warning goes to a stderr with no destination under pythonw.

    Keyed on liveness, not on counts. A session the user deliberately closed is
    *supposed* to leave the layout, and a plain size comparison would refuse
    that shrink forever, wedging the reconcile task against a layout that can
    only ever grow. A repo that is still in `live` but absent from the capture
    is the opposite: it did not go anywhere, so the capture failed to see it.
    """
    try:
        previous = layout_mod.load(path)
    except Exception:
        return ""
    if previous is None or not previous.windows:
        return ""

    new_cwds = {norm(t.cwd) for w in fresh.windows for t in w.tabs}
    lost = sorted(
        {
            t.cwd
            for w in previous.windows
            for t in w.tabs
            if norm(t.cwd) not in new_cwds and norm(t.cwd) in live
        }
    )
    if not lost:
        return ""

    names = ", ".join(os.path.basename(c.rstrip("\\/")) for c in lost[:6])
    if len(lost) > 6:
        names += f" (+{len(lost) - 6} more)"
    return f"{len(lost)} running session(s) missing from this capture — {names}"


def _log(message: str) -> None:
    import datetime

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp}  {message}"
    print(line)
    try:
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _transcript_sizes(index) -> dict[str, int]:
    return {norm(info.cwd): info.size for info in index.values()}


def _reporter(args):
    """Where this run's output goes, decided once instead of at every message.

    An unattended run is started by Task Scheduler under pythonw, where stdout
    has no destination at all - so anything printed there is simply lost, and
    choosing between `print` and `_log` is a correctness question rather than a
    formatting one. That choice was being made four separate times in one
    function, and the last of them left its detail lines on bare `print`: which
    repos failed to start, and the explanation of Codex's trust prompt, were
    invisible in exactly the run nobody is watching.

    `blank` asks for a separating line, which only the console gets. A
    timestamped empty line is litter in a log file.
    """
    def say(message: str, *, blank: bool = False) -> None:
        if args.unattended:
            _log(message)
            return
        if blank:
            print()
        print(message)

    return say


def _deploy_layout(lo, args) -> int:
    """Deploy an already-loaded Layout: plan, print, repair torn transcripts,
    launch, and place. Shared by `up` (loads the saved layout from disk) and
    `restart` (deploys a layout it just captured in the same run)."""
    say = _reporter(args)
    live = discover_mod.live_sessions()
    monitors = win32_mod.list_monitors()
    # Built once and reused by both the size guard and the torn-tail repair —
    # this scan walks every transcript on the machine.
    index = discover_mod.transcript_index(need_title=False)
    plan = deploy_mod.plan_deploy(lo, live, monitors, _transcript_sizes(index))

    if not plan:
        total = sum(len(w.tabs) for w in lo.windows)
        say(f"All {total} session(s) already running — nothing to do.")
        return 0

    for entry in plan:
        x, y, w, h = entry.rect
        say(f"window {entry.id}  ({x},{y} {w}x{h}, {entry.state})")
        for tab, delay in zip(entry.tabs, entry.delays):
            say(f"    +{delay:>3}s  {tab.title}  [{tab.cwd}]")
        for tab in entry.skipped:
            say(f"      --   {tab.title}  (already running)")
        for tab in entry.missing:
            say(f"      --   {tab.title}  [warn] directory not found: {tab.cwd}")
        if args.dry_run:
            print("    argv: " + " ".join(repr(a) for a in entry.argv))

    if args.dry_run:
        print("\nDry run — nothing launched.")
        return 0

    # Repair before launching; reporting lives here, where the rest of the
    # user-facing output does.
    for transcript_path, outcome in transcript_mod.repair_all(
        deploy_mod.transcripts_to_repair(plan, index)
    ):
        if isinstance(outcome, OSError):
            say(f"[warn] could not repair {transcript_path}: {outcome}")
        elif outcome:
            say(f"[reloaded] repaired torn transcript: {transcript_path}")

    results = deploy_mod.execute(plan)
    failures = 0
    for r in results:
        if r.hwnd is None:
            say(f"[warn] window {r.window_id}: could not identify the new "
                "window; geometry not applied")
            failures += 1
        elif not r.placed:
            say(f"[warn] window {r.window_id}: launched but geometry could "
                "not be applied")
            failures += 1
    launched = sum(len(e.tabs) for e in plan)
    say(f"Launched {launched} session(s) in {len(plan)} window(s).", blank=True)

    silent = _never_started(lo, discover_mod.live_sessions())
    if silent:
        say(f"[reloaded] {len(silent)} tab(s) opened but no session started:",
            blank=True)
        for cwd in silent[:6]:
            say(f"    - {cwd}")
        say("    Codex asks whether to trust a directory it has not seen")
        say("    before it starts. Answer it in the tab - this tool will not")
        say("    answer a security question on your behalf.")

    return 1 if failures else 0


def _never_started(lo, live) -> list[str]:
    """Tabs the deploy launched that have no live session behind them.

    Usually Codex asking whether to trust a directory it has not seen: the tab
    opens, the prompt waits, and no session ever appears. Reported rather than
    answered - it is a security question, and a launcher has no business
    clicking through one for the user.

    Not Codex-specific. A session that failed to start for any reason is worth
    naming, because the tab looks perfectly healthy either way.
    """
    return [t.cwd for w in lo.windows for t in w.tabs if norm(t.cwd) not in live]


def cmd_up(args) -> int:
    path = layout_path(args.layout)
    if not path.exists():
        print(f"No layout at {path}. Run `reloaded capture` first.")
        return 1

    lo = layout_mod.load(path)

    if args.unattended:
        ready, reason = readiness_mod.wait_for_ready(lo)
        _log(f"readiness: {reason}")
        if not ready:
            _log("proceeding anyway — windows will be clamped onto available monitors")

        # One-shot, not part of the retry loop above (an old WT will not
        # become new by waiting) — only worth the ~3s PowerShell round trip
        # for the unattended path, where nobody is watching it happen live.
        wt_ok, wt_detail = readiness_mod.check_wt_version()
        if not wt_ok:
            _log(f"[warn] {wt_detail} — window placement may not work correctly")

    return _deploy_layout(lo, args)


# How long to wait for a restarted session to reappear before concluding its
# tab closed instead of relaunching. live_sessions() matches on process name
# and cwd, so a session counts the moment claude.exe exists - well before it
# has finished reading a large transcript. This is a wait for a spawn, not for
# a startup.
RELAUNCH_WAIT_SECONDS = 20.0
# A full psutil.process_iter() sweep per tick, which teardown's own polling
# loop avoids precisely because it is "real, avoidable cost". It cannot be
# avoided here - the pid being waited for does not exist yet, so there is
# nothing cheaper to poll - so the tick is kept coarse instead. Nobody
# restarting a session needs the answer a quarter-second sooner.
RELAUNCH_POLL_SECONDS = 1.0


@dataclass(frozen=True)
class _Restarting:
    """One session `restart <repo>` is putting back.

    Every field is read while that session is still running, because once
    teardown has finished there is nothing left to read: the pid is gone,
    psutil raises, and each lookup quietly falls back to a default. For Codex
    that default disables the sandbox. Snapshotting is not an optimisation
    here, it is the only correct order to ask the questions in.

    One record rather than the four dictionaries keyed by cwd this replaced.
    Those cost nine `.get(norm(cwd), default)` lookups, wrote the same two
    defaults twice each, and let three call sites hand a Codex tab Claude's
    launch command by forgetting an optional argument.
    """

    # Where to type: the window, and the tab element inside it.
    hwnd: int
    item: object
    # What was running there.
    cwd: str
    pid: int
    # How it was STARTED — by hand, or by this package. Decides whether the
    # restart-marker machinery applies at all. A different question from
    # `agent`, and conflating the two sends a Codex tab `/exit`.
    launcher: str
    # WHICH CLI it is. Decides how it is asked to quit, and what comes back.
    agent: str
    # The exact command line it was running. Empty means the kind's default,
    # which is the outcome this whole record exists to avoid arriving at by
    # accident.
    command: str
    size_bytes: int

    @property
    def key(self) -> str:
        """Its normalized cwd, which is how every map around it is keyed."""
        return norm(self.cwd)


def _launch_single_tab(s: _Restarting) -> None:
    """Open `s` as a new tab via `wt -w 0`.

    Reached only as `restart <repo>`'s fallback, when the tab closed instead of
    relaunching in place. Placement is genuinely best-effort and the caller
    must not promise otherwise: `-w 0` means Windows Terminal's *most recently
    used* window, which is usually the one teardown just brought forward, but
    is not the same thing as a captured HWND. If the original window closed
    with its last tab, the session lands in whichever WT window is MRU, or in a
    brand-new one if none exists. Either way it arrives at the end of a tab
    strip rather than in its old slot - restoring the slot is what the in-place
    restart is for, and this path exists because that did not happen.
    """
    import subprocess

    argv = deploy_mod.wt_argv_single_tab(s.cwd, s.size_bytes, s.agent, s.command)
    subprocess.Popen(argv, close_fds=True)


def _type_relaunch(s: _Restarting) -> bool:
    """Put the launcher into the idle shell a hand-launched session left behind.

    That shell is a plain interactive prompt with no session in it and nothing
    to close it, so this is the only way its tab gets a session back in the
    slot it already occupies. The tab is upgraded in the process: from the next
    restart on it behaves like any reloaded-launched one.

    Written to a file and run by one short line rather than typed out, because
    SendKeys reads `{` and `(` as syntax - escaped, the launcher is 838
    keystrokes, and they did not all arrive when tried.
    """
    import time

    path = relaunch_script_path(s.cwd)
    path.write_text(
        deploy_mod.relaunch_script(s.cwd, s.size_bytes, s.agent, s.command),
        encoding="utf-8")

    import uiautomation as auto

    # The session has only just ended, so its shell needs a moment to finish
    # returning to a prompt. That wait happens BEFORE the tab is claimed, not
    # after: confirming the tab and then sleeping a second leaves a second in
    # which focus can move, and typing at an unconfirmed target is exactly how
    # a stray /exit reaches a live session. Nothing goes between select_tab
    # returning True and the keystrokes.
    time.sleep(1.0)
    if not tabs_mod.select_tab(s.hwnd, s.item):
        return False
    auto.SendKeys("& '%s'{Enter}" % path)
    return True


def _live_tab_count(hwnd: int) -> int:
    """How many tabs that window has now, or 0 if it is gone."""
    try:
        return len(tabs_mod.list_tab_items(hwnd))
    except Exception:
        return 0


def _wait_for_session(cwd: str, *, not_pid: int | None = None) -> int | None:
    """Poll for a live session at `cwd`, returning its pid or None on timeout.

    ``not_pid`` is the pid this restart tried to end, and a match for it is not
    an answer. Accepting any pid meant a session that ignored /exit - still
    running, still the same process - satisfied the poll on the first tick and
    got reported as restarted. That was not a narrow reaping race: it held for
    as long as the old process stayed alive, which for a timed-out exit is
    indefinitely.
    """
    import time

    key = norm(cwd)
    deadline = time.time() + RELAUNCH_WAIT_SECONDS
    while True:
        pid = discover_mod.live_sessions().get(key)
        if pid is not None and pid != not_pid:
            return pid
        if time.time() >= deadline:
            return None
        time.sleep(RELAUNCH_POLL_SECONDS)


def _sweep_stale_markers() -> None:
    """Drop restart markers left by an attempt that never reached its tab.

    Markers are deliberately never deleted by the writer: the shell that reads
    one does so moments after its session exits, and deleting eagerly would be
    a race we lose. deploy's TTL already makes an old marker inert, so this is
    housekeeping, not correctness.
    """
    import time

    cutoff = time.time() - deploy_mod.RESTART_MARKER_TTL_SECONDS
    try:
        stale = [m for m in restart_marker_dir().glob("*.marker")
                 if m.stat().st_mtime < cutoff]
    except OSError:
        return
    for m in stale:
        try:
            m.unlink()
        except OSError:
            pass


def cmd_restart_one(args, repos: list[str]) -> int:
    """Restart only the named sessions, in place, without closing their tabs."""
    cwds = [resolve_repo(r, args.repos_root) for r in repos]
    live = discover_mod.live_sessions()

    refusal = _nothing_running_there(cwds, live)
    if refusal:
        print(refusal)
        return 1

    try:
        plans = teardown_mod.plan_down(args.repos_root, live=live, only=cwds)
    except tabs_mod.UIAUnavailable as exc:
        return _report_uia_unavailable(exc)

    refusal = _no_tab_to_type_into(cwds, plans)
    if refusal:
        print(refusal)
        return 1

    if args.dry_run:
        _preview_restart(plans)
        return 0

    _sweep_stale_markers()
    tabs_before = {plan.hwnd: plan.total_tabs for plan in plans}
    sessions = _snapshot_restarts(plans, args)
    by_key = {s.key: s for s in sessions}

    def arm(cwd):
        s = by_key.get(norm(cwd))
        if s is not None and s.launcher == discover_mod.RELOADED:
            restart_marker(cwd).write_text("restart", encoding="utf-8")

    print("Exiting the named sessions — this will steal keyboard focus...")
    # Armed per tab, as its turn comes, rather than all up front: targets are
    # exited serially with a wait each, so a marker written now for the last
    # target would spend every earlier target's wait ageing toward its TTL.
    down = teardown_mod.execute_down(
        plans, close_emptied=False, before_exit=arm,
        kinds={s.key: s.agent for s in sessions},
    )
    _print_down_result(down, timed_out_note=" — not restarted")

    stuck = {norm(cwd) for _t, cwd in down["timed_out"]}
    print("\nWaiting for them to come back...")
    failed = False
    for s in sessions:
        if s.key in stuck:
            came_back = _report_never_exited(s)
        elif s.launcher == discover_mod.HAND:
            came_back = _relaunch_by_typing(s)
        else:
            came_back = _await_relaunch(s, tabs_before)
        failed = failed or not came_back
    return 1 if failed else 0


def _nothing_running_there(cwds, live) -> str:
    """Why this batch cannot start, or "" if every named repo has a session."""
    missing = [c for c in cwds if norm(c) not in live]
    if not missing:
        return ""
    return "\n".join([f"Not running: {c}" for c in missing]
                     + ["", "`reloaded status` lists the live sessions."])


def _no_tab_to_type_into(cwds, plans) -> str:
    """Why this batch cannot proceed, or "" if every repo resolved to a tab.

    Every requested repo must have a tab this tool can drive, or the batch is
    refused whole. Acting on the resolvable subset used to restart some repos,
    skip the rest without a word, and still return 0 - leaving the skipped
    repos' markers armed on disk.
    """
    planned = {norm(t.cwd) for t in _targets(plans)}
    unresolved = [c for c in cwds if norm(c) not in planned]
    if not unresolved:
        return ""
    return "\n".join(
        [f"No Windows Terminal tab found for {c}" for c in unresolved]
        + ["",
           "    Running, but no tab this tool can drive — started outside "
           "Windows Terminal, or UIA cannot read its title.",
           "    Nothing was restarted; re-run without it to restart the rest."]
    )


def _preview_restart(plans) -> None:
    """`--dry-run`: what would happen, and the one consequence worth previewing."""
    for t in _targets(plans):
        print(f"Would restart {t.cwd} (pid {t.pid}) in place, leaving its window open.")
        if discover_mod.launcher_kind(t.pid) == discover_mod.HAND:
            # Restarting a hand-launched session rewrites what its tab runs,
            # permanently. A preview that reads the same for both kinds hides
            # that until after the fact.
            print("    Started by hand — its tab would be upgraded to the "
                  "reloaded launcher,")
            print("    which self-closes when the session exits.")
    print("\nDry run — no marker written, nothing sent.")


def _snapshot_restarts(plans, args) -> list[_Restarting]:
    """Everything the relaunch will need, read while the sessions are alive.

    The single place these facts are gathered, and it runs before anything is
    asked to quit. See _Restarting for why the order is load-bearing rather
    than merely tidy.
    """
    sizes = _transcript_sizes(discover_mod.transcript_index(need_title=False))
    live_kinds = discover_mod.live_agents()
    saved = _recorded_launches(args)

    sessions = []
    for plan in plans:
        for t in plan.targets:
            key = norm(t.cwd)
            saved_agent, saved_command = saved.get(
                key, (agents_mod.DEFAULT_KIND, ""))
            # Both from the same process, in one read. See session_launch for
            # what taking them from two sweeps costs.
            agent, command = discover_mod.session_launch(t.pid)
            agent = agent or live_kinds.get(key) or saved_agent
            if not command and agent == saved_agent:
                # The layout lends its command line only to the agent it
                # recorded it for. A repo that has changed CLI since the last
                # capture would otherwise hand claude's flags to a Codex tab,
                # and launcher_command trusts the command over the agent.
                command = saved_command
            sessions.append(_Restarting(
                hwnd=plan.hwnd,
                item=t.item,
                cwd=t.cwd,
                pid=t.pid,
                launcher=discover_mod.launcher_kind(t.pid),
                agent=agent,
                command=command,
                size_bytes=sizes.get(key, 0),
            ))
    return sessions


def _report_never_exited(s: _Restarting) -> bool:
    """It did not quit inside the timeout, so nothing was restarted.

    Its marker is deliberately left armed. "Timed out" only means it had not
    exited within EXIT_TIMEOUT_SECONDS, not that it never will - and if the
    marker is gone when it does, the shell breaks out of the restart loop and
    CLOSE_TAB_IF_STARTED closes the tab. Disarming here would destroy the very
    session this command was asked to bring back. Left armed, the worst case is
    a late restart, which is what was requested, bounded by the TTL.
    """
    print(f"    {s.cwd} never exited — still running as pid {s.pid}, not restarted")
    # Only a reloaded-launched session has a marker at all. Saying otherwise
    # promises a delayed restart nothing can perform - seen for real against a
    # hand-launched session, which is armed with nothing by design.
    if s.launcher == discover_mod.RELOADED:
        mins = deploy_mod.RESTART_MARKER_TTL_SECONDS // 60
        print(f"        its restart is still armed: it will restart if it "
              f"exits within {mins} min, and be ignored after that")
    return False


def _relaunch_by_typing(s: _Restarting) -> bool:
    """Hand-launched: its shell is idle at a prompt in a tab nothing will close.

    No marker machinery applies, so the launcher is typed into that waiting
    shell instead. The tab is upgraded by it and behaves like a
    reloaded-launched one from here on.
    """
    print(f"    {s.cwd} was started by hand — typing the launcher into its tab")
    if not _type_relaunch(s):
        print(f"    [warn] could not reach that tab — start {s.cwd} by hand")
        return False
    pid = _wait_for_session(s.cwd, not_pid=s.pid)
    if pid is None:
        print(f"    [warn] {s.cwd} did not come back — start it by hand")
        return False
    print(f"    restarted in place -> {s.cwd} (pid {pid}); its tab is "
          "now upgraded and will self-close on exit")
    return True


def _await_relaunch(s: _Restarting, tabs_before: dict[int, int]) -> bool:
    """Reloaded-launched: its own shell reads the marker and relaunches in place."""
    pid = _wait_for_session(s.cwd, not_pid=s.pid)
    if pid is not None:
        print(f"    restarted in place -> {s.cwd} (pid {pid})")
        return True

    # Nothing live at that cwd. Either the tab closed (no restart loop, in a
    # session started before this existed) or the relaunch failed fast enough
    # that the startup guard kept the tab open to show the error. Those need
    # opposite responses, so ask the window rather than inferring from the
    # absent pid: a second tab beside an error message is the worst of both.
    if _live_tab_count(s.hwnd) >= tabs_before.get(s.hwnd, 0):
        print(f"    [warn] {s.cwd} did not come back, but still has its tab — "
              "read the error in that window rather than opening another")
        return False
    return _reopen_in_a_new_tab(s)


def _reopen_in_a_new_tab(s: _Restarting) -> bool:
    """Its tab is gone, so there is no slot left to restart into.

    The tab going means the shell that could have read this marker went with
    it, which is what makes deleting safe here and nowhere else - and
    necessary, because the replacement opened below DOES have the restart loop.
    Left armed, the marker outlives the tab that ignored it and fires on the
    user's next /exit in the new session, restarting one they meant to close.
    """
    print(f"    {s.cwd} did not relaunch in place — its tab closed; reopening it")
    try:
        restart_marker(s.cwd).unlink(missing_ok=True)
    except OSError as exc:
        # Swallowing this used to be safe-looking and was not. The tab opened
        # below DOES carry the restart loop, so a marker that survives here
        # outlives the tab that ignored it and fires on the user's next /exit,
        # restarting a session they meant to close. The docstring above says
        # deleting it is necessary; a failed delete has to be treated as
        # exactly that, not as a shrug.
        print(f"    [warn] could not clear {s.cwd}'s restart marker: {exc}")
        print("        Not reopening it here — the new tab would inherit that "
              "marker and restart on your next /exit.")
        repo = os.path.basename(s.cwd.rstrip("\\/")) or s.cwd
        print(f"        Delete {restart_marker(s.cwd)} and run "
              f"`reloaded open {repo}`.")
        return False
    _launch_single_tab(s)
    if _wait_for_session(s.cwd) is None:
        print(f"    [warn] {s.cwd} did not come back — start it by hand")
        return False
    return True


# A marker is deleted in exactly one place above: after the tab that could have
# read it is confirmed gone. Everywhere else, deleting means guessing that a
# live shell will not read it, and that guess is unrecoverable when wrong - the
# shell leaves the restart loop, CLOSE_TAB_IF_STARTED fires, and the session
# the restart was meant to bring back is destroyed instead. A timed-out target
# is therefore left armed, because "did not exit in 20s" is not "will never
# exit". The rest of staleness is handled where being wrong costs nothing:
# deploy's TTL makes an old marker inert, and _sweep_stale_markers clears the
# directory on the next run.


def _agent_kinds(plans) -> dict[str, str]:
    """Which CLI each planned target is, asked of that target's own process.

    `live_agents()` answers the same question by sweeping every process on the
    machine and keying the result by directory. That is a second enumeration,
    taken at a different moment, and a target it misses - because the sweep
    raced the process, or because two sessions share a directory and one
    overwrote the other - falls back to Claude Code. A Codex tab then gets
    `/exit` typed into it.

    A plan already holds each target's pid. Asking it directly is both cheaper
    and incapable of disagreeing with itself. The sweep remains the fallback
    for a pid that will not answer, and the kind's own default after that.
    """
    kinds: dict[str, str] = {}
    sweep = None
    for t in _targets(plans):
        kind, _command = discover_mod.session_launch(t.pid)
        if not kind:
            if sweep is None:
                sweep = discover_mod.live_agents()
            kind = sweep.get(norm(t.cwd), agents_mod.DEFAULT_KIND)
        kinds[norm(t.cwd)] = kind
    return kinds


def _targets(plans):
    for plan in plans:
        yield from plan.targets


def cmd_restart_self(args) -> int:
    """Arm a restart for the session this command is running inside.

    Every other restart path drives a session from outside it: select its tab,
    type the quit keys, wait for it to come back. None of that works on
    yourself. The command would be typing into its own tab and would then die
    with the session it just ended, before it could watch for the return or
    report anything - and whether a quit keystroke even lands while the session
    is busy running that very command is not something this package knows.

    So it does not try. The tab's shell already runs its agent inside a loop
    that checks for a marker each time the agent exits, and the honest shape of
    "restart me" is to leave that marker and get out of the way. The session
    ends when you end it, cleanly, through its own UI.
    """
    repos = list(getattr(args, "repos", None) or [])
    if repos:
        # `--self` is checked before `repos`, so naming both would arm the
        # calling session and silently ignore what was named - the caller
        # asking for one restart and getting a different one, reported as
        # success. Refuse rather than pick.
        print(f"`--self` restarts the session you are calling from, so it "
              f"cannot also take a repo name ({', '.join(repos)}).")
        print("    Drop `--self` to restart those, or drop them to restart this one.")
        return 1

    found = discover_mod.owning_session()
    if found is None:
        print("Not running inside an agent session.")
        print("    `restart --self` arms the session it is called from, so it "
              "has to be called from inside one —")
        print("    a Bash tool call in Claude Code, or a shell command in "
              "Codex. From an ordinary terminal,")
        print("    name the repo instead: `reloaded restart <repo>`.")
        return 1

    pid, cwd, kind = found
    if not cwd:
        print(f"Found the session (pid {pid}) but cannot read its directory, "
              "so its marker cannot be addressed.")
        print(f"    Restart it from another session: `reloaded restart <repo>`.")
        return 1

    marker = restart_marker(cwd)
    label = agents_mod.for_kind(kind).quit_label

    if getattr(args, "cancel", False):
        # Safe here and almost nowhere else: the session asking is still
        # running, so nothing is about to read this marker. Every other place
        # that deletes one is guessing about a shell it cannot see.
        existed = marker.exists()
        try:
            marker.unlink(missing_ok=True)
        except OSError as exc:
            print(f"[warn] could not remove {marker}: {exc}")
            return 1
        print(f"Disarmed {cwd}." if existed
              else f"Nothing was armed for {cwd}.")
        return 0

    if discover_mod.launcher_kind(pid) == discover_mod.HAND:
        print(f"{cwd} was started by hand, so nothing will read a marker.")
        print("    Its shell is a plain prompt with no restart loop in it — "
              "arming would leave a file on disk")
        print("    that no one collects. Restart it once from another session "
              "with `reloaded restart <repo>`,")
        print("    which types the launcher into its tab and upgrades it; "
              "after that this works.")
        return 1

    if args.dry_run:
        print(f"Would arm {cwd} (pid {pid}, {kind}) by writing {marker}.")
        print("\nDry run — nothing written.")
        return 0

    try:
        marker.write_text("restart", encoding="utf-8")
    except OSError as exc:
        print(f"[warn] could not arm {cwd}: {exc}")
        return 1

    ttl = deploy_mod.RESTART_MARKER_TTL_SECONDS
    repo = os.path.basename(cwd.rstrip("\\/")) or cwd
    print(f"Armed {cwd} (pid {pid}, {kind}).")
    print(f"\n    Quit with {label} in the next {ttl // 60} minutes and its "
          "tab relaunches it in the same slot.")
    # Naming the consequence rather than saying the marker is "ignored". It is
    # ignored, and the tab still closes - the loop breaks and the shell exits
    # like any ordinary quit. A user who read "ignored" as "nothing happens"
    # would be surprised by an empty slot.
    print(f"    Later than that the marker is stale, and quitting just closes "
          "the tab as usual —")
    print(f"    you would reopen it with `reloaded open {repo}`, at the end "
          "of the strip.")
    print("\n    `reloaded restart --self --cancel` calls it off.")
    return 0


def cmd_restart(args) -> int:
    if getattr(args, "self_", False):
        return cmd_restart_self(args)

    repos = list(getattr(args, "repos", None) or [])
    if repos:
        return cmd_restart_one(args, repos)

    try:
        lo, path, live, title_map = _capture_layout(args)
    except tabs_mod.UIAUnavailable as exc:
        return _report_uia_unavailable(exc)

    if not lo.windows:
        print("No Claude Code tabs found — nothing to restart.")
        return 1

    total = sum(len(w.tabs) for w in lo.windows)

    if args.dry_run:
        print(f"Would capture {len(lo.windows)} window(s), {total} session(s):")
        _print_window_tree(lo)
        print(f"\nWould gracefully exit all {total} session(s), then relaunch them from this capture.")
        print("\nDry run — nothing captured, sent, closed, or relaunched.")
        return 0

    layout_mod.save(lo, path)
    _print_capture_summary(lo, path)

    print("\nExiting current sessions — this will steal keyboard focus...")
    try:
        plans = teardown_mod.plan_down(args.repos_root, live=live, title_map=title_map)
    except tabs_mod.UIAUnavailable as exc:
        return _report_uia_unavailable(exc)

    down_result = teardown_mod.execute_down(
        plans, kinds=discover_mod.live_agents())
    _print_down_result(
        down_result,
        timed_out_note=" — the relaunch below will skip them rather than duplicate them",
    )

    print("\nRelaunching...")
    rc = _deploy_layout(lo, args)
    return 1 if (rc or down_result["timed_out"]) else 0


def cmd_down(args) -> int:
    try:
        plans = teardown_mod.plan_down(args.repos_root)
    except tabs_mod.UIAUnavailable as exc:
        return _report_uia_unavailable(exc)

    if not plans:
        print("No live Claude Code tabs found — nothing to exit.")
        return 0

    total = sum(len(p.targets) for p in plans)
    print(f"{total} live session(s) across {len(plans)} window(s):")
    for p in plans:
        note = ""
        if len(p.targets) != p.total_tabs:
            note = f"  ({p.total_tabs - len(p.targets)} other tab(s) here — window stays open)"
        print(f"  window 0x{p.hwnd:X}{note}")
        for t in p.targets:
            print(f"    - {t.title}  [{t.cwd}]")

    if tasks_mod.logon_launcher_installed():
        print(
            "\n[warn] the logon launcher is installed — it will relaunch these "
            "sessions at your next logon unless you also run "
            "`reloaded uninstall-tasks`."
        )

    if args.dry_run:
        print("\nDry run — nothing sent, nothing closed.")
        return 0

    # Read off each target's OWN pid, not from a second cwd-keyed sweep of
    # every process on the machine. The sweep can miss a session that is in
    # this plan - it is a separate enumeration, taken later - and a target
    # missing from it gets Claude's /exit sent to a Codex tab. The pid is
    # right here; asking it directly cannot disagree with itself.
    kinds = _agent_kinds(plans)
    # Named rather than assumed: half these tabs may be quit with an interrupt
    # rather than /exit, and a line that says otherwise is the user's only
    # record of what this command did to their desktop.
    labels = sorted({agents_mod.for_kind(kinds.get(norm(t.cwd))).quit_label
                     for t in _targets(plans)})
    print(f"\nSending {' / '.join(labels)} to {total} session(s) — "
          "this will steal keyboard focus...")
    result = teardown_mod.execute_down(plans, kinds=kinds)
    print()
    _print_down_result(result)
    return 1 if result["timed_out"] else 0


def cmd_install_tasks(args) -> int:
    import pathlib

    package_dir = str(pathlib.Path(__file__).resolve().parents[1])
    return tasks_mod.install(package_dir, args.layout)


def cmd_uninstall_tasks(args) -> int:
    return tasks_mod.uninstall()


def cmd_status(args) -> int:
    path = layout_path(args.layout)
    live = discover_mod.live_sessions()

    print(f"live sessions : {len(live)}")
    for cwd in sorted(live):
        print(f"    {cwd}")

    if not path.exists():
        print(f"\nno layout saved at {path} — run `reloaded capture`")
        return 1

    lo = layout_mod.load(path)
    print(f"\nlayout        : {path}")
    print(f"saved         : {lo.saved_ts}")

    saved_monitors = {m.device for m in lo.monitors}
    current_monitors = {m.device for m in win32_mod.list_monitors()}
    if saved_monitors != current_monitors:
        print(f"[warn] monitors changed: saved {sorted(saved_monitors)}, now {sorted(current_monitors)}")
        print("       windows will be clamped onto available monitors")

    # readiness.wait_for_ready only ever runs inside `up --unattended`, so an
    # attended `up` or a manual `status` never exercises it — the one piece
    # of this package's logic with no everyday path. timeout=0 makes this an
    # instant single check rather than the real (bounded) logon wait.
    ready, reason = readiness_mod.wait_for_ready(lo, timeout=0)
    print(f"readiness     : {reason}" if ready else f"[warn] readiness: {reason}")

    wt_ok, wt_detail = readiness_mod.check_wt_version()
    print(f"terminal      : {wt_detail}" if wt_ok else f"[warn] terminal: {wt_detail}")

    would_launch = 0
    for i, w in enumerate(lo.windows):
        print(f"\n  {layout_mod.window_id(i)}  {layout_mod.window_header(w)}")
        for t in w.tabs:
            running = norm(t.cwd) in live
            if not running:
                would_launch += 1
            mark = "running" if running else "would launch"
            print(f"      {mark:12}  {t.title}{layout_mod.tab_flags(t)}")

    orphans = set(live) - {norm(t.cwd) for w in lo.windows for t in w.tabs}
    if orphans:
        print(f"\n[drift] {len(orphans)} live session(s) are not in the layout:")
        for cwd in sorted(orphans):
            print(f"    {cwd}")
        print("        run `reloaded capture` to fold them in")

    print(f"\n`up` would launch {would_launch} session(s).")
    return 0


def _recorded_launches(args) -> dict[str, tuple[str, str]]:
    """(agent, command) per repo, as the saved layout records them.

    The fallback for a session whose live process cannot be read. Empty when
    there is no layout, or none can be parsed - callers then fall back again,
    to the kind's default.

    Loaded once for the whole batch. The per-repo version of this re-read and
    re-parsed the entire layout file for every repo being restarted.
    """
    try:
        lo = layout_mod.load(layout_path(args.layout))
    except Exception:
        return {}
    # First match wins, not last. Capture dedups by cwd, but a hand-edited
    # layout can hold two tabs for one repo, and a dict comprehension would
    # quietly reverse which of them this reads - a behaviour change smuggled
    # inside a refactor is worse than the duplicate itself.
    out: dict[str, tuple[str, str]] = {}
    for w in lo.windows:
        for t in w.tabs:
            out.setdefault(norm(t.cwd), (t.agent, t.command))
    return out


def _recorded_launch(args, cwd) -> tuple[str, str]:
    """One repo's (agent, command) from the saved layout, or the defaults."""
    return _recorded_launches(args).get(
        norm(cwd), (agents_mod.DEFAULT_KIND, ""))


def cmd_open(args) -> int:
    cwd = resolve_repo(args.repo, args.repos_root)
    if not os.path.isdir(cwd):
        print(f"Not a directory: {cwd}")
        return 1

    live = discover_mod.live_sessions()
    if norm(cwd) in live:
        print(f"Already running: {cwd} (pid {live[norm(cwd)]})")
        return 0

    sizes = _transcript_sizes(discover_mod.transcript_index(need_title=False))
    # Nothing is running to read the kind off, so the saved layout is the
    # only record of what this repo runs. Absent from it, or no layout at
    # all, means Claude Code - which is what every repo meant before a
    # second kind existed.
    agent, command = _recorded_launch(args, cwd)
    argv = deploy_mod.wt_argv_single_tab(cwd, sizes.get(norm(cwd), 0),
                                         agent, command)

    if args.dry_run:
        print(" ".join(repr(a) for a in argv))
        return 0

    import subprocess

    subprocess.Popen(argv, close_fds=True)
    print(f"Launched {cwd}")
    return 0


def cmd_edit(args) -> int:
    path = layout_path(args.layout)
    if path.exists():
        lo = layout_mod.load(path)
    else:
        print(f"No layout at {path} — capturing the current arrangement to start from.")
        try:
            lo, path, _live, _title_map = _capture_layout(args)
        except tabs_mod.UIAUnavailable as exc:
            return _report_uia_unavailable(exc)
        if not lo.windows:
            print("No Claude Code tabs found. Open some sessions first.")
            return 1
    return editor_mod.run(lo, path, args.repos_root)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reloaded", description="Windows Terminal launcher for Claude Code."
    )
    p.add_argument("--layout", default="default", help="layout name (default: default)")
    p.add_argument("--repos-root", default=DEFAULT_REPOS_ROOT, help="root directory holding repos")
    sub = p.add_subparsers(dest="command")

    cap = sub.add_parser("capture", help="snapshot the current arrangement into the layout")
    cap.add_argument(
        "--force", action="store_true",
        help="save even if the capture holds fewer sessions than the saved layout",
    )

    up = sub.add_parser("up", help="deploy the saved layout")
    up.add_argument("--dry-run", action="store_true", help="print what would launch, spawn nothing")
    up.add_argument("--unattended", action="store_true",
                    help="non-interactive: wait for readiness, log to file, never prompt")

    sub.add_parser("status", help="compare the saved layout against live sessions")

    open_ = sub.add_parser("open", help="add one repo as a new tab in the current window (see also: up)")
    open_.add_argument("repo", help="repo name under --repos-root, or an absolute path")
    open_.add_argument("--dry-run", action="store_true", help="print the command, spawn nothing")

    sub.add_parser("edit", help="open the interactive layout editor")

    down = sub.add_parser(
        "down",
        help="gracefully /exit every live Claude Code tab (waits up to 20s each), then close emptied windows",
    )
    down.add_argument("--dry-run", action="store_true", help="print what would be exited/closed, send nothing")

    restart = sub.add_parser(
        "restart",
        help="restart named sessions in place; with no names, capture the current "
             "arrangement, gracefully /exit everything, then relaunch it exactly as it was",
    )
    restart.add_argument(
        "repos",
        nargs="*",
        help="repo name or path to restart in place, keeping its tab and window "
             "(default: every session, via a full capture and relaunch)",
    )
    restart.add_argument(
        "--self", dest="self_", action="store_true",
        help="arm the session this is run from, then quit it yourself; the tab "
             "relaunches it in place (call from inside a session)",
    )
    restart.add_argument(
        "--cancel", action="store_true",
        help="with --self, disarm instead of arming",
    )
    restart.add_argument(
        "--dry-run", action="store_true", help="print what would be captured/exited/relaunched, do nothing"
    )
    restart.set_defaults(unattended=False)

    sub.add_parser("install-tasks", help="register the logon deploy and periodic reconcile")
    sub.add_parser("uninstall-tasks", help="remove the scheduled tasks")
    return p


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "capture":
        return cmd_capture(args)
    if args.command == "up":
        return cmd_up(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "open":
        return cmd_open(args)
    if args.command == "down":
        return cmd_down(args)
    if args.command == "restart":
        return cmd_restart(args)
    if args.command == "install-tasks":
        return cmd_install_tasks(args)
    if args.command == "uninstall-tasks":
        return cmd_uninstall_tasks(args)
    if args.command in (None, "edit"):
        return cmd_edit(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
