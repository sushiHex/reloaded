"""A placement that failed says which check failed.

On a real logon two of four windows reported "launched but geometry could not
be applied" and ended up on the wrong monitor. The message is all there was:
`set_geometry` and `verify_and_fix_geometry` each return a bare bool, and three
different failures collapse into it — the window is no longer a Terminal
window, `GetWindowPlacement` refused, or `SetWindowPlacement` did.

This does not fix the placement. Nothing here knows yet why those two windows
refused, and guessing at a cause is how a fix gets written for a mechanism
nobody traced. It makes the next occurrence answerable.
"""
from __future__ import annotations

import pytest

import reloaded.win32 as win32_mod


@pytest.fixture
def wt_window(monkeypatch):
    """A window that is a Terminal window and accepts placement."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: win32_mod.WT_CLASS)
    monkeypatch.setattr(win32_mod, "_apply_geometry",
                        lambda hwnd, rect, state: True)


def test_a_placement_that_worked_carries_no_reason(wt_window):
    placed = win32_mod.set_geometry(1, [0, 0, 800, 600], "normal")

    assert placed.ok
    assert placed.why == ""


def test_a_window_that_is_no_longer_a_terminal_says_so(monkeypatch):
    """The HWND-reuse guard. Windows can hand the same number to an unrelated
    window, and moving that one would be worse than not placing ours."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: "Notepad")

    placed = win32_mod.set_geometry(1, [0, 0, 800, 600], "normal")

    assert not placed.ok
    assert "no longer" in placed.why or "not a" in placed.why


def test_a_refused_placement_says_which_call_refused(monkeypatch):
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: win32_mod.WT_CLASS)
    monkeypatch.setattr(win32_mod, "_apply_geometry",
                        lambda hwnd, rect, state: False)

    placed = win32_mod.set_geometry(1, [0, 0, 800, 600], "normal")

    assert not placed.ok
    assert placed.why, "a failure with no reason is what this exists to remove"


def test_a_placement_that_stuck_is_not_reapplied(monkeypatch):
    """The common case, and the reason verify reads before it writes."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: win32_mod.WT_CLASS)
    monkeypatch.setattr(win32_mod, "get_geometry",
                        lambda hwnd: ([0, 0, 800, 600], "normal", "D1", 96))

    def _must_not_apply(*a):
        raise AssertionError("reapplied a placement that had not drifted")

    monkeypatch.setattr(win32_mod, "_apply_geometry", _must_not_apply)

    assert win32_mod.verify_and_fix_geometry(1, [0, 0, 800, 600], "normal").ok


def test_a_drifted_placement_that_will_not_reapply_says_it_drifted(monkeypatch):
    """Distinguishes "Terminal moved it back and we could not stop it" from
    "the call was refused" — the two look identical today."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: win32_mod.WT_CLASS)
    monkeypatch.setattr(win32_mod, "get_geometry",
                        lambda hwnd: ([99, 99, 800, 600], "normal", "D1", 96))
    monkeypatch.setattr(win32_mod, "_apply_geometry",
                        lambda hwnd, rect, state: False)

    placed = win32_mod.verify_and_fix_geometry(1, [0, 0, 800, 600], "normal")

    assert not placed.ok
    assert "drift" in placed.why


def test_the_deploy_warning_carries_the_reason(monkeypatch, capsys):
    """The reason has to reach the log, or it may as well not be measured —
    the logon run is the one nobody is watching."""
    import reloaded.__main__ as main_mod
    import reloaded.deploy as deploy_mod

    monkeypatch.setattr(deploy_mod, "plan_deploy", lambda *a, **k: [
        deploy_mod.PlanEntry(id="w1", state="normal", rect=[0, 0, 8, 6],
                             tabs=[], skipped=[], missing=[], delays=[],
                             argv=["wt"])])
    monkeypatch.setattr(deploy_mod, "execute", lambda p: [
        deploy_mod.LaunchResult(window_id="w1", hwnd=1, placed=False,
                                why="SetWindowPlacement refused")])
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda **k: {})

    main_mod._deploy_layout(
        main_mod.layout_mod.Layout(version=1, saved_ts="", monitors=[], windows=[]),
        __import__("types").SimpleNamespace(
            repos_root=r"C:\repos", unattended=False, layout="default",
            dry_run=False),
    )

    assert "SetWindowPlacement refused" in capsys.readouterr().out
