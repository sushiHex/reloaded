"""`reloaded restart <repo>` — restarting named sessions rather than all of them.

The fixture models pids honestly: a session that never exits keeps its ORIGINAL
pid, and a relaunched one gets a new pid. An earlier version of this file
manufactured a pid as soon as teardown had been called, which made every
timeout look like a success and hid exactly the bug these tests now pin.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
from reloaded.paths import norm

REPOS = r"C:\repos"
CWD_A = r"C:\repos\app-a"
CWD_B = r"C:\repos\app-b"
OLD_PID_A, OLD_PID_B = 111, 222


def _args(**kw):
    defaults = {
        "layout": "default", "repos_root": REPOS, "unattended": False,
        "dry_run": False, "repos": [],
    }
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


class _Item:
    """Opaque stand-in for a TabItemControl."""


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A tiny model of the machine: which sessions are live, under which pid,
    and what each moving part did."""
    w = {
        # cwd -> pid, or absent. Mutated by the fake teardown / launcher.
        "pids": {norm(CWD_A): OLD_PID_A},
        # cwds whose tab exists in a WT window plan_down can see
        "planned": [CWD_A],
        # cwds whose /exit will time out, leaving the session running
        "wont_exit": set(),
        # cwds whose relaunch-in-place will not happen (a pre-loop tab)
        "wont_relaunch": set(),
        # whether the fallback wt launch succeeds
        "launch_works": True,
        "armed": [],
        "launched": [],
        "down_kwargs": {},
        "saved_layout": False,
        "tab_counts": {1: 1},
    }

    monkeypatch.setattr(main_mod, "restart_marker",
                        lambda cwd: tmp_path / (norm(cwd).replace("\\", "_").replace(":", "") + ".marker"))
    monkeypatch.setattr(main_mod, "restart_marker_dir", lambda: tmp_path)
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda *a, **k: {})
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: dict(w["pids"]))
    # Everything here models a reloaded-launched session; the hand-launched
    # path has its own file. Without this the real launcher_kind runs against
    # fabricated pids, can read nothing, and safely answers HAND - which sends
    # every test in this file down the wrong branch.
    monkeypatch.setattr(main_mod.discover_mod, "launcher_kind",
                        lambda pid: main_mod.discover_mod.RELOADED)
    monkeypatch.setattr(main_mod.layout_mod, "save",
                        lambda lo, path: w.__setitem__("saved_layout", True))
    monkeypatch.setattr(main_mod, "RELAUNCH_WAIT_SECONDS", 0.2, raising=False)
    monkeypatch.setattr(main_mod, "RELAUNCH_POLL_SECONDS", 0.01, raising=False)

    def plan_down(repos_root, **kw):
        w["plan_kwargs"] = kw
        only = kw.get("only")
        keep = [c for c in w["planned"]
                if only is None or norm(c) in {norm(x) for x in only}]
        if not keep:
            return []
        targets = [(f"tab-{i}", c, w["pids"][norm(c)], _Item())
                   for i, c in enumerate(keep)]
        return [main_mod.teardown_mod.WindowPlan(hwnd=1, total_tabs=len(targets),
                                                 targets=targets)]

    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", plan_down)

    def execute_down(plans, log=print, **kw):
        w["down_kwargs"] = kw
        before = kw.get("before_exit")
        exited, timed_out = [], []
        for plan in plans:
            for title, cwd, _pid, _item in plan.targets:
                if before is not None:
                    before(cwd)
                    w["armed"].append(cwd)
                if norm(cwd) in {norm(c) for c in w["wont_exit"]}:
                    timed_out.append((title, cwd))
                    continue  # session keeps running under its ORIGINAL pid
                exited.append((title, cwd))
                del w["pids"][norm(cwd)]
                if norm(cwd) in {norm(c) for c in w["wont_relaunch"]}:
                    w["tab_counts"][1] = w["tab_counts"][1] - 1  # its tab closed
                else:
                    w["pids"][norm(cwd)] = 900 + len(exited)  # back, new pid
        return {"exited": exited, "timed_out": timed_out,
                "closed": [], "left_open": [1]}

    monkeypatch.setattr(main_mod.teardown_mod, "execute_down", execute_down)

    def launch(cwd):
        w["launched"].append(cwd)
        if w["launch_works"]:
            w["pids"][norm(cwd)] = 777

    monkeypatch.setattr(main_mod, "_launch_single_tab",
                        lambda cwd, **kw: launch(cwd), raising=False)
    monkeypatch.setattr(main_mod, "_live_tab_count",
                        lambda hwnd: w["tab_counts"].get(hwnd, 0), raising=False)
    return w


