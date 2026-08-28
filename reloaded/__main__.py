"""Reloaded CLI."""
from __future__ import annotations

import argparse
import os
import sys

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


def _deploy_layout(lo, args) -> int:
    """Deploy an already-loaded Layout: plan, print, repair torn transcripts,
    launch, and place. Shared by `up` (loads the saved layout from disk) and
    `restart` (deploys a layout it just captured in the same run)."""
    live = discover_mod.live_sessions()
    monitors = win32_mod.list_monitors()
    # Built once and reused by both the size guard and the torn-tail repair —
    # this scan walks every transcript on the machine.
    index = discover_mod.transcript_index(need_title=False)
    plan = deploy_mod.plan_deploy(lo, live, monitors, _transcript_sizes(index))

    if not plan:
        total = sum(len(w.tabs) for w in lo.windows)
        msg = f"All {total} session(s) already running — nothing to do."
        _log(msg) if args.unattended else print(msg)
        return 0

    for entry in plan:
        x, y, w, h = entry.rect
        print(f"window {entry.id}  ({x},{y} {w}x{h}, {entry.state})")
        for tab, delay in zip(entry.tabs, entry.delays):
            print(f"    +{delay:>3}s  {tab.title}  [{tab.cwd}]")
        for tab in entry.skipped:
            print(f"      --   {tab.title}  (already running)")
        for tab in entry.missing:
            print(f"      --   {tab.title}  [warn] directory not found: {tab.cwd}")
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
            print(f"[warn] could not repair {transcript_path}: {outcome}")
        elif outcome:
            print(f"[reloaded] repaired torn transcript: {transcript_path}")

    results = deploy_mod.execute(plan)
    failures = 0
    for r in results:
        if r.hwnd is None:
            print(f"[warn] window {r.window_id}: could not identify the new window; geometry not applied")
            failures += 1
        elif not r.placed:
            print(f"[warn] window {r.window_id}: launched but geometry could not be applied")
            failures += 1
    launched = sum(len(e.tabs) for e in plan)
    summary = f"Launched {launched} session(s) in {len(plan)} window(s)."
    _log(summary) if args.unattended else print(f"\n{summary}")

    silent = _never_started(lo, discover_mod.live_sessions())
    if silent:
        note = f"[reloaded] {len(silent)} tab(s) opened but no session started:"
        _log(note) if args.unattended else print(f"\n{note}")
        for cwd in silent[:6]:
            print(f"    - {cwd}")
        print("    Codex asks whether to trust a directory it has not seen")
        print("    before it starts. Answer it in the tab - this tool will not")
        print("    answer a security question on your behalf.")

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


