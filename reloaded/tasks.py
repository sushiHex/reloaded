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

import ast
import os
import pathlib
import subprocess

from . import pwsh, readiness

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


def _bootstrap_command(package_dir: str, layout: str, repos_root: str,
                       subcommand: list[str]) -> str:
    """A self-contained Python one-liner: put the package on sys.path and run
    a reloaded subcommand directly. Neither launcher needs a PYTHONPATH
    environment variable or a cmd.exe wrapper as a result -- both would
    otherwise risk allocating a console. repr() handles the Python-literal
    escaping for package_dir correctly regardless of its contents (backslashes,
    an embedded quote, ...), rather than re-deriving that rule by hand.
    """
    # Global options, so they precede the subcommand: after it argparse does
    # not treat them as options at all and exits 2 into a launcher with no
    # console. `repos_root` is required rather than defaulted - it went
    # missing here once already, and a parameter that can be omitted is how.
    argv = ["--layout", layout, "--repos-root", repos_root] + subcommand
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


def _build_vbs(package_dir: str, layout: str, repos_root: str) -> str:
    bootstrap = _bootstrap_command(
        package_dir, layout, repos_root, ["up", "--unattended"])
    run_command = "pyw.exe " + _launcher_argv(bootstrap)
    return (
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.Run {_vbs_quote(run_command)}, 0, False\r\n'
    )


def _encode_vbs(vbs: str) -> bytes:
    """The launcher as bytes Windows Script Host reads back correctly.

    UTF-16: wscript.exe decodes a BOM-less file with the ANSI code page, so a
    non-ASCII install path arrives mojibake'd, sys.path.insert points at a
    directory that does not exist, the import raises, and - because wscript has
    no console - every logon silently restores nothing. utf-8-sig is not the
    fix and must not be substituted: WSH rejects it outright with "Invalid
    character". UTF-16 carries a BOM WSH does honour.

    Encoded rather than written in text mode: _build_vbs emits explicit \\r\\n,
    and text mode's default newline=None rewrites every \\n as os.linesep,
    storing each terminator as \\r\\r\\n (measured on the installed launcher: 4
    CR for 2 LF).
    """
    return vbs.encode("utf-16")


def _publish(path: pathlib.Path, data: bytes) -> None:
    """Replace `path` with `data`, or leave it exactly as it was.

    Written to a temporary file and moved into place, because `open(path, "w")`
    truncates before it can fail: a crash mid-write would leave a 0-byte
    launcher that logon_launcher_installed() still reports as installed, having
    destroyed a working one. layout.save takes the same precaution for the same
    reason. The same guarantee is what lets a failed install put the previous
    launcher back with this function.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
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
        # errors="replace": the output is read for status tokens and shown as
        # a reason, so an undecodable byte must not raise after the script has
        # already changed something.
        capture_output=True, text=True, errors="replace",
        # See readiness.NO_WINDOW: a console-subsystem child otherwise gets a
        # visible console. Harmless from an interactive install; required if
        # this is ever reached from a windowless parent.
        creationflags=readiness.NO_WINDOW,
    )


def _register_reconcile_task(package_dir: str, layout: str,
                             repos_root: str) -> tuple[int, str, bool | None]:
    """Register the reconcile task: (exit code, reason, task as it was).

    The third value matters only on failure. True means the task's definition
    is verifiably what it was before this call - untouched, or put back; False
    means it could not be put back; None means nothing said either way.

    The previous definition never leaves PowerShell: it is exported, compared
    and re-registered there, so no encoding sits between the snapshot and the
    restore. Verified on Windows PowerShell 5.1, unelevated, against a
    throwaway task: an export registered back with -Xml exports identically.

    Absent only when the lookup says ObjectNotFound - an enum, so not
    localized (measured: CmdletizationQuery_NotFound). Any other error means
    the task could not be read, and nothing is changed: treated as absent,
    the rollback would delete it. Compared ordinally, because PowerShell's -eq
    ignores case.
    """
    bootstrap = _bootstrap_command(package_dir, layout, repos_root, ["capture"])
    argv = _launcher_argv(bootstrap)
    name = pwsh.literal(TASK_RECONCILE)
    script = f"""
