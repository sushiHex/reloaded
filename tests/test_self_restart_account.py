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


# ── and what it now promises instead ─────────────────────────────────────


@pytest.fixture
def session(monkeypatch, tmp_path):
    """A reloaded-launched Claude session above this process."""
    monkeypatch.setattr(discover_mod, "owning_session", lambda: (111, CWD, "claude"))
    monkeypatch.setattr(discover_mod, "launcher_kind",
                        lambda pid: discover_mod.RELOADED)
    monkeypatch.setattr(main_mod, "restart_marker", lambda cwd: tmp_path / "m.marker")
    monkeypatch.setattr(main_mod, "_dispatch_restart", lambda *a, **k: 4242)
    monkeypatch.setattr(main_mod, "log_path", lambda: tmp_path / "reloaded.log")


def _self_args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "dry_run": False,
         "repos": [], "self_": True, "cancel": False, "arm_only": False,
         "after": 0.0, "dispatched": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_the_promise_is_no_longer_unconditional(session, capsys):
    """"Nothing further to do" is only true when it works. It was printed the
    instant the helper was spawned, before anything had been attempted."""
    main_mod.cmd_restart(_self_args())

    out = capsys.readouterr().out
    assert "Nothing further to do" not in out
    assert "reloaded.log" in out, "it never says where the account will be"
