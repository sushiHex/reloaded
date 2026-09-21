"""A `--self` restart has to be able to fail out loud.

`--self` prints its result and then the session it is talking to goes away, so
the helper it dispatched is the only thing left that knows what happened - and
its stdout and stderr went to `subprocess.DEVNULL`. `_dispatch_restart`'s own
docstring already named the shape:

    the refusal goes to the helper's DEVNULL after the dispatching session has
    said "Nothing further to do."

Measured on 2026-09-21: a helper was dispatched, gave up thirty seconds later
against a session busy compacting, and nine minutes afterwards the session was
still running with nothing anywhere saying why. The only record was the
dispatch line preserved in the session transcript.

Nothing new has to be written to report it. The helper runs the ordinary named
restart, which already prints `never exited — still running as pid N, not
restarted` and whether the marker is still armed. It was speaking into a closed
pipe.
"""
from __future__ import annotations

import subprocess
import types

import pytest

import reloaded.__main__ as main_mod
import reloaded.discover as discover_mod

CWD = r"C:\repos\app"


class _Item:
    """Opaque stand-in for a TabItemControl, as in test_teardown.py."""


def _plan():
    return main_mod.teardown_mod.WindowPlan(
        hwnd=1, total_tabs=1,
        targets=[main_mod.teardown_mod.Target("app", CWD, 111, _Item())])


# ── where the helper speaks ──────────────────────────────────────────────


@pytest.fixture
def spawn(monkeypatch, tmp_path):
    """The real spawn's keyword arguments, without spawning anything.

    `sys.executable` is not on conftest's forbidden list, so a real detached
    python could outlive the suite. Popen is replaced rather than trusted to
    refuse.
    """
    calls = []
    log = tmp_path / "reloaded.log"

    class _Popen:
        def __init__(self, argv, **kw):
            calls.append(kw)
            self.pid = 4242

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    monkeypatch.setattr(main_mod, "log_path", lambda: log)
    return types.SimpleNamespace(calls=calls, log=log)


def test_the_helper_is_not_handed_a_closed_pipe(spawn):
    main_mod._dispatch_restart(CWD, "default", 5.0, r"C:\repos")

    kw = spawn.calls[0]
    assert kw["stdout"] is not subprocess.DEVNULL, "its account still goes nowhere"
    assert kw["stderr"] is not subprocess.DEVNULL


def test_what_the_helper_writes_lands_in_the_log(spawn):
    """The handle it is given is the log itself, so the helper's own prose —
    `never exited — still running as pid N` and the rest — needs no
    transport of its own."""
    main_mod._dispatch_restart(CWD, "default", 5.0, r"C:\repos")

    assert spawn.calls[0]["stdout"].name == str(spawn.log)


def test_stderr_goes_to_the_same_place(spawn):
    """A traceback out of the helper is the loudest thing it can produce and
    the one most worth keeping."""
    main_mod._dispatch_restart(CWD, "default", 5.0, r"C:\repos")

    kw = spawn.calls[0]
    assert kw["stderr"] is kw["stdout"]
    # Without this the assertion above passes on the very bug it is about:
    # DEVNULL is DEVNULL.
    assert kw["stderr"] is not subprocess.DEVNULL


def test_the_log_is_appended_to(spawn):
    """Opening the shared log `w` would erase every earlier run's audit trail
    on every restart."""
    spawn.log.write_text("an earlier run\n", encoding="utf-8")

    main_mod._dispatch_restart(CWD, "default", 5.0, r"C:\repos")

    assert "an earlier run" in spawn.log.read_text(encoding="utf-8")


def test_the_dispatch_is_announced_in_the_log(spawn):
    """The helper's own output says nothing about which session asked for it,
    and by the time it writes, the session that did has gone."""
    main_mod._dispatch_restart(CWD, "default", 5.0, r"C:\repos")

    text = spawn.log.read_text(encoding="utf-8")
    assert CWD in text
    assert "4242" in text, "the helper's pid is how its lines are identified"


def test_a_log_that_fills_after_the_spawn_is_not_a_spawn_failure(spawn,
                                                                 monkeypatch):
    """The helper is already running by then. Raising out of here would have
    the caller clear the attempt file and tell the user to start another
    restart, on top of the one now typing at their session. Codex review of
    this branch."""
    class _Full:
        name = "full"

        def write(self, text):
            raise OSError("no space left on device")

        def close(self):
            pass

    monkeypatch.setattr(main_mod, "_open_account", lambda: _Full())

    assert main_mod._dispatch_restart(CWD, "default", 5.0, r"C:\repos") == 4242


