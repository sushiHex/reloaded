"""A `--self` restart must not give up while its own marker can still deliver.

`--self` cannot restart its session itself - it would die with the session it
ends - so it hands the job to a detached helper that runs the ordinary
`restart <cwd> --after N` path. That helper types into a session which is, by
construction, in the middle of a turn: the tool call that dispatched it has to
return and the reply after it has to land.

The numbers underneath assumed that turn was short. It is not always:

    09:07:44Z  helper dispatched; marker armed moments later
    09:07:45Z  the same turn begins an 81-second auto-compaction
    ~09:08:15Z helper gives up - "did not exit within 20s"
    09:09:07Z  the compaction ends and the session is finally reachable
    09:09:50Z  the marker expires
    09:16:32Z  the session is still running as the original pid

Forty-odd seconds in which one more `/exit` would have worked, and nothing
tried. The marker's remaining life is the honest bound and not an invented
number: `deploy.restart_loop` compares the marker's age at the moment the agent
exits against `RESTART_MARKER_TTL_SECONDS`, so that span is exactly the one in
which exiting still means coming back. Past it the loop breaks and
`CLOSE_TAB_IF_STARTED` closes the tab instead.
"""
from __future__ import annotations

import types

import psutil
import pytest

import reloaded.__main__ as main_mod
import reloaded.deploy as deploy_mod
import reloaded.teardown as teardown_mod

CWD = r"C:\repos\app"


class _Item:
    """Opaque stand-in for a TabItemControl, as in test_teardown.py."""


def _plan():
    return teardown_mod.WindowPlan(
        hwnd=1, total_tabs=1,
        targets=[teardown_mod.Target("app", CWD, 111, _Item())])


@pytest.fixture
def clock(monkeypatch):
    """A clock that only moves when `execute_down` sleeps.

    A stand-in module rather than `monkeypatch.setattr(time, "time", ...)`:
    `teardown.time` IS the stdlib module, so patching through it would reach
    every other user of the clock in the process for the length of the test.
    """
    now = {"t": 1000.0}

    def sleep(seconds):
        now["t"] += seconds

    monkeypatch.setattr(teardown_mod, "time",
                        types.SimpleNamespace(time=lambda: now["t"], sleep=sleep))
    return now


@pytest.fixture
def busy_session(monkeypatch, clock):
    """A session that never exits, and a record of when keys were sent to it."""
    sent = []
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(
        teardown_mod.tabs, "send_quit_keystrokes",
        lambda keys, *, dismiss_overlay=True, still_needed=None:
            sent.append(clock["t"]))
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: True)
    return sent


# ── how long the keys keep going out ─────────────────────────────────────


def test_a_patient_teardown_keeps_knocking(busy_session, clock):
    """The default is one resend at six seconds. Against a session busy for
    eighty, that is one knock and then silence for the rest of the window."""
    start = clock["t"]

    teardown_mod.execute_down([_plan()], log=lambda *_: None,
                              patience=deploy_mod.RESTART_MARKER_TTL_SECONDS)

    assert len(busy_session) > 2, "it knocked once and gave up"
    assert busy_session[-1] - start > teardown_mod.EXIT_TIMEOUT_SECONDS, (
        "every knock still landed inside the old twenty-second deadline")


def test_a_disarmed_restart_stops_knocking(monkeypatch, busy_session, clock):
    """Not just "stops eventually" — it must stop at the first resend after
    the marker goes, because the very next key could end a session that
    nothing is now authorised to bring back."""
    armed = {"yes": True}
    ttl = deploy_mod.RESTART_MARKER_TTL_SECONDS

    def _cancel_once_it_has_knocked(cwd):
        return armed["yes"] and len(busy_session) < 3

    teardown_mod.execute_down([_plan()], log=lambda *_: None, patience=ttl,
                              still_wanted=_cancel_once_it_has_knocked)

    assert len(busy_session) == 3, "it kept typing after being disarmed"


