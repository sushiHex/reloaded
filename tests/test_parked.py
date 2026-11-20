"""Parked tabs (parked.py): kept by capture, reopened waiting by deploy,
skipped while open, and never standing in for a live session.

The tab's own shell writes and removes the records; that half runs in real
PowerShell in test_restart_loop.py. These cover what reads them.
"""
from __future__ import annotations

import json
import types

import pytest

import reloaded.__main__ as main_mod
from reloaded import capture as capture_mod, deploy as deploy_mod, discover as discover_mod
from reloaded import parked as parked_mod
from reloaded.layout import Tab, save
from reloaded.paths import norm, parked_record
from reloaded.win32 import Proc

from conftest import MONITORS, make_layout, make_window

APP = r"C:\repos\app"
SITE = r"C:\repos\site"
RESUME = "codex resume 01a045f4-a5ff-7ee1-ab94-d1ac3e775990 --no-daemon"


def _park(cwd=APP, agent="codex", command=RESUME, pid=4242, created=1_700_000_000.0):
    parked_record(cwd, agent).write_text(json.dumps({
        "cwd": cwd, "agent": agent, "command": command,
        "shell_pid": pid, "shell_created": int(created * 1000)}), encoding="utf-8")
    return pid, created


def _table(pid=None, created=None):
    return {} if pid is None else {pid: Proc(1, "pwsh.exe", created)}


def test_a_record_reads_back_and_is_open_only_for_its_own_shell():
    pid, created = _park()
    [r] = parked_mod.load_all()
    assert (r.cwd, r.agent, r.command, r.shell_pid) == (APP, "codex", RESUME, pid)
    assert parked_mod.is_open(r, _table(pid, created))
    # The same pid, reused by a later process: not the tab.
    assert not parked_mod.is_open(r, _table(pid, created + 60))
    assert not parked_mod.is_open(r, _table())


def test_a_record_that_cannot_be_read_is_skipped():
    _park()
    parked_record(SITE, "codex").write_text("{not json", encoding="utf-8")
    assert [r.cwd for r in parked_mod.load_all()] == [APP]


def test_unpark_removes_every_kind_s_record_for_the_directory(capsys):
    _park(agent="codex")
    _park(agent="claude")
    args = types.SimpleNamespace(repo=APP, repos_root=r"C:\repos")
    assert main_mod.cmd_unpark(args) == 0
    assert parked_mod.load_all() == []
    assert main_mod.cmd_unpark(args) == 1
    assert "Not parked" in capsys.readouterr().out


# --- deploy -----------------------------------------------------------------

def _plan(tabs, table, monkeypatch):
    monkeypatch.setattr(deploy_mod.win32, "process_table", lambda: table)
    monkeypatch.setattr(deploy_mod.os.path, "isdir", lambda p: True)
    lo = make_layout([make_window([0, 0, 800, 600], tabs)])
    return deploy_mod.plan_deploy(lo, {}, MONITORS, {}, running=discover_mod.Census({}).running())


def test_a_closed_parked_tab_reopens_waiting_with_its_record_s_command(monkeypatch):
    _park()
    [entry] = _plan([Tab(cwd=SITE, title="site", agent="codex"),
                     Tab(cwd=APP, title="app", agent="codex", command="codex resume --last",
                         session_id="old")], {}, monkeypatch)
    site, app = entry.tabs
    assert app.parked and not site.parked
    assert app.command == RESUME and app.session_id == ""
    # It starts nothing, so it takes no turn in the stagger.
    assert entry.delays == [0, 0]
    tab_command = entry.rest_argv[-1]
    assert "$rlParked=$true" in tab_command and RESUME in tab_command


def test_an_open_parked_tab_is_skipped_like_a_running_one(monkeypatch):
    pid, created = _park()
    plan = _plan([Tab(cwd=APP, title="app", agent="codex")], _table(pid, created), monkeypatch)
    assert plan == []


def test_a_tab_saved_parked_but_since_unparked_starts_its_session(monkeypatch):
    """`unpark`, or Enter in the tab, removed the record before any capture
    rewrote the layout: the record decides, so the tab starts - rather than
    opening parked and writing its record straight back."""
    [entry] = _plan([Tab(cwd=APP, title="app", agent="codex", parked=True)], {}, monkeypatch)
    [tab] = entry.tabs
    assert not tab.parked
    assert "$rlSkip=$false" in entry.argv[-1]


