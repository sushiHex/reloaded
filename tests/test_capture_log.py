"""A capture that changes the layout says so in the log.

The reconcile task runs `capture` every five minutes, forever, under `pyw.exe`
so that nothing it prints has anywhere to go. Before this, it left no trace of
any kind: `~/.reloaded/reloaded.log` held deploy lines from `up` and not one
line from the thousands of captures that had rewritten the layout underneath
them. A reboot took sessions out of the layout and there was nothing to say
which, or when, or why.

Recorded unconditionally rather than only when unattended, unlike the deploy
path. Whether anyone was watching is a fact about the run; that the layout lost
a repo is a fact about the user's state, and it is worth the same line either
way.

Silent when nothing changed. A line every five minutes saying "10 sessions,
same as last time" is 288 a day, and a log nobody can skim is the same as no
log - which is the thing being fixed.
"""
from __future__ import annotations

import types

import pytest

from conftest import make_layout, make_window
import reloaded.__main__ as main_mod
from reloaded.layout import Tab


def _args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "unattended": False,
         "force": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


def _layout(*repos):
    return make_layout([make_window(
        [0, 0, 800, 600],
        [Tab(cwd=rf"C:\repos\{r}", title=r) for r in repos],
    )])


@pytest.fixture
def capture(monkeypatch, tmp_path):
    """`cmd_capture` with the desktop replaced and the log redirected."""
    log = tmp_path / "reloaded.log"
    path = tmp_path / "default.json"
    state = {"live": _layout("app", "beta"), "log": log, "path": path}

    monkeypatch.setattr(main_mod, "layout_path", lambda name: path)
    monkeypatch.setattr(main_mod, "log_path", lambda: log)
    monkeypatch.setattr(main_mod.capture_mod, "capture_live",
                        lambda *a, **k: state["live"])
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    monkeypatch.setattr(main_mod.discover_mod, "title_to_cwd", lambda idx: {})
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda: {})
    monkeypatch.setattr(main_mod.transcript_mod, "prune_torn_backups", lambda d: [])
    monkeypatch.setattr(main_mod.discover_mod, "default_projects_dir", lambda: tmp_path)

    def run(**kw):
        main_mod.cmd_capture(_args(**kw))
        return log.read_text(encoding="utf-8") if log.exists() else ""

    state["run"] = run
    return state


def test_losing_a_repo_is_recorded_with_the_names(capture):
    capture["run"]()
    capture["live"] = _layout("app")

    written = capture["run"]()

    assert "beta" in written
    assert "lost" in written.lower()


def test_the_record_says_where_the_previous_layout_was_kept(capture):
    """A line that says something was lost and not how to get it back names a
    problem and withholds the answer."""
    capture["run"]()
    capture["live"] = _layout("app")

    written = capture["run"]()

    assert "default.prev" in written


def test_gaining_a_repo_is_recorded(capture):
    capture["live"] = _layout("app")
    capture["run"]()
    capture["live"] = _layout("app", "beta")

    written = capture["run"]()

    assert "beta" in written


def test_an_unchanged_layout_records_nothing(capture):
    """288 lines a day of "same as last time" is a log nobody skims."""
    capture["run"]()
    before = capture["log"].read_text(encoding="utf-8") if capture["log"].exists() else ""

    capture["run"]()

    after = capture["log"].read_text(encoding="utf-8") if capture["log"].exists() else ""
    assert after == before


def test_moving_a_window_records_nothing(capture):
    """Geometry churn is what the reconcile mostly does."""
    before = capture["run"]()
    capture["live"] = make_layout([make_window(
        [900, 900, 800, 600],
        [Tab(cwd=r"C:\repos\app", title="app"), Tab(cwd=r"C:\repos\beta", title="beta")],
    )])

    assert capture["run"]() == before


def test_the_first_capture_records_what_it_created(capture):
    """The one run where "gained" is the whole layout. Worth a line: it is the
    start of the history every later line is a delta against."""
    written = capture["run"]()

    assert "gained 2" in written
    assert r"c:\repos\app" in written


def test_a_refusal_is_recorded(capture, monkeypatch):
    """The refusal is the run doing its job, and it is the one outcome where
    the layout on disk no longer matches what is running. Unattended, it was
    previously invisible."""
    capture["run"]()
    monkeypatch.setattr(main_mod, "_capture_shrinkage",
                        lambda fresh, path, live: "1 running session(s) missing — beta")
    capture["live"] = _layout("app")

    written = capture["run"]()

    assert "refus" in written.lower()
    assert "beta" in written


def test_the_record_does_not_wait_for_an_unattended_run(capture):
    """The deploy log only exists when nobody is watching. This one is about
    what happened to the user's saved state, which is worth the same line
    whether or not a console was attached."""
    capture["run"]()
    capture["live"] = _layout("app")

    written = capture["run"](unattended=False)

    assert "beta" in written
