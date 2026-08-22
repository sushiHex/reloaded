"""Telling a hand-launched session from a reloaded-launched one.

They need opposite handling on restart. A reloaded tab's shell runs the
launcher, so /exit either relaunches in place (new tabs) or closes the tab
(older ones). A hand-launched tab's shell is a plain interactive prompt: /exit
leaves it sitting there with no session and nothing to close it, so the
launcher has to be put into that shell instead.

The two are distinguishable from the parent shell's own command line - the
launcher is right there in it.
"""
from __future__ import annotations

import reloaded.discover as discover_mod
from reloaded.discover import HAND, RELOADED, launcher_kind

RELOADED_CMDLINE = [
    "pwsh", "-NoExit", "-Command",
    "$env:CLAUDE_CODE_CHILD_SESSION=$null; $env:CLAUDE_CODE_RESUME_TOKEN_THRESHOLD"
    "='999999999'; Write-Host '[reloaded] retro' -ForegroundColor DarkGray; "
    "$rlStart=Get-Date; claude --dangerously-skip-permissions --continue; "
    "if (((Get-Date)-$rlStart).TotalSeconds -gt 10) { exit }",
]
HAND_CMDLINE = [r"C:\Program Files\PowerShell\7\pwsh.exe"]


class _Proc:
    def __init__(self, ppid, cmdline=None, raises=None):
        self._ppid = ppid
        self._cmdline = cmdline or []
        self._raises = raises

    def ppid(self):
        return self._ppid

    def cmdline(self):
        if self._raises:
            raise self._raises
        return list(self._cmdline)


def _stub(monkeypatch, parent):
    """psutil.Process(pid) -> the session, whose parent is `parent`."""
    procs = {999: _Proc(ppid=1), 1: parent}

    class _Fake:
        Error = Exception
        NoSuchProcess = Exception
        AccessDenied = Exception

        @staticmethod
        def Process(pid):
            if pid not in procs:
                raise LookupError(pid)
            return procs[pid]

    monkeypatch.setattr(discover_mod, "_psutil", _Fake, raising=False)
    return _Fake


def test_a_shell_running_the_launcher_is_reloaded_launched(monkeypatch):
    _stub(monkeypatch, _Proc(ppid=0, cmdline=RELOADED_CMDLINE))
    assert launcher_kind(999) == RELOADED


def test_a_plain_interactive_shell_is_hand_launched(monkeypatch):
    _stub(monkeypatch, _Proc(ppid=0, cmdline=HAND_CMDLINE))
    assert launcher_kind(999) == HAND


def test_an_unreadable_parent_is_treated_as_hand_launched(monkeypatch):
    """The safe default. Assuming `reloaded` means restart waits for a
    relaunch that no loop will ever perform, then declares failure. Assuming
    `hand` means it types the launcher into a shell - which a reloaded tab,
    having closed itself, will not be around to receive."""
    _stub(monkeypatch, _Proc(ppid=0, raises=PermissionError("denied")))
    assert launcher_kind(999) == HAND


def test_a_vanished_process_is_treated_as_hand_launched(monkeypatch):
    _stub(monkeypatch, _Proc(ppid=0, cmdline=HAND_CMDLINE))
    assert launcher_kind(12345) == HAND


def test_the_marker_is_the_launcher_variable_not_the_word_reloaded(monkeypatch):
    """A repo literally named `reloaded` puts that word in every launcher's
    Write-Host banner AND in a hand-launched shell's window title. Matching on
    it would classify by repo name."""
    _stub(monkeypatch, _Proc(
        ppid=0,
        cmdline=[r"C:\Program Files\PowerShell\7\pwsh.exe", "-WorkingDirectory",
                 r"C:\Users\kfaim\repos\reloaded"],
    ))
    assert launcher_kind(999) == HAND