def test_a_log_that_cannot_be_opened_does_not_stop_the_restart(monkeypatch,
                                                               tmp_path):
    """Losing the account is bad. Refusing to restart because the account
    cannot be opened is worse - that is the whole command failing over its
    own bookkeeping."""
    calls = []

    class _Popen:
        def __init__(self, argv, **kw):
            calls.append(kw)
            self.pid = 4242

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    monkeypatch.setattr(main_mod, "log_path", lambda: tmp_path / "nope" / "x.log")

    assert main_mod._dispatch_restart(CWD, "default", 5.0, r"C:\repos") == 4242
    assert calls, "the helper was never spawned"


# ── the attempt a failed restart leaves behind ───────────────────────────


def _args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "dry_run": False,
         "after": 0.0, "force": False, "attempt": ""}
    d.update(kw)
    return types.SimpleNamespace(**d)


TOKEN = "tok0123456789abc"


def _settled(token="earlier", *, unreadable=False):
    """An attempt whose helper can no longer be working.

    A record that has not reached its deadline is not a failure, it is a
    restart still happening, so every test about *reporting* has to write one
    that has.
    """
    import os
    import time

    path = main_mod.restart_attempt(CWD, token)
    old = time.time() - main_mod._attempt_budget(0.0) - 1
    path.write_text("" if unreadable else f"{old:.0f} {old:.0f}",
                    encoding="utf-8")
    if unreadable:
        # No deadline to read, so settlement has to come from the mtime.
        os.utime(path, (old, old))
    return path


def _record(token, after=0.0):
    """Record an attempt the way `cmd_restart_self` does."""
    import time

    main_mod._record_attempt([CWD], token,
                             time.time() + main_mod._attempt_budget(after))


@pytest.fixture
def restart_one(monkeypatch):
    """`cmd_restart_one` with the desktop removed and its outcome settable."""
    import time

    # `after` is a real `time.sleep` at the top of cmd_restart_one, and these
    # tests pass the real five seconds because that is what `--self` passes.
    # Patched on the stdlib module because the import there is function-local.
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    state = {"came_back": True}
    session = main_mod._Restarting(
        hwnd=1, item=_Item(), cwd=CWD, pid=111,
        launcher=discover_mod.RELOADED, agent="claude", command="", size_bytes=0)

    monkeypatch.setattr(main_mod.discover_mod, "live_sessions",
                        lambda: {main_mod.norm(CWD): 111})
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", lambda *a, **k: [_plan()])
    monkeypatch.setattr(main_mod, "_snapshot_restarts", lambda plans, args: [session])
    monkeypatch.setattr(main_mod, "_sweep_stale_markers", lambda: None)
    monkeypatch.setattr(main_mod, "_warn_about_empty_relaunches", lambda s: None)
    monkeypatch.setattr(main_mod, "_print_down_result", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_await_relaunch",
                        lambda s, before: state["came_back"])
    monkeypatch.setattr(
        main_mod.teardown_mod, "execute_down",
        lambda plans, **kw: {"exited": [], "timed_out": [], "closed": [],
                             "left_open": []})
    return state


def test_a_dispatched_restart_that_works_clears_the_attempt(restart_one):
    _record(TOKEN)

    main_mod.cmd_restart_one(_args(after=5.0, attempt=TOKEN), [CWD])

    assert not main_mod.restart_attempts(CWD)


def test_a_dispatched_restart_that_fails_leaves_its_attempt(restart_one):
    restart_one["came_back"] = False
    _record(TOKEN)

    main_mod.cmd_restart_one(_args(after=5.0, attempt=TOKEN), [CWD])

    assert main_mod.restart_attempts(CWD), (
        "nothing on disk says the restart did not happen")


def test_a_helper_does_not_settle_a_later_helpers_attempt(restart_one):
    """Two `--self` calls for one repository are two outstanding restarts. If
    the older helper — which may have waited out a whole marker — settles the
    newer one's record and the newer one then dies, nothing is left to report
    the death this file exists for. Codex review of this branch."""
    _record(TOKEN)
    _record("the-newer-dispatch")

    main_mod.cmd_restart_one(_args(after=5.0, attempt=TOKEN), [CWD])

    assert main_mod.restart_attempts(CWD) == [
        main_mod.restart_attempt(CWD, "the-newer-dispatch")], (
        "an older helper settled a record that was not its own")


def test_each_dispatch_owns_a_path_rather_than_a_line_in_one(restart_one):
    """Carrying the token inside a shared file made the clear a
    read-then-delete, and a newer dispatch taking that path over between the
    two steps put the loss straight back — rarer, not removed. Codex second
    pass on this branch."""
    _record("one")
    _record("two")

    assert len(main_mod.restart_attempts(CWD)) == 2, (
        "two outstanding restarts share one record")


