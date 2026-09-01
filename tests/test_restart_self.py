"""`restart --self`: the one restart a session can ask for on its own behalf.

Every other path drives a session from outside it - select its tab, type the
quit keys, wait for it to come back. None of that works on yourself. The
command would type into its own tab and then die with the session it just
ended, before it could watch for the return or report anything.

So it does not try. The tab's shell already runs its agent inside a loop that
checks for a marker each time the agent exits. "Restart me" is: leave the
marker, get out of the way, and let the user quit cleanly through the session's
own UI.

That makes the refusals as important as the arming. A marker left where no loop
will read it is a file on disk that nobody collects, and a command that says
"armed" about it has lied.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
import reloaded.discover as discover_mod

CWD = r"C:\repos\app"


def _args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "unattended": False,
         "dry_run": False, "repos": [], "self_": True, "cancel": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


@pytest.fixture
def session(monkeypatch, tmp_path):
    """A reloaded-launched Claude session above this process, unless a test
    says otherwise."""
    state = {"found": (111, CWD, "claude"),
             "launcher": discover_mod.RELOADED,
             "marker": tmp_path / "m.marker"}

    monkeypatch.setattr(discover_mod, "owning_session", lambda: state["found"])
    monkeypatch.setattr(discover_mod, "launcher_kind",
                        lambda pid: state["launcher"])
    monkeypatch.setattr(main_mod, "restart_marker", lambda cwd: state["marker"])
    return state


def test_it_arms_the_session_it_is_called_from(session, capsys):
    rc = main_mod.cmd_restart(_args())

    assert rc == 0
    assert session["marker"].read_text(encoding="utf-8") == "restart"


def test_it_says_how_to_finish_the_job(session, capsys):
    """Arming alone does nothing visible. A command that returns 0 and changes
    nothing on screen reads as "done"."""
    main_mod.cmd_restart(_args())

    out = capsys.readouterr().out
    assert "/exit" in out, "it never says how to quit"
    assert "2 minutes" in out, "it never says the marker expires"


def test_a_codex_session_is_told_to_use_its_own_quit_keys(session, capsys):
    """`/exit` does nothing in Codex. Printing it would send the user off to
    type something that cannot work."""
    session["found"] = (111, CWD, "codex")

    main_mod.cmd_restart(_args())

    out = capsys.readouterr().out
    assert "Ctrl+C" in out
    assert "/exit" not in out


def test_outside_a_session_it_refuses_and_says_what_to_run(capsys, monkeypatch):
    monkeypatch.setattr(discover_mod, "owning_session", lambda: None)

    rc = main_mod.cmd_restart(_args())

    assert rc == 1
    assert "restart <repo>" in capsys.readouterr().out


def test_a_hand_launched_session_is_refused(session, capsys):
    """Its shell is a plain prompt with no loop in it. Arming would leave a
    file nobody collects, and report success for a restart that cannot
    happen."""
    session["launcher"] = discover_mod.HAND

    rc = main_mod.cmd_restart(_args())

    assert rc == 1
    assert not session["marker"].exists()
    out = capsys.readouterr().out
    assert "started by hand" in out
    assert "restart <repo>" in out, "no way out is offered"


def test_a_session_whose_directory_cannot_be_read_is_refused(session, capsys):
    """The marker is addressed by a hash of the cwd. Without one there is no
    file to write, and guessing at the directory would arm someone else."""
    session["found"] = (111, "", "claude")

    rc = main_mod.cmd_restart(_args())

    assert rc == 1
    assert not session["marker"].exists()


def test_the_dry_run_writes_nothing(session, capsys):
    rc = main_mod.cmd_restart(_args(dry_run=True))

    assert rc == 0
    assert not session["marker"].exists()
    assert "Dry run" in capsys.readouterr().out


def test_cancel_removes_the_marker(session, capsys):
    """Safe here and almost nowhere else: the session asking is still running,
    so nothing is about to read it."""
    session["marker"].write_text("restart", encoding="utf-8")

    rc = main_mod.cmd_restart(_args(cancel=True))

    assert rc == 0
    assert not session["marker"].exists()
    assert "Disarmed" in capsys.readouterr().out


def test_cancel_with_nothing_armed_says_so(session, capsys):
    rc = main_mod.cmd_restart(_args(cancel=True))

    assert rc == 0
    assert "Nothing was armed" in capsys.readouterr().out


def test_cancel_does_not_check_the_launcher_kind(session):
    """A hand-launched session cannot be armed, but it can have been armed
    before it was one - by an older build, or by a restart that upgraded the
    tab. Refusing to clean up would leave the file forever."""
    session["launcher"] = discover_mod.HAND
    session["marker"].write_text("restart", encoding="utf-8")

    assert main_mod.cmd_restart(_args(cancel=True)) == 0
    assert not session["marker"].exists()


def test_a_marker_that_cannot_be_written_is_reported(session, capsys, monkeypatch):
    class _Unwritable:
        def exists(self):
            return False

        def write_text(self, *a, **k):
            raise OSError("read-only")

    monkeypatch.setattr(main_mod, "restart_marker", lambda cwd: _Unwritable())

    rc = main_mod.cmd_restart(_args())

    assert rc == 1
    assert "could not arm" in capsys.readouterr().out


def test_naming_a_repo_still_takes_the_normal_path(session, monkeypatch):
    """`--self` is a different command wearing the same verb. It must not
    swallow the one people already use."""
    called = []
    monkeypatch.setattr(main_mod, "cmd_restart_one",
                        lambda args, repos: called.append(repos) or 0)

    main_mod.cmd_restart(_args(self_=False, repos=["app"]))

    assert called == [["app"]]


# ── finding the session you are inside ───────────────────────────────────


class _Proc:
    def __init__(self, pid, image, cwd=CWD, parent=None, cwd_raises=None):
        self.pid = pid
        self._image = image
        self._cwd = cwd
        self._parent = parent
        self._cwd_raises = cwd_raises

    def name(self):
        return self._image

    def cwd(self):
        if self._cwd_raises:
            raise self._cwd_raises
        return self._cwd

    def parent(self):
        return self._parent


def _chain(monkeypatch, leaf):
    fake = types.SimpleNamespace(Process=lambda pid: leaf)
    monkeypatch.setattr(discover_mod, "_ps", lambda: fake)


def test_the_agent_above_this_process_is_found(monkeypatch):
    """Walking up, not matching directories. A shell a session spawns can be
    anywhere - a subdirectory, a sibling, somewhere else entirely - and the
    parent chain is the only thing that says "I am inside this one"."""
    agent = _Proc(111, "claude.exe", parent=_Proc(1, "WindowsTerminal.exe"))
    shell = _Proc(222, "pwsh.exe", parent=agent)
    _chain(monkeypatch, _Proc(333, "python.exe", parent=shell))

    assert discover_mod.owning_session() == (111, CWD, "claude")


def test_codex_is_recognised_too(monkeypatch):
    agent = _Proc(111, "codex.exe", parent=None)
    _chain(monkeypatch, _Proc(222, "pwsh.exe", parent=agent))

    assert discover_mod.owning_session() == (111, CWD, "codex")


def test_the_image_name_is_matched_case_insensitively(monkeypatch):
    agent = _Proc(111, "CLAUDE.EXE", parent=None)
    _chain(monkeypatch, _Proc(222, "pwsh.exe", parent=agent))

    assert discover_mod.owning_session()[2] == "claude"


def test_no_agent_above_means_none(monkeypatch):
    """Run from an ordinary terminal. Saying "not inside a session" is the
    truthful answer; picking the nearest live session would not be."""
    _chain(monkeypatch, _Proc(222, "pwsh.exe", parent=_Proc(1, "explorer.exe")))

    assert discover_mod.owning_session() is None


def test_the_walk_is_bounded(monkeypatch):
    """A pid loop, or a chain longer than any real one. Hanging on it would be
    worse than answering None."""
    loop = _Proc(1, "pwsh.exe")
    loop._parent = loop
    _chain(monkeypatch, loop)

    assert discover_mod.owning_session() is None


def test_an_unreadable_directory_still_reports_the_session(monkeypatch):
    """Losing the cwd here would be reported as "not inside a session" - a
    confidently wrong answer, when the truth is "inside one I cannot fully
    read"."""
    agent = _Proc(111, "claude.exe", parent=None,
                  cwd_raises=PermissionError("denied"))
    _chain(monkeypatch, _Proc(222, "pwsh.exe", parent=agent))

    assert discover_mod.owning_session() == (111, "", "claude")


def test_an_unreadable_parent_ends_the_walk(monkeypatch):
    class _Blind(_Proc):
        def parent(self):
            raise PermissionError("denied")

    _chain(monkeypatch, _Blind(222, "pwsh.exe"))

    assert discover_mod.owning_session() is None
