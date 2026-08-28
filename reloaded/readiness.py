"""Bounded wait for the machine to be able to host the layout.

A fixed logon delay is a coin flip after an ungraceful shutdown: volumes may
still be checking, and displays frequently enumerate late — deploying early puts
every window on the wrong monitor.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

# powershell is a console-subsystem program, so Windows gives it a console
# unless told not to - and on Win11 that console is a visible Windows Terminal
# window titled with the exe path. This call is reached from `up --unattended`,
# which is exactly the logon-launcher path that runs under pythonw precisely so
# nothing flashes on screen. Without the flag every logon pops a stray window.
# getattr: the constant is Windows-only, and 0 is "no extra flags" elsewhere.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

from . import win32
from .layout import Layout

# --pos=x,y and -w -1 verified present at this version; older releases are
# unverified, not necessarily broken — this is a "probably fine below this"
# warning, not a hard requirement enforced anywhere.
MIN_WT_VERSION = (1, 24, 0, 0)


def _missing_monitors(lo: Layout) -> list[str]:
    present = {m.device for m in win32.list_monitors()}
    return sorted({w.monitor for w in lo.windows if w.monitor and w.monitor not in present})


def _missing_repos(lo: Layout) -> list[str]:
    """Repos not yet readable — could be an unmounted volume, or a deleted repo.

    Not part of the readiness gate: unlike a monitor, a missing repo has no
    guarantee of ever appearing (a pinned tab for a deleted repo persists
    forever, since capture only records live sessions and never prunes it).
    Waiting on it would turn one stale tab into a permanent 120s boot delay.
    Reported for visibility only; deploy.py skips a tab whose directory is
    still missing when it actually launches.
    """
    missing = []
    for w in lo.windows:
        for t in w.tabs:
            if not os.path.isdir(t.cwd):
                missing.append(t.cwd)
    return sorted(set(missing))


def _persisted_path_dirs() -> list[str]:
    """PATH directories as Windows has them stored, not as this process
    inherited them.

    A process keeps the PATH it started with. An installer that adds a
    directory afterwards is invisible to everything already running - so a
    long-lived session asking `is codex installed?` gets the wrong answer, and
    the tabs it goes on to launch, which DO get a fresh environment, would have
    resolved it fine. Seen exactly that: codex on the user PATH, absent from
    the session doing the checking, and the logon deploy would have waited out
    its whole timeout for a binary that was installed.

    Registry rather than `setx` or a subprocess: this runs on the logon path
    where spawning a shell to read an environment variable is its own hazard.
    """
    import winreg

    out: list[str] = []
    for hive, key in (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ):
        try:
            with winreg.OpenKey(hive, key) as handle:
                value, _kind = winreg.QueryValueEx(handle, "Path")
        except OSError:
            continue
        out.extend(
            os.path.expandvars(part).strip('"')
            for part in str(value).split(os.pathsep) if part.strip()
        )
    return out


def _on_path(binary: str) -> bool:
    """Whether `binary` is resolvable, by this process or by one started now."""
    if shutil.which(binary) is not None:
        return True
    exts = [e for e in os.environ.get("PATHEXT", ".EXE").split(os.pathsep) if e]
    for directory in _persisted_path_dirs():
        for ext in exts:
            if os.path.isfile(os.path.join(directory, binary + ext)):
                return True
    return False


def required_binaries(lo: Layout) -> list[str]:
    """Which agent binaries this layout's tabs actually need on PATH.

    Waiting unconditionally for `claude` made a Codex-only layout sit out its
    whole timeout for a binary it never uses, then report that as the reason
    it was not ready - which sends people looking in the wrong place.
    """
    from . import agents as agents_mod

    return sorted({
        agents_mod.for_kind(t.agent).binary
        for w in lo.windows for t in w.tabs
    })


def wait_for_ready(lo: Layout, timeout: float = 120.0, poll: float = 2.0) -> tuple[bool, str]:
    """Block until wt.exe, the layout's agent binaries, and the saved
    monitors are available.

    Returns (ready, reason). A False result still allows deploy to proceed with
    clamping — it reports what was never satisfied. Missing repo directories
    are surfaced in `reason` but never block readiness (see _missing_repos).
    """
    deadline = time.time() + timeout
    reason = ""
    have_wt = False
    # Only what this layout's tabs actually need.
    missing = set(required_binaries(lo))
    while True:
        # PATH does not shrink while we wait, so once found stop re-scanning.
        if not have_wt:
            have_wt = shutil.which("wt") is not None
        missing = {b for b in missing if not _on_path(b)}

        if not have_wt:
            reason = "wt.exe not on PATH"
        elif missing:
            reason = f"{', '.join(sorted(missing))} not on PATH"
        else:
            missing_mon = _missing_monitors(lo)
            if missing_mon:
                reason = f"monitors not present: {', '.join(missing_mon)}"
            else:
                missing_repo = _missing_repos(lo)
                if missing_repo:
                    return True, f"ready (repo(s) still missing: {', '.join(missing_repo[:3])})"
                return True, "ready"

        if time.time() >= deadline:
            return False, reason
        time.sleep(poll)


def wt_version() -> tuple[int, ...] | None:
    """Installed Windows Terminal version, or None if it can't be determined.

    `wt --version` does not cooperate with output redirection (verified —
    it prints nothing through a captured stdout), so this queries the
    installed package directly via PowerShell instead.
    """
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "$p = Get-AppxPackage -Name '*WindowsTerminal*' | Select-Object -First 1; "
             "if ($p) { $p.Version.ToString() }"],
            capture_output=True, text=True, timeout=10,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        return tuple(int(part) for part in out.split("."))
    except ValueError:
        return None


def check_wt_version() -> tuple[bool, str]:
    """One-shot version check — not part of wait_for_ready's retry loop,
    since an old Windows Terminal will not become new by waiting.

    The window-restore mechanism (`--pos=x,y`, `-w -1`) was verified only
    against 1.24.11911.0. On an older release these flags may be
    unrecognized, and the failure mode is untested: wt could error and open
    nothing, or silently route tabs into an existing window instead of a new
    one. This is a warning, not a block — refusing to run without a
    confirmed failure mode of our own would be guessing in the other
    direction.
    """
    version = wt_version()
    if version is None:
        return True, "could not determine Windows Terminal version"
    if version < MIN_WT_VERSION:
        got = ".".join(str(p) for p in version)
        want = ".".join(str(p) for p in MIN_WT_VERSION)
        return False, f"Windows Terminal {got} is older than the verified baseline {want}+"
    return True, "Windows Terminal " + ".".join(str(p) for p in version)
