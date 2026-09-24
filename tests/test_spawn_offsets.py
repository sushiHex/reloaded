"""A tab's delay starts when its own window is spawned, not when execute did.

Codex review of PR #28, two findings with one root cause. Delays are numbered
globally across the plan (`deploy.plan_deploy`), but each is a `Start-Sleep`
inside a shell that does not exist until its window has been spawned — and
`launch_window` polls for up to twenty seconds before giving up on an HWND.

So `elapsed`, measured from before `execute`, and `delay`, measured from a
spawn that may be a minute later, are not on the same clock. Two consequences:

  - the restore marker could lapse while windows were still being spawned,
    letting a reconcile overwrite the layout mid-restore — the exact failure
    this branch exists to prevent;
  - a later window's tab could be judged "opened but no session started"
    while its shell was still sleeping, recreating the false alarm this
    branch exists to remove.
"""
from __future__ import annotations

import time
import types

import pytest

import reloaded.__main__ as main_mod
import reloaded.deploy as deploy_mod
from reloaded.layout import Tab


def _entry(window_id, delays):
    return deploy_mod.PlanEntry(
        id=window_id, state="normal", rect=[0, 0, 800, 600],
        tabs=[Tab(cwd=rf"C:\repos\{window_id}-{d}", title=f"{window_id}-{d}")
              for d in delays],
        skipped=[], missing=[], delays=list(delays), argv=["wt"],
    )


@pytest.fixture
def slow_launch(monkeypatch, tmp_path):
    """Two windows, the first of which takes 20s to identify — `launch_window`'s
    own timeout, not an invented number."""
    clock = {"now": 0.0}
    monkeypatch.setattr(deploy_mod.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(deploy_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(deploy_mod.win32, "set_geometry",
                        lambda h, r, s: deploy_mod.win32.Placed(True, ""))
    monkeypatch.setattr(deploy_mod.win32, "verify_and_fix_geometry",
                        lambda h, r, s: deploy_mod.win32.Placed(True, ""))

    def _launch(argv, tabs):
        clock["now"] += 20.0
        return 1

    monkeypatch.setattr(deploy_mod, "launch_window", _launch)
    return clock


def test_each_window_records_when_it_was_spawned(slow_launch):
    results = deploy_mod.execute([_entry("w1", [0, 4]), _entry("w2", [8, 12])])

    # Taken before each `launch_window`, which is where Popen happens; the
    # twenty seconds after it are the HWND hunt, not the spawn.
    assert [r.spawned_at for r in results] == [0.0, 20.0]


def test_the_marker_is_refreshed_as_windows_are_spawned(slow_launch, monkeypatch):
    """Four slow windows outlast a deadline computed only from in-shell
    delays, so the marker has to move with the spawning rather than be set
    once at the top."""
    marks = []
    real = deploy_mod.mark_restoring
    monkeypatch.setattr(deploy_mod, "mark_restoring",
                        lambda plan: marks.append(slow_launch["now"]) or real(plan))

    deploy_mod.execute([_entry(f"w{i}", [i * 4]) for i in range(4)])

    assert marks[0] == 0.0, "marked before the first spawn"
    assert marks[-1] >= 80.0, "and again after the last, 80s later"
    assert len(marks) >= 5, "refreshed as each window is spawned, not just twice"


def test_a_tab_is_judged_from_its_own_window_spawn(slow_launch):
    """w2's tabs carry delays of 8s and 12s, but their shells did not exist
    until 20s in, so their turns fall at 38s and 42s. At 30s neither has had
    one; w1's two, spawned at 0s, are overdue."""
    results = deploy_mod.execute([_entry("w1", [0, 4]), _entry("w2", [8, 12])])
    plan = [_entry("w1", [0, 4]), _entry("w2", [8, 12])]

    silent = main_mod._never_started(plan, results, live={}, elapsed=30.0)

    assert silent == [r"C:\repos\w1-0", r"C:\repos\w1-4"]


def test_the_old_reading_would_have_blamed_them(slow_launch):
    """Pinning the bug rather than only the fix: judged from `execute`'s start,
    w2's +8s tab looks overdue at 30s even though its turn is at 38s."""
    plan = [_entry("w1", [0, 4]), _entry("w2", [8, 12])]
    without_offsets = [
        t.cwd for entry in plan for t, d in zip(entry.tabs, entry.delays)
        if d + deploy_mod.SESSION_START_ALLOWANCE <= 30.0
    ]

    assert r"C:\repos\w2-8" in without_offsets, "the false positive, reproduced"


def test_a_window_that_never_launched_judges_nothing(slow_launch, monkeypatch):
    """No spawn time means no turn. Blaming a tab whose window was never
    identified would be blaming it for someone else's failure."""
    monkeypatch.setattr(deploy_mod, "launch_window", lambda argv, tabs: None)
    plan = [_entry("w1", [0])]

    results = deploy_mod.execute(plan)

    assert results[0].spawned_at is None
    assert main_mod._never_started(plan, results, live={}, elapsed=10_000) == []


def _split(window_id, delays, spawned_at, rest_spawned_at=None, rest_error=""):
    entry = _entry(window_id, delays)
    entry.rest_argv = ["wt", "-w", f"reloaded-{window_id}-x"]
    result = deploy_mod.LaunchResult(window_id=window_id, hwnd=1, placed=True,
                                     spawned_at=spawned_at,
                                     rest_spawned_at=rest_spawned_at,
                                     rest_error=rest_error)
    return entry, result


def test_each_tab_is_timed_from_the_call_that_opened_it():
    """A window's first tab is spawned with the window; its others only once
    every window is placed (deploy.wt_argvs). Timed from the later call, a
    first tab that never started would pass for one still starting."""
    entry, result = _split("w1", [0, 4], spawned_at=0.0, rest_spawned_at=100.0)

    assert main_mod._never_started([entry], [result], live={}, elapsed=30.0) == [
        entry.tabs[0].cwd]
    assert main_mod._still_starting([entry], [result], elapsed=30.0) == [
        entry.tabs[1].cwd]


def test_tabs_that_were_never_opened_are_neither_silent_nor_starting():
    """Their `wt` could not be spawned; that is reported by name, as a
    failure, not as a session that failed to start."""
    entry, result = _split("w1", [0, 4], spawned_at=0.0, rest_error="no wt")

    assert entry.tabs[1].cwd not in main_mod._never_started(
        [entry], [result], live={}, elapsed=10_000)
    assert entry.tabs[1].cwd not in main_mod._still_starting(
        [entry], [result], elapsed=0.0)
