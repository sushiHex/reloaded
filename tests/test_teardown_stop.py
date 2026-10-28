"""`down` tells each tab it is emptying to close when its session goes.

A reloaded tab no longer closes when its session ends: it waits for an Enter
to start it again (deploy.ended_prompt). Under `down`, an Enter landing between
the session's exit and the tab's close would start it again, and the close
would end what it had just started - so teardown leaves a stop file for each
target's shell first, and removes them all when it is done.
"""
from __future__ import annotations

import sys
import types

import reloaded.teardown as teardown_mod
from reloaded.paths import shell_stop

PLAN = teardown_mod.WindowPlan(hwnd=0x10, total_tabs=1, targets=[
    teardown_mod.Target("app", r"C:\repos\app", 20, object())])


def _parents(monkeypatch, shells):
    proc = lambda pid: types.SimpleNamespace(ppid=lambda: shells[pid])  # noqa: E731
    monkeypatch.setattr(sys.modules["psutil"], "Process", proc)


def test_each_target_s_shell_gets_a_stop_file_for_the_teardown(monkeypatch):
    _parents(monkeypatch, {20: 10})
    seen = []
    monkeypatch.setattr(teardown_mod, "_down",
                        lambda *a, **k: seen.append(shell_stop(10).exists()))

    teardown_mod.execute_down([PLAN], log=lambda *_: None)

    assert seen == [True], "written before any session is asked to quit"
    assert not shell_stop(10).exists(), "a holdout that quits later keeps its tab"


def test_a_restart_writes_none(monkeypatch):
    """`restart` puts the session back into the very tab being emptied."""
    _parents(monkeypatch, {20: 10})
    seen = []
    monkeypatch.setattr(teardown_mod, "_down",
                        lambda *a, **k: seen.append(shell_stop(10).exists()))

    teardown_mod.execute_down([PLAN], log=lambda *_: None, close_emptied=False)

    assert seen == [False]


def test_a_target_whose_shell_cannot_be_read_is_skipped(monkeypatch):
    def _gone(pid):
        raise LookupError(pid)
    monkeypatch.setattr(sys.modules["psutil"], "Process", _gone)
    monkeypatch.setattr(teardown_mod, "_down", lambda *a, **k: None)

    assert teardown_mod.execute_down([PLAN], log=lambda *_: None)["exited"] == []
