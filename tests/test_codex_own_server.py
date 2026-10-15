"""Codex tabs on a server of their own, and `restart --self` naming the thread.

Measured on Codex 0.162.1: attached to the shared daemon, a tab's tool calls
and MCP servers are the daemon's children, the daemon holds every attached
thread's rollout, and a turn outlives its tab. With `--no-daemon` they are the
tab's own again (agents.CODEX.own_server_flag).
"""
from __future__ import annotations

import json
import os
import types

import pytest

import reloaded.agents as agents_mod
import reloaded.deploy as deploy_mod
import reloaded.discover as discover_mod
import reloaded.relaunch as relaunch_mod


# ── own_server ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("command,expected", [
    ("codex resume --last --dangerously-bypass-approvals-and-sandbox",
     "codex resume --no-daemon --last --dangerously-bypass-approvals-and-sandbox"),
    ("codex --dangerously-bypass-approvals-and-sandbox",
     "codex --no-daemon --dangerously-bypass-approvals-and-sandbox"),
    # Before the prompt, where it is still an option.
    ("codex -m gpt 'fix it'", "codex --no-daemon -m gpt 'fix it'"),
    ("codex resume abc", "codex resume --no-daemon abc"),
    # Already its own.
    ("codex --no-daemon resume --last", "codex --no-daemon resume --last"),
    # Codex refuses --no-daemon beside --remote.
    ("codex --remote ws://h:1 resume --last", "codex --remote ws://h:1 resume --last"),
    # A full path to the program is still the program.
    ("'C:\\Program Files\\codex.exe' resume --last",
     "'C:\\Program Files\\codex.exe' resume --no-daemon --last"),
])
def test_a_codex_launch_gets_a_server_of_its_own(command, expected):
    assert agents_mod.own_server("codex", command) == expected


@pytest.mark.parametrize("command,expected", [
    # Booleans the help does not list: read as taking a value, `--yolo`
    # swallowed `resume`, and hid the `--remote` after `--not-so-yolo`.
    ("codex --yolo resume --last", "codex --yolo resume --no-daemon --last"),
    ("codex --not-so-yolo --remote ws://h:1 resume --last",
     "codex --not-so-yolo --remote ws://h:1 resume --last"),
    ("codex --remote=ws://h:1", "codex --remote=ws://h:1"),
    ("codex fork --last", "codex fork --no-daemon --last"),
    # Not a session: `codex agents` refuses the flag (`codex agents --help`).
    ("codex agents", "codex agents"),
    ("codex exec 'do it'", "codex exec 'do it'"),
])
def test_only_a_session_in_the_tab_is_given_its_own_server(command, expected):
    assert agents_mod.own_server("codex", command) == expected


def test_claude_has_no_such_flag():
    assert agents_mod.own_server("claude", "claude --continue") == "claude --continue"


def test_every_codex_tab_reloaded_opens_is_on_its_own_server():
    """Captured from a tab opened by hand on the daemon, and from the default."""
    for command in ("codex resume --last --dangerously-bypass-approvals-and-sandbox", ""):
        launcher = deploy_mod.launcher_command(r"C:\repos\app", 0, 0, "codex", command)
        assert "codex resume --no-daemon --last" in launcher


def test_the_subcommand_is_read_past_options_and_their_values():
    assert agents_mod.subcommand("codex", ["codex.exe", "-c", "a=b", "app-server"]) == "app-server"
    assert agents_mod.subcommand("codex", ["codex.exe", "--no-daemon", "resume"]) == "resume"
    assert agents_mod.subcommand("codex", ["codex.exe"]) == ""


# ── held_threads ─────────────────────────────────────────────────────────


def _rollout(tmp_path, name, thread_id, source="cli"):
    path = tmp_path / f"rollout-2026-10-15T13-45-20-{name}.jsonl"
    meta = {"type": "session_meta", "payload": {"id": thread_id, "source": source}}
    path.write_text(json.dumps(meta) + "\n{}\n", encoding="utf-8")
    return str(path)


def _holding(monkeypatch, paths):
    files = [types.SimpleNamespace(path=p) for p in paths]
    proc = types.SimpleNamespace(open_files=lambda: files)
    monkeypatch.setattr(discover_mod, "_ps", lambda: types.SimpleNamespace(Process=lambda pid: proc))


def test_the_threads_a_codex_writes_are_its_root_rollouts(tmp_path, monkeypatch):
    """A subagent's rollout is held beside its root's (measured), and is not
    the tab's conversation."""
    _holding(monkeypatch, [
        _rollout(tmp_path, "root", "T-root"),
        _rollout(tmp_path, "sub", "T-sub", {"subagent": {"thread_spawn": {}}}),
        str(tmp_path / "not-a-rollout.log")])

    assert discover_mod.held_threads(1, "codex") == ["T-root"]


def test_unreadable_handles_or_files_hold_nothing(tmp_path, monkeypatch):
    bad = tmp_path / "rollout-x.jsonl"
    bad.write_text("not json\n", encoding="utf-8")
    _holding(monkeypatch, [str(bad), str(tmp_path / "rollout-gone.jsonl")])
    assert discover_mod.held_threads(1, "codex") == []

    def _denied():
        raise PermissionError("denied")
    proc = types.SimpleNamespace(open_files=_denied)
    monkeypatch.setattr(discover_mod, "_ps", lambda: types.SimpleNamespace(Process=lambda pid: proc))
    assert discover_mod.held_threads(1, "codex") == []


# ── restart --self names the thread ──────────────────────────────────────