# ── the happy path ───────────────────────────────────────────────────────


def test_a_named_repo_is_armed_and_comes_back(world, capsys):
    rc = main_mod.cmd_restart(_args(repos=["app-a"]))

    assert rc == 0, capsys.readouterr().out
    assert world["armed"] == [CWD_A]
    assert world["launched"] == [], "an in-place restart must not open a second tab"


def test_the_marker_really_exists_on_disk_when_armed(world):
    """Not `marker.exists() or something_else` — the earlier version of this
    assertion passed whenever teardown had run, marker or no marker."""
    main_mod.cmd_restart(_args(repos=["app-a"]))

    assert main_mod.restart_marker(CWD_A).exists()


def test_a_targeted_restart_never_closes_windows(world):
    main_mod.cmd_restart(_args(repos=["app-a"]))

    assert world["down_kwargs"].get("close_windows") is False


def test_a_targeted_restart_selects_only_the_named_repo(world):
    main_mod.cmd_restart(_args(repos=["app-a"]))

    only = world["plan_kwargs"].get("only")
    assert [norm(c) for c in only] == [norm(CWD_A)]


def test_a_targeted_restart_does_not_rewrite_the_saved_layout(world):
    main_mod.cmd_restart(_args(repos=["app-a"]))

    assert world["saved_layout"] is False


def test_markers_are_armed_through_the_teardown_hook_not_up_front(world):
    """Armed up front, marker N has to survive every earlier target's exit
    wait. Through the hook it only has to survive its own."""
    main_mod.cmd_restart(_args(repos=["app-a"]))

    assert world["down_kwargs"].get("before_exit") is not None


# ── the session that would not exit ──────────────────────────────────────


def test_a_session_that_never_exited_is_not_called_restarted(world, capsys):
    """The bug this file exists for. The old process is still alive at that
    cwd, so any poll that accepts "some pid" reports a restart that never
    happened."""
    world["wont_exit"] = {CWD_A}

    rc = main_mod.cmd_restart(_args(repos=["app-a"]))

    out = capsys.readouterr().out
    assert "restarted in place" not in out, out
    assert rc == 1


def test_a_session_that_never_exited_stays_armed(world):
    """Deliberately NOT disarmed, after weighing two bad outcomes.

    Disarming looks tidy: a marker left on disk fires on the user's own /exit
    later. But "timed out" only means the session had not exited within
    EXIT_TIMEOUT_SECONDS - it may exit a second afterwards. If the marker is
    gone by then, the shell breaks out of the restart loop, falls into
    CLOSE_TAB_IF_STARTED, and closes the tab: the restart destroys the very
    session it was asked to bring back, while reporting it as still running.

    Left armed, the worst case is that the session restarts late - which is
    what was asked for - and the TTL bounds how late. Losing a session is far
    worse than restarting one twice.
    """
    world["wont_exit"] = {CWD_A}

    main_mod.cmd_restart(_args(repos=["app-a"]))

    assert main_mod.restart_marker(CWD_A).exists()


def test_a_still_armed_timeout_says_the_restart_is_pending(world, capsys):
    """Leaving it armed is only safe if the user is told, or a session that
    comes back minutes later looks like it restarted itself."""
    world["wont_exit"] = {CWD_A}

    main_mod.cmd_restart(_args(repos=["app-a"]))

    out = capsys.readouterr().out.lower()
    assert "still armed" in out or "will restart" in out


def test_a_session_that_never_exited_is_not_duplicated(world):
    """It is still running. Launching another gives two claude processes in
    one repo, both writing the same transcript."""
    world["wont_exit"] = {CWD_A}

    main_mod.cmd_restart(_args(repos=["app-a"]))

    assert world["launched"] == []


# ── the pre-loop tab that closes instead of relaunching ──────────────────


def test_a_tab_that_closed_is_reopened(world, capsys):
    world["wont_relaunch"] = {CWD_A}

    rc = main_mod.cmd_restart(_args(repos=["app-a"]))

    assert world["launched"] == [CWD_A]
    assert rc == 0, capsys.readouterr().out