def test_a_cancel_mid_sequence_stops_the_remaining_keys(monkeypatch, clock):
    """`--cancel` can land between the loop's check and any key in a sequence,
    and a key after the disarm ends a session whose tab then closes rather than
    relaunching. The per-key predicate is where that has to be caught.
    Codex review of this branch."""
    sent = []
    armed = {"yes": True}

    def _keys(quit_keys, *, dismiss_overlay=True, still_needed=None):
        for key in quit_keys:
            if still_needed is not None and not still_needed():
                break
            sent.append(key)
            armed["yes"] = False  # disarmed the instant the first key goes out
        return len(sent)

    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "tab_is_selected", lambda item: True)
    monkeypatch.setattr(teardown_mod.win32, "is_foreground", lambda hwnd: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_quit_keystrokes", _keys)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: True)

    teardown_mod.execute_down(
        [_plan()], log=lambda *_: None,
        patience=deploy_mod.RESTART_MARKER_TTL_SECONDS,
        still_wanted=lambda cwd: armed["yes"])

    assert sent == ["/exit"], "it kept typing after the restart was disarmed"


def test_a_disarmed_send_is_not_reported_as_a_vanished_session(monkeypatch,
                                                               clock):
    """Zero keys has two opposite causes now: nothing left to type at, or a
    sequence stopped on purpose. Reporting the second as the first is a lie,
    and waiting out the deadline for an exit nobody wants any more would log a
    timeout contradicting it. Codex review of this branch."""
    logs = []
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "tab_is_selected", lambda item: True)
    monkeypatch.setattr(teardown_mod.win32, "is_foreground", lambda hwnd: True)
    monkeypatch.setattr(
        teardown_mod.tabs, "send_quit_keystrokes",
        lambda keys, *, dismiss_overlay=True, still_needed=None: 0)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: True)
    start = clock["t"]

    result = teardown_mod.execute_down(
        [_plan()], log=logs.append,
        patience=deploy_mod.RESTART_MARKER_TTL_SECONDS,
        still_wanted=lambda cwd: False)

    out = "\n".join(logs)
    assert "disarmed" in out
    assert "had already gone" not in out, "a live session reported as vanished"
    assert result["timed_out"] == [("app", CWD)]
    assert clock["t"] - start < deploy_mod.RESTART_MARKER_TTL_SECONDS, (
        "it waited out the whole marker for an exit nobody wanted")


def test_a_session_that_really_went_is_still_reported_as_gone(monkeypatch,
                                                              clock):
    """The other cause, unchanged: zero keys because the target had already
    ended on its own while an earlier target was being waited out."""
    logs = []
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(
        teardown_mod.tabs, "send_quit_keystrokes",
        lambda keys, *, dismiss_overlay=True, still_needed=None: 0)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: True)

    result = teardown_mod.execute_down(
        [_plan()], log=logs.append,
        patience=deploy_mod.RESTART_MARKER_TTL_SECONDS,
        still_wanted=lambda cwd: False)

    assert "had already gone" in "\n".join(logs)
    assert result["exited"] == [("app", CWD)]


def test_the_knocking_stops_before_the_marker_dies(busy_session, clock):
    """A key that lands in the last moments provokes an exit that arrives
    after the marker is stale - and a stale marker means the shell leaves its
    restart loop and the tab closes, which is the opposite of a restart."""
    start = clock["t"]
    ttl = deploy_mod.RESTART_MARKER_TTL_SECONDS

    teardown_mod.execute_down([_plan()], log=lambda *_: None, patience=ttl)

    last = busy_session[-1] - start
    assert last <= ttl - teardown_mod.EXIT_RESEND_CUTOFF_SECONDS, (
        f"last knock at {last:.0f}s leaves under "
        f"{teardown_mod.EXIT_RESEND_CUTOFF_SECONDS:.0f}s for the session to act")


def test_patience_does_not_mean_a_keystroke_every_poll(busy_session):
    """Each resend steals keyboard focus. Patience buys a longer window, not a
    denser one."""
    ttl = deploy_mod.RESTART_MARKER_TTL_SECONDS

    teardown_mod.execute_down([_plan()], log=lambda *_: None, patience=ttl)

    assert len(busy_session) <= 1 + ttl / teardown_mod.EXIT_RESEND_EVERY_SECONDS