def test_one_record_parks_one_of_two_same_kind_tabs(monkeypatch):
    """The one saved parked; its sibling is an ordinary session."""
    _park()
    [entry] = _plan([Tab(cwd=APP, title="app", agent="codex", command="codex resume a"),
                     Tab(cwd=APP, title="app", agent="codex", parked=True)], {}, monkeypatch)
    assert [t.parked for t in entry.tabs] == [False, True]
    assert entry.tabs[0].command == "codex resume a"


def test_a_parked_claude_tab_s_transcript_is_not_repaired():
    tab = Tab(cwd=APP, title="app", agent="claude", parked=True)
    entry = types.SimpleNamespace(tabs=[tab])
    index = {norm(APP): types.SimpleNamespace(path="t.jsonl")}
    assert deploy_mod.transcripts_to_repair([entry], index) == []


def test_open_refuses_a_parked_repo(capsys, monkeypatch):
    """Started here, it would take its conversation back from the phone."""
    _park()
    monkeypatch.setattr(main_mod.os.path, "isdir", lambda p: True)
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    args = types.SimpleNamespace(repo=APP, repos_root=r"C:\repos", dry_run=True,
                                 layout="default")
    assert main_mod.cmd_open(args) == 1
    assert "Parked" in capsys.readouterr().out


def test_a_parked_tab_is_not_reported_as_failing_to_start():
    tab = Tab(cwd=APP, title="app", agent="codex", parked=True)
    entry = types.SimpleNamespace(tabs=[tab], delays=[0], rest_argv=[])
    result = types.SimpleNamespace(spawned_at=0.0, rest_spawned_at=None, rest_error="")
    assert main_mod._never_started([entry], [result], {}, elapsed=600.0) == []
    assert main_mod._still_starting([entry], [result], elapsed=0.0) == []


# --- capture ----------------------------------------------------------------

def test_capture_keeps_an_open_parked_tab_in_its_place(monkeypatch):
    """Its shell stands in for its session, placed by the window that shell
    is in and by its title, between the live tabs either side of it."""
    monkeypatch.setattr(capture_mod.discover, "held_threads", lambda pid, kind: [])
    monkeypatch.setattr(capture_mod.discover, "session_command", lambda pid: "codex")
    pid, created = _park()
    record = parked_mod.load_all()[0]
    sessions = [discover_mod.Session(1, SITE, "codex", 1.0, 77),
                discover_mod.Session(2, r"C:\repos\meta", "codex", 2.0, 77),
                discover_mod.Session(pid, APP, "codex", created, 77)]
    live = {norm(SITE): 1, norm(r"C:\repos\meta"): 2, norm(APP): pid}
    lo = capture_mod.build_layout(
        [{"hwnd": 77, "rect": [0, 0, 800, 600], "state": "normal", "monitor": "m", "dpi": 96,
          "titles": ["site⁠", "app⁠", "meta⁠"]}],
        MONITORS, {}, live, r"C:\repos", "t", sessions=sessions, parked={pid: record})
    tabs = lo.windows[0].tabs
    assert [t.title for t in tabs] == ["site", "app", "meta"]
    assert [t.parked for t in tabs] == [False, True, False]
    assert tabs[1].command == RESUME


def test_capture_keeps_a_closed_parked_tab_in_its_saved_slot():
    previous = make_layout([
        make_window([0, 0, 800, 600], [Tab(cwd=SITE, title="site", agent="codex"),
                                       Tab(cwd=APP, title="app", agent="codex", command=RESUME),
                                       Tab(cwd=r"C:\repos\meta", title="meta")]),
        make_window([800, 0, 800, 600], [Tab(cwd=r"C:\repos\lone", title="lone",
                                             agent="codex")]),
    ])
    fresh = make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=SITE, title="site", agent="codex"), Tab(cwd=r"C:\repos\meta", title="meta")])])
    closed = {(norm(APP), "codex"), (norm(r"C:\repos\lone"), "codex")}
    out = capture_mod.carry_parked(fresh, previous, closed)
    assert [t.title for t in out.windows[0].tabs] == ["site", "app", "meta"]
    assert out.windows[0].tabs[1].parked
    # Its window is gone from the capture: the saved one comes back for it.
    assert out.windows[1].rect == [800, 0, 800, 600]
    assert [t.title for t in out.windows[1].tabs] == ["lone"]


