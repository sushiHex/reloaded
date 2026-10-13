"""A restart is confirmed by the pid that stays, not the first one seen (#69).

`restart alpha`, in a trusted Claude repo with no conversation, reported
"restarted in place (pid 82008)". 82008 was the tab's `--continue` attempt,
which exited 1 two seconds later; the session that came back was 73020, the
fallback's. Had there been no fallback, a dead relaunch would have been
reported as a restart all the same.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
from reloaded.paths import norm

CWD = r"C:\repos\alpha"
OLD = 111


@pytest.fixture
def polls(monkeypatch):
    """`live_sessions` answers from a script, one entry per poll, the last
    repeating; each poll's sleep advances a fake clock. Returns the script."""
    script: list = []
    clock = {"now": 1000.0}
    monkeypatch.setattr(main_mod, "time", types.SimpleNamespace(
        time=lambda: clock["now"],
        sleep=lambda s: clock.__setitem__("now", clock["now"] + s)))

    def live():
        pid = script.pop(0) if len(script) > 1 else script[0]
        return {} if pid is None else {norm(CWD): pid}

    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", live)
    monkeypatch.setattr(main_mod, "RELAUNCH_POLL_SECONDS", 1.0)
    monkeypatch.setattr(main_mod, "RELAUNCH_SETTLE_SECONDS", 5.0)
    monkeypatch.setattr(main_mod, "RELAUNCH_WAIT_SECONDS", 20.0)
    return script


def test_the_pid_reported_is_the_one_that_stayed(polls):
    polls += [OLD, 82008, 82008, 73020]

    assert main_mod._wait_for_session(CWD, not_pid=OLD) == 73020


def test_a_relaunch_that_died_is_not_a_restart(polls):
    polls += [OLD, 82008, 82008, None]

    assert main_mod._wait_for_session(CWD, not_pid=OLD) is None


def test_the_old_session_still_running_is_not_a_restart(polls):
    polls += [OLD]

    assert main_mod._wait_for_session(CWD, not_pid=OLD) is None


def test_a_session_that_stays_is_confirmed(polls):
    polls += [OLD, 222]

    assert main_mod._wait_for_session(CWD, not_pid=OLD) == 222
