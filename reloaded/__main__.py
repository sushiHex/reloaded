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
from .paths import layout_path, log_path, norm, resolve_repo

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
        lo, path, _live, _title_map = _capture_layout(args)
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
    layout_mod.save(lo, path)
    _print_capture_summary(lo, path)
    return 0


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
    return 1 if failures else 0


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


def cmd_restart(args) -> int:
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

    down_result = teardown_mod.execute_down(plans)
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
    result = teardown_mod.execute_down(plans)
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

    sub.add_parser("capture", help="snapshot the current arrangement into the layout")

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
        help="capture the current arrangement, gracefully /exit everything, then relaunch it exactly as it was",
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
