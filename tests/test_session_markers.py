"""A restart request goes to the session, not to its directory (#37), and is
neither swept nor armed out from under it (#33).

A directory's marker is watched by every tab in that directory, so in one
holding two reloaded tabs whichever exited first took the request: the other
session did not come back, and the one that took it restarted unasked. A loop
launched since f28e60c reads a marker named by its own shell's pid first.
Addressed there, a request has one reader. A loop older than that reads only
the directory's, so it is still sent there.
"""
from __future__ import annotations

import os
import time
import types

import psutil
import pytest

import reloaded.__main__ as main_mod
import reloaded.discover as discover_mod
import reloaded.teardown as teardown_mod
from reloaded.deploy import RESTART_MARKER_TTL_SECONDS

NEW_LOOP = ["pwsh", "-NoExit", "-Command",
            "$rlNext=$null; $rlOwn='C:\\x\\shell-' + $PID + '.marker'; "
            "while ($true) { $rlStart=Get-Date; claude --continue }"]
OLD_LOOP = ["pwsh", "-NoExit", "-Command",
            "while ($true) { $rlStart=Get-Date; claude --continue }"]


def _table(monkeypatch, shell_cmdline=None, unreadable=False):
    """Session 20 under shell 10."""
    def cmdline():
        if unreadable:
            raise PermissionError("denied")
        return list(shell_cmdline)

    shell = types.SimpleNamespace(pid=10, cmdline=cmdline)
    session = types.SimpleNamespace(pid=20, parent=lambda: shell)
    procs = {20: session, 10: shell}

    def process(pid):
        if pid not in procs:
            raise LookupError(pid)
        return procs[pid]

    monkeypatch.setattr(discover_mod, "_ps",
                        lambda: types.SimpleNamespace(Process=process))


def test_a_loop_with_its_own_marker_is_addressed_by_its_shell(monkeypatch):
    _table(monkeypatch, NEW_LOOP)
    assert discover_mod.marker_shell(20) == 10


def test_an_older_loop_has_only_its_directory(monkeypatch):
    _table(monkeypatch, OLD_LOOP)
    assert discover_mod.marker_shell(20) is None


def test_an_unreadable_shell_falls_back_to_the_directory(monkeypatch):
    """Every loop reads its directory's marker too, so that is the request
    still reaching an unreadable shell rather than one sent nowhere."""
    _table(monkeypatch, unreadable=True)
    assert discover_mod.marker_shell(20) is None
    assert discover_mod.marker_shell(404) is None


def _restarting(marker_shell):
    return main_mod._Restarting(
        hwnd=1, item=object(), cwd=r"C:\repos\shared", pid=20,
        launcher=discover_mod.RELOADED, agent="claude", command="claude",
        size_bytes=0, marker_shell=marker_shell)


def test_restart_writes_the_sessions_own_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(main_mod, "shell_marker",
                        lambda pid: tmp_path / f"shell-{pid}.marker")
    monkeypatch.setattr(main_mod, "restart_marker",
                        lambda cwd: tmp_path / "directory.marker")

    assert _restarting(10).marker == tmp_path / "shell-10.marker"
    assert _restarting(None).marker == tmp_path / "directory.marker"


def test_the_snapshot_reads_the_marker_shell_while_the_session_lives(monkeypatch):
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda **k: {})
    monkeypatch.setattr(main_mod, "_recorded_launches", lambda args: {})
    monkeypatch.setattr(main_mod.discover_mod, "session_launch",
                        lambda pid: ("claude", "claude --continue"))
    monkeypatch.setattr(main_mod.discover_mod, "launcher_kind",
                        lambda pid: main_mod.discover_mod.RELOADED)
    monkeypatch.setattr(main_mod.discover_mod, "marker_shell", lambda pid: 10)
    plan = teardown_mod.WindowPlan(hwnd=1, total_tabs=1, targets=[
        teardown_mod.Target("shared", r"C:\repos\shared", 20, object())])

    [s] = main_mod._snapshot_restarts([plan], types.SimpleNamespace())

    assert s.marker_shell == 10


def test_a_hand_started_session_reads_no_marker_shell(monkeypatch):
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda **k: {})
    monkeypatch.setattr(main_mod, "_recorded_launches", lambda args: {})
    monkeypatch.setattr(main_mod.discover_mod, "session_launch",
                        lambda pid: ("claude", "claude"))
    monkeypatch.setattr(main_mod.discover_mod, "launcher_kind",
                        lambda pid: main_mod.discover_mod.HAND)
    monkeypatch.setattr(main_mod.discover_mod, "marker_shell",
                        lambda pid: pytest.fail("no loop reads a marker there"))
    plan = teardown_mod.WindowPlan(hwnd=1, total_tabs=1, targets=[
        teardown_mod.Target("shared", r"C:\repos\shared", 20, object())])

    [s] = main_mod._snapshot_restarts([plan], types.SimpleNamespace())

    assert s.marker_shell is None


def test_a_marker_rearmed_after_the_listing_is_not_swept(monkeypatch, tmp_path):
    """The sweep listed a stale marker, a restart running alongside re-armed
    that path, and the sweep then deleted the fresh one: the tab exited, found
    nothing, and closed instead of relaunching."""
    marker = tmp_path / "shell-10.marker"
    marker.write_text("restart", encoding="utf-8")
    old = time.time() - RESTART_MARKER_TTL_SECONDS - 60
    os.utime(marker, (old, old))

    def listing(pattern):
        yield marker
        os.utime(marker, None)  # the other restart arms it, between the two

    monkeypatch.setattr(main_mod, "restart_marker_dir",
                        lambda: types.SimpleNamespace(glob=listing))

    main_mod._sweep_stale_markers()

    assert marker.exists()


def test_a_gone_session_is_not_armed(monkeypatch):
    """Its turn came after it had ended on its own. Its loop had already
    looked for a marker, so one written now waits for the next quit there
    and relaunches a session the user meant to close."""
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_quit_keystrokes",
                        lambda keys, **k: 0)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    armed = []

    sent = teardown_mod._send_exit(1, object(), pid=4321,
                                   before_send=lambda: armed.append(1))

    assert armed == []
    assert sent.keys == 0


def test_a_running_session_is_armed_before_its_keys(monkeypatch):
    order = []
    monkeypatch.setattr(teardown_mod.tabs, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(teardown_mod.tabs, "send_quit_keystrokes",
                        lambda keys, **k: order.append("keys") or 2)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)

    teardown_mod._send_exit(1, object(), pid=4321,
                            before_send=lambda: order.append("arm"))

    assert order == ["arm", "keys"]