def _launch_single_tab(cwd: str) -> None:
    """Open one repo as a tab via `wt -w 0`.

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

    sizes = _transcript_sizes(discover_mod.transcript_index(need_title=False))
    argv = deploy_mod.wt_argv_single_tab(cwd, sizes.get(norm(cwd), 0))
    subprocess.Popen(argv, close_fds=True)


def _type_relaunch(hwnd: int, item, cwd: str, size_bytes: int = 0) -> bool:
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

    path = relaunch_script_path(cwd)
    path.write_text(deploy_mod.relaunch_script(cwd, size_bytes), encoding="utf-8")

    if not tabs_mod.select_tab(hwnd, item):
        return False

    import uiautomation as auto

    # The session has only just ended; give its shell a moment to finish
    # returning to a prompt before typing at it.
    time.sleep(1.0)
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

    not_running = [c for c in cwds if norm(c) not in live]
    if not_running:
        for c in not_running:
            print(f"Not running: {c}")
        print("\n`reloaded status` lists the live sessions.")
        return 1

    try:
        plans = teardown_mod.plan_down(args.repos_root, live=live, only=cwds)
    except tabs_mod.UIAUnavailable as exc:
        return _report_uia_unavailable(exc)

    # Every requested repo must have a tab we can actually type into, or the
    # batch is refused whole. Acting on the resolvable subset used to restart
    # some repos, skip the rest without a word, and still return 0 - with the
    # skipped repos' markers left armed on disk.
    planned = {norm(cwd) for _t, cwd, _p, _i in _targets(plans)}
    unresolved = [c for c in cwds if norm(c) not in planned]
    if unresolved:
        for c in unresolved:
            print(f"No Windows Terminal tab found for {c}")
        print(
            "\n    Running, but no tab this tool can drive — started outside "
            "Windows Terminal, or UIA cannot read its title."
        )
        print("    Nothing was restarted; re-run without it to restart the rest.")
        return 1

    if args.dry_run:
        for _title, cwd, pid, _item in _targets(plans):
            print(f"Would restart {cwd} (pid {pid}) in place, leaving its window open.")
            if discover_mod.launcher_kind(pid) == discover_mod.HAND:
                # The one consequence worth previewing: this rewrites what the
                # tab runs, permanently. A preview reading the same for both
                # kinds hides it until after the fact.
                print("    Started by hand — its tab would be upgraded to the "
                      "reloaded launcher,")
                print("    which self-closes when the session exits.")
        print("\nDry run — no marker written, nothing sent.")
        return 0

    _sweep_stale_markers()
    tabs_before = {plan.hwnd: plan.total_tabs for plan in plans}
    sizes = _transcript_sizes(discover_mod.transcript_index(need_title=False))

    # Two different classifications, and conflating them sends a Codex tab
    # `/exit`. `launchers` is how a session was STARTED - by hand or by this
    # package - which decides whether the marker machinery applies at all.
    # `agent_kinds` is WHICH CLI it is, which decides how it is asked to quit.
    launchers = {
        norm(cwd): discover_mod.launcher_kind(pid)
        for _t, cwd, pid, _i in _targets(plans)
    }
    agent_kinds = discover_mod.live_agents()

    def arm(cwd):
        if launchers.get(norm(cwd)) == discover_mod.RELOADED:
            restart_marker(cwd).write_text("restart", encoding="utf-8")

    print("Exiting the named sessions — this will steal keyboard focus...")
    # Armed per tab, as its turn comes, rather than all up front: targets are
    # exited serially with a wait each, so a marker written now for the last
    # target would spend every earlier target's wait ageing toward its TTL.
    down = teardown_mod.execute_down(plans, close_windows=False, before_exit=arm,
                                     kinds=agent_kinds)
    _print_down_result(down, timed_out_note=" — not restarted")

    stuck = {norm(cwd) for _t, cwd in down["timed_out"]}
    print("\nWaiting for them to come back...")
    failed = False
    for plan in plans:
        for _title, cwd, old_pid, item in plan.targets:
            if norm(cwd) in stuck:
                # Deliberately left armed. "Timed out" only means it had not
                # exited within EXIT_TIMEOUT_SECONDS, not that it never will -
                # and if the marker is gone when it does, the shell breaks out
                # of the restart loop and CLOSE_TAB_IF_STARTED closes the tab.
                # Disarming here would destroy the session this command was
                # asked to bring back. Left armed the worst case is a late
                # restart, which is what was requested, bounded by the TTL.
                print(
                    f"    {cwd} never exited — still running as pid {old_pid}, "
                    f"not restarted"
                )
                # Only a reloaded-launched target has a marker at all. Saying
                # otherwise tells the user to expect a delayed restart that
                # nothing can perform - seen for real against a hand-launched
                # session, which is armed with nothing by design.
                if launchers.get(norm(cwd)) == discover_mod.RELOADED:
                    mins = deploy_mod.RESTART_MARKER_TTL_SECONDS // 60
                    print(
                        f"        its restart is still armed: it will restart if it "
                        f"exits within {mins} min, and be ignored after that"
                    )
                failed = True
                continue

            if launchers.get(norm(cwd)) == discover_mod.HAND:
                # Its shell is waiting at a prompt in a tab that never closed.
                # Nothing will relaunch it, so put the launcher in there.
                print(f"    {cwd} was started by hand — typing the launcher into its tab")
                if not _type_relaunch(plan.hwnd, item, cwd, sizes.get(norm(cwd), 0)):
                    print(f"    [warn] could not reach that tab — start {cwd} by hand")
                    failed = True
                    continue
                pid = _wait_for_session(cwd, not_pid=old_pid)
                if pid is None:
                    print(f"    [warn] {cwd} did not come back — start it by hand")
                    failed = True
                    continue
                print(f"    restarted in place -> {cwd} (pid {pid}); its tab is "
                      "now upgraded and will self-close on exit")
                continue

            pid = _wait_for_session(cwd, not_pid=old_pid)
            if pid is not None:
                print(f"    restarted in place -> {cwd} (pid {pid})")
                continue

            # Nothing live at that cwd. Either the tab closed (no restart loop
            # in a session started before this existed) or the relaunch failed
            # fast enough that the startup guard kept the tab open to show the
            # error. Those need opposite responses, so ask the window rather
            # than inferring from the absent pid: a second tab beside an error
            # message is the worst of both.
            if _live_tab_count(plan.hwnd) >= tabs_before.get(plan.hwnd, 0):
                print(
                    f"    [warn] {cwd} did not come back, but still has its tab — "
                    "read the error in that window rather than opening another"
                )
                failed = True
                continue

            # The tab is gone, so the shell that could have read this marker is
            # gone with it. That makes deleting safe here and nowhere else -
            # and it is necessary, because the replacement launched below DOES
            # have the restart loop. Left armed, the marker outlives the tab
            # that ignored it and fires on the user's next /exit in the new
            # session, restarting one they meant to close.
            print(f"    {cwd} did not relaunch in place — its tab closed; reopening it")
            try:
                restart_marker(cwd).unlink(missing_ok=True)
            except OSError:
                pass
            _launch_single_tab(cwd)
            if _wait_for_session(cwd) is None:
                print(f"    [warn] {cwd} did not come back — start it by hand")
                failed = True
    return 1 if failed else 0


# A marker is deleted in exactly one place above: after the tab that could have
# read it is confirmed gone. Everywhere else, deleting means guessing that a
# live shell will not read it, and that guess is unrecoverable when wrong - the
# shell leaves the restart loop, CLOSE_TAB_IF_STARTED fires, and the session
# the restart was meant to bring back is destroyed instead. A timed-out target
# is therefore left armed, because "did not exit in 20s" is not "will never
# exit". The rest of staleness is handled where being wrong costs nothing:
# deploy's TTL makes an old marker inert, and _sweep_stale_markers clears the
# directory on the next run.


def _targets(plans):
    for plan in plans:
        yield from plan.targets


def cmd_restart(args) -> int:
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
        for title, cwd, _pid, _item in p.targets:
            print(f"    - {title}  [{cwd}]")

    if tasks_mod.logon_launcher_installed():
        print(
            "\n[warn] the logon launcher is installed — it will relaunch these "
            "sessions at your next logon unless you also run "
            "`reloaded uninstall-tasks`."
        )

    if args.dry_run:
        print("\nDry run — nothing sent, nothing closed.")
        return 0

    print(f"\nSending /exit to {total} session(s) — this will steal keyboard focus...")
    result = teardown_mod.execute_down(plans, kinds=discover_mod.live_agents())
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
    argv = deploy_mod.wt_argv_single_tab(cwd, sizes.get(norm(cwd), 0))

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