def test_a_tab_that_closed_has_its_marker_disarmed(world):
    """The one place disarming is safe, and necessary.

    A session with no restart loop closes its tab on /exit, so the marker is
    never read - and the shell that could have read it is provably gone, which
    is why deleting here cannot destroy anything. Left armed it survives into
    the REPLACEMENT session, whose launcher does have the loop: the user's next
    /exit within the TTL then restarts a session they meant to close.
    """
    world["wont_relaunch"] = {CWD_A}

    main_mod.cmd_restart(_args(repos=["app-a"]))

    assert not main_mod.restart_marker(CWD_A).exists()


def test_the_fallback_says_so_rather_than_pretending(world, capsys):
    world["wont_relaunch"] = {CWD_A}

    main_mod.cmd_restart(_args(repos=["app-a"]))

    assert "reopening" in capsys.readouterr().out.lower()


def test_a_failed_fallback_launch_is_reported_as_a_failure(world, capsys):
    world["wont_relaunch"] = {CWD_A}
    world["launch_works"] = False

    rc = main_mod.cmd_restart(_args(repos=["app-a"]))

    assert rc == 1
    assert "by hand" in capsys.readouterr().out


def test_a_tab_that_is_still_there_is_not_duplicated(world, capsys):
    """A relaunch that fails inside the startup grace leaves the tab open on
    purpose, showing its error. Concluding "the tab closed" from the absent
    pid alone adds a second tab beside the error the user needs to read."""
    world["wont_relaunch"] = {CWD_A}
    world["tab_counts"] = {1: 1}          # ... but the tab never went away

    def execute_no_relaunch(plans, log=print, **kw):
        world["down_kwargs"] = kw
        for plan in plans:
            for _t, cwd, _p, _i in plan.targets:
                if kw.get("before_exit"):
                    kw["before_exit"](cwd)
                del world["pids"][norm(cwd)]
        return {"exited": [("tab-0", CWD_A)], "timed_out": [],
                "closed": [], "left_open": [1]}

    import reloaded.teardown as teardown_mod
    object.__setattr__(main_mod.teardown_mod, "execute_down", execute_no_relaunch)
    try:
        rc = main_mod.cmd_restart(_args(repos=["app-a"]))
    finally:
        object.__setattr__(main_mod.teardown_mod, "execute_down",
                           teardown_mod.execute_down)

    out = capsys.readouterr().out
    assert world["launched"] == [], out
    assert rc == 1
    assert "still has its tab" in out.lower() or "did not come back" in out.lower()


# ── selection and refusal ────────────────────────────────────────────────


def test_a_repo_that_is_not_running_is_refused_without_touching_anything(world, capsys):
    rc = main_mod.cmd_restart(_args(repos=["app-b"]))

    assert rc == 1
    assert world["down_kwargs"] == {}
    assert world["armed"] == []
    assert "app-b" in capsys.readouterr().out


def test_a_repo_with_no_visible_tab_is_refused_rather_than_half_done(world, capsys):
    """`restart a b` where UIA resolves only a's tab used to restart a, skip b
    silently, and return 0 — with b's marker left armed."""
    world["pids"][norm(CWD_B)] = OLD_PID_B   # live...
    world["planned"] = [CWD_A]               # ...but no tab we can see

    rc = main_mod.cmd_restart(_args(repos=["app-a", "app-b"]))

    assert rc == 1
    assert world["armed"] == [], "nothing may be armed on a refused batch"
    assert world["down_kwargs"] == {}, "nothing may be exited on a refused batch"
    out = capsys.readouterr().out
    assert "app-b" in out


def test_several_repos_all_restart(world, capsys):
    world["pids"][norm(CWD_B)] = OLD_PID_B
    world["planned"] = [CWD_A, CWD_B]
    world["tab_counts"] = {1: 2}

    rc = main_mod.cmd_restart(_args(repos=["app-a", "app-b"]))

    assert rc == 0, capsys.readouterr().out
    assert world["armed"] == [CWD_A, CWD_B]


def test_dry_run_writes_no_marker_and_sends_nothing(world, capsys):
    rc = main_mod.cmd_restart(_args(repos=["app-a"], dry_run=True))

    assert rc == 0
    assert world["down_kwargs"] == {}
    assert not main_mod.restart_marker(CWD_A).exists()
    assert "would" in capsys.readouterr().out.lower()