def test_reporting_clears_every_settled_attempt(session, capsys):
    """Reported once means once. Leaving the older files behind would have the
    next restart announce a restart from two restarts ago."""
    _settled("one")
    _settled("two")

    main_mod.cmd_restart(_self_args(arm_only=True))

    assert "never reported back" in capsys.readouterr().out
    assert main_mod.restart_attempts(CWD) == []


def test_a_refusal_before_any_keystroke_leaves_it_too(restart_one, monkeypatch):
    """`_nothing_running_there` and `_no_tab_to_type_into` are exactly the
    silent failures the dispatcher's docstring warned about: the helper
    refuses into DEVNULL after the caller has already promised a restart.

    Through `restart_one` rather than a bare monkeypatch: without its stubs
    this walked the real process table and slept the real five seconds, which
    is how a green test costs fifteen of them.
    """
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    monkeypatch.setattr(main_mod.discover_mod, "sweep", lambda: ({}, {}))
    _record(TOKEN)

    assert main_mod.cmd_restart_one(_args(after=5.0, attempt=TOKEN), [CWD]) == 1
    assert main_mod.restart_attempts(CWD)


def test_a_restart_someone_is_watching_settles_nothing(restart_one):
    """Named from another tab, the failure is already on the caller's screen,
    and nothing dispatched it — so there is no attempt of its own to settle."""
    _record(TOKEN)

    main_mod.cmd_restart_one(_args(), [CWD])

    assert main_mod.restart_attempts(CWD), (
        "a hand-typed restart consumed a dispatched restart's record")


def test_a_dry_run_settles_nothing(restart_one, monkeypatch):
    monkeypatch.setattr(main_mod, "_preview_restart", lambda plans: None)
    _record(TOKEN)

    main_mod.cmd_restart_one(_args(after=5.0, attempt=TOKEN, dry_run=True),
                             [CWD])

    assert main_mod.restart_attempts(CWD)


# ── and who writes it ────────────────────────────────────────────────────


def test_the_dispatcher_records_the_attempt_not_the_helper(session):
    """The helper sleeps `--after` before it could write anything, and its
    detached spawn is documented as possibly not surviving. An attempt file
    only written by helpers that lived cannot report the ones that did not.
    Codex review of this branch."""
    main_mod.cmd_restart(_self_args())

    assert main_mod.restart_attempts(CWD)


def test_a_spawn_that_failed_leaves_no_attempt(session, monkeypatch, capsys):
    """Nothing is outstanding, so the next `--self` must not report a helper
    that never existed."""
    def _boom(*a, **k):
        raise OSError("no")

    monkeypatch.setattr(main_mod, "_dispatch_restart", _boom)

    assert main_mod.cmd_restart(_self_args()) == 1
    assert not main_mod.restart_attempts(CWD)


def test_arming_only_records_no_attempt(session):
    """`--arm-only` spawns nothing: the user quits the session themselves and
    there is no helper whose silence needs explaining."""
    main_mod.cmd_restart(_self_args(arm_only=True))

    assert not main_mod.restart_attempts(CWD)


def test_a_dry_run_dispatch_records_nothing(session):
    main_mod.cmd_restart(_self_args(dry_run=True))

    assert not main_mod.restart_attempts(CWD)


# ── and what the next `--self` does with it ──────────────────────────────


@pytest.fixture
def session(monkeypatch, tmp_path):
    """A reloaded-launched Claude session above this process."""
    monkeypatch.setattr(discover_mod, "owning_session", lambda: (111, CWD, "claude"))
    monkeypatch.setattr(discover_mod, "launcher_kind",
                        lambda pid: discover_mod.RELOADED)
    monkeypatch.setattr(main_mod, "restart_marker", lambda cwd: tmp_path / "m.marker")
    monkeypatch.setattr(main_mod, "_dispatch_restart",
                        lambda *a, **k: 4242)
    monkeypatch.setattr(main_mod, "log_path", lambda: tmp_path / "reloaded.log")
    # `cmd_restart_self` asks whether this directory is shared before it
    # dispatches. Unstubbed that is a real process_iter walk per test, which
    # both costs seconds and makes the answer depend on what the developer
    # happens to have open.
    monkeypatch.setattr(main_mod.discover_mod, "crowded_dirs", lambda: {})


def _self_args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "dry_run": False,
         "repos": [], "self_": True, "cancel": False, "arm_only": False,
         "after": 0.0}
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_the_next_restart_names_the_one_that_never_reported_back(session, capsys):
    _settled()

    main_mod.cmd_restart(_self_args())

    out = capsys.readouterr().out
    assert "never reported back" in out, "it said nothing about the last attempt"
    assert "reloaded.log" in out, "it never says where to read the account"