class _Session:
    pid = 20

    def __init__(self, argv):
        self._argv = argv

    def cmdline(self):
        return self._argv


ARGV = ["codex.exe", "resume", "--last", "--dangerously-bypass-approvals-and-sandbox"]


def test_a_codex_comes_back_to_the_thread_it_holds(monkeypatch):
    monkeypatch.setenv("CODEX_SESSION_ID", "T1")
    monkeypatch.setattr(discover_mod, "held_threads", lambda pid, kind: ["T1"] if pid == 20 else [])

    assert relaunch_mod._resume_command(_Session(ARGV), "codex") == (
        "codex resume --no-daemon T1 --dangerously-bypass-approvals-and-sandbox")


def test_an_id_its_process_does_not_hold_is_not_taken(monkeypatch):
    """Inherited from some other Codex - a variable alone says nothing about
    whose it is. The restart resumes as the command does, as before."""
    monkeypatch.setenv("CODEX_SESSION_ID", "T-other")
    monkeypatch.setattr(discover_mod, "held_threads", lambda pid, kind: ["T1"])

    assert relaunch_mod._resume_command(_Session(ARGV), "codex") == (
        "codex resume --no-daemon --last --dangerously-bypass-approvals-and-sandbox")


def test_an_unconfirmed_id_with_nothing_to_resume_is_refused(monkeypatch):
    """Started as plain `codex`: without the id, the restart would end this
    conversation and open an empty one."""
    monkeypatch.setenv("CODEX_SESSION_ID", "T1")
    monkeypatch.setattr(discover_mod, "held_threads", lambda pid, kind: [])

    with pytest.raises(relaunch_mod.Unconfirmed):
        relaunch_mod._resume_command(_Session(["codex.exe", "--yolo"]), "codex")


def test_claude_still_needs_its_own_pid_beside_the_id(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "S1")
    monkeypatch.setenv("CLAUDE_PID", "999")
    argv = ["claude.exe", "--continue"]

    assert "--resume" not in relaunch_mod._resume_command(_Session(argv), "claude")
    monkeypatch.setenv("CLAUDE_PID", "20")
    assert relaunch_mod._resume_command(_Session(argv), "claude").endswith("--resume S1")


# ── capture records the thread; the launch resumes it ────────────────────


def test_a_kind_that_holds_no_record_is_not_asked(monkeypatch):
    """Claude Code keeps no transcript open; reading every Claude session's
    handles would cost each capture ~150 ms a session for nothing."""
    monkeypatch.setattr(discover_mod, "_ps", lambda: pytest.fail("read open files"))

    assert discover_mod.held_threads(1, "claude") == []


TID = "01a1414f-f035-76b1-a2e3-5ddb2b3db568"


@pytest.mark.parametrize("command,expected", [
    ("codex resume --last --dangerously-bypass-approvals-and-sandbox",
     f"codex resume {TID} --dangerously-bypass-approvals-and-sandbox"),
    # The program as written: a smoke fake's full path stays the fake.
    (r"C:\smoke\.fakes\codex.exe resume --last", rf"C:\smoke\.fakes\codex.exe resume {TID}"),
    ("codex -c 'model=\"x\"' 'fix it'", f"codex resume {TID} -c 'model=\"x\"'"),
    ("", f"codex resume {TID} --dangerously-bypass-approvals-and-sandbox"),
])
def test_a_captured_thread_is_resumed_by_name(command, expected):
    assert agents_mod.resuming_exactly("codex", command, TID) == expected


def test_an_id_that_is_not_a_plain_id_is_not_spliced_in():
    command = "codex resume --last"
    assert agents_mod.resuming_exactly("codex", command, "x; Remove-Item -Recurse C:\\") == command
    assert agents_mod.resuming_exactly("codex", command, "") == command


def test_a_tab_with_a_thread_launches_into_it():
    """Two Codex tabs in one directory both came back as `--last`: the same
    conversation twice, and the other one out of reach."""
    launcher = deploy_mod.launcher_command(
        r"C:\repos\app", 0, 0, "codex",
        "codex resume --last --dangerously-bypass-approvals-and-sandbox", TID)

    assert f"codex resume --no-daemon {TID} --dangerously-bypass-approvals-and-sandbox" in launcher
    assert "--last" not in launcher


@pytest.mark.parametrize("held,expected", [
    ([TID], TID),
    # Two roots: a switch inside the TUI leaves no way to tell which is shown.
    ([TID, "01a14150-1856-70a1-b9a9-59b8e88a5552"], ""),
    # None: on the shared daemon, or before its first prompt.
    ([], ""),
])
def test_capture_records_the_thread_only_when_it_is_the_only_one(monkeypatch, held, expected):
    import reloaded.capture as capture_mod
    from reloaded.layout import Monitor
    from reloaded.paths import norm

    monkeypatch.setattr(discover_mod, "session_command", lambda pid: "codex resume --last")
    monkeypatch.setattr(discover_mod, "held_threads", lambda pid, kind: held)
    cwd = r"C:\repos\app"
    lo = capture_mod.build_layout(
        [{"titles": ["app"], "monitor": "M", "rect": [0, 0, 100, 100], "state": "normal",
          "dpi": 96, "inset": [0, 0, 0, 0]}],
        [Monitor(device="M", primary=True, work=[0, 0, 2560, 1392], dpi=96)],
        {}, {norm(cwd): 7}, r"C:\repos", "t", kinds={norm(cwd): "codex"})

    assert [t.session_id for w in lo.windows for t in w.tabs] == [expected]
