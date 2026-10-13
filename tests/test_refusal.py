"""A refusal that repeats every reconcile is said once, then periodically.

The reconcile runs `capture` every five minutes. Recording every refusal wrote
288 identical lines a day for any condition that persisted (#24); and a
capture that read no tab at all returned before the guard and wrote nothing,
so total failure was silent where partial failure was deafening.
"""
from __future__ import annotations

import datetime
import types

import pytest

from conftest import make_layout, make_window
import reloaded.__main__ as main_mod
import reloaded.paths as paths_mod
import reloaded.refusal as refusal_mod
from reloaded.layout import Tab

T0 = datetime.datetime(2026, 10, 1, 9, 30, 0).timestamp()
HOUR = 3600.0


@pytest.fixture
def log():
    lines = []

    def record(line):
        lines.append(line)
        return True

    record.lines = lines
    return record


def test_the_first_refusal_is_said(log):
    refusal_mod.note("default", "refused: beta", log, now=T0)

    assert log.lines == ["refused: beta"]


def test_the_same_refusal_again_is_not(log):
    for i in range(5):
        refusal_mod.note("default", "refused: beta", log, now=T0 + i * 300)

    assert log.lines == ["refused: beta"]


def test_an_ongoing_refusal_says_so_periodically(log):
    """Reads the rendered timestamp, not just the presence of a line: a
    record that once failed to parse reported every attempt as "an unknown
    time" and the tests that only looked for the message could not tell."""
    refusal_mod.note("default", "refused: beta", log, now=T0)
    refusal_mod.note("default", "refused: beta", log, now=T0 + HOUR)
    refusal_mod.note("default", "refused: beta", log, now=T0 + 6 * HOUR)

    assert log.lines[1] == ("refused: beta — still refusing, since "
                            "2026-10-01 09:30:00 (3 times)")


def test_the_heartbeat_restarts_its_interval(log):
    for h in (0, 6, 7, 11.9, 12):
        refusal_mod.note("default", "refused: beta", log, now=T0 + h * HOUR)

    assert len(log.lines) == 3


def test_a_different_refusal_is_said_at_once(log):
    refusal_mod.note("default", "refused: beta", log, now=T0)
    refusal_mod.note("default", "refused: beta, gamma", log, now=T0 + 300)

    assert log.lines == ["refused: beta", "refused: beta, gamma"]


def test_a_save_says_how_long_it_went_on(log):
    refusal_mod.note("default", "refused: beta", log, now=T0)
    refusal_mod.note("default", "refused: beta", log, now=T0 + 300)

    refusal_mod.saved("default", log)

    assert log.lines[-1] == ("[reloaded] layout saved after 2 refusal(s) since "
                             "2026-10-01 09:30:00: refused: beta")


def test_a_forced_save_says_it_was_forced(log):
    """A save past --force proves nothing got fixed; saying only "saved" would
    read as the condition having ended."""
    refusal_mod.note("default", "refused: beta", log, now=T0)

    refusal_mod.saved("default", log, forced=True)

    assert "saved with --force" in log.lines[-1]


def test_a_save_with_nothing_refused_says_nothing(log):
    refusal_mod.saved("default", log)

    assert log.lines == []


def test_a_refusal_that_recurs_after_a_save_is_said_again(log):
    refusal_mod.note("default", "refused: beta", log, now=T0)
    refusal_mod.saved("default", log)

    refusal_mod.note("default", "refused: beta", log, now=T0 + 600)

    assert log.lines[-1] == "refused: beta"


def test_a_line_that_did_not_land_is_not_counted_as_said():
    """Otherwise one failed write hides the first warning for six hours."""
    lines = []

    def record(line):
        lines.append(line)
        return next(landed)

    landed = iter([False, True, True])
    for i in range(3):
        refusal_mod.note("default", "refused: beta", record, now=T0 + i * 300)

    # Retried once it had not landed, then counted as said once it had.
    assert lines == ["refused: beta", "refused: beta"]


@pytest.mark.parametrize("garbage", [
    "{not json",
    '{"message": "refused: beta", "since": 0, "said": NaN, "count": 1}',
    '{"message": "refused: beta", "since": NaN, "said": 0, "count": 1}',
    '{"message": "refused: beta", "since": 0, "said": 0, "count": Infinity}',
])
def test_an_unreadable_record_repeats_rather_than_suppresses(log, garbage):
    """A NaN `said` compares false against every interval, so trusting it
    would silence the refusal for good."""
    refusal_mod.note("default", "refused: beta", log, now=T0)
    refusal_mod._path("default").write_text(garbage, encoding="utf-8")

    refusal_mod.note("default", "refused: beta", log, now=T0 + 300)
    refusal_mod.saved("default", log)

    assert log.lines[:2] == ["refused: beta", "refused: beta"]


@pytest.mark.parametrize("name", [r"..\layouts\default", r"C:\temp\work"])
def test_a_path_shaped_layout_name_cannot_reach_the_layout(name, tmp_path, monkeypatch):
    """`--layout` takes anything. Used as a filename, `..\\layouts\\default`
    resolved to the layout file itself, and the record replaced it."""
    monkeypatch.setattr(paths_mod, "state_dir", lambda: tmp_path)

    p = refusal_mod._path(name)

    assert p.parent == tmp_path / "refusals"
    assert p != paths_mod.layout_path(name)


