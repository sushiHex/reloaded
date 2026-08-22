"""`restart <repo>` against a session that was started by hand.

Its shell is a plain interactive prompt, so /exit leaves a live shell sitting in
a tab with no session in it. Neither of the reloaded paths applies: there is no
marker loop to relaunch it, and the tab does not close, so the reopen-a-new-tab
fallback would both fail its own tab-count check and be wrong if it didn't.

What happens instead is that the launcher is typed into that waiting shell,
which upgrades the tab: from then on it behaves like a reloaded-launched one.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
from reloaded.discover import HAND, RELOADED
from reloaded.paths import norm

REPOS = r"C:\repos"
CWD_H = r"C:\repos\by-hand"
CWD_R = r"C:\repos\by-reloaded"


def _args(**kw):
    d = {"layout": "default", "repos_root": REPOS, "unattended": False,
         "dry_run": False, "repos": []}
    d.update(kw)
    return types.SimpleNamespace(**d)


class _Item:
    pass


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = {
        "pids": {norm(CWD_H): 111, norm(CWD_R): 222},
        "kinds": {norm(CWD_H): HAND, norm(CWD_R): RELOADED},
        "armed": [], "typed": [], "launched": [],
        "tabs": 2,
    }

    monkeypatch.setattr(main_mod, "restart_marker",
                        lambda cwd: tmp_path / (norm(cwd).replace("\\", "_").replace(":", "") + ".m"))
    monkeypatch.setattr(main_mod, "restart_marker_dir", lambda: tmp_path)
    monkeypatch.setattr(main_mod, "layout_path", lambda n: tmp_path / "l.json")
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda *a, **k: {})
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: dict(w["pids"]))
    monkeypatch.setattr(main_mod.discover_mod, "launcher_kind",
                        lambda pid: w["kinds"][
                            next(c for c, p in w["pids"].items() if p == pid)])
    monkeypatch.setattr(main_mod, "RELAUNCH_WAIT_SECONDS", 0.2, raising=False)
    monkeypatch.setattr(main_mod, "RELAUNCH_POLL_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(main_mod, "_live_tab_count", lambda hwnd: w["tabs"])
    monkeypatch.setattr(main_mod, "_launch_single_tab", w["launched"].append)

    def plan_down(repos_root, **kw):
        only = {norm(c) for c in (kw.get("only") or [])}
        targets = [(f"t{i}", c, w["pids"][norm(c)], _Item())
                   for i, c in enumerate([CWD_H, CWD_R])
                   if not only or norm(c) in only]
        if not targets:
            return []
        return [main_mod.teardown_mod.WindowPlan(hwnd=1, total_tabs=w["tabs"],
                                                 targets=targets)]

    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", plan_down)

    def execute_down(plans, log=print, **kw):
        before = kw.get("before_exit")
        exited = []
        for plan in plans:
            for title, cwd, _pid, _i in plan.targets:
                if before:
                    before(cwd)
                exited.append((title, cwd))
                del w["pids"][norm(cwd)]
                # A reloaded tab with the loop comes straight back. A
                # hand-launched one does not - its shell just returns to a
                # prompt, tab still there, no session.
                if w["kinds"][norm(cwd)] == RELOADED:
                    w["pids"][norm(cwd)] = 900
        return {"exited": exited, "timed_out": [], "closed": [], "left_open": [1]}

    monkeypatch.setattr(main_mod.teardown_mod, "execute_down", execute_down)

    def type_relaunch(hwnd, item, cwd, size_bytes=0):
        w["typed"].append(cwd)
        w["pids"][norm(cwd)] = 777      # the shell runs it and claude comes up
        return True

    monkeypatch.setattr(main_mod, "_type_relaunch", type_relaunch, raising=False)
    # record what got armed, without breaking the real write
    real_marker = main_mod.restart_marker
    monkeypatch.setattr(main_mod, "restart_marker",
                        lambda cwd: (w["armed"].append(cwd) or real_marker(cwd))
                        if False else real_marker(cwd))
    return w


def test_a_hand_launched_session_is_relaunched_by_typing(world, capsys):
    rc = main_mod.cmd_restart(_args(repos=["by-hand"]))

    assert world["typed"] == [CWD_H], capsys.readouterr().out
    assert rc == 0


def test_a_hand_launched_session_is_never_armed(world):
    """Its shell has no loop, so a marker would sit on disk unread - and then
    be inherited by the upgraded launcher this restart installs, firing on the
    user's next /exit."""
    main_mod.cmd_restart(_args(repos=["by-hand"]))

    assert not main_mod.restart_marker(CWD_H).exists()


