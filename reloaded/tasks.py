"""Logon and periodic scheduling, without requiring elevation.

Both mechanisms run through the stable `py`/`pyw` launcher (C:\\Windows\\pyw.exe,
part of Windows' unconditional executable search order, unaffected by any
single Python install being upgraded, moved, or reinstalled elsewhere) and
embed their own `sys.path` setup in a `-c` bootstrap rather than relying on a
PYTHONPATH environment variable or a cmd.exe wrapper -- so neither ever
allocates a console. Windows 11 makes Windows Terminal the default console
host, and a console -- even one from a scheduled task, even briefly -- risks
being adopted as a tab in an existing WT window; the original .cmd-based
logon launcher is exactly how that bug was first found.

* Logon deploy -- a .vbs in the Startup folder, run via wscript.exe (itself
  console-free) which launches pyw.exe directly. `schtasks /SC ONLOGON` is
  denied to a non-elevated user (verified), so a Startup entry is used
  instead. `up --unattended` handles its own readiness wait, so the lack of
  a fixed startup delay is not a problem.
* Periodic reconcile -- a scheduled task registered via PowerShell
  (Register-ScheduledTask), not `schtasks.exe`: classic schtasks has no flag
  for ExecutionTimeLimit or for clearing the battery-power conditions it
  defaults to (confirmed: `schtasks /Create /?` exposes neither). Both
  matter here -- with the defaults, an unbounded hung run can wedge
  reconciliation for days (MultipleInstances defaults to not queuing a
  second run, so one stuck instance blocks every subsequent trigger), and
  the default battery flags silently stop reconciliation on any unplugged
  laptop. PowerShell registration also lets a re-run of `install-tasks`
  cleanly replace an existing schtasks.exe-created task of the same name
  (verified) -- no separate migration path is needed for that part.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

from . import readiness

TASK_RECONCILE = "Reloaded-Reconcile"
STARTUP_VBS_NAME = "reloaded-up.vbs"
_LEGACY_CMD_NAME = "reloaded-up.cmd"  # pre-fix installs used this; removed on uninstall/reinstall

RECONCILE_INTERVAL_MINUTES = 5
# Must stay below the interval: under MultipleInstances=IgnoreNew, a run that
# is never force-killed can block every later trigger indefinitely.
RECONCILE_EXECUTION_TIME_LIMIT_MINUTES = 4
# Task Scheduler's XML schema rejects TimeSpan.MaxValue for a repetition
# duration (verified: it errors as out-of-range); this is the standard
# "effectively forever" stand-in.
_REPETITION_DURATION_DAYS = 3650


def startup_dir() -> pathlib.Path:
    appdata = os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Roaming"
    )
    return pathlib.Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def startup_vbs_path() -> pathlib.Path:
    return startup_dir() / STARTUP_VBS_NAME


def _vbs_quote(s: str) -> str:
    """Escape a value for embedding in a VBScript double-quoted string literal."""
    return '"' + s.replace('"', '""') + '"'


def _ps_quote(s: str) -> str:
    """Escape a value for embedding in a PowerShell single-quoted string literal."""
    return "'" + s.replace("'", "''") + "'"


def _bootstrap_command(package_dir: str, layout: str, subcommand: list[str]) -> str:
    """A self-contained Python one-liner: put the package on sys.path and run
    a reloaded subcommand directly. Neither launcher needs a PYTHONPATH
    environment variable or a cmd.exe wrapper as a result -- both would
    otherwise risk allocating a console. repr() handles the Python-literal
    escaping for package_dir correctly regardless of its contents (backslashes,
    an embedded quote, ...), rather than re-deriving that rule by hand.
    """
    argv = ["--layout", layout] + subcommand
    return (
        f"import sys; sys.path.insert(0, {package_dir!r}); "
        f"from reloaded.__main__ import main; "
        f"raise SystemExit(main({argv!r}))"
    )


def _launcher_argv(bootstrap: str) -> str:
    """The full pyw.exe command line, quoted the way Windows' standard argv
    parser (the C runtime convention, also what CommandLineToArgvW expects)
    will read it back apart. Built with subprocess.list2cmdline rather than
    hand-rolled quoting -- this package already shipped one real bug from
    hand-rolled argument-splitting assumptions (wt's `;` handling); reusing a
    correct, tested primitive here avoids re-deriving that class of mistake.
    """
    return subprocess.list2cmdline(["-3", "-c", bootstrap])


def _build_vbs(package_dir: str, layout: str) -> str:
    bootstrap = _bootstrap_command(package_dir, layout, ["up", "--unattended"])
    run_command = "pyw.exe " + _launcher_argv(bootstrap)
    return (
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.Run {_vbs_quote(run_command)}, 0, False\r\n'
    )


def _write_vbs(path: pathlib.Path, vbs: str) -> None:
    """Write the launcher so Windows Script Host reads it back correctly.

    UTF-16: wscript.exe decodes a BOM-less file with the ANSI code page, so a
    non-ASCII install path arrives mojibake'd, sys.path.insert points at a
    directory that does not exist, the import raises, and - because wscript has
    no console - every logon silently restores nothing. utf-8-sig is not the
    fix and must not be substituted: WSH rejects it outright with "Invalid
    character". UTF-16 carries a BOM WSH does honour.

    newline="": _build_vbs emits explicit \\r\\n, and text mode's default
    newline=None rewrites every \\n as os.linesep, storing each terminator as
    \\r\\r\\n (measured on the installed launcher: 4 CR for 2 LF).

    Written to a temporary file and moved into place, because `open(path, "w")`
    truncates before it can fail: an encode error or a crash mid-write would
    leave a 0-byte launcher that logon_launcher_installed() still reports as
    installed, having destroyed a working one. layout.save takes the same
    precaution for the same reason.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-16", newline="") as fh:
            fh.write(vbs)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _run_powershell(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True,
        # See readiness.NO_WINDOW: a console-subsystem child otherwise gets a
        # visible console. Harmless from an interactive install; required if
        # this is ever reached from a windowless parent.
        creationflags=readiness.NO_WINDOW,
    )


def _register_reconcile_task(package_dir: str, layout: str) -> tuple[int, str]:
    bootstrap = _bootstrap_command(package_dir, layout, ["capture"])
    argv = _launcher_argv(bootstrap)
    script = f"""
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute 'pyw.exe' -Argument {_ps_quote(argv)}
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes {RECONCILE_INTERVAL_MINUTES}) `
    -RepetitionDuration (New-TimeSpan -Days {_REPETITION_DURATION_DAYS})
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes {RECONCILE_EXECUTION_TIME_LIMIT_MINUTES}) `
    -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName {_ps_quote(TASK_RECONCILE)} -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
"""
    proc = _run_powershell(script)
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()


def logon_launcher_installed() -> bool:
    """Whether the logon launcher (.vbs) exists - callers use this to warn
    before an action (like `down`) whose effect the launcher would undo at
    the next logon. The periodic reconcile task runs `capture`, not `up`
    (see _register_reconcile_task) - it only re-snapshots the current state
    and never relaunches anything, so it is not a source of this hazard."""
    return startup_vbs_path().exists()


def _remove_reconcile_task() -> tuple[bool, str]:
    """Returns (ok, message). Distinguishes "never existed" from "removal
    failed" by checking Get-ScheduledTask's structured result rather than
    matching schtasks.exe's localized error text — the previous version
    misclassified a real failure as "not found" on any non-English Windows.
    """
    script = f"""
$ErrorActionPreference = 'Stop'
$t = Get-ScheduledTask -TaskName {_ps_quote(TASK_RECONCILE)} -ErrorAction SilentlyContinue
if ($null -eq $t) {{ Write-Output 'ABSENT'; exit 0 }}
Unregister-ScheduledTask -TaskName {_ps_quote(TASK_RECONCILE)} -Confirm:$false
Write-Output 'REMOVED'
"""
    proc = _run_powershell(script)
    stdout = (proc.stdout or "").strip()
    if proc.returncode == 0 and stdout == "ABSENT":
        return True, f"No scheduled task named {TASK_RECONCILE}"
    if proc.returncode == 0 and stdout == "REMOVED":
        return True, f"Removed {TASK_RECONCILE}"
    detail = (proc.stderr or stdout or f"exit {proc.returncode}").strip()
    return False, f"[warn] could not remove {TASK_RECONCILE}: {detail}"


def install(package_dir: str, layout: str = "default") -> int:
    vbs = _build_vbs(package_dir, layout)
    path = startup_vbs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_vbs(path, vbs)
    except (OSError, ValueError) as exc:
        # ValueError as well as OSError: a lone surrogate in package_dir raises
        # UnicodeEncodeError, which is a ValueError, and would otherwise escape
        # this handler as a traceback out of an unattended install.
        print(f"could not write {path}: {exc}")
        return 1
    print(f"Logon deploy   -> {path}")

    legacy = startup_dir() / _LEGACY_CMD_NAME
    if legacy.exists():
        try:
            legacy.unlink()
            print(f"Removed legacy launcher -> {legacy}")
        except OSError as exc:
            print(f"[warn] could not remove legacy launcher {legacy}: {exc}")

    rc, detail = _register_reconcile_task(package_dir, layout)
    if rc != 0:
        print(f"Failed to register {TASK_RECONCILE} (exit {rc}): {detail}")
        return rc
    print(
        f"Reconcile task -> {TASK_RECONCILE}, every {RECONCILE_INTERVAL_MINUTES} min, "
        f"runs even on battery, force-killed after {RECONCILE_EXECUTION_TIME_LIMIT_MINUTES} min if hung"
    )
    return 0


def uninstall() -> int:
    rc = 0

    for name, path in (("logon deploy", startup_vbs_path()), ("legacy launcher", startup_dir() / _LEGACY_CMD_NAME)):
        if path.exists():
            try:
                path.unlink()
                print(f"Removed {path}")
            except OSError as exc:
                print(f"could not remove {path}: {exc}")
                rc = 1
        elif name == "logon deploy":
            print(f"No logon deploy at {path}")

    ok, message = _remove_reconcile_task()
    print(message)
    if not ok:
        rc = 1

    return rc