def test_each_layout_keeps_its_own_record(log):
    refusal_mod.note("work", "refused: beta", log, now=T0)

    refusal_mod.saved("default", log)
    refusal_mod.note("work", "refused: beta", log, now=T0 + 300)

    assert log.lines == ["refused: beta"]


def test_the_record_lives_in_the_redirected_state_dir(tmp_path, monkeypatch):
    """A name imported from paths at load time would miss the suite's
    redirect and write into the user's real ~/.reloaded."""
    monkeypatch.setattr(paths_mod, "state_dir", lambda: tmp_path)

    assert refusal_mod._path("default").parent == tmp_path / "refusals"


# --- through cmd_capture -------------------------------------------------


def _layout(*repos):
    return make_layout([make_window(
        [0, 0, 800, 600], [Tab(cwd=rf"C:\repos\{r}", title=r) for r in repos])])


@pytest.fixture
def capture(monkeypatch, tmp_path):
    log = tmp_path / "reloaded.log"
    state = {"live": _layout("app", "beta"), "running": {}}
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / f"{name}.json")
    monkeypatch.setattr(main_mod, "log_path", lambda: log)
    monkeypatch.setattr(main_mod.capture_mod, "capture_live",
                        lambda *a, **k: state["live"])
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions",
                        lambda: dict(state["running"]))
    monkeypatch.setattr(main_mod.discover_mod, "title_to_cwd", lambda idx: {})
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda: {})
    monkeypatch.setattr(main_mod.transcript_mod, "prune_torn_backups", lambda d: [])
    monkeypatch.setattr(main_mod.discover_mod, "default_projects_dir", lambda: tmp_path)

    def run(**kw):
        args = {"layout": "default", "repos_root": r"C:\repos", "force": False}
        args.update(kw)
        main_mod.cmd_capture(types.SimpleNamespace(**args))
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    state["run"] = run
    return state


def test_a_repeating_shrinkage_refusal_is_one_line(capture, monkeypatch):
    capture["run"]()
    monkeypatch.setattr(main_mod, "_capture_shrinkage",
                        lambda fresh, path, live: "1 running session(s) missing — beta")
    capture["live"] = _layout("app")

    for _ in range(4):
        written = capture["run"]()

    assert sum("refused to overwrite" in line for line in written) == 1


def test_the_save_that_ends_it_is_recorded(capture, monkeypatch):
    capture["run"]()
    monkeypatch.setattr(main_mod, "_capture_shrinkage",
                        lambda fresh, path, live: "1 running session(s) missing — beta")
    capture["live"] = _layout("app")
    capture["run"]()
    capture["run"]()
    monkeypatch.setattr(main_mod, "_capture_shrinkage", lambda fresh, path, live: "")

    written = capture["run"]()

    assert "saved after 2 refusal(s)" in written[-1]


def test_capturing_nothing_while_sessions_run_is_recorded_once(capture):
    """Total failure: every tab unreadable, so the layout comes back empty and
    returned before the shrinkage guard - the one case the log never heard
    of."""
    capture["live"] = make_layout([])
    capture["running"] = {r"c:\repos\app": 1, r"c:\repos\beta": 2}

    capture["run"]()
    written = capture["run"]()

    assert len(written) == 1
    assert "captured nothing" in written[0]
    assert "2 repo(s)" in written[0] and "beta" in written[0]


def test_capturing_nothing_with_nothing_running_is_not_a_refusal(capture):
    capture["live"] = make_layout([])

    assert capture["run"]() == []


def test_unreadable_tabs_are_recorded_once(capture, monkeypatch):
    def _no_uia(*a, **k):
        raise main_mod.tabs_mod.UIAUnavailable("uiautomation is not installed")

    monkeypatch.setattr(main_mod.capture_mod, "capture_live", _no_uia)

    capture["run"]()
    written = capture["run"]()

    assert len(written) == 1
    assert "could not read tabs: uiautomation is not installed" in written[0]


# --- the log's size --------------------------------------------------------


def test_an_oversized_log_is_rotated(monkeypatch, tmp_path):
    log = tmp_path / "reloaded.log"
    log.write_text("x" * 100, encoding="utf-8")
    monkeypatch.setattr(main_mod, "log_path", lambda: log)
    monkeypatch.setattr(main_mod, "LOG_MAX_BYTES", 50)

    assert main_mod._append("fresh")

    assert log.read_text(encoding="utf-8") == "fresh\n"
    assert (tmp_path / "reloaded.log.1").read_text(encoding="utf-8") == "x" * 100


def test_a_rotation_that_fails_still_writes_the_line(monkeypatch, tmp_path):
    """The relaunch helper holds the log open, and Windows will not move an
    open file. That costs the rotation, not the line."""
    log = tmp_path / "reloaded.log"
    log.write_text("x" * 100, encoding="utf-8")
    monkeypatch.setattr(main_mod, "log_path", lambda: log)
    monkeypatch.setattr(main_mod, "LOG_MAX_BYTES", 50)

    tried = []

    def _held_open(self, target):
        tried.append(target)
        raise PermissionError("in use")

    monkeypatch.setattr(type(log), "replace", _held_open)

    assert main_mod._append("fresh")

    assert tried, "never tried to rotate"
    assert log.read_text(encoding="utf-8").endswith("fresh\n")