def test_a_hand_launched_session_is_not_sent_to_the_new_tab_fallback(world):
    """Its tab never closed. Opening another would leave two tabs for one repo,
    one of them a dead prompt."""
    main_mod.cmd_restart(_args(repos=["by-hand"]))

    assert world["launched"] == []


def test_a_reloaded_session_still_uses_the_marker(world):
    main_mod.cmd_restart(_args(repos=["by-reloaded"]))

    assert world["typed"] == [], "a reloaded tab relaunches itself"
    assert world["launched"] == []


def test_a_mixed_batch_handles_each_by_its_own_kind(world, capsys):
    rc = main_mod.cmd_restart(_args(repos=["by-hand", "by-reloaded"]))

    assert rc == 0, capsys.readouterr().out
    assert world["typed"] == [CWD_H]


def test_the_output_says_the_tab_was_upgraded(world, capsys):
    """The tab's behaviour changes permanently - it will self-close from now
    on. That is worth one line rather than a silent change."""
    main_mod.cmd_restart(_args(repos=["by-hand"]))

    out = capsys.readouterr().out.lower()
    assert "hand" in out or "upgrad" in out


def test_the_dry_run_warns_that_a_hand_launched_tab_gets_changed(world, capsys):
    """Restarting a hand-launched session rewrites what its tab runs, for good
    - it self-closes from then on. A preview that reads the same for both kinds
    hides the one consequence worth previewing."""
    main_mod.cmd_restart(_args(repos=["by-hand"], dry_run=True))

    out = capsys.readouterr().out.lower()
    assert "hand" in out
    assert "self-clos" in out or "upgrad" in out


def test_the_dry_run_does_not_warn_about_a_reloaded_tab(world, capsys):
    main_mod.cmd_restart(_args(repos=["by-reloaded"], dry_run=True))

    assert "upgrad" not in capsys.readouterr().out.lower()


def test_a_hand_launched_timeout_does_not_claim_a_restart_is_armed(world, capsys):
    """Seen in a real run against `fonts`: it timed out and was told "its
    restart is still armed: it will restart if it exits within 2 min". Nothing
    was armed - hand-launched sessions never are - so the user was told to
    expect a restart that cannot happen."""
    def execute_timeout(plans, log=print, **kw):
        for plan in plans:
            for title, cwd, _p, _i in plan.targets:
                if kw.get("before_exit"):
                    kw["before_exit"](cwd)
        return {"exited": [], "timed_out": [("t0", CWD_H)],
                "closed": [], "left_open": [1]}

    import reloaded.teardown as teardown_mod
    monkey = main_mod.teardown_mod.execute_down
    main_mod.teardown_mod.execute_down = execute_timeout
    try:
        main_mod.cmd_restart(_args(repos=["by-hand"]))
    finally:
        main_mod.teardown_mod.execute_down = monkey

    out = capsys.readouterr().out.lower()
    assert "never exited" in out
    assert "still armed" not in out, out


def test_a_relaunch_that_will_not_type_is_reported(world, capsys):
    """Typing depends on foregrounding the right tab, which teardown itself
    documents as best-effort."""
    import reloaded.__main__ as m
    m._type_relaunch = lambda hwnd, item, cwd, size_bytes=0: False

    rc = main_mod.cmd_restart(_args(repos=["by-hand"]))

    assert rc == 1
    assert "by hand" in capsys.readouterr().out.lower()
