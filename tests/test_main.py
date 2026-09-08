from __future__ import annotations

import types

from conftest import make_layout, make_window

import reloaded.__main__ as main_mod
from reloaded.layout import Tab
from reloaded.tabs import UIAUnavailable


def _args(**kw):
    defaults = {"layout": "default", "repos_root": r"C:\repos", "unattended": False}
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def test_cmd_capture_reports_a_missing_uiautomation_instead_of_a_traceback(tmp_path, monkeypatch, capsys):
    """The regression this guards: capture_live's UIAUnavailable propagated
    straight out of main() as a raw traceback on a fresh install without the
    uiautomation package -- psutil already degraded gracefully, this did not."""
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")

    def raises(*a, **k):
        raise UIAUnavailable("uiautomation is not installed")

    monkeypatch.setattr(main_mod.capture_mod, "capture_live", raises)
    rc = main_mod.cmd_capture(_args())
    assert rc == 1
    out = capsys.readouterr().out
    assert "uiautomation is not installed" in out
    assert "pip install uiautomation" in out


def test_cmd_edit_reports_a_missing_uiautomation_instead_of_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")

    def raises(*a, **k):
        raise UIAUnavailable("uiautomation is not installed")

    monkeypatch.setattr(main_mod.capture_mod, "capture_live", raises)
    rc = main_mod.cmd_edit(_args())
    assert rc == 1
    out = capsys.readouterr().out
    assert "pip install uiautomation" in out


def test_cmd_down_reports_a_missing_uiautomation_instead_of_a_traceback(monkeypatch, capsys):
    def raises(repos_root):
        raise UIAUnavailable("uiautomation is not installed")

    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", raises)
    rc = main_mod.cmd_down(_args(dry_run=False))
    assert rc == 1
    out = capsys.readouterr().out
    assert "pip install uiautomation" in out


def test_cmd_down_reports_nothing_to_exit_when_no_live_tabs(monkeypatch, capsys):
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", lambda repos_root: [])
    rc = main_mod.cmd_down(_args(dry_run=False))
    assert rc == 0
    assert "nothing to exit" in capsys.readouterr().out.lower()


def _fake_plan(hwnd=0x100, total_tabs=1, targets=None):
    return main_mod.teardown_mod.WindowPlan(
        hwnd=hwnd,
        total_tabs=total_tabs,
        targets=targets or [main_mod.teardown_mod.Target(
            "app-a", r"C:\repos\app-a", 111, object())],
    )


