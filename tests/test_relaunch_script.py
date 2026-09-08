"""The script typed into a hand-launched tab to upgrade it.

A session started by hand sits in a plain interactive shell. Its tab has no
restart loop and nothing that closes it, so /exit leaves a bare prompt where a
session used to be. `restart <repo>` puts the reloaded launcher into that shell
instead - after which the tab behaves like any reloaded-launched one.

It cannot be typed directly: SendKeys reads { and ( as syntax, and the escaped
launcher is 838 keystrokes that did not arrive intact when tried. So it goes
into a file and one short line runs it. That changes one thing - the launcher's
final `exit` runs in script scope, where it need not reach the host shell - so
the script closes the shell by pid instead. These tests run it for real.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from reloaded.deploy import STARTUP_GRACE_SECONDS, relaunch_script

SHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(SHELL is None, reason="no PowerShell on PATH")

CWD = r"C:\repos\stub-repo"


def test_the_script_carries_the_real_launcher(tmp_path):
    """Whatever else it does, a restarted hand-launched tab must end up running
    the same command a reloaded-launched one does."""
    text = relaunch_script(CWD, 0)
    assert "claude --dangerously-skip-permissions --continue" in text
    assert "while ($true)" in text, "the restart loop must come with it"
    assert "$env:CLAUDE_CODE_CHILD_SESSION=$null" in text


def test_the_script_does_not_rely_on_exit_reaching_the_host(tmp_path):
    """`exit` inside a called script ends the script, not necessarily the
    shell that called it - so the tab would linger where a reloaded tab
    closes."""
    assert "exit }" not in relaunch_script(CWD, 0)


def test_running_the_script_closes_the_shell_that_called_it(tmp_path):
    """The load-bearing one. A reloaded tab closes itself once its session has
    run long enough to have started properly; an upgraded hand-launched tab has
    to do the same or it accumulates dead tabs.

    Runs the real script in a real shell, with `claude` stubbed and the startup
    grace forced past, then asserts the shell process actually ended.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    (stub_dir / "claude.cmd").write_text("@exit /b 0\r\n", encoding="ascii")

    script = tmp_path / "relaunch.ps1"
    # Force the elapsed-time guard true without waiting for it: the guard's
    # own arithmetic is covered by test_restart_loop, this is about scope.
    body = relaunch_script(CWD, 0).replace(
        f"-gt {STARTUP_GRACE_SECONDS}", "-gt -1"
    )
    script.write_text(body, encoding="utf-8")

    proc = subprocess.run(
        [SHELL, "-NoProfile", "-NoExit", "-Command",
         f"$env:PATH='{stub_dir}';& '{script}';'SHELL SURVIVED'"],
        capture_output=True, text=True, timeout=90,
    )
    assert "SHELL SURVIVED" not in proc.stdout, (
        "the shell outlived the script - the tab would be left open at a prompt\n"
        f"stdout: {proc.stdout!r}"
    )
