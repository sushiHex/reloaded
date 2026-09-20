"""Three ways a restore's marker still failed to protect the layout.

Codex re-review of PR #28, after the first round of fixes. Each is the same
guard being right in one place and absent or wrong in another.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
import reloaded.deploy as deploy_mod
import reloaded.editor as editor_mod
from conftest import make_layout, make_window
from reloaded.layout import Tab


def _entry(window_id, delays):
    return deploy_mod.PlanEntry(
        id=window_id, state="normal", rect=[0, 0, 800, 600],
        tabs=[Tab(cwd=rf"C:\repos\{window_id}", title=window_id)],
        skipped=[], missing=[], delays=list(delays), argv=["wt"],
    )


# ── the editor recaptures too ────────────────────────────────────────────


def test_the_editors_recapture_stands_down_during_a_restore(monkeypatch, capsys):
    """`c` recaptures and `s` saves it. Guarding `capture` and `restart` and
    not this one leaves a third way to write a layout that is missing every
    session which has not started yet."""
    deploy_mod.mark_restoring([_entry("w1", [0, 4, 8])])

    def _must_not_capture(*a, **k):
        raise AssertionError("recaptured while a restore was still starting")

    monkeypatch.setattr(editor_mod.capture_mod, "capture_live", _must_not_capture)
    monkeypatch.setattr("builtins.input", lambda *a: next(_commands))
    _commands = iter(["c", "q"])

    lo = make_layout([make_window([0, 0, 800, 600],
                                  [Tab(cwd=r"C:\repos\app", title="app")])])
    editor_mod.run(lo, "layout.json", r"C:\repos")

    assert "still starting" in capsys.readouterr().out


def test_the_editor_recaptures_normally_when_no_restore_is_running(monkeypatch):
    captured = []
    monkeypatch.setattr(
        editor_mod.capture_mod, "capture_live",
        lambda root, previous: captured.append(1) or make_layout(
            [make_window([0, 0, 800, 600],
                         [Tab(cwd=r"C:\repos\app", title="app")])]))
    _commands = iter(["c", "q", "y"])
    monkeypatch.setattr("builtins.input", lambda *a: next(_commands))

    lo = make_layout([make_window([0, 0, 800, 600],
                                  [Tab(cwd=r"C:\repos\app", title="app")])])
    editor_mod.run(lo, "layout.json", r"C:\repos")

    assert captured == [1]


# ── the spawn time is the spawn, not the end of the HWND hunt ────────────


def test_spawned_at_is_taken_before_the_hwnd_is_hunted(monkeypatch):
    """`launch_window` runs Popen and then polls for the window for up to
    twenty seconds. Timing from its return pushes a delay-zero tab's turn
    twenty seconds into the future, so a session stuck at a trust prompt is
    reported as "still starting" and — since nothing checks again — never
    reported at all."""
    clock = {"now": 0.0}
    monkeypatch.setattr(deploy_mod.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(deploy_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(deploy_mod.win32, "set_geometry",
                        lambda h, r, s: deploy_mod.win32.Placed(True, ""))
    monkeypatch.setattr(deploy_mod.win32, "verify_and_fix_geometry",
                        lambda h, r, s: deploy_mod.win32.Placed(True, ""))

    def _slow_identify(argv, tabs):
        clock["now"] += 20.0  # the poll, after the spawn
        return 1

    monkeypatch.setattr(deploy_mod, "launch_window", _slow_identify)

    results = deploy_mod.execute([_entry("w1", [0])])

    assert results[0].spawned_at == 0.0, "timed from the poll, not the spawn"


# ── overlapping restores must not shorten each other ─────────────────────


def test_a_short_restore_does_not_shorten_a_long_one(monkeypatch):
    """The marker is one file for the machine. A three-session restore of one
    layout replacing a thirteen-session restore's deadline lets the reconcile
    capture the longer one's half-finished desktop."""
    deploy_mod.mark_restoring([_entry("w1", [0, 4, 8, 12, 16, 20, 24, 28])])
    long_left = deploy_mod.restoring_for()

    deploy_mod.mark_restoring([_entry("w2", [0])])

    assert deploy_mod.restoring_for() >= long_left - 1


def test_a_longer_restore_does_extend_a_short_one(monkeypatch):
    deploy_mod.mark_restoring([_entry("w1", [0])])

    deploy_mod.mark_restoring([_entry("w2", [0, 4, 8, 12, 16, 20, 24, 28])])

    assert deploy_mod.restoring_for() > deploy_mod.RESTORE_GRACE_SECONDS


def test_refreshing_within_one_restore_is_never_mistaken_for_shortening():
    """What makes the "do not shorten" rule safe for the per-spawn refresh.

    Remaining time can only decay from the window it was written with, so a
    refresh carrying the same plan is always <= the existing deadline and is
    never skipped - which is the whole point of refreshing as each window is
    spawned.
    """
    plan = [_entry("w1", [0, 4])]
    deploy_mod.mark_restoring(plan)

    assert deploy_mod.restoring_for() <= deploy_mod.restore_window(plan)