$ErrorActionPreference = 'Stop'
function Definition {{
    try {{ $null = Get-ScheduledTask -TaskPath '\\' -TaskName {name} }}
    catch {{ if ($_.CategoryInfo.Category -ne 'ObjectNotFound') {{ throw }}; return '' }}
    Export-ScheduledTask -TaskPath '\\' -TaskName {name}
}}
function Same($a, $b) {{ [string]::Equals([string]$a, [string]$b, [StringComparison]::Ordinal) }}
try {{ $before = Definition }} catch {{ 'TASK:KEPT'; throw }}
try {{
    $action = New-ScheduledTaskAction -Execute 'pyw.exe' -Argument {pwsh.literal(argv)}
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes {RECONCILE_INTERVAL_MINUTES}) `
        -RepetitionDuration (New-TimeSpan -Days {_REPETITION_DURATION_DAYS})
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes {RECONCILE_EXECUTION_TIME_LIMIT_MINUTES}) `
        -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskPath '\\' -TaskName {name} -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
}} catch {{
    $failure = $_
    try {{
        if (-not (Same (Definition) $before)) {{
            if ($before) {{ Register-ScheduledTask -TaskPath '\\' -TaskName {name} -Xml $before -Force | Out-Null }}
            else {{ Unregister-ScheduledTask -TaskPath '\\' -TaskName {name} -Confirm:$false }}
            if (-not (Same (Definition) $before)) {{ throw 'its definition still differs' }}
        }}
        'TASK:KEPT'
    }} catch {{ "TASK:CHANGED $_" }}
    throw $failure
}}
"""
    try:
        proc = _run_powershell(script)
    except OSError as exc:
        # PowerShell never started, so it changed nothing.
        return 1, str(exc), True
    detail = (proc.stderr or "").strip() or f"exit {proc.returncode}"
    kept = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("TASK:KEPT"):
            kept = True
        elif line.startswith("TASK:CHANGED"):
            kept = False
            detail += f" (putting it back failed too: {line[12:].strip()})"
    return proc.returncode, detail, kept


_REQUIRED = ("psutil", "uiautomation")


def _preflight(package_dir: str) -> str:
    """Why the interpreter both launchers start cannot run them, or "".

    Run through the same bare `pyw.exe -3` they use, not `sys.executable`:
    that is a pipx venv or whatever ran this install, and the py launcher
    picks a system Python of its own. Imports that fail there fail at the
    next logon with no console, and the first symptom is a layout that
    stopped updating.

    Both dependencies are imported eagerly here although reloaded imports
    them lazily: without psutil a capture sees no sessions and still exits
    cleanly, which is the silent failure this exists to catch.

    The interpreter is reported in ascii() so its path survives any console
    code page, and success needs the closing record as well as exit 0: a
    launcher can exit 0 without running Python at all (PYLAUNCHER_DRYRUN).
    """
    probe = ("import sys; print('interpreter', ascii(sys.executable), flush=True); "
             f"sys.path.insert(0, {package_dir!r}); "
             f"import {', '.join(_REQUIRED)}; from reloaded.__main__ import main; "
             "print('imports ok')")
    try:
        proc = subprocess.run(["pyw.exe", "-3", "-c", probe], capture_output=True,
                              text=True, errors="replace", timeout=60)
    except FileNotFoundError:
        return ("pyw.exe was not found. Both launchers start Python through it: "
                "install the Python launcher (python.org's installer includes it).")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"could not run `pyw.exe -3` to check the interpreter it starts: {exc}"
    lines = (proc.stdout or "").splitlines()
    if proc.returncode == 0 and "imports ok" in lines:
        return ""
    exe = next(filter(None, map(_interpreter_record, lines)), None)
    errors = (proc.stderr or "").strip().splitlines()
    reason = errors[-1] if errors else f"exit {proc.returncode}"
    if exe is None:
        return f"`pyw.exe -3` did not start a Python: {reason}"
    message = (f"{exe}, which `pyw.exe -3` starts for both launchers, cannot "
               f"run reloaded: {reason}")
    # Only a missing dependency of ours has a known fix. Anything else - a
    # broken checkout, a DLL that will not load - is shown as it is.
    if any(reason == f"ModuleNotFoundError: No module named '{m}'" for m in _REQUIRED):
        python = pathlib.Path(exe).with_name("python.exe")
        # `&` and a literal: PowerShell will not run a quoted path without the
        # call operator, and expands `$` inside double quotes.
        message += (f"\n    Install them for that interpreter: "
                    f"& {pwsh.literal(str(python if python.exists() else exe))} "
                    f"-m pip install {' '.join(_REQUIRED)}")
    return message


def _interpreter_record(line: str) -> str | None:
    """The path in the probe's `interpreter <ascii literal>` line, else None.
    ascii() picks double quotes for a path holding an apostrophe."""
    if not line.startswith("interpreter "):
        return None
    try:
        return ast.literal_eval(line[len("interpreter "):])
    except (ValueError, SyntaxError):
        return None


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
$t = Get-ScheduledTask -TaskName {pwsh.literal(TASK_RECONCILE)} -ErrorAction SilentlyContinue
if ($null -eq $t) {{ Write-Output 'ABSENT'; exit 0 }}
Unregister-ScheduledTask -TaskName {pwsh.literal(TASK_RECONCILE)} -Confirm:$false
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


def install(package_dir: str, layout: str, repos_root: str) -> int:
    """Register both launchers with the layout AND the repository root.

    Both are configuration the user chose and neither is recoverable by the
    scheduled process: it starts with its own working directory and its own
    idea of a default root.

    The root changes behaviour for the reconcile, which runs `capture` and
    resolves tab titles against it. It changes nothing for the logon launcher
    today - `up` deploys from the absolute cwd saved in each tab and never
    reads `repos_root` - so passing it there is consistency rather than a fix,
    and worth having only because a launcher that carries half the
    configuration is how the other half went missing.

    Both commands are fixed here, at registration. A later run that omits
    either option replaces it with a default, so what was baked in is printed:
    the documented upgrade path is "re-run install-tasks after moving the
    checkout", and a bare re-run must not revert a custom root in silence.

    A failed install leaves both launchers as they were, or says which one it
    could not put back. The interpreter is checked before either is touched;
    the launcher file is replaced atomically, so failing to write it changes
    nothing; and a task that will not register is restored by the script that
    registers it, after which the previous launcher file is put back too.
    Best effort for failures it sees, not a transaction: the new launcher is
    live for the seconds registration takes, and a killed install does not
    roll back.
    """
    print(f"Registering with --layout {layout} --repos-root {repos_root}")
    problem = _preflight(package_dir)
    if problem:
        print(problem)
        print("Nothing was installed.")
        return 1

    path = startup_vbs_path()
    try:
        try:
            # Read rather than asked: exists() answers False for a file it
            # cannot stat, and the rollback would then delete a launcher it
            # had replaced.
            before = path.read_bytes()
        except FileNotFoundError:
            before = None
        path.parent.mkdir(parents=True, exist_ok=True)
        _publish(path, _encode_vbs(_build_vbs(package_dir, layout, repos_root)))
    except (OSError, ValueError) as exc:
        # ValueError as well as OSError: a lone surrogate in package_dir raises
        # UnicodeEncodeError, which is a ValueError, and would otherwise escape
        # this handler as a traceback out of an unattended install.
        print(f"could not write {path}: {exc}")
        print("Nothing was installed.")
        return 1

    # Nothing is printed between publishing and putting back: a print that
    # raises there would leave the new launcher beside the old task.
    rc, detail, kept = _register_reconcile_task(package_dir, layout, repos_root)
    if rc != 0:
        restored = _put_back(path, before, layout, repos_root)
        print(f"Failed to register {TASK_RECONCILE} (exit {rc}): {detail}")
        print(restored)
        print({True: f"{TASK_RECONCILE} is as it was before this install.",
               False: f"[warn] {TASK_RECONCILE} could not be put back as it was. "
                      "Check it in Task Scheduler, or run `reloaded uninstall-tasks`.",
               None: f"[warn] could not tell whether {TASK_RECONCILE} was changed. "
                     "Check it in Task Scheduler."}[kept])
        return rc

    print(f"Logon deploy   -> {path}")
    # Only once both are in place: a failed install must not take this away.
    legacy = startup_dir() / _LEGACY_CMD_NAME
    if legacy.exists():
        try:
            legacy.unlink()
            print(f"Removed legacy launcher -> {legacy}")
        except OSError as exc:
            print(f"[warn] could not remove legacy launcher {legacy}: {exc}")
    print(
        f"Reconcile task -> {TASK_RECONCILE}, every {RECONCILE_INTERVAL_MINUTES} min, "
        f"runs even on battery, force-killed after {RECONCILE_EXECUTION_TIME_LIMIT_MINUTES} min if hung"
    )
    return 0


def _put_back(path: pathlib.Path, before: bytes | None, layout: str,
              repos_root: str) -> str:
    """Restore the logon launcher to `before` (None: absent), saying what happened.

    A launcher this attempt created is removed; one it replaced is restored,
    never removed. If that fails, _publish has left the new one intact, so what
    it now runs is known and is what gets said.
    """
    try:
        if before is None:
            path.unlink(missing_ok=True)
            return f"Removed the logon launcher this install wrote: {path}"
        _publish(path, before)
        return f"Put the previous logon launcher back: {path}"
    except OSError as exc:
        what = ("could not remove the logon launcher this install wrote"
                if before is None else "could not put the previous logon launcher back")
        return (f"[warn] {what} ({exc}): {path} runs `up --unattended` with "
                f"--layout {layout} --repos-root {repos_root} at the next sign-in.")


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
