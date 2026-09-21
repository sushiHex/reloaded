"""`restart <repo>` will not type into a directory it cannot aim at.

Two agents in one directory collapse to one entry everywhere: the layout saves
one tab with one kind, and a restore brings back one session. `status` already
names that. `restart` is the command with the keystrokes, and it did not.

What it did instead was pick twice, independently. `discover._sessions` keeps
the *last* process enumerated at a cwd; `teardown.plan_down` keeps the *first*
tab that resolves to it and logs that "which one this is cannot be established".
The `Target` built from those two choices can therefore hold one process's pid
beside another process's tab — so the quit keys go to one session, the wait
watches the other, and the outcome is reported against the wrong one.

Three directories on this machine hold two agents each. Refusing is the only
answer that does not guess, and it is refused whole, for the same reason
`_no_tab_to_type_into` refuses whole: acting on the resolvable subset restarts
some repos, skips the rest without a word, and returns 0.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
import reloaded.discover as discover_mod
from reloaded.paths import norm

SHARED = r"C:\repos\shared"
ALONE = r"C:\repos\alone"


class _Item:
    """Opaque stand-in for a TabItemControl, as in test_teardown.py."""


def _plan(*cwds):
    return main_mod.teardown_mod.WindowPlan(
        hwnd=1, total_tabs=len(cwds),
        targets=[main_mod.teardown_mod.Target(c.rsplit("\\", 1)[-1], c, 111,
                                              _Item())
                 for c in cwds])


def _args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "dry_run": False,
         "after": 0.0, "force": False, "dispatched": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


@pytest.fixture
def desktop(monkeypatch):
    """A machine with both directories live, crowding settable per test."""
    state = {"crowded": {}, "swept": 0, "executed": 0}

    def _sweep():
        state["swept"] += 1
        return ({norm(SHARED): 111, norm(ALONE): 222}, dict(state["crowded"]))

    monkeypatch.setattr(main_mod.discover_mod, "sweep", _sweep)
    monkeypatch.setattr(
        main_mod.discover_mod, "live_sessions",
        lambda: pytest.fail("a second process sweep for a question sweep() "
                            "already answered"))
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down",
                        lambda root, **k: [_plan(SHARED, ALONE)])
    monkeypatch.setattr(main_mod, "_snapshot_restarts", lambda plans, args: [])
    monkeypatch.setattr(main_mod, "_sweep_stale_markers", lambda: None)
    monkeypatch.setattr(main_mod, "_warn_about_empty_relaunches", lambda s: None)
    monkeypatch.setattr(main_mod, "_print_down_result", lambda *a, **k: None)

    def _execute_down(plans, **kw):
        state["executed"] += 1
        return {"exited": [], "timed_out": [], "closed": [], "left_open": []}

    monkeypatch.setattr(main_mod.teardown_mod, "execute_down", _execute_down)
    return state


def test_a_shared_directory_is_refused(desktop):
    desktop["crowded"] = {norm(SHARED): ["claude", "codex"]}

    assert main_mod.cmd_restart_one(_args(), [SHARED]) == 1
    assert desktop["executed"] == 0, "it typed at a tab it could not aim at"


def test_the_refusal_names_what_is_there(desktop, capsys):
    desktop["crowded"] = {norm(SHARED): ["claude", "codex"]}

    main_mod.cmd_restart_one(_args(), [SHARED])

    out = capsys.readouterr().out
    assert SHARED in out
    assert "claude, codex" in out, "it never says what is sharing the directory"


def test_the_refusal_says_what_cannot_be_established(desktop, capsys):
    """A refusal without a reason reads as a bug in the tool rather than a
    fact about the machine, and the way out is not guessable."""
    desktop["crowded"] = {norm(SHARED): ["claude", "codex"]}

    main_mod.cmd_restart_one(_args(), [SHARED])

    out = capsys.readouterr().out
    assert "which" in out
    assert "own directory" in out, "it never says how to make this restartable"


def test_two_of_the_same_kind_are_refused_too(desktop):
    """The kinds being equal does not make the tab any more identifiable. A
    refusal that only caught the mixed pair would be answering the example
    rather than the problem."""
    desktop["crowded"] = {norm(SHARED): ["claude", "claude"]}

    assert main_mod.cmd_restart_one(_args(), [SHARED]) == 1
    assert desktop["executed"] == 0


def test_the_whole_batch_is_refused(desktop, capsys):
    """Restarting the clean one and silently skipping the other is what
    `_no_tab_to_type_into` already refuses to do."""
    desktop["crowded"] = {norm(SHARED): ["claude", "codex"]}

    assert main_mod.cmd_restart_one(_args(), [ALONE, SHARED]) == 1
    assert desktop["executed"] == 0
    assert "Nothing was restarted" in capsys.readouterr().out


def test_a_directory_of_its_own_restarts_normally(desktop):
    desktop["crowded"] = {norm(SHARED): ["claude", "codex"]}

    main_mod.cmd_restart_one(_args(), [ALONE])

    assert desktop["executed"] == 1


def test_crowding_elsewhere_is_not_this_restarts_problem(desktop):
    """Three directories on this machine are shared. Refusing every restart
    because one of them is would be refusing the command."""
    desktop["crowded"] = {norm(r"C:\repos\other"): ["claude", "codex"]}

    main_mod.cmd_restart_one(_args(), [ALONE])

    assert desktop["executed"] == 1


def test_the_check_costs_no_extra_walk_of_the_process_table(desktop):
    """`sweep()` exists because asking these two questions separately pays two
    `process_iter` walks - 1.47s each against 679 processes on this machine."""
    main_mod.cmd_restart_one(_args(), [ALONE])

    assert desktop["swept"] == 1


# ── and the advice that used to point here ───────────────────────────────


def test_self_does_not_send_a_hand_started_session_somewhere_it_will_refuse(
        monkeypatch, tmp_path, capsys):
    """`--self` refuses a hand-started session and tells it to run
    `reloaded restart <repo>` from another session instead. In a shared
    directory that command will refuse too, so the advice is a dead end."""
    monkeypatch.setattr(discover_mod, "owning_session",
                        lambda: (111, SHARED, "claude"))
    monkeypatch.setattr(discover_mod, "launcher_kind",
                        lambda pid: discover_mod.HAND)
    monkeypatch.setattr(main_mod, "restart_marker", lambda cwd: tmp_path / "m")
    monkeypatch.setattr(main_mod.discover_mod, "crowded_dirs",
                        lambda: {norm(SHARED): ["claude", "codex"]})

    rc = main_mod.cmd_restart(types.SimpleNamespace(
        layout="default", repos_root=r"C:\repos", dry_run=False, repos=[],
        self_=True, cancel=False, arm_only=False, after=0.0, dispatched=False))

    out = capsys.readouterr().out
    assert rc == 1
    assert "share" in out, "it never mentions the directory is shared"
    assert "own directory" in out


@pytest.fixture
def self_in_a_shared_directory(monkeypatch, tmp_path):
    """A reloaded-launched session in a directory that holds two."""
    spawned = []
    monkeypatch.setattr(discover_mod, "owning_session",
                        lambda: (111, SHARED, "claude"))
    monkeypatch.setattr(discover_mod, "launcher_kind",
                        lambda pid: discover_mod.RELOADED)
    monkeypatch.setattr(main_mod, "restart_marker", lambda cwd: tmp_path / "m")
    monkeypatch.setattr(main_mod.discover_mod, "crowded_dirs",
                        lambda: {norm(SHARED): ["claude", "codex"]})
    monkeypatch.setattr(main_mod, "_dispatch_restart",
                        lambda *a, **k: spawned.append(a) or 4242)
    return spawned


def _self_args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "dry_run": False,
         "repos": [], "self_": True, "cancel": False, "arm_only": False,
         "after": 0.0, "attempt": ""}
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_a_dispatch_from_a_shared_directory_is_refused_before_it_spawns(
        self_in_a_shared_directory, capsys):
    """The helper would reach `_shared_directory`'s refusal on its own — but
    inside a detached process, after this command had printed success and the
    user had been told their session was coming back. Which is the exact
    failure the sibling branch exists to remove."""
    rc = main_mod.cmd_restart(_self_args())

    assert rc == 1
    assert self_in_a_shared_directory == [], "it spawned a helper that will refuse"
    assert "claude, codex" in capsys.readouterr().out


def test_that_refusal_leaves_no_attempt_behind(self_in_a_shared_directory):
    main_mod.cmd_restart(_self_args())

    assert not main_mod.restart_attempt(SHARED).exists()


def test_the_dry_run_does_not_preview_something_that_cannot_happen(
        self_in_a_shared_directory, capsys):
    main_mod.cmd_restart(_self_args(dry_run=True))

    out = capsys.readouterr().out
    assert "Would hand" not in out
    assert "share" in out


def test_arming_is_not_refused_in_a_shared_directory(monkeypatch, tmp_path):
    """Arming needs no tab. The marker is read by the shell of whichever
    session exits, and the session that exits is the one the user quits — so
    the ambiguity that stops every other path does not arise here."""
    marker = tmp_path / "m"
    monkeypatch.setattr(discover_mod, "owning_session",
                        lambda: (111, SHARED, "claude"))
    monkeypatch.setattr(discover_mod, "launcher_kind",
                        lambda pid: discover_mod.RELOADED)
    monkeypatch.setattr(main_mod, "restart_marker", lambda cwd: marker)
    monkeypatch.setattr(
        main_mod.discover_mod, "crowded_dirs",
        lambda: pytest.fail("arming asked a question it does not need"))

    assert main_mod.cmd_restart(_self_args(arm_only=True)) == 0
    assert marker.read_text(encoding="utf-8") == "restart"


def test_a_dispatch_from_a_directory_of_its_own_still_works(monkeypatch,
                                                            tmp_path):
    spawned = []
    monkeypatch.setattr(discover_mod, "owning_session",
                        lambda: (111, ALONE, "claude"))
    monkeypatch.setattr(discover_mod, "launcher_kind",
                        lambda pid: discover_mod.RELOADED)
    monkeypatch.setattr(main_mod, "restart_marker", lambda cwd: tmp_path / "m")
    monkeypatch.setattr(main_mod.discover_mod, "crowded_dirs", lambda: {})
    monkeypatch.setattr(main_mod, "_dispatch_restart",
                        lambda *a, **k: spawned.append(a) or 4242)

    assert main_mod.cmd_restart(_self_args()) == 0
    assert len(spawned) == 1


def test_self_still_gives_the_normal_advice_in_a_directory_of_its_own(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(discover_mod, "owning_session",
                        lambda: (111, ALONE, "claude"))
    monkeypatch.setattr(discover_mod, "launcher_kind",
                        lambda pid: discover_mod.HAND)
    monkeypatch.setattr(main_mod, "restart_marker", lambda cwd: tmp_path / "m")
    monkeypatch.setattr(main_mod.discover_mod, "crowded_dirs", lambda: {})

    main_mod.cmd_restart(types.SimpleNamespace(
        layout="default", repos_root=r"C:\repos", dry_run=False, repos=[],
        self_=True, cancel=False, arm_only=False, after=0.0, dispatched=False))

    assert "restart <repo>" in capsys.readouterr().out
