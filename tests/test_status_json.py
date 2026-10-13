"""`status --json`: the same answers as the text, for something that parses.

Both renderings are built from one collected `_Status`, so agreement between
them proves only that they share it. The expectations here are therefore also
written out independently: each session's own agent on a mixed-agent desktop,
two sessions sharing a directory, a saved tab whose directory is gone, and two
unsaved sessions of one kind in one directory.
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import types

import pytest

import reloaded.__main__ as main_mod
import reloaded.discover as discover_mod
from conftest import MONITORS, make_layout, make_window
from reloaded.layout import Tab, save
from reloaded.paths import norm

APP = r"C:\repos\app"
BETA = r"C:\repos\beta"
GONE = r"C:\repos\gone"
GAMMA = r"C:\repos\gamma"


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    """Saved: app as Claude and Codex in w1; beta as Codex and a deleted repo
    in w2. Live: both of app's, a Claude session in beta - the wrong agent for
    beta's tab - and two Codex sessions in gamma that no tab claims."""
    path = tmp_path / "default.json"
    save(make_layout([
        make_window([0, 0, 800, 600], [Tab(cwd=APP, title="app", agent="claude"),
                                       Tab(cwd=APP, title="app", agent="codex")]),
        make_window([800, 0, 800, 600], [Tab(cwd=BETA, title="beta", agent="codex"),
                                         Tab(cwd=GONE, title="gone")]),
    ]), path)
    monkeypatch.setattr(main_mod, "layout_path", lambda name: path)
    monkeypatch.setattr(main_mod.discover_mod, "census", lambda: discover_mod.Census({
        norm(APP): [(11, "claude"), (12, "codex")],
        norm(BETA): [(15, "claude")],
        norm(GAMMA): [(13, "codex"), (14, "codex")],
    }))
    monkeypatch.setattr(main_mod.os.path, "isdir", lambda p: p != GONE)
    monkeypatch.setattr(main_mod.win32_mod, "list_monitors", lambda: MONITORS)
    monkeypatch.setattr(main_mod.readiness_mod, "wait_for_ready",
                        lambda lo, timeout: (True, "ready"))
    monkeypatch.setattr(main_mod.readiness_mod, "check_wt_version",
                        lambda: (False, "Windows Terminal 1.20 is older than verified"))
    return path


def _run(capsys, *, as_json):
    args = types.SimpleNamespace(layout="default", repos_root=r"C:\repos",
                                 json=as_json)
    rc = main_mod.cmd_status(args)
    return rc, capsys.readouterr().out


def _doc(capsys):
    rc, out = _run(capsys, as_json=True)
    return rc, json.loads(out)  # the whole of stdout: no prose around it


def test_each_live_session_has_its_own_agent_and_pid(desktop, capsys):
    _rc, doc = _doc(capsys)

    assert doc["schema"] == main_mod.STATUS_SCHEMA
    assert doc["live"] == [
        {"path": norm(APP), "pid": 11, "agent": "claude"},
        {"path": norm(APP), "pid": 12, "agent": "codex"},
        {"path": norm(BETA), "pid": 15, "agent": "claude"},
        {"path": norm(GAMMA), "pid": 13, "agent": "codex"},
        {"path": norm(GAMMA), "pid": 14, "agent": "codex"},
    ]
    assert doc["shared"] == [{"path": norm(APP), "agents": ["claude", "codex"]},
                             {"path": norm(GAMMA), "agents": ["codex", "codex"]}]


def test_each_saved_tab_is_judged_by_its_own_agent(desktop, capsys):
    """beta's Codex tab would launch although beta has a live session: it is a
    Claude one, which is reported as unsaved instead."""
    _rc, doc = _doc(capsys)

    assert [(t["window"], t["path"], t["agent"], t["state"]) for t in doc["tabs"]] == [
        ("w1", APP, "claude", "running"),
        ("w1", APP, "codex", "running"),
        ("w2", BETA, "codex", "launch"),
        ("w2", GONE, "claude", "missing"),
    ]
    assert {"path": norm(BETA), "agent": "claude"} in doc["not_in_layout"]