def test_a_session_that_frees_up_late_is_still_caught(monkeypatch, clock):
    """The measured case: reachable only after eighty seconds, which is past
    the old deadline and well inside the marker's life."""
    sent = []
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(
        teardown_mod.tabs, "send_quit_keystrokes",
        lambda keys, *, dismiss_overlay=True, still_needed=None:
            sent.append(clock["t"]))
    # It ignores every key until 80s in, then acts on the next one it gets.
    freed_at = clock["t"] + 80.0
    monkeypatch.setattr(
        psutil, "pid_exists",
        lambda pid: not (clock["t"] >= freed_at and any(t >= freed_at for t in sent)))
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: True)

    result = teardown_mod.execute_down(
        [_plan()], log=lambda *_: None,
        patience=deploy_mod.RESTART_MARKER_TTL_SECONDS)

    assert result["exited"] == [("app", CWD)]
    assert result["timed_out"] == []


def test_without_patience_that_same_session_is_lost(monkeypatch, clock):
    """Pinning the defect, not only the fix. Under the default deadline the
    keys stop at twenty seconds and the session is reported as a holdout."""
    sent = []
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(
        teardown_mod.tabs, "send_quit_keystrokes",
        lambda keys, *, dismiss_overlay=True, still_needed=None:
            sent.append(clock["t"]))
    freed_at = clock["t"] + 80.0
    monkeypatch.setattr(
        psutil, "pid_exists",
        lambda pid: not (clock["t"] >= freed_at and any(t >= freed_at for t in sent)))
    monkeypatch.setattr(teardown_mod.win32, "close_window", lambda hwnd: True)

    result = teardown_mod.execute_down([_plan()], log=lambda *_: None)

    assert result["timed_out"] == [("app", CWD)]


def test_omitting_patience_changes_nothing_about_the_old_behaviour(busy_session,
                                                                   clock):
    """The default path is every `down`, every `up --restart`, every named
    restart. It keeps its twenty-second deadline and its single resend."""
    start = clock["t"]

    result = teardown_mod.execute_down([_plan()], log=lambda *_: None)

    assert len(busy_session) == 2, "initial send plus exactly one resend"
    assert busy_session[1] - start == pytest.approx(
        teardown_mod.EXIT_RETRY_AFTER_SECONDS, abs=teardown_mod.EXIT_POLL_SECONDS)
    assert result["timed_out"] == [("app", CWD)]


# ── who asks for it ──────────────────────────────────────────────────────