def test_cmd_down_dry_run_sends_nothing(monkeypatch, capsys):
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", lambda repos_root: [_fake_plan()])
    monkeypatch.setattr(main_mod.tasks_mod, "logon_launcher_installed", lambda: False)

    def must_not_run(*a, **k):
        raise AssertionError("execute_down must not run on --dry-run")

    monkeypatch.setattr(main_mod.teardown_mod, "execute_down", must_not_run)

    rc = main_mod.cmd_down(_args(dry_run=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "app-a" in out


def test_cmd_down_warns_when_logon_launcher_installed(monkeypatch, capsys):
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", lambda repos_root: [_fake_plan()])
    monkeypatch.setattr(main_mod.tasks_mod, "logon_launcher_installed", lambda: True)

    main_mod.cmd_down(_args(dry_run=True))

    out = capsys.readouterr().out
    assert "logon" in out.lower()
    assert "uninstall-tasks" in out


def test_cmd_down_executes_and_summarizes_results(monkeypatch, capsys):
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", lambda repos_root: [_fake_plan()])
    monkeypatch.setattr(main_mod.tasks_mod, "logon_launcher_installed", lambda: False)
    monkeypatch.setattr(
        main_mod.teardown_mod,
        "execute_down",
        lambda plans, **kw: {
            "exited": [("app-a", r"C:\repos\app-a")],
            "timed_out": [],
            "closed": [0x100],
            "left_open": [],
        },
    )

    rc = main_mod.cmd_down(_args(dry_run=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Exited 1 session" in out
    assert "closed 1 window" in out


def test_cmd_down_nonzero_exit_when_a_session_times_out(monkeypatch, capsys):
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", lambda repos_root: [_fake_plan()])
    monkeypatch.setattr(main_mod.tasks_mod, "logon_launcher_installed", lambda: False)
    monkeypatch.setattr(
        main_mod.teardown_mod,
        "execute_down",
        lambda plans, **kw: {
            "exited": [],
            "timed_out": [("app-a", r"C:\repos\app-a")],
            "closed": [],
            "left_open": [0x100],
        },
    )

    rc = main_mod.cmd_down(_args(dry_run=False))
    assert rc == 1
    out = capsys.readouterr().out
    assert "did not exit in time" in out


def _fake_captured_layout():
    return make_layout([make_window([0, 0, 100, 100], [Tab(cwd=r"C:\repos\app-a", title="app-a")])])


def test_capture_layout_passes_its_own_discovery_snapshot_into_capture_live(tmp_path, monkeypatch):
    """The whole point of returning live/title_map is that a caller (restart)
    can feed the exact same snapshot into plan_down afterward instead of
    triggering another psutil scan and transcript-corpus walk — so
    capture_live must receive the very same dicts _capture_layout computed,
    not merely equal-looking ones."""
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")
    sentinel_live = {"sentinel": 1}
    sentinel_title_map = {"sentinel": "title"}
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: sentinel_live)
    monkeypatch.setattr(
        main_mod.discover_mod, "title_to_cwd", lambda index: sentinel_title_map
    )
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda: {})

    captured = {}

    def fake_capture_live(repos_root, previous=None, *, live=None, title_map=None):
        captured["live"] = live
        captured["title_map"] = title_map
        return _fake_captured_layout()

    monkeypatch.setattr(main_mod.capture_mod, "capture_live", fake_capture_live)

    lo, path, live, title_map = main_mod._capture_layout(_args())

    assert captured["live"] is sentinel_live
    assert captured["title_map"] is sentinel_title_map
    assert live is sentinel_live
    assert title_map is sentinel_title_map


def test_cmd_restart_reports_a_missing_uiautomation_instead_of_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")

    def raises(*a, **k):
        raise UIAUnavailable("uiautomation is not installed")

    monkeypatch.setattr(main_mod.capture_mod, "capture_live", raises)
    rc = main_mod.cmd_restart(_args(dry_run=False))
    assert rc == 1
    out = capsys.readouterr().out
    assert "pip install uiautomation" in out


def test_cmd_restart_reports_nothing_to_restart_when_no_live_tabs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")
    monkeypatch.setattr(main_mod.capture_mod, "capture_live", lambda *a, **k: make_layout([]))

    rc = main_mod.cmd_restart(_args(dry_run=False))
    assert rc == 1
    assert "nothing to restart" in capsys.readouterr().out.lower()


def test_cmd_restart_dry_run_captures_nothing_and_sends_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")
    monkeypatch.setattr(main_mod.capture_mod, "capture_live", lambda *a, **k: _fake_captured_layout())

    def must_not_run(*a, **k):
        raise AssertionError("dry-run must not save, exit, or relaunch anything")

    monkeypatch.setattr(main_mod.layout_mod, "save", must_not_run)
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", must_not_run)
    monkeypatch.setattr(main_mod, "_deploy_layout", must_not_run)

    rc = main_mod.cmd_restart(_args(dry_run=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "app-a" in out
    assert "Dry run" in out


def test_cmd_restart_saves_the_fresh_capture_before_exiting(tmp_path, monkeypatch, capsys):
    """The capture must be saved BEFORE anything is exited - it's the only
    record of "exactly as it was", and would be lost if sessions closed first."""
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")
    lo = _fake_captured_layout()
    monkeypatch.setattr(main_mod.capture_mod, "capture_live", lambda *a, **k: lo)

    order = []
    monkeypatch.setattr(
        main_mod.layout_mod, "save", lambda layout, path: order.append(("save", layout))
    )
    monkeypatch.setattr(
        main_mod.teardown_mod,
        "plan_down",
        lambda repos_root, **kw: order.append("plan_down") or [],
    )
    monkeypatch.setattr(main_mod, "_deploy_layout", lambda layout, args: order.append("deploy") or 0)

    rc = main_mod.cmd_restart(_args(dry_run=False))
    assert rc == 0
    assert order[0] == ("save", lo)
    assert order[1] == "plan_down"
    assert order[2] == "deploy"


def test_cmd_restart_relaunches_from_the_captured_layout_even_if_a_session_times_out(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")
    monkeypatch.setattr(main_mod.capture_mod, "capture_live", lambda *a, **k: _fake_captured_layout())
    monkeypatch.setattr(main_mod.layout_mod, "save", lambda layout, path: None)
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", lambda repos_root, **kw: [_fake_plan()])
    monkeypatch.setattr(
        main_mod.teardown_mod,
        "execute_down",
        lambda plans, **kw: {
            "exited": [],
            "timed_out": [("app-a", r"C:\repos\app-a")],
            "closed": [],
            "left_open": [0x100],
        },
    )
    deployed = []
    monkeypatch.setattr(
        main_mod, "_deploy_layout", lambda layout, args: deployed.append(layout) or 0
    )

    rc = main_mod.cmd_restart(_args(dry_run=False))
    assert rc == 1  # nonzero because of the timeout, even though deploy itself "succeeded"
    assert len(deployed) == 1
    out = capsys.readouterr().out
    assert "did not exit in time" in out


def test_cmd_restart_reports_teardown_uiautomation_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")
    monkeypatch.setattr(main_mod.capture_mod, "capture_live", lambda *a, **k: _fake_captured_layout())
    monkeypatch.setattr(main_mod.layout_mod, "save", lambda layout, path: None)

    def raises(repos_root, **kw):
        raise UIAUnavailable("uiautomation is not installed")

    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", raises)

    rc = main_mod.cmd_restart(_args(dry_run=False))
    assert rc == 1
    assert "pip install uiautomation" in capsys.readouterr().out
