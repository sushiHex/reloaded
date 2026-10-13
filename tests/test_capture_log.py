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


def test_log_writes_exactly_what_it_prints(monkeypatch, tmp_path, capsys):
    """The one pre-existing behaviour this change touches.

    `_log` built one line and used it for both halves. Splitting the file write
    out into `_record` briefly gave each half its own `now()`, so the console
    and the log could disagree by a second whenever the write landed across a
    tick - and lining those two up is the whole reason anyone reads both for
    the same run.

    Nothing caught it: every test of the deploy path monkeypatches `_log`
    wholesale, so the seam inside it was never exercised. The clock is stubbed
    to tick on every read, which turns a race into a certainty - a second read
    takes a different second and the two lines cannot match.
    """
    import datetime as dt

    log = tmp_path / "reloaded.log"
    monkeypatch.setattr(main_mod, "log_path", lambda: log)

    ticks = iter([dt.datetime(2026, 1, 1, 0, 0, s) for s in range(10)])

    class _Ticking(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return next(ticks)

    monkeypatch.setattr(dt, "datetime", _Ticking)

    main_mod._log("readiness: ready")

    printed = capsys.readouterr().out
    assert printed == log.read_text(encoding="utf-8")
    assert printed == "2026-01-01 00:00:00  readiness: ready\n"


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


def test_a_loss_is_printed_as_well_as_recorded(capture, capsys):
    """The console said nothing about losses at all.

    `_print_capture_summary` prints a window count and a tree, so someone
    watching a capture drop three repos saw a cheerful "Captured 4 window(s)"
    and had to go read a log file to find out. Recorded for the run nobody
    watches; printed for the run somebody does.
    """
    capture["run"]()
    capture["live"] = _layout("app")
    capsys.readouterr()

    capture["run"]()

    out = capsys.readouterr().out
    assert "dropped 1 tab(s)" in out
    assert r"c:\repos\beta" in out


def test_a_gain_is_recorded_but_not_announced(capture, capsys):
    """The summary underneath already shows it. A warning for the ordinary
    case trains people to skim past the line that matters."""
    capture["live"] = _layout("app")
    capture["run"]()
    capture["live"] = _layout("app", "beta")
    capsys.readouterr()

    written = capture["run"]()

    assert "beta" in written
    assert "dropped" not in capsys.readouterr().out


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


# ── per tab, not per directory (#20) ─────────────────────────────────────


def _tabs(*tabs):
    """Tabs given as (repo, agent, command)."""
    return make_layout([make_window(
        [0, 0, 800, 600],
        [Tab(cwd=rf"C:\repos\{r}", title=r, agent=a, command=c) for r, a, c in tabs],
    )])


def _lines(written):
    return [line for line in written.splitlines() if "[reloaded] layout" in line]


def test_losing_one_of_two_tabs_in_a_directory_is_recorded(capture):
    """The loss the directory-level record could not see: `retro` still had a
    tab, so nothing had changed - and its Codex session was never restored."""
    capture["live"] = _tabs(("retro", "claude", ""), ("retro", "codex", ""))
    capture["run"]()
    capture["live"] = _tabs(("retro", "claude", ""))

    [line] = _lines(capture["run"]())[1:]

    assert r"lost 1 (codex c:\repos\retro)" in line


def test_a_tab_changing_kind_is_a_loss_and_a_gain(capture):
    capture["live"] = _tabs(("app", "claude", ""))
    capture["run"]()
    capture["live"] = _tabs(("app", "codex", ""))

    [line] = _lines(capture["run"]())[1:]

    assert r"lost 1 (claude c:\repos\app)" in line
    assert r"gained 1 (codex c:\repos\app)" in line


def test_a_changed_command_is_recorded_once(capture):
    """Including one that went empty: an unreadable process means the tab now
    comes back with its kind's default flags rather than its own."""
    capture["live"] = _tabs(("app", "claude", "claude --continue"))
    capture["run"]()
    capture["live"] = _tabs(("app", "claude", ""))

    written = capture["run"]()
    again = capture["run"]()

    [line] = _lines(written)[1:]
    assert (r"changed the command of 1 (claude c:\repos\app: "
            "`claude --continue` -> (default))") in line
    assert again == written, "an unchanged save recorded something"


def test_a_command_flapping_back_is_recorded_each_way():
    """Each save changes what that tab comes back as, so each is a line."""
    a = _tabs(("app", "claude", "claude --continue"))
    b = _tabs(("app", "claude", ""))
    tc = main_mod.layout_mod.tab_changes

    assert tc(a, b)[2] == [((r"c:\repos\app", "claude"), "claude --continue", "")]
    assert tc(b, a)[2] == [((r"c:\repos\app", "claude"), "", "claude --continue")]


def test_a_tab_lost_beside_a_changed_one_says_both():
    """Two tabs of one kind in one directory, one gone and the other now
    resuming something else: a per-key count would have said only "lost 1"."""
    before = _tabs(("app", "claude", "claude --resume a"),
                   ("app", "claude", "claude --resume b"))
    after = _tabs(("app", "claude", "claude --resume c"))

    lost, gained, changed = main_mod.layout_mod.tab_changes(before, after)

    assert lost == [(r"c:\repos\app", "claude")]
    assert gained == []
    assert len(changed) == 1 and changed[0][2] == "claude --resume c"


def test_a_malformed_saved_command_cannot_fail_the_record():
    """`from_dict` keeps whatever the file held. Compared after the save has
    already happened, a non-string there must not raise."""
    before = _tabs(("app", "claude", "claude"), ("app", "claude", "x"))
    before.windows[0].tabs[1].command = 1

    main_mod.layout_mod.tab_changes(before, _tabs(("app", "claude", "claude")))


def test_the_line_names_the_layout_and_its_writer(capture):
    [line] = _lines(capture["run"]())

    assert line.split("  ", 1)[1].startswith("[reloaded] layout default gained 2 (")
    assert line.endswith("[capture]")


def test_an_editor_save_is_recorded(monkeypatch, tmp_path):
    """It is the one writer that went around the record, so a tab deleted by
    hand left no trace."""
    log, path = tmp_path / "reloaded.log", tmp_path / "default.json"
    monkeypatch.setattr(main_mod, "log_path", lambda: log)
    monkeypatch.setattr(main_mod, "layout_path", lambda name: path)
    main_mod.layout_mod.save(_layout("app", "beta"), path)
    typed = iter(["d 1.2", "s"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))

    main_mod.cmd_edit(_args())

    [line] = _lines(log.read_text(encoding="utf-8"))
    assert r"layout default lost 1 (claude c:\repos\beta) [editor]" in line
