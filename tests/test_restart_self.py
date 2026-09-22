"""`restart --self`: what `/relaunch` runs.

The command itself only finds the session it is running inside and hands it to
`relaunch`, which has its own tests. What is tested here is the part in front of
that: the refusals, and the walk up the process tree that finds the session.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
import reloaded.discover as discover_mod

CWD = r"C:\repos\app"


def _args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "unattended": False,
         "dry_run": False, "repos": [], "self_": True}
    d.update(kw)
    return types.SimpleNamespace(**d)


@pytest.fixture
def session(monkeypatch):
    """A Claude session above this process, and a record of what relaunch was
    asked to do."""
    handed = []
    monkeypatch.setattr(discover_mod, "owning_session",
                        lambda: (111, CWD, "claude"))
    monkeypatch.setattr(
        main_mod.relaunch_mod, "relaunch",
        lambda pid, cwd, kind, dry_run=False: handed.append(
            (pid, cwd, kind, dry_run)) or 0)
    return handed


def test_it_hands_the_session_it_is_inside_to_relaunch(session):
    assert main_mod.cmd_restart(_args()) == 0
    assert session == [(111, CWD, "claude", False)]


def test_the_dry_run_is_passed_through(session):
    main_mod.cmd_restart(_args(dry_run=True))

    assert session[0][3] is True


def test_outside_a_session_it_refuses_and_says_what_to_run(capsys, monkeypatch):
    monkeypatch.setattr(discover_mod, "owning_session", lambda: None)

    assert main_mod.cmd_restart(_args()) == 1
    assert "restart <repo>" in capsys.readouterr().out


def test_a_session_whose_directory_cannot_be_read_is_refused(monkeypatch, capsys):
    """The marker is addressed by a hash of the cwd. Without one there is no
    marker, and guessing at the directory would arm someone else's."""
    monkeypatch.setattr(discover_mod, "owning_session", lambda: (111, "", "claude"))
    monkeypatch.setattr(main_mod.relaunch_mod, "relaunch",
                        lambda *a, **k: pytest.fail("relaunched without a cwd"))

    assert main_mod.cmd_restart(_args()) == 1


def test_naming_a_repo_alongside_self_is_refused(session, capsys):
    """`--self` is checked first, so naming both would restart the calling
    session and ignore what was named - a different restart than the one asked
    for, reported as success."""
    assert main_mod.cmd_restart(_args(repos=["other-repo"])) == 1
    assert session == []
    assert "cannot also take a repo name" in capsys.readouterr().out


def test_naming_a_repo_still_takes_the_normal_path(session, monkeypatch):
    """`--self` is a different command wearing the same verb. It must not
    swallow the one people already use."""
    called = []
    monkeypatch.setattr(main_mod, "cmd_restart_one",
                        lambda args, repos: called.append(repos) or 0)

    main_mod.cmd_restart(_args(self_=False, repos=["app"]))

    assert called == [["app"]]


def test_there_is_nothing_else_to_type(capsys):
    """`/relaunch` is one command with no options. The flags the old design
    needed - `--arm-only`, `--cancel`, `--after` - are gone, and asking for one
    is an error rather than a silent no-op."""
    for gone in ("--arm-only", "--cancel", "--after"):
        with pytest.raises(SystemExit):
            main_mod.build_parser().parse_args(["restart", "--self", gone])


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
