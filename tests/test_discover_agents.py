"""Discovery sees both kinds, and excludes the desktop-app Codex.

A second codex.exe runs as a child of the Codex desktop app. It is not a
terminal tab, and relaunching it into one would be wrong - so it is excluded
by its parent process rather than by guessing from its working directory.
"""
from __future__ import annotations

import sys

import reloaded.discover as discover_mod
from reloaded.paths import norm

REPOS = r"C:\Users\k\repos"


class _Proc:
    def __init__(self, pid, name, cwd, parent_name="pwsh.exe", ppid=900):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "ppid": ppid}
        self._cwd = cwd
        self._parent_name = parent_name

    def cwd(self):
        if self._cwd is None:
            raise PermissionError("denied")
        return self._cwd

    def name(self):
        return self.info["name"]


def _stub(monkeypatch, procs):
    parents = {}
    for p in procs:
        parents[p.info["ppid"]] = _Proc(p.info["ppid"], p._parent_name, None)

    class _Fake:
        @staticmethod
        def process_iter(fields=None):
            return list(procs)

        @staticmethod
        def Process(pid):
            if pid in parents:
                return parents[pid]
            raise LookupError(pid)

    monkeypatch.setitem(sys.modules, "psutil", _Fake)


def test_a_claude_session_is_found(monkeypatch):
    _stub(monkeypatch, [_Proc(1, "claude.exe", REPOS + r"\alpha")])

    assert discover_mod.live_sessions() == {norm(REPOS + r"\alpha"): 1}


def test_a_codex_session_is_found(monkeypatch):
    """The gap this closes: a Codex tab was invisible to every command."""
    _stub(monkeypatch, [_Proc(2, "codex.exe", REPOS + r"\beta")])

    assert discover_mod.live_sessions() == {norm(REPOS + r"\beta"): 2}


def test_both_kinds_are_reported_together(monkeypatch):
    _stub(monkeypatch, [
        _Proc(1, "claude.exe", REPOS + r"\alpha", ppid=901),
        _Proc(2, "codex.exe", REPOS + r"\beta", ppid=902),
    ])

    assert discover_mod.live_agents() == {
        norm(REPOS + r"\alpha"): "claude",
        norm(REPOS + r"\beta"): "codex",
    }


def test_the_desktop_app_codex_is_excluded(monkeypatch):
    """It is not a terminal tab. Relaunching it into one would be wrong."""
    _stub(monkeypatch, [
        _Proc(3, "codex.exe",
              r"C:\Program Files\WindowsApps\OpenAI.Codex\app",
              parent_name="ChatGPT.exe", ppid=903),
    ])

    assert discover_mod.live_sessions() == {}
    assert discover_mod.live_agents() == {}


def test_a_terminal_codex_beside_a_desktop_one_is_still_found(monkeypatch):
    """Both run at once on this machine. Excluding by image name would lose
    the real tab along with the desktop app."""
    _stub(monkeypatch, [
        _Proc(3, "codex.exe", r"C:\Program Files\WindowsApps\OpenAI.Codex\app",
              parent_name="ChatGPT.exe", ppid=903),
        _Proc(4, "codex.exe", REPOS + r"\beta", ppid=904),
    ])

    assert discover_mod.live_sessions() == {norm(REPOS + r"\beta"): 4}


def test_an_unreadable_parent_does_not_drop_the_session(monkeypatch):
    """A session that is otherwise a normal match should not vanish because
    its parent could not be inspected."""
    class _Fake:
        @staticmethod
        def process_iter(fields=None):
            return [_Proc(5, "codex.exe", REPOS + r"\gamma")]

        @staticmethod
        def Process(pid):
            raise PermissionError("denied")

    monkeypatch.setitem(sys.modules, "psutil", _Fake)

    assert discover_mod.live_sessions() == {norm(REPOS + r"\gamma"): 5}


def test_a_process_with_no_readable_cwd_is_skipped(monkeypatch):
    _stub(monkeypatch, [_Proc(6, "codex.exe", None)])

    assert discover_mod.live_sessions() == {}


def test_an_unrelated_process_is_ignored(monkeypatch):
    _stub(monkeypatch, [_Proc(7, "notepad.exe", REPOS + r"\delta")])

    assert discover_mod.live_sessions() == {}


def test_live_sessions_still_returns_plain_pids(monkeypatch):
    """Nine call sites depend on dict[str, int]; the kind travels separately
    rather than changing this signature."""
    _stub(monkeypatch, [_Proc(8, "codex.exe", REPOS + r"\eps")])

    assert all(isinstance(v, int) for v in discover_mod.live_sessions().values())