def test_a_running_session_is_running_even_where_its_directory_is_gone(
        desktop, capsys, monkeypatch):
    """Claimed before the directory is checked, as deploy.plan_deploy does: a
    session that is live is not one `up` would skip as missing."""
    monkeypatch.setattr(main_mod.os.path, "isdir", lambda p: False)

    _rc, doc = _doc(capsys)

    assert [t["state"] for t in doc["tabs"]] == ["running", "running",
                                                  "missing", "missing"]
    assert doc["would_launch"] == 0


def test_a_tab_whose_directory_is_gone_is_not_counted_as_a_launch(desktop, capsys):
    """`up` skips it (deploy.plan_deploy), so counting it would promise a
    launch that does not happen."""
    _rc, doc = _doc(capsys)
    _rc, text = _run(capsys, as_json=False)

    assert doc["would_launch"] == 1
    assert "`up` would launch 1 session(s)." in text
    assert re.search(r"^ {6}missing +gone$", text, re.M)


def test_every_unsaved_session_is_counted_not_every_directory(desktop, capsys):
    """Two unsaved Codex sessions in gamma are two, which the set of rendered
    lines this replaced reported as one."""
    _rc, doc = _doc(capsys)
    _rc, text = _run(capsys, as_json=False)

    gamma = [o for o in doc["not_in_layout"] if o["path"] == norm(GAMMA)]
    assert gamma == [{"path": norm(GAMMA), "agent": "codex"}] * 2
    assert "[drift] 3 live session(s) are not in the layout" in text
    assert text.count(f"    {norm(GAMMA)} (codex)\n") == 2


def test_the_json_and_the_text_give_the_same_answers(desktop, capsys):
    text_rc, text = _run(capsys, as_json=False)
    json_rc, doc = _doc(capsys)

    assert text_rc == json_rc == 0
    # The text's count is per directory, and says so by listing directories.
    assert f"live sessions : {len({s['path'] for s in doc['live']})}" in text
    assert f"`up` would launch {doc['would_launch']} session(s)." in text
    assert (f"[drift] {len(doc['not_in_layout'])} live session(s) are not "
            "in the layout") in text
    marks = re.findall(r"^ {6}(running|would launch|missing) ", text, re.M)
    assert marks == [{"launch": "would launch"}.get(t["state"], t["state"])
                     for t in doc["tabs"]]
    assert f"[warn] terminal: {doc['terminal']['detail']}" in text
    assert doc["terminal"]["ok"] is False and doc["readiness"]["ok"] is True


def test_no_layout_is_still_one_json_document_and_a_failure(tmp_path, monkeypatch, capsys):
    """Without a layout there is nothing to diff, and no terminal query.

    Guarded at the executor rather than at the query: the query runs on a
    worker thread, where a failing stub raises into a future nobody reads.
    """
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "none.json")
    monkeypatch.setattr(main_mod.discover_mod, "census", lambda: discover_mod.Census(
        {norm(APP): [(11, "claude")]}))
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor",
                        lambda *a, **k: pytest.fail("a terminal query with no layout"))

    rc, doc = _doc(capsys)

    assert rc == 1
    assert doc["layout"] == {"path": str(tmp_path / "none.json"), "exists": False,
                             "saved": None}
    assert doc["live"] == [{"path": norm(APP), "pid": 11, "agent": "claude"}]
    assert "tabs" not in doc and "would_launch" not in doc


def test_status_asks_for_one_census_and_no_other_walk(desktop, capsys, monkeypatch):
    """It used to walk the process table twice: once for the live sessions,
    again to count them per agent."""
    walks = []
    census = main_mod.discover_mod.census
    monkeypatch.setattr(main_mod.discover_mod, "census",
                        lambda: walks.append(1) or census())
    for walk in ("sweep", "running", "live_sessions", "live_agents"):
        monkeypatch.setattr(main_mod.discover_mod, walk,
                            lambda: pytest.fail("a second process walk"))

    _run(capsys, as_json=False)

    assert walks == [1]


def test_the_flag_is_on_the_status_command():
    assert main_mod.build_parser().parse_args(["status", "--json"]).json is True
    assert main_mod.build_parser().parse_args(["status"]).json is False