@pytest.fixture
def restart_one(monkeypatch, tmp_path):
    """`cmd_restart_one` with the desktop removed, reporting what patience it
    asked teardown for."""
    import time

    # `after` is a real `time.sleep` at the top of cmd_restart_one. Patched on
    # the stdlib module because the import there is function-local.
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    asked = {}
    session = main_mod._Restarting(
        hwnd=1, item=_Item(), cwd=CWD, pid=111,
        launcher=main_mod.discover_mod.RELOADED, agent="claude",
        command="", size_bytes=0)

    monkeypatch.setattr(main_mod.discover_mod, "live_sessions",
                        lambda: {main_mod.norm(CWD): 111})
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down",
                        lambda *a, **k: [_plan()])
    monkeypatch.setattr(main_mod, "_snapshot_restarts", lambda plans, args: [session])
    monkeypatch.setattr(main_mod, "_sweep_stale_markers", lambda: None)
    monkeypatch.setattr(main_mod, "_warn_about_empty_relaunches", lambda s: None)
    monkeypatch.setattr(main_mod, "_print_down_result", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_await_relaunch", lambda s, before: True)

    def _execute_down(plans, **kw):
        asked["patience"] = kw.get("patience")
        asked["still_wanted"] = kw.get("still_wanted")
        return {"exited": [], "timed_out": [], "closed": [], "left_open": []}

    monkeypatch.setattr(main_mod.teardown_mod, "execute_down", _execute_down)
    return asked


def _args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "dry_run": False,
         "after": 0.0, "force": False, "dispatched": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_a_dispatched_restart_asks_for_the_markers_whole_life(restart_one):
    main_mod.cmd_restart_one(_args(after=5.0, dispatched=True), [CWD])

    assert restart_one["patience"] == deploy_mod.RESTART_MARKER_TTL_SECONDS


def test_a_restart_someone_is_watching_asks_for_nothing_extra(restart_one):
    """Named from another tab, the caller sees the failure and can simply run
    it again. Two minutes per target is a cost only the unattended case has
    any reason to pay."""
    main_mod.cmd_restart_one(_args(), [CWD])

    assert restart_one["patience"] is None


def test_the_public_delay_flag_does_not_buy_patience(restart_one):
    """`--after` is an ordinary option and a named restart takes any number of
    repos, so inferring "this was dispatched" from it would hand
    `restart a b c --after 5` two minutes of typing per repo at sessions
    whose owner is watching. Codex review of this branch."""
    main_mod.cmd_restart_one(_args(after=5.0), [CWD])

    assert restart_one["patience"] is None


def test_a_dispatched_restart_stops_typing_once_it_is_disarmed(restart_one):
    """`--self --cancel` removes the marker and says plainly that it cannot
    recall the helper. The helper is what is typing."""
    main_mod.cmd_restart_one(_args(after=5.0, dispatched=True), [CWD])

    assert restart_one["still_wanted"] is not None


def test_the_patience_is_the_markers_ttl_and_not_a_number_of_its_own():
    """If these ever drift apart, the helper is either typing after the marker
    is dead or giving up while it is alive."""
    assert (main_mod.SELF_EXIT_PATIENCE_SECONDS
            == deploy_mod.RESTART_MARKER_TTL_SECONDS)


# ── what a holdout is told afterwards ────────────────────────────────────


def _holdout():
    return main_mod._Restarting(
        hwnd=1, item=_Item(), cwd=CWD, pid=111,
        launcher=main_mod.discover_mod.RELOADED, agent="claude",
        command="", size_bytes=0)


def test_a_holdout_is_told_what_is_really_left(capsys):
    """This printed the TTL as a constant, which was near enough while the
    wait before it was twenty seconds. Waiting out the whole marker makes it a
    claim about a window that closed while the caller waited."""
    main_mod.restart_marker(CWD).write_text("restart", encoding="utf-8")

    main_mod._report_never_exited(_holdout())

    out = capsys.readouterr().out
    assert "still armed" in out
    assert "2 min" not in out, "it is still quoting the TTL as a constant"


def test_an_expired_marker_is_not_described_as_armed(capsys, monkeypatch):
    """The case a dispatched restart now reaches every time it fails: it spent
    the marker's whole life trying."""
    import os
    import time

    path = main_mod.restart_marker(CWD)
    path.write_text("restart", encoding="utf-8")
    stale = time.time() - deploy_mod.RESTART_MARKER_TTL_SECONDS - 1
    os.utime(path, (stale, stale))

    main_mod._report_never_exited(_holdout())

    out = capsys.readouterr().out
    assert "still armed" not in out
    assert "closes the tab" in out, "it never says what quitting now does"


def test_a_missing_marker_is_not_described_as_armed(capsys):
    main_mod._report_never_exited(_holdout())

    assert "still armed" not in capsys.readouterr().out


def test_a_hand_started_holdout_is_promised_nothing(capsys):
    """It is armed with nothing by design, so there is no marker to read and
    no window to quote."""
    main_mod.restart_marker(CWD).write_text("restart", encoding="utf-8")
    hand = main_mod._Restarting(
        hwnd=1, item=_Item(), cwd=CWD, pid=111,
        launcher=main_mod.discover_mod.HAND, agent="claude",
        command="", size_bytes=0)

    main_mod._report_never_exited(hand)

    assert "armed" not in capsys.readouterr().out
