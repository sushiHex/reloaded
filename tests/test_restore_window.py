"""A restore is not finished when `execute` returns.

`execute` returns once the last `wt` has been spawned. The four-second stagger
is a `Start-Sleep` inside each launched shell, so a thirteen-session restore is
still starting its last session forty-eight seconds later.

Three things asked "is it up?" and got the answer for a moment that had not
arrived yet. Measured on a real logon, 2026-09-20:

    01:41:07  launched 13 sessions, staggered +0s … +48s
    01:41:12  [reloaded] 12 tab(s) opened but no session started
    01:41:50  [reloaded] layout lost 4 (kakeibo, meta, prepped, techart-career-ops)

The four dropped are exactly the four launched last. Two of them had not been
started at all when the reconcile decided they were gone, and `_capture_shrinkage`
could not object: a session that has not started is not in `live`, which is
indistinguishable from one the user closed.
"""
from __future__ import annotations

import time
import types

import pytest

from conftest import make_layout, make_window
import reloaded.__main__ as main_mod
import reloaded.deploy as deploy_mod
from reloaded.layout import Tab
from reloaded.paths import restore_marker


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr("reloaded.paths.state_dir", lambda: tmp_path)
    return tmp_path


def _plan(n):
    tabs = [Tab(cwd=rf"C:\repos\r{i}", title=f"r{i}") for i in range(n)]
    return [deploy_mod.PlanEntry(
        id="w1", state="normal", rect=[0, 0, 800, 600], tabs=tabs, skipped=[],
        missing=[],
        delays=[i * deploy_mod.STAGGER_SECONDS for i in range(n)],
        argv=["wt"],
    )]


# ── the window a restore is still inside ─────────────────────────────────


def test_the_window_covers_the_last_session_and_then_some(state):
    """The last tab sleeps `(n-1) * STAGGER` before it starts, and then needs
    time to actually start."""
    window = deploy_mod.restore_window(_plan(13))

    assert window > 12 * deploy_mod.STAGGER_SECONDS


def test_a_single_session_restore_still_gets_a_window(state):
    assert deploy_mod.restore_window(_plan(1)) > 0


def test_marking_a_restore_leaves_something_capture_can_read(state):
    deploy_mod.mark_restoring(_plan(3))

    assert restore_marker().exists()
    assert deploy_mod.restoring_for() > 0


def test_the_mark_expires_on_its_own(state, monkeypatch):
    """Nothing can clear it at the right moment: `execute` returns long before
    the sessions it started have finished starting. So it has to lapse."""
    deploy_mod.mark_restoring(_plan(3))
    later = time.time() + deploy_mod.restore_window(_plan(3)) + 1
    monkeypatch.setattr(time, "time", lambda: later)

    assert deploy_mod.restoring_for() == 0


def test_no_mark_means_no_restore(state):
    assert deploy_mod.restoring_for() == 0


def test_an_unreadable_mark_does_not_block_forever(state):
    """A capture that could never save again because of a corrupt marker is a
    worse failure than the one this prevents."""
    restore_marker().write_text("not a number", encoding="utf-8")

    assert deploy_mod.restoring_for() == 0


# ── capture stands down while one is in flight ───────────────────────────


@pytest.fixture
def capture(monkeypatch, state):
    saved = []
    monkeypatch.setattr(main_mod, "layout_path", lambda name: state / "default.json")
    monkeypatch.setattr(main_mod, "log_path", lambda: state / "reloaded.log")
    monkeypatch.setattr(main_mod.capture_mod, "capture_live", lambda *a, **k: make_layout(
        [make_window([0, 0, 800, 600], [Tab(cwd=r"C:\repos\app", title="app")])]))
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    monkeypatch.setattr(main_mod.discover_mod, "title_to_cwd", lambda idx: {})
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda: {})
    monkeypatch.setattr(main_mod.discover_mod, "crowded_dirs", lambda: {})
    monkeypatch.setattr(main_mod.transcript_mod, "prune_torn_backups", lambda d: [])
    monkeypatch.setattr(main_mod.discover_mod, "default_projects_dir", lambda: state)
    monkeypatch.setattr(main_mod.layout_mod, "save",
                        lambda lo, p: saved.append(lo))
    return saved


def _args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "unattended": False,
         "force": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_a_capture_does_not_overwrite_a_layout_mid_restore(capture, state, capsys):
    """The 13 → 9 loss, prevented. Sessions that have not started yet are not
    in `live`, so the shrinkage guard reads them as deliberately closed."""
    deploy_mod.mark_restoring(_plan(13))

    rc = main_mod.cmd_capture(_args())

    assert rc == 1
    assert capture == [], "the layout was overwritten while a restore was starting"
    assert "still starting" in capsys.readouterr().out


def test_the_stand_down_says_how_long_is_left(capture, state, capsys):
    deploy_mod.mark_restoring(_plan(13))

    main_mod.cmd_capture(_args())

    out = capsys.readouterr().out
    assert "s left" in out


def test_a_capture_after_the_window_saves_normally(capture, state, monkeypatch):
    deploy_mod.mark_restoring(_plan(3))
    later = time.time() + deploy_mod.restore_window(_plan(3)) + 1
    monkeypatch.setattr(time, "time", lambda: later)

    assert main_mod.cmd_capture(_args()) == 0
    assert capture, "a capture after the restore window did not save"


def test_force_overrides_the_stand_down(capture, state):
    """The same escape every other capture refusal has. A user who knows the
    restore failed should not have to wait it out."""
    deploy_mod.mark_restoring(_plan(13))

    assert main_mod.cmd_capture(_args(force=True)) == 0
    assert capture


def test_the_stand_down_is_recorded(capture, state):
    """Unattended it is the only trace, and this is the reconcile declining to
    do the thing it exists to do."""
    deploy_mod.mark_restoring(_plan(13))

    main_mod.cmd_capture(_args())

    assert "still starting" in (state / "reloaded.log").read_text(encoding="utf-8")
