"""The launcher's relaunch loop, exercised as PowerShell rather than as text.

Every other test of `launcher_command` asserts on the string. That is enough
for a flat list of statements. This is the first construct in it with control
flow, and a string match cannot tell a loop that runs twice from one that never
stops, nor a guard that fires from one that is merely present. So these run the
real command in a real shell, with a stub on PATH standing in for `claude`.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

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


def _refuse_a_shadowed_claude(tmp_path) -> None:
    """Skip rather than launch the real agent, if the profile shadows the stub.

    The stub works by owning PATH, and that is enough for an executable
    lookup. It is not enough for a function or an alias: PowerShell resolves
    those FIRST, so a profile defining `claude` would run the real one - with
    `--dangerously-skip-permissions --continue`, in a temp directory, for real.

    Nothing on this machine does that today. This exists so that the day
    someone adds it, the test says so instead of quietly starting an agent.
    """
    probe = subprocess.run(
        [SHELL, "-Command",
         "$c = Get-Command claude -ErrorAction SilentlyContinue; "
         "if ($c) { \"$($c.CommandType)|$($c.Source)\" } else { 'none|' }"],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
        env={**os.environ, "PATH": str(tmp_path)},
    )
    kind, _, source = probe.stdout.strip().partition("|")
    if kind != "Application" or pathlib.Path(source).parent != tmp_path:
        pytest.skip(
            f"the PowerShell profile resolves `claude` to a {kind} ({source!r}) "
            "rather than this test's stub - running it would launch the real "
            "agent"
        )


@pytest.fixture
def run_launcher(monkeypatch, tmp_path):
    """Run the real launcher command with `claude` stubbed.

    The stub appends a line per invocation, so the return value is the number
    of times the loop actually launched a session.
    """
    marker = tmp_path / "restart.marker"
    monkeypatch.setattr(deploy_mod, "restart_marker", lambda cwd: marker)
    # Where the loop looks for this directory's conversations: none, unless a
    # test files one.
    monkeypatch.setattr(deploy_mod.agents_mod, "conversations_dir",
                        lambda kind, cwd: str(tmp_path / "conversations"))

    tally = tmp_path / "runs.txt"
    stub = tmp_path / "claude.cmd"
    # A .cmd on PATH is what `claude` resolves to for a shell, and it needs no
    # interpreter of its own. `>>` appends, so the tally counts every launch.
    stub.write_text(f"@echo run>>{tally}\r\n", encoding="ascii")
    monkeypatch.setenv("PATH", str(tmp_path) + ";" + str(pathlib.Path().cwd()))

    def _run(*, marker_body: str | None = None, marker_age_seconds: float = 0.0,
             through_wt_escaping: bool = False, load_profile: bool = False,
             answers: str = "", after: str = ""):
        if tally.exists():
            tally.unlink()
        if marker_body is not None:
            marker.write_text(marker_body, encoding="utf-8")
            if marker_age_seconds:
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
        if load_profile:
            _refuse_a_shadowed_claude(tmp_path)
        argv = [SHELL] + ([] if load_profile else ["-NoProfile"]) + ["-Command", cmd + after]
        # The tab's answers when its session ends (deploy.ended_prompt); end
        # of input hands the shell back, so the launcher finishes.
        proc = subprocess.run(
            argv, input=answers, capture_output=True, text=True, timeout=60,
            cwd=str(tmp_path),
        )
        runs = len(tally.read_text(encoding="ascii").split()) if tally.exists() else 0
        return runs, marker.exists(), proc

    return _run


def test_without_a_marker_the_session_launches_once(run_launcher):
    runs, marker_left, proc = run_launcher()
    assert runs == 1, proc.stderr
    assert not marker_left


def test_enter_starts_the_ended_session_again_in_its_tab(run_launcher):
    """Quitting to restart is common; the tab waits instead of closing."""
    runs, _marker_left, proc = run_launcher(answers="\n\n")
    assert runs == 3, proc.stderr
    assert "ended. Enter starts it again" in proc.stdout


def test_anything_typed_runs_and_hands_the_shell_back(run_launcher, tmp_path):
    """Its prompt is the shell's: relaunch's helper types a resume command
    there when no loop took its marker, and a teardown resend's `/exit` lands
    there as text - neither may start the session again."""
    runs, _m, proc = run_launcher(answers="'typed-ran'\n", after="; 'AFTER'")
    assert runs == 1, proc.stderr
    assert "typed-ran" in proc.stdout and "AFTER" in proc.stdout


def test_down_s_stop_file_closes_the_tab(run_launcher, tmp_path, monkeypatch):
    """`down` leaves a stop file for the shell before quitting its session,
    and the tab closes as tabs always did - an Enter cannot start the session
    again under the teardown that is closing it. A PowerShell stub runs in
    the loop's own process, so its `$PID` is the loop shell's."""
    monkeypatch.setattr(deploy_mod, "restart_marker_dir", lambda: tmp_path)
    tally = tmp_path / "runs.txt"
    (tmp_path / "claude.cmd").unlink()
    (tmp_path / "claude.ps1").write_text(
        f"Add-Content -LiteralPath '{tally}' 'run'\n"
        f"Set-Content -LiteralPath ('{tmp_path}\\shell-' + $PID + '.stop') 'stop'\n",
        encoding="utf-8")

    runs, _m, proc = run_launcher(answers="\n", after="; 'AFTER'")

    assert runs == 1, proc.stderr
    assert "AFTER" not in proc.stdout, "the tab waited instead of closing"
    assert not list(tmp_path.glob("shell-*.stop")), "a stop file is read once"


def test_an_old_stop_file_is_not_obeyed(run_launcher, tmp_path, monkeypatch):
    """A holdout `down` gave up on keeps its tab when the user quits it later."""
    monkeypatch.setattr(deploy_mod, "restart_marker_dir", lambda: tmp_path)
    (tmp_path / "claude.cmd").unlink()
    (tmp_path / "claude.ps1").write_text(
        f"$s = '{tmp_path}\\shell-' + $PID + '.stop'; Set-Content -LiteralPath $s 'stop'; "
        f"(Get-Item -LiteralPath $s).LastWriteTime = (Get-Date).AddMinutes(-10)\n",
        encoding="utf-8")

    _runs, _m, proc = run_launcher(after="; 'AFTER'")

    assert "AFTER" in proc.stdout, proc.stderr


def test_enter_brings_back_the_command_that_last_ran(run_launcher, tmp_path):
    """After `restart --self` ran its exact resume command, Enter must bring
    that conversation back, not whatever the tab was first launched with."""
    tally = tmp_path / "runs.txt"
    (tmp_path / "resume-exact.cmd").write_text(f"@echo exact>>{tally}\r\n",
                                               encoding="ascii")

    run_launcher(marker_body="resume-exact --resume 0673eda5", answers="\n")

    assert tally.read_text(encoding="ascii").split() == ["run", "exact", "exact"]


def test_a_fresh_marker_relaunches_the_session_in_place(run_launcher):
    """The whole point: the tab does not close and reopen, it runs claude
    again inside the shell it already had."""
    runs, marker_left, proc = run_launcher(marker_body="restart")
    assert runs == 2, proc.stderr


def test_a_marker_holding_a_command_runs_that_command_once(run_launcher, tmp_path):
    """`restart --self` writes its exact resume command into the marker. The
    tab's own invocation is how it was first launched - replayed, it brought
    back the directory's latest conversation, an empty one, or Claude Code's
    picker - so the restart runs the command instead, and only that once."""
    tally = tmp_path / "runs.txt"
    (tmp_path / "resume-exact.cmd").write_text(f"@echo exact>>{tally}\r\n",
                                               encoding="ascii")

    runs, marker_left, proc = run_launcher(
        marker_body="resume-exact --resume 0673eda5")

    assert tally.read_text(encoding="ascii").split() == ["run", "exact"], proc.stderr
    assert not marker_left


def test_a_shells_own_marker_is_taken_before_the_directorys(run_launcher, tmp_path,
                                                           monkeypatch):
    """A directory's marker is watched by every tab there, so in one holding
    a Claude and a Codex tab, whichever exited first took the other's resume
    command. The shell's own marker has one reader. A PowerShell stub runs in
    the loop's own process, so its `$PID` is the loop shell's: it files a
    request addressed to that shell, beside a directory request, and exits."""
    monkeypatch.setattr(deploy_mod, "restart_marker_dir", lambda: tmp_path)
    tally = tmp_path / "runs.txt"
    (tmp_path / "claude.cmd").unlink()
    (tmp_path / "claude.ps1").write_text(
        f"Add-Content -LiteralPath '{tally}' 'run'\n"
        f"if (-not (Test-Path '{tmp_path}\\filed')) {{\n"
        f"  New-Item '{tmp_path}\\filed' | Out-Null\n"
        f"  Set-Content -LiteralPath ('{tmp_path}\\shell-' + $PID + '.marker') 'own-request'\n"
        f"}}\n", encoding="utf-8")
    (tmp_path / "own-request.cmd").write_text(f"@echo own>>{tally}\r\n", encoding="ascii")

    _runs, marker_left, proc = run_launcher(marker_body="restart")

    lines = tally.read_text(encoding="utf-8", errors="replace").split()
    # Own request first (its command), then the directory's (the invocation).
    assert lines == ["run", "own", "run"], (lines, proc.stderr)
    assert not marker_left
    assert not list(tmp_path.glob("shell-*.marker"))


WINDOWS_POWERSHELL = shutil.which("powershell")  # before any fixture narrows PATH


@pytest.mark.skipif(WINDOWS_POWERSHELL is None, reason="no Windows PowerShell 5.1")
def test_a_marker_command_is_read_as_utf8(run_launcher, tmp_path, monkeypatch):
    """Python writes the marker as BOM-less UTF-8, and Windows PowerShell 5.1 -
    the fallback shell - reads such a file in the ANSI code page unless told
    otherwise, mangling a non-ASCII path or name. Run under 5.1 because pwsh 7
    defaults to UTF-8 and would pass either way. What the shell read is
    written back by PowerShell itself, so no console re-encoding intervenes."""
    monkeypatch.setattr(sys.modules[__name__], "SHELL", WINDOWS_POWERSHELL)
    out = tmp_path / "read.txt"

    run_launcher(marker_body=(f"Set-Content -LiteralPath '{out}' "
                              "-Value 'café' -Encoding UTF8"))

    assert out.read_text(encoding="utf-8-sig").strip() == "café"


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


def _claude_without_a_conversation(tmp_path):
    """A stub `claude` in a directory with no conversation yet. `--continue`
    exits 1 there, as the real one does (measured, 2.1.296), until a run
    without it has started one. Returns its command lines, one per run.

    It always exits explicitly, as an executable does: a script that does not
    leaves `$LASTEXITCODE` at whatever ran before it."""
    tally = tmp_path / "runs.txt"
    started = tmp_path / "started"
    (tmp_path / "claude.cmd").unlink()
    (tmp_path / "claude.ps1").write_text(
        f"Add-Content -LiteralPath '{tally}' ($args -join ' ')\n"
        f"if ($args -notcontains '--continue') {{ New-Item -Force '{started}' | Out-Null }}\n"
        f"elseif (-not (Test-Path '{started}')) {{ exit 1 }}\n"
        "exit 0\n", encoding="utf-8")
    return lambda: tally.read_text(encoding="utf-8").splitlines()


def test_a_directory_with_no_conversation_starts_one(run_launcher, tmp_path):
    """#59: a repo first opened through Reloaded never started - its tab sat
    at "No conversation found to continue". It starts a new one instead."""
    lines = _claude_without_a_conversation(tmp_path)

    _runs, _left, proc = run_launcher()

    assert lines() == ["--dangerously-skip-permissions --continue",
                       "--dangerously-skip-permissions"], proc.stderr


def test_it_starts_one_however_long_the_failed_resume_took(run_launcher, tmp_path,
                                                         monkeypatch):
    """In a directory not yet trusted, Claude Code asks first and looks for a
    conversation only once answered: exit 1 at 35s, measured. A brand-new
    repo opens that way. With no grace at all, every run is past it."""
    monkeypatch.setattr(deploy_mod, "STARTUP_GRACE_SECONDS", 0)
    lines = _claude_without_a_conversation(tmp_path)

    _runs, _left, proc = run_launcher()

    assert lines() == ["--dangerously-skip-permissions --continue",
                       "--dangerously-skip-permissions"], proc.stderr


def test_where_a_conversation_exists_a_failed_resume_is_left_on_screen(run_launcher,
                                                                      tmp_path):
    """A quick exit 1 there is a resume that failed some other way. A new
    conversation would become the latest, the one every restart continues."""
    lines = _claude_without_a_conversation(tmp_path)
    (tmp_path / "conversations").mkdir()
    (tmp_path / "conversations" / "0673eda5.jsonl").write_text("{}\n", encoding="utf-8")

    _runs, _left, proc = run_launcher()

    assert lines() == ["--dangerously-skip-permissions --continue"], proc.stderr


def test_a_restart_after_that_resumes_what_was_started(run_launcher, tmp_path):
    """Decided in the tab on every pass, not once at launch: a tab launched
    without `--continue` would come back empty from every restart after."""
    lines = _claude_without_a_conversation(tmp_path)

    _runs, _left, proc = run_launcher(marker_body="restart")

    assert lines() == ["--dangerously-skip-permissions --continue",
                       "--dangerously-skip-permissions",
                       "--dangerously-skip-permissions --continue"], proc.stderr
