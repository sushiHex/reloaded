"""Reading back the command line a live session is running.

Capture stores it on the tab so a session comes back with the flags it was
actually running, rather than with whatever this package would have chosen.

The string does not stay data. deploy.launcher_command splices it into a
PowerShell command, which is then typed into a terminal - so how it is quoted
is a correctness question, not a formatting one.
"""
from __future__ import annotations

import sys

import reloaded.discover as discover_mod


class _Proc:
    def __init__(self, argv=None, raises=None):
        self._argv = argv or []
        self._raises = raises

    def cmdline(self):
        if self._raises:
            raise self._raises
        return list(self._argv)


def _stub(monkeypatch, proc):
    class _Fake:
        @staticmethod
        def Process(pid):
            if proc is None:
                raise LookupError(pid)
            return proc

    monkeypatch.setitem(sys.modules, "psutil", _Fake)


def test_the_running_command_is_read_back(monkeypatch):
    _stub(monkeypatch, _Proc([
        r"C:\Users\k\bin\codex.exe", "resume", "--last",
        "--dangerously-bypass-approvals-and-sandbox",
    ]))

    assert discover_mod.session_command(7) == (
        "codex resume --last --dangerously-bypass-approvals-and-sandbox"
    )


def test_the_executable_is_reduced_to_its_bare_name(monkeypatch):
    """A captured absolute path would pin the saved layout to one install
    location. Every agent binary is on PATH by the time a deploy starts."""
    _stub(monkeypatch, _Proc([r"C:\Program Files\x\claude.exe", "--continue"]))

    assert discover_mod.session_command(7) == "claude --continue"


def test_an_argument_containing_a_space_is_quoted(monkeypatch):
    _stub(monkeypatch, _Proc([r"C:\x\codex.exe", "--profile", "my profile"]))

    assert discover_mod.session_command(7) == "codex --profile 'my profile'"


def test_a_plain_flag_is_left_unquoted(monkeypatch):
    """Quoting every argument would be safe and unreadable. A saved layout is
    something the user reads."""
    _stub(monkeypatch, _Proc([r"C:\x\codex.exe", "resume", "--last"]))

    assert discover_mod.session_command(7) == "codex resume --last"


def test_a_powershell_separator_cannot_split_the_command(monkeypatch):
    """Unquoted, `;` ends the invocation and whatever follows runs as its own
    PowerShell statement, inside the tab, at launch."""
    _stub(monkeypatch, _Proc([r"C:\x\codex.exe", "--flag", "a; Remove-Item x"]))

    assert discover_mod.session_command(7) == "codex --flag 'a; Remove-Item x'"


def test_a_subexpression_is_passed_rather_than_evaluated(monkeypatch):
    """Double quotes would not have been enough. PowerShell expands `$` inside
    them, so `$(...)` would have run at launch rather than been handed to the
    agent as text."""
    _stub(monkeypatch, _Proc([r"C:\x\codex.exe", "--p", "$(Get-Date)"]))

    out = discover_mod.session_command(7)
    assert out == "codex --p '$(Get-Date)'"
    assert '"' not in out


def test_an_embedded_quote_is_doubled_not_escaped(monkeypatch):
    """A backslash escape does nothing inside a PowerShell single-quoted
    string. Doubling the quote is what keeps the string closed."""
    _stub(monkeypatch, _Proc([r"C:\x\codex.exe", "--p", "it's here"]))

    assert discover_mod.session_command(7) == "codex --p 'it''s here'"


def test_an_executable_name_that_is_not_a_plain_word_yields_no_command(monkeypatch):
    """argv[0] is the command itself, so it cannot be quoted out of harm's
    way - quoting it makes PowerShell print the name instead of running it.
    The kind's default is the safer answer."""
    _stub(monkeypatch, _Proc([r"C:\x\co dex;evil.exe", "--last"]))

    assert discover_mod.session_command(7) == ""


def test_an_unreadable_process_yields_no_command(monkeypatch):
    """Falls back to the kind's default rather than failing the capture. A
    capture that dropped a tab over one unreadable cmdline would be worse than
    one that relaunches it with default flags."""
    _stub(monkeypatch, _Proc(raises=PermissionError("denied")))

    assert discover_mod.session_command(7) == ""


def test_a_vanished_process_yields_no_command(monkeypatch):
    _stub(monkeypatch, None)

    assert discover_mod.session_command(7) == ""


def test_an_empty_cmdline_yields_no_command(monkeypatch):
    _stub(monkeypatch, _Proc([]))

    assert discover_mod.session_command(7) == ""
