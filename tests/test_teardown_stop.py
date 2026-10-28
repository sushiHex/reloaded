"""`down` tells each tab it is emptying to close when its session goes.

A reloaded tab no longer closes when its session ends: it waits for an Enter
to start it again (deploy.ended_prompt). Under `down`, an Enter landing between
the session's exit and the tab's close would start it again, and the close
would end what it had just started - so teardown leaves a stop file for each
target's shell as its turn comes, and removes them all when it is done.
"""
from __future__ import annotations

import sys
import types

import reloaded.teardown as teardown_mod
from reloaded.paths import shell_stop


def _plan(*pids):
    return teardown_mod.WindowPlan(hwnd=0x10, total_tabs=len(pids), targets=[
        teardown_mod.Target(f"t{p}", rf"C:\repos\t{p}", p, object()) for p in pids])


def _world(monkeypatch, shells):
    proc = lambda pid: types.SimpleNamespace(ppid=lambda: shells[pid])  # noqa: E731
    monkeypatch.setattr(sys.modules["psutil"], "Process", proc)
    seen = []

    def _send(hwnd, item, *, pid, **kw):
        seen.append((pid, sorted(p for p in shells.values() if shell_stop(p).exists())))
        return teardown_mod.Sent(reached=False, keys=0)

    monkeypatch.setattr(teardown_mod, "_send_exit", _send)
    return seen


def test_each_target_s_stop_is_written_as_its_turn_comes(monkeypatch):
    """Targets wait up to EXIT_TIMEOUT_SECONDS each; a stop written for all of
    them at the start would be stale - and ignored - by the eighth."""
    seen = _world(monkeypatch, {20: 10, 21: 11})

    teardown_mod.execute_down([_plan(20, 21)], log=lambda *_: None)

    assert seen == [(20, [10]), (21, [10, 11])]
    assert not shell_stop(10).exists() and not shell_stop(11).exists(), \
        "a holdout that quits later keeps its tab"


def test_a_restart_writes_none(monkeypatch):
    """`restart` puts the session back into the very tab being emptied."""
    seen = _world(monkeypatch, {20: 10})

    teardown_mod.execute_down([_plan(20)], log=lambda *_: None, close_emptied=False)

    assert seen == [(20, [])]


def test_a_target_whose_shell_cannot_be_read_is_still_asked_to_quit(monkeypatch):
    seen = _world(monkeypatch, {})

    teardown_mod.execute_down([_plan(20)], log=lambda *_: None)

    assert seen == [(20, [])]
