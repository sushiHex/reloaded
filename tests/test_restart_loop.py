"""The launcher's relaunch loop, exercised as PowerShell rather than as text.

Every other test of `launcher_command` asserts on the string. That is enough
for a flat list of statements. This is the first construct in it with control
flow, and a string match cannot tell a loop that runs twice from one that never
stops, nor a guard that fires from one that is merely present. So these run the
real command in a real shell, with a stub on PATH standing in for `claude`.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

import reloaded.deploy as deploy_mod
from reloaded.deploy import (
    RESTART_MARKER_TTL_SECONDS,
    launcher_command,
    new_tab_args,
)

SHELL = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(SHELL is None, reason="no PowerShell on PATH")

CWD = r"C:\repos\stub-repo"


@pytest.fixture
def run_launcher(monkeypatch, tmp_path):
    """Run the real launcher command with `claude` stubbed.

    The stub appends a line per invocation, so the return value is the number
    of times the loop actually launched a session.
    """
    marker = tmp_path / "restart.marker"
    monkeypatch.setattr(deploy_mod, "restart_marker", lambda cwd: marker)

    tally = tmp_path / "runs.txt"
    stub = tmp_path / "claude.cmd"
    # A .cmd on PATH is what `claude` resolves to for a shell, and it needs no
    # interpreter of its own. `>>` appends, so the tally counts every launch.
    stub.write_text(f"@echo run>>{tally}\r\n", encoding="ascii")
    monkeypatch.setenv("PATH", str(tmp_path) + ";" + str(pathlib.Path().cwd()))

    def _run(*, marker_body: str | None = None, marker_age_seconds: float = 0.0,
             through_wt_escaping: bool = False, load_profile: bool = False):
        if tally.exists():
            tally.unlink()
        if marker_body is not None:
            marker.write_text(marker_body, encoding="utf-8")
            if marker_age_seconds:
                import os
                import time

                old = time.time() - marker_age_seconds
                os.utime(marker, (old, old))
        if through_wt_escaping:
            # The exact string wt is handed, put back through wt's documented
            # unescape. Production never passes launcher_command() to a shell
            # directly - it goes through _wt_escape first - so a test that
            # skips that boundary proves PowerShell parsing and nothing about
            # what PowerShell actually receives.
            cmd = new_tab_args(CWD, launcher_command(CWD, 0, 0))[-1].replace("\\;", ";")
        else:
            cmd = launcher_command(CWD, 0, 0)
        argv = [SHELL] + ([] if load_profile else ["-NoProfile"]) + ["-Command", cmd]
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
        )
        runs = len(tally.read_text(encoding="ascii").split()) if tally.exists() else 0
        return runs, marker.exists(), proc

    return _run


def test_without_a_marker_the_session_launches_once(run_launcher):
    runs, marker_left, proc = run_launcher()
    assert runs == 1, proc.stderr
    assert not marker_left


def test_a_fresh_marker_relaunches_the_session_in_place(run_launcher):
    """The whole point: the tab does not close and reopen, it runs claude
    again inside the shell it already had."""
    runs, marker_left, proc = run_launcher(marker_body="restart")
    assert runs == 2, proc.stderr


def test_the_marker_is_consumed_so_the_next_exit_is_final(run_launcher):
    """Left behind, one restart request would relaunch the session forever."""
    _runs, marker_left, proc = run_launcher(marker_body="restart")
    assert not marker_left, proc.stderr


def test_no_loop_semicolon_reaches_wt_unescaped(run_launcher):
    """wt treats `;` as a subcommand separator inside an argument. The loop
    added ten semicolons to a command that had four; one left raw tears the
    launcher into fragments and the tab runs something else entirely.

    Asserted on the argument itself rather than by round-tripping it: escaping
    and then unescaping proves nothing, because unescaping a string that was
    never escaped returns it unchanged. Verified by mutation — with _wt_escape
    stubbed to `return s`, a round-trip version of this test still passed.
    """
    arg = new_tab_args(CWD, launcher_command(CWD, 0, 0))[-1]
    raw = [i for i, ch in enumerate(arg) if ch == ";" and (i == 0 or arg[i - 1] != "\\")]
    assert raw == [], f"{len(raw)} unescaped semicolon(s) in the wt argument"


def test_the_loop_still_runs_after_wt_unescapes_it(run_launcher):
    """The other half: escaping must also be reversible without corrupting the
    PowerShell. This runs what wt hands the shell, not what we handed wt."""
    runs, _left, proc = run_launcher(marker_body="restart", through_wt_escaping=True)
    assert runs == 2, proc.stderr


def test_the_loop_still_works_with_the_real_profile_loaded(run_launcher):
    """Production does NOT pass -NoProfile, and PowerShell resolves an
    unqualified name to an alias or function before an executable. A profile
    that defined `claude` would shadow the real one - and a `break` inside such
    a function can bind to this loop and skip marker consumption entirely."""
    runs, _left, proc = run_launcher(marker_body="restart", load_profile=True)
    assert runs == 2, proc.stderr or proc.stdout


def test_a_marker_that_cannot_be_deleted_does_not_spin_forever(run_launcher, tmp_path):
    """The loop's only exits are `break`s, and one of them is reached only
    after the marker is gone. `Remove-Item` runs with -ErrorAction
    SilentlyContinue, so a failure to delete is invisible: the marker is still
    there on the next pass, still fresh, and the tab relaunches claude in a
    tight loop forever.

    Reproduced with a non-empty directory at the marker path, which
    `Remove-Item -Force` cannot remove without -Recurse. A locked or
    ACL-protected file does the same thing.
    """
    marker = tmp_path / "restart.marker"
    marker.mkdir()
    (marker / "occupied.txt").write_text("x", encoding="ascii")

    runs, _left, proc = run_launcher()

    assert runs <= 2, f"relaunched {runs} times on an undeletable marker"


def test_a_stale_marker_is_discarded_rather_than_obeyed(run_launcher):
    """A restart that never reached its tab leaves a marker. Hours later the
    user exits that session by hand and must not have it come back."""
    age = RESTART_MARKER_TTL_SECONDS + 60
    runs, marker_left, proc = run_launcher(marker_body="restart", marker_age_seconds=age)
    assert runs == 1, proc.stderr
    assert not marker_left, "a stale marker must still be cleaned up"
