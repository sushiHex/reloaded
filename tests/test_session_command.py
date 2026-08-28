"""Reading back the command line a live session is running.

Capture stores it on the tab so a session comes back with the flags it was
actually running, rather than with whatever this package would have chosen.
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

    assert discover_mod.session_command(7) == 'codex --profile "my profile"'


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
