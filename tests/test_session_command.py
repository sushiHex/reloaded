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
    def __init__(self, argv=None, raises=None, image=None, name_raises=None):
        self._argv = argv or []
        self._raises = raises
        self._image = image
        self._name_raises = name_raises

    def cmdline(self):
        if self._raises:
            raise self._raises
        return list(self._argv)

    def name(self):
        if self._name_raises:
            raise self._name_raises
        return self._image or ""


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


# ── session_launch: the kind and the command, from one process ───────────
#
# The promise is both or neither. A caller that gets a command without a kind
# fills the kind in from somewhere else - a cwd-keyed sweep, or a saved layout
# - and can end up pairing one process's command line with another's identity.
# Teardown chooses quit keystrokes from the kind while launcher_command chooses
# the invocation from the command, so that pair interrupts one CLI and
# relaunches the other.


def test_the_kind_and_the_command_come_back_together(monkeypatch):
    _stub(monkeypatch, _Proc([r"C:\x\codex.exe", "resume"], image="codex.exe"))

    assert discover_mod.session_launch(7) == ("codex", "codex resume")


def test_the_image_name_decides_the_kind(monkeypatch):
    _stub(monkeypatch, _Proc([r"C:\x\claude.exe", "--continue"],
                             image="claude.exe"))

    assert discover_mod.session_launch(7)[0] == "claude"


def test_an_unreadable_image_name_falls_back_to_the_command(monkeypatch):
    """The narrow gap a name-only lookup leaves: the cmdline reads but the
    image name does not. A command line that starts with `claude` is evidence
    about which agent it is, and using it keeps the both-or-neither promise."""
    _stub(monkeypatch, _Proc([r"C:\x\claude.exe", "--continue"],
                             name_raises=PermissionError("denied")))

    assert discover_mod.session_launch(7) == ("claude", "claude --continue")


def test_an_unreadable_cmdline_still_yields_the_kind(monkeypatch):
    """The other way round, and the common one on Windows: the image name is
    readable when the command line is not. The kind is worth keeping."""
    _stub(monkeypatch, _Proc(raises=PermissionError("denied"), image="codex.exe"))

    assert discover_mod.session_launch(7) == ("codex", "")


def test_a_process_that_cannot_be_opened_yields_neither(monkeypatch):
    _stub(monkeypatch, None)

    assert discover_mod.session_launch(7) == ("", "")


def test_an_unrelated_process_is_not_claimed_as_an_agent(monkeypatch):
    """A recycled pid now belonging to something else. Returning a kind for it
    would let a restart relaunch a stranger's command line."""
    _stub(monkeypatch, _Proc([r"C:\Windows\svchost.exe", "-k", "netsvcs"],
                             image="svchost.exe"))

    assert discover_mod.session_launch(7)[0] == ""


def test_a_command_never_arrives_without_a_kind(monkeypatch):
    """The invariant, stated directly. Every readable command line here also
    names its agent, so no caller has to source the two halves separately."""
    for proc in (
        _Proc([r"C:\x\codex.exe", "resume"], image="codex.exe"),
        _Proc([r"C:\x\claude.exe"], name_raises=OSError("x")),
        _Proc([r"C:\x\codex.exe"], image="CODEX.EXE"),
    ):
        _stub(monkeypatch, proc)
        kind, command = discover_mod.session_launch(7)
        assert not (command and not kind), (kind, command)
