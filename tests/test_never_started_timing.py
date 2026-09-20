"""A tab that has not been launched yet cannot have failed to start.

`_never_started` ran the moment `execute` returned, which is when the last `wt`
was spawned — not when the last session started. The stagger is a `Start-Sleep`
inside each launched shell, so on a real logon it reported 12 of 13 tabs as
"opened but no session started" five seconds into a forty-eight second
staggered start, and blamed a Codex trust prompt for Claude tabs that had not
been asked to run yet.

A warning that fires on nearly every healthy restore is worse than none: it
taught the reader that a restore had failed when it had not.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
import reloaded.deploy as deploy_mod
from reloaded.layout import Tab
from reloaded.paths import norm


def _plan(n):
    tabs = [Tab(cwd=rf"C:\repos\r{i}", title=f"r{i}") for i in range(n)]
    return [deploy_mod.PlanEntry(
        id="w1", state="normal", rect=[0, 0, 800, 600], tabs=tabs, skipped=[],
        missing=[],
        delays=[i * deploy_mod.STAGGER_SECONDS for i in range(n)],
        argv=["wt"],
    )]


def test_a_tab_still_sleeping_out_its_delay_is_not_a_failure():
    """The whole of the real incident: 13 tabs, checked at +5s."""
    assert main_mod._never_started(_plan(13), live={}, elapsed=5) == []


def test_a_tab_whose_turn_passed_without_a_session_is_a_failure():
    """The signal worth keeping — a Codex trust prompt really does leave a tab
    open with nothing behind it."""
    late = deploy_mod.STAGGER_SECONDS + deploy_mod.SESSION_START_ALLOWANCE + 1

    silent = main_mod._never_started(_plan(2), live={}, elapsed=late)

    assert silent == [r"C:\repos\r0", r"C:\repos\r1"]


def test_a_started_session_is_never_reported():
    late = 10_000
    live = {norm(rf"C:\repos\r{i}"): i for i in range(2)}

    assert main_mod._never_started(_plan(2), live=live, elapsed=late) == []


def test_only_the_tabs_whose_turn_has_come_are_judged():
    """Mid-stagger: the first tab has had its chance and the last has not."""
    elapsed = deploy_mod.SESSION_START_ALLOWANCE + 1

    silent = main_mod._never_started(_plan(5), live={}, elapsed=elapsed)

    assert silent == [r"C:\repos\r0"]


def test_a_deploy_says_the_rest_are_still_coming(monkeypatch, capsys):
    """Replacing the false alarm with the true statement. Without it a restore
    that launched 13 and reported nothing reads as though it did nothing."""
    plan = _plan(13)
    monkeypatch.setattr(deploy_mod, "plan_deploy", lambda *a, **k: plan)
    monkeypatch.setattr(deploy_mod, "execute", lambda p: [
        deploy_mod.LaunchResult(window_id="w1", hwnd=1, placed=True)])
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda **k: {})
    monkeypatch.setattr(main_mod.transcript_mod, "repair_torn_tail", lambda *a, **k: None)

    main_mod._deploy_layout(
        main_mod.layout_mod.Layout(version=1, saved_ts="", monitors=[], windows=[]),
        types.SimpleNamespace(repos_root=r"C:\repos", unattended=False,
                              layout="default", dry_run=False),
    )

    out = capsys.readouterr().out
    assert "still starting" in out
    assert "status" in out, "no way to check on them later is offered"
