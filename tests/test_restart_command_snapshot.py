"""The command a session was launched with is read while it is still running.

`restart` puts a session back the way it was, and the flags are half of "the
way it was". Both places that need them run after execute_down, so reading a
command line at the point of use means reading it off a process that has
already quit. psutil raises, session_command returns "", and the relaunch
quietly uses the kind's default instead.

For Claude Code that costs the user's own flags. For Codex the default is
`--dangerously-bypass-approvals-and-sandbox`, so a session deliberately
started WITHOUT the bypass comes back WITH it, silently. That is a security
setting escalating itself during a routine restart.

Windows also reuses pids. A dead pid that has been handed to something else
returns that process's command line, which then gets typed into a terminal.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
from reloaded.discover import HAND, RELOADED
from reloaded.paths import norm

REPOS = r"C:\repos"
CWD = r"C:\repos\safe-codex"
SAFE = "codex resume --last"


def _args(**kw):
    d = {"layout": "default", "repos_root": REPOS, "unattended": False,
         "dry_run": False, "repos": []}
    d.update(kw)
    return types.SimpleNamespace(**d)


class _Item:
    pass


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = {"alive": {111}, "typed": [], "launched": [], "asked_dead": []}

    monkeypatch.setattr(main_mod, "restart_marker",
                        lambda cwd: tmp_path / "m")
    monkeypatch.setattr(main_mod, "restart_marker_dir", lambda: tmp_path)
    monkeypatch.setattr(main_mod, "layout_path", lambda n: tmp_path / "l.json")
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda *a, **k: {})
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions",
                        lambda: {norm(CWD): 111} if 111 in w["alive"] else {})
    monkeypatch.setattr(main_mod.discover_mod, "live_agents",
                        lambda: {norm(CWD): "codex"})
    monkeypatch.setattr(main_mod.discover_mod, "launcher_kind", lambda pid: HAND)
    monkeypatch.setattr(main_mod, "RELAUNCH_WAIT_SECONDS", 0.2, raising=False)
    monkeypatch.setattr(main_mod, "RELAUNCH_POLL_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(main_mod, "_live_tab_count", lambda hwnd: 2)

    def session_launch(pid):
        """What psutil actually does: a dead pid has neither name nor cmdline.

        Both come back together or not at all - see discover.session_launch.
        """
        if pid not in w["alive"]:
            w["asked_dead"].append(pid)
            return "", ""
        return "codex", SAFE

    monkeypatch.setattr(main_mod.discover_mod, "session_launch", session_launch)

    def plan_down(repos_root, **kw):
        return [main_mod.teardown_mod.WindowPlan(
            hwnd=1, total_tabs=2, targets=[main_mod.teardown_mod.Target("t", CWD, 111, _Item())])]

    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", plan_down)

    def execute_down(plans, log=print, **kw):
        for plan in plans:
            for _title, cwd, _pid, _i in plan.targets:
                if kw.get("before_exit"):
                    kw["before_exit"](cwd)
        w["alive"].discard(111)          # the session is gone from here on
        return {"exited": [("t", CWD)], "timed_out": [], "closed": [],
                "left_open": [1]}

    monkeypatch.setattr(main_mod.teardown_mod, "execute_down", execute_down)
    monkeypatch.setattr(
        main_mod, "_type_relaunch",
        lambda s: w["typed"].append(s) or True,
        raising=False)
    monkeypatch.setattr(
        main_mod, "_launch_single_tab",
        lambda s: w["launched"].append(s),
        raising=False)
    return w


def test_the_relaunch_uses_the_command_the_session_was_running(world):
    main_mod.cmd_restart(_args(repos=["safe-codex"]))

    assert [s.command for s in world["typed"]] == [SAFE]


def test_no_command_line_is_read_off_a_dead_pid(world):
    """The pid is gone by the time the relaunch needs it. Asking anyway gets
    "" on a good day and a recycled process's command line on a bad one."""
    main_mod.cmd_restart(_args(repos=["safe-codex"]))

    assert world["asked_dead"] == []


def test_the_bypass_flag_is_not_added_to_a_session_that_lacked_it(world):
    """The failure this prevents, stated as itself: falling back to the Codex
    default turns approvals and the sandbox off in a session that had them.

    Asserted against the script that actually gets run, not against the string
    passed along to build it. An empty command reads as harmless right up until
    launcher_command turns it into the kind's default - which is the whole
    mechanism, so a test that stops short of it proves nothing.

    Built from the record's OWN agent, not from a hardcoded "codex". Naming the
    kind here would have let a wrong `s.agent` through, which is precisely the
    defect this file exists to catch.
    """
    import reloaded.deploy as deploy_mod

    main_mod.cmd_restart(_args(repos=["safe-codex"]))

    assert world["typed"], "nothing was relaunched"
    s = world["typed"][0]
    assert s.agent == "codex", "the relaunch was aimed at the wrong CLI"
    script = deploy_mod.relaunch_script(s.cwd, s.size_bytes, s.agent, s.command)
    assert "--dangerously-bypass-approvals-and-sandbox" not in script
    assert SAFE in script