def test_it_does_not_claim_the_previous_one_failed(session, capsys):
    """Presence cannot establish failure. A helper killed moments after the
    session came back leaves the same file as one that achieved nothing, and
    only the log distinguishes them. Codex review of this branch."""
    _settled()

    main_mod.cmd_restart(_self_args())

    assert "failed" not in capsys.readouterr().out


def test_an_unreadable_attempt_is_still_reported(session, capsys):
    """The helper dying hardest is exactly the case that must not fall
    silent."""
    _settled(unreadable=True)

    main_mod.cmd_restart(_self_args())

    assert "never reported back" in capsys.readouterr().out


def test_a_reported_attempt_is_not_reported_forever(session, capsys):
    """Reported once, then cleared. A permanent notice about a restart from
    days ago is noise the next real one hides behind.

    Through `--arm-only`, which spawns nothing: a second dispatching call
    would leave an attempt of its own and be reporting that, which is correct
    and would prove nothing about the first."""
    _settled()

    main_mod.cmd_restart(_self_args(arm_only=True))
    capsys.readouterr()
    main_mod.cmd_restart(_self_args(arm_only=True))

    assert "never reported back" not in capsys.readouterr().out


def test_a_restart_still_in_flight_is_not_called_a_failure(session, capsys):
    """Repeating `--self` moments later finds a record that has neither failed
    nor finished — the first helper is still inside its delay, its patience,
    or its wait for the session to come back. Codex review of this branch."""
    main_mod.cmd_restart(_self_args())
    capsys.readouterr()

    main_mod.cmd_restart(_self_args())

    assert "never reported back" not in capsys.readouterr().out


def test_a_restart_still_in_flight_keeps_its_record(session):
    """And the reason it matters: if that first helper later dies while the
    second succeeds and clears its own token, this is the only thing left to
    report the first."""
    main_mod.cmd_restart(_self_args())

    main_mod.cmd_restart(_self_args())

    assert len(main_mod.restart_attempts(CWD)) == 2


def test_a_long_delay_is_part_of_the_record(session, capsys):
    """`--after` takes an arbitrary delay, so a constant budget would settle a
    record while its helper was still asleep — `--after 300` judged after 145
    seconds. The deadline is written by the process that chose the delay.
    Codex review of this branch."""
    _record("slow", after=300.0)

    main_mod.cmd_restart(_self_args(arm_only=True))

    out = capsys.readouterr().out
    assert "never reported back" not in out
    assert main_mod.restart_attempts(CWD), "it consumed a helper still sleeping"


def test_the_budget_counts_both_relaunch_waits(session):
    """`_await_relaunch` spends one and then `_reopen_in_a_new_tab` spends
    another, so budgeting one left the tail of the fallback path unprotected."""
    assert main_mod._attempt_budget(0.0) >= (
        main_mod.SELF_EXIT_PATIENCE_SECONDS + 2 * main_mod.RELAUNCH_WAIT_SECONDS)


def test_once_its_helper_can_only_be_finished_it_is_reported(session, capsys):
    """The bound is the helper's own worst case — its delay, the whole marker
    it may spend asking, and the wait for the session to return."""
    _settled()

    main_mod.cmd_restart(_self_args(arm_only=True))

    assert "never reported back" in capsys.readouterr().out


def test_a_dry_run_reports_the_attempt_without_eating_it(session, capsys):
    """`--dry-run` promises to change nothing. Consuming the only record of an
    unreported restart on the way to saying it did nothing would leave no real
    invocation able to warn about it. Codex review of this branch."""
    _settled()

    main_mod.cmd_restart(_self_args(dry_run=True))

    assert "never reported back" in capsys.readouterr().out
    assert main_mod.restart_attempts(CWD), "a dry run destroyed the evidence"


def test_the_next_real_restart_still_reports_it(session, capsys):
    """The half that matters: the dry run left it, so this one finds it."""
    _settled()
    main_mod.cmd_restart(_self_args(dry_run=True))
    capsys.readouterr()

    main_mod.cmd_restart(_self_args(arm_only=True))

    assert "never reported back" in capsys.readouterr().out


def test_a_first_restart_says_nothing_about_previous_ones(session, capsys):
    main_mod.cmd_restart(_self_args())

    assert "never reported back" not in capsys.readouterr().out


def test_the_promise_is_no_longer_unconditional(session, capsys):
    """"Nothing further to do" is only true when it works. It was printed the
    instant the helper was spawned, before anything had been attempted."""
    main_mod.cmd_restart(_self_args())

    out = capsys.readouterr().out
    assert "Nothing further to do" not in out
    assert "reloaded.log" in out, "it never says where the account will be"
