"""The guard has to hold at the moment of the write, not only on the way in.

Codex third pass on PR #28. `_capture_layout` spends seconds in a process
sweep, a transcript-corpus walk and a per-window UIA scan, so a restore that
starts *inside* those scans writes its marker after the entrance check has
already passed. The layout is then committed from a snapshot taken while
sessions were still coming up — the exact write this guard exists to stop.
"""
from __future__ import annotations

import types

import pytest

from conftest import make_layout, make_window
import reloaded.__main__ as main_mod
import reloaded.deploy as deploy_mod
from reloaded.layout import Tab


def _plan(n):
    return [deploy_mod.PlanEntry(
        id="w1", state="normal", rect=[0, 0, 800, 600],
        tabs=[Tab(cwd=rf"C:\repos\r{i}", title=f"r{i}") for i in range(n)],
        skipped=[], missing=[],
        delays=[i * deploy_mod.STAGGER_SECONDS for i in range(n)],
        argv=["wt"],
    )]


def _args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "unattended": False,
         "force": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


@pytest.fixture
def capture(monkeypatch, tmp_path):
    """A capture whose scans take long enough for a restore to begin inside
    them — which is the real shape, not an invented one: the UIA tab scan alone
    is driven per window against a Terminal that is creating tabs."""
    saved = []
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")
    monkeypatch.setattr(main_mod, "log_path", lambda: tmp_path / "reloaded.log")
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    monkeypatch.setattr(main_mod.discover_mod, "title_to_cwd", lambda idx: {})
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda: {})
    monkeypatch.setattr(main_mod.transcript_mod, "prune_torn_backups", lambda d: [])
    monkeypatch.setattr(main_mod.discover_mod, "default_projects_dir", lambda: tmp_path)
    monkeypatch.setattr(main_mod.layout_mod, "save", lambda lo, p: saved.append(lo))

    def _slow_scan(*a, **k):
        # An `up` begins while this capture is still reading the desktop.
        deploy_mod.mark_restoring(_plan(13))
        return make_layout([make_window(
            [0, 0, 800, 600], [Tab(cwd=r"C:\repos\app", title="app")])])

    monkeypatch.setattr(main_mod.capture_mod, "capture_live", _slow_scan)
    return saved


def test_a_restore_starting_mid_capture_still_stops_the_write(capture, capsys):
    assert main_mod.cmd_capture(_args()) == 1
    assert capture == [], "committed a layout captured while sessions were coming up"
    assert "still starting" in capsys.readouterr().out


def test_force_still_writes_through_the_late_check(capture):
    assert main_mod.cmd_capture(_args(force=True)) == 0
    assert capture


def test_a_restart_starting_mid_capture_does_not_save_either(monkeypatch, tmp_path):
    """`restart` has the same two-step: check, scan, save — and then deploys
    from what it saved, so the sessions it missed are not relaunched."""
    saved = []
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")
    monkeypatch.setattr(main_mod, "log_path", lambda: tmp_path / "reloaded.log")
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    monkeypatch.setattr(main_mod.discover_mod, "title_to_cwd", lambda idx: {})
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda: {})
    monkeypatch.setattr(main_mod.layout_mod, "save", lambda lo, p: saved.append(lo))
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", lambda *a, **k: [])
    monkeypatch.setattr(main_mod, "_deploy_layout", lambda lo, args: 0)

    def _slow_scan(*a, **k):
        deploy_mod.mark_restoring(_plan(13))
        return make_layout([make_window(
            [0, 0, 800, 600], [Tab(cwd=r"C:\repos\app", title="app")])])

    monkeypatch.setattr(main_mod.capture_mod, "capture_live", _slow_scan)

    assert main_mod.cmd_restart(_args(dry_run=False, repos=[], self_=False)) == 1
    assert saved == []


def test_the_editors_first_capture_stands_down_too(monkeypatch, tmp_path, capsys):
    """`cmd_edit` captures when the named layout does not exist yet, so
    `reloaded --layout new edit` followed by `s` is another way to persist a
    half-restored desktop. The `c` command was guarded; this path was not."""
    deploy_mod.mark_restoring(_plan(13))
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "missing.json")

    def _must_not_capture(*a, **k):
        raise AssertionError("captured while a restore was still starting")

    monkeypatch.setattr(main_mod, "_capture_layout", _must_not_capture)

    assert main_mod.cmd_edit(_args()) == 1
    assert "still starting" in capsys.readouterr().out


def test_the_editor_opens_an_existing_layout_during_a_restore(monkeypatch, tmp_path):
    """Only the capture is refused. Editing a layout already on disk reads
    nothing from the live desktop and is none of this guard's business."""
    deploy_mod.mark_restoring(_plan(13))
    path = tmp_path / "default.json"
    main_mod.layout_mod.save(make_layout([make_window(
        [0, 0, 800, 600], [Tab(cwd=r"C:\repos\app", title="app")])]), path)
    monkeypatch.setattr(main_mod, "layout_path", lambda name: path)
    monkeypatch.setattr(main_mod.editor_mod, "run", lambda lo, p, root: 0)

    assert main_mod.cmd_edit(_args()) == 0
