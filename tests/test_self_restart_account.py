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
         "after": 0.0, "force": False, "dispatched": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


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


def test_a_dispatched_restart_that_works_leaves_nothing_behind(restart_one):
    main_mod.cmd_restart_one(_args(after=5.0, dispatched=True), [CWD])

    assert not main_mod.restart_attempt(CWD).exists()


def test_a_dispatched_restart_that_fails_leaves_its_attempt(restart_one):
    restart_one["came_back"] = False

    main_mod.cmd_restart_one(_args(after=5.0, dispatched=True), [CWD])

    assert main_mod.restart_attempt(CWD).exists(), (
        "nothing on disk says the restart did not happen")


def test_a_refusal_before_any_keystroke_leaves_one_too(monkeypatch):
    """`_nothing_running_there` and `_no_tab_to_type_into` are exactly the
    silent failures the dispatcher's docstring warned about: the helper
    refuses into DEVNULL after the caller has already promised a restart."""
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})

    assert main_mod.cmd_restart_one(_args(after=5.0, dispatched=True), [CWD]) == 1
    assert main_mod.restart_attempt(CWD).exists()


def test_a_restart_someone_is_watching_records_nothing(restart_one):
    """Named from another tab, the failure is already on the caller's screen.
    A file for them to trip over later would be noise."""
    restart_one["came_back"] = False

    main_mod.cmd_restart_one(_args(), [CWD])

    assert not main_mod.restart_attempt(CWD).exists()


def test_a_dry_run_records_nothing(restart_one, monkeypatch):
    monkeypatch.setattr(main_mod, "_preview_restart", lambda plans: None)

    main_mod.cmd_restart_one(_args(after=5.0, dispatched=True, dry_run=True),
                             [CWD])

    assert not main_mod.restart_attempt(CWD).exists()


def test_the_public_delay_flag_records_nothing(restart_one):
    """`--after` is an ordinary option that a named restart accepts alongside
    any number of repos, so it cannot be read as "this was dispatched".
    Codex review of this branch."""
    restart_one["came_back"] = False

    main_mod.cmd_restart_one(_args(after=5.0), [CWD])

    assert not main_mod.restart_attempt(CWD).exists()


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


def _self_args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "dry_run": False,
         "repos": [], "self_": True, "cancel": False, "arm_only": False,
         "after": 0.0}
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_the_next_restart_names_the_one_that_never_reported_back(session, capsys):
    main_mod.restart_attempt(CWD).write_text("1758445664 23900", encoding="utf-8")

    main_mod.cmd_restart(_self_args())

    out = capsys.readouterr().out
    assert "never reported back" in out, "it said nothing about the last attempt"
    assert "reloaded.log" in out, "it never says where to read the account"


def test_it_does_not_claim_the_previous_one_failed(session, capsys):
    """Presence cannot establish failure. A helper killed moments after the
    session came back leaves the same file as one that achieved nothing, and
    only the log distinguishes them. Codex review of this branch."""
    main_mod.restart_attempt(CWD).write_text("1758445664 23900", encoding="utf-8")

    main_mod.cmd_restart(_self_args())

    assert "failed" not in capsys.readouterr().out


def test_an_unreadable_attempt_is_still_reported(session, capsys):
    """The helper dying hardest is exactly the case that must not fall
    silent."""
    main_mod.restart_attempt(CWD).write_text("", encoding="utf-8")

    main_mod.cmd_restart(_self_args())

    assert "never reported back" in capsys.readouterr().out


def test_a_reported_attempt_is_not_reported_forever(session, capsys):
    """Reported once, then cleared. A permanent notice about a restart from
    days ago is noise the next real one hides behind."""
    main_mod.restart_attempt(CWD).write_text("1758445664 23900", encoding="utf-8")

    main_mod.cmd_restart(_self_args())
    capsys.readouterr()
    main_mod.cmd_restart(_self_args())

    assert "never reported back" not in capsys.readouterr().out


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
