"""The script typed into a hand-launched tab to upgrade it.

A session started by hand sits in a plain interactive shell. Its tab has no
restart loop, so /exit leaves a bare prompt where a session used to be.
`restart <repo>` puts the reloaded launcher into that shell instead - after
which the tab behaves like any reloaded-launched one: when its session ends it
waits for an Enter to start it again, and otherwise hands the shell back.

It cannot be typed directly: SendKeys reads { and ( as syntax, and the escaped
launcher is 838 keystrokes that did not arrive intact when tried. So it goes
into a file and one short line runs it. These tests run it for real.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from reloaded.deploy import relaunch_script

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


def test_the_script_never_stops_the_shell_that_called_it():
    """It used to stop its shell by pid once the session had run 10s, which
    closed the tab and lost its place in the window."""
    assert "Stop-Process" not in relaunch_script(CWD, 0)


@pytest.mark.parametrize("answers,runs", [
    ("", 1),      # end of input: hand the shell back
    ("\n", 2),    # Enter: the session again, then end of input
])
def test_the_shell_that_ran_it_is_handed_back(tmp_path, answers, runs):
    """The load-bearing one, run for real: the script returns to the shell
    that called it, which outlives it - and Enter starts the session again
    first."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    tally = tmp_path / "runs.txt"
    (stub_dir / "claude.cmd").write_text(f"@echo run>>{tally}\r\n", encoding="ascii")

    script = tmp_path / "relaunch.ps1"
    script.write_text(relaunch_script(CWD, 0), encoding="utf-8")

    proc = subprocess.run(
        [SHELL, "-NoProfile", "-Command",
         f"$env:PATH='{stub_dir}';& '{script}';'SHELL SURVIVED'"],
        input=answers, capture_output=True, text=True, timeout=90,
    )
    assert "SHELL SURVIVED" in proc.stdout, proc.stderr
    assert len(tally.read_text(encoding="ascii").split()) == runs