def test_a_parked_tab_alone_in_its_window_is_not_moved_into_another():
    """Its window closed; the window now first in the capture is a different
    one, and is not where it was."""
    previous = make_layout([
        make_window([0, 0, 800, 600], [Tab(cwd=APP, title="app", agent="codex")]),
        make_window([800, 0, 800, 600], [Tab(cwd=SITE, title="site", agent="codex")]),
    ])
    fresh = make_layout([make_window([800, 0, 800, 600], [
        Tab(cwd=SITE, title="site", agent="codex")])])
    out = capture_mod.carry_parked(fresh, previous, {(norm(APP), "codex")})
    assert [[t.title for t in w.tabs] for w in out.windows] == [["site"], ["app"]]
    assert out.windows[1].rect == [0, 0, 800, 600]


def test_a_parked_tab_in_a_window_capture_could_not_read_is_kept(monkeypatch):
    """Its shell is open, but its window's tabs timed out: nothing placed it,
    so the saved layout's place for it stands."""
    pid, created = _park()
    previous = make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=APP, title="app", agent="codex", command=RESUME)])])
    monkeypatch.setattr(capture_mod.win32, "list_monitors", lambda: MONITORS)
    monkeypatch.setattr(capture_mod.win32, "list_wt_windows", lambda: [77])
    monkeypatch.setattr(capture_mod.tabs, "list_tab_items", lambda hwnd: [])
    monkeypatch.setattr(capture_mod.win32, "process_table", lambda: _table(pid, created))
    monkeypatch.setattr(capture_mod.win32, "tab_shells", lambda: {pid: 77})
    monkeypatch.setattr(capture_mod.discover, "sessions", lambda: [])
    lo = capture_mod.capture_live(r"C:\repos", previous, live={}, title_map={})
    assert [(t.title, t.parked) for w in lo.windows for t in w.tabs] == [("app", True)]


def test_a_parked_tab_does_not_stand_in_for_a_lost_live_sibling(tmp_path, monkeypatch):
    """Two Codex tabs in one directory: one parked, one live. A capture that
    lost the live one's window still holds the parked one - which must not
    count as the live one, or the loss is saved."""
    _park()
    path = tmp_path / "default.json"
    save(make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=APP, title="app", agent="codex"),
        Tab(cwd=APP, title="app", agent="codex", parked=True)])]), path)
    fresh = make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=APP, title="app", agent="codex", parked=True)])])
    monkeypatch.setattr(main_mod.discover_mod, "running",
                        lambda: discover_mod.Census({norm(APP): [(9, "codex")]}).running())
    assert main_mod._capture_shrinkage(fresh, path, {norm(APP): 9}) != ""


# --- status -----------------------------------------------------------------

@pytest.fixture
def desktop(tmp_path, monkeypatch):
    path = tmp_path / "default.json"
    save(make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=APP, title="app", agent="codex"), Tab(cwd=SITE, title="site", agent="codex")])]),
        path)
    monkeypatch.setattr(main_mod, "layout_path", lambda name: path)
    monkeypatch.setattr(main_mod.discover_mod, "census",
                        lambda: discover_mod.Census({norm(SITE): [(5, "codex")]}))
    monkeypatch.setattr(main_mod.discover_mod, "session_command",
                        lambda pid: "codex --no-daemon")
    monkeypatch.setattr(main_mod.os.path, "isdir", lambda p: True)
    monkeypatch.setattr(main_mod.win32_mod, "list_monitors", lambda: MONITORS)
    monkeypatch.setattr(main_mod.win32_mod, "process_table", lambda: {})
    monkeypatch.setattr(main_mod.readiness_mod, "wait_for_ready", lambda lo, timeout: (True, "ok"))
    monkeypatch.setattr(main_mod.readiness_mod, "check_wt_version", lambda: (True, "wt"))
    return path


def test_status_names_a_parked_tab(desktop, capsys):
    _park()
    args = types.SimpleNamespace(layout="default", repos_root=r"C:\repos", json=True)
    main_mod.cmd_status(args)
    doc = json.loads(capsys.readouterr().out)
    assert [t["state"] for t in doc["tabs"]] == ["parked", "running"]
    assert doc["parked"] == [{"path": APP, "agent": "codex", "open": False}]
    assert doc["would_launch"] == 0
