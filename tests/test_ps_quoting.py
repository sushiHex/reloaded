"""A captured argument must reach the agent as itself, never as PowerShell.

Found by cross-review: `_ps_arg` doubled only the ASCII apostrophe, but
PowerShell reads four typographic quotes as one too. A U+2019 in an argument
closed the string, and the rest ran as commands - reproduced in PowerShell 7
and 5.1. A leading `@` was left bare, and PowerShell splatted it: `@session`
arrived as nothing. Run for real, with `claude` a function that reports what
it was given.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

import reloaded.agents as agents_mod
from reloaded.discover import _format_command

SHELLS = [s for s in (shutil.which("pwsh"), shutil.which("powershell")) if s]
pytestmark = pytest.mark.skipif(not SHELLS, reason="no PowerShell on PATH")

ARGS = [
    "id’; Write-Output INJECTED; #",
    "‘also‘; Write-Output INJECTED",
    "@session",
    "it's",
    "$(Write-Output INJECTED)",
]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("arg", ARGS)
def test_an_argument_arrives_as_itself(shell, arg):
    command = _format_command(["claude", "--resume", arg])
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              "function claude { foreach ($a in $args) { "
              "Write-Output ('ARG:' + (([int[]][char[]]$a) -join ',')) } }; " + command)

    out = subprocess.run([shell, "-NoProfile", "-Command", script],
                         capture_output=True, text=True, encoding="utf-8",
                         timeout=60).stdout

    assert "INJECTED" not in out
    received = ["".join(map(chr, map(int, line[4:].split(","))))
                for line in out.splitlines() if line.startswith("ARG:")]
    assert received == ["--resume", arg]


def test_the_tokenizer_reads_typographic_quotes_as_powershell_does():
    """`resuming` judges options by the tokens; a curly quote must open and
    close the same string PowerShell sees."""
    command = _format_command(["claude", "--append-system-prompt", "a’b c"])

    assert agents_mod.resuming("claude", command) == command + " --continue"
