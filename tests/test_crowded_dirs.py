"""Two agents in one directory, as the directory-keyed parts of the tool see it.

`live_sessions()` and friends are keyed by normalized cwd, so a `codex.exe` and
a `claude.exe` in the same repository collapse to one entry there. Capture no
longer uses them to decide what is saved - it places each session by its tab's
shell (test_two_agents_one_directory) - but restarts and teardown still aim by
directory, so `status` names such a directory, the way `teardown.plan_down`
names the tab-side ambiguity.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
import reloaded.discover as discover_mod
import reloaded.win32 as win32_mod
from reloaded.paths import norm
from reloaded.win32 import Proc


def _Proc(name, pid, cwd):
    return types.SimpleNamespace(name=name, pid=pid, cwd=cwd)


@pytest.fixture
def processes(monkeypatch):
    """Replace the process table. Everything here is a fake — nothing reaches
    the machine's own processes, which is what made an earlier draft of this
    take eight seconds and depend on what the developer had open."""
    procs = []
    monkeypatch.setattr(win32_mod, "process_table",
                        lambda: {p.pid: Proc(0, p.name, 0.0) for p in procs})
    monkeypatch.setitem(__import__("sys").modules, "psutil", types.SimpleNamespace(
        Process=lambda pid: types.SimpleNamespace(
            cwd=lambda: {p.pid: p.cwd for p in procs}[pid])))
    return procs


def test_one_agent_per_directory_is_not_crowded(processes):
    processes += [_Proc("claude.exe", 1, r"C:\repos\app"),
                  _Proc("codex.exe", 2, r"C:\repos\beta")]

    assert discover_mod.sweep()[1] == {}


def test_two_kinds_in_one_directory_are_named(processes):
    processes += [_Proc("codex.exe", 1, r"C:\repos\app"),
                  _Proc("claude.exe", 2, r"C:\repos\app")]

    assert discover_mod.sweep()[1] == {norm(r"C:\repos\app"): ["claude", "codex"]}


def test_two_of_the_same_kind_are_named_too(processes):
    """Still only one entry, still only one restored — the kinds being equal
    does not make the loss smaller."""
    processes += [_Proc("claude.exe", 1, r"C:\repos\app"),
                  _Proc("claude.exe", 2, r"C:\repos\app")]

    assert discover_mod.sweep()[1] == {norm(r"C:\repos\app"): ["claude", "claude"]}


def test_the_collapsed_session_map_is_unchanged(processes):
    """The whole point: `live_sessions` cannot show this, which is why it
    needs saying separately."""
    processes += [_Proc("codex.exe", 1, r"C:\repos\app"),
                  _Proc("claude.exe", 2, r"C:\repos\app")]

    assert len(discover_mod.live_sessions()) == 1


def test_the_highest_pid_holds_a_shared_directory(processes):
    """Which one survives the collapse is pid order, as psutil.process_iter
    gave it - not whatever order the process table happens to list."""
    processes += [_Proc("claude.exe", 9, r"C:\repos\app"),
                  _Proc("codex.exe", 3, r"C:\repos\app")]

    assert discover_mod.live_sessions() == {norm(r"C:\repos\app"): 9}
    assert discover_mod.live_agents() == {norm(r"C:\repos\app"): "claude"}


def test_status_warns_about_a_shared_directory(monkeypatch, tmp_path, capsys):
    """In `status` rather than `capture`: this needs a process sweep of its
    own, and the reconcile runs capture every five minutes forever."""
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "none.json")
    monkeypatch.setattr(
        main_mod.discover_mod, "sweep",
        lambda: ({norm(r"C:\repos\app"): 1},
                 {norm(r"C:\repos\app"): ["claude", "codex"]}))

    main_mod.cmd_status(types.SimpleNamespace(layout="default",
                                              repos_root=r"C:\repos"))

    out = capsys.readouterr().out
    assert "share" in out
    assert "claude, codex" in out
    # Saved and restored now (see test_two_agents_one_directory); what is left
    # is that quitting is still aimed by directory.
    assert "Capture saves each as its own tab" in out
    assert "may leave one" in out


def test_status_says_nothing_when_no_directory_is_shared(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "none.json")
    monkeypatch.setattr(main_mod.discover_mod, "sweep", lambda: ({}, {}))

    main_mod.cmd_status(types.SimpleNamespace(layout="default",
                                              repos_root=r"C:\repos"))

    assert "share" not in capsys.readouterr().out


def test_status_without_a_layout_starts_no_terminal_query(monkeypatch, tmp_path):
    """Found by review: started and never read, the ~1.7s PowerShell query
    still held the exiting process until it finished."""
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "none.json")
    monkeypatch.setattr(main_mod.discover_mod, "sweep", lambda: ({}, {}))
    asked = []
    monkeypatch.setattr(main_mod.readiness_mod, "check_wt_version",
                        lambda: asked.append(1) or (True, ""))

    assert main_mod.cmd_status(types.SimpleNamespace(
        layout="default", repos_root=r"C:\repos")) == 1
    assert asked == []
