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

    assert discover_mod.held_threads(1) == ["T-root"]


def test_unreadable_handles_or_files_hold_nothing(tmp_path, monkeypatch):
    bad = tmp_path / "rollout-x.jsonl"
    bad.write_text("not json\n", encoding="utf-8")
    _holding(monkeypatch, [str(bad), str(tmp_path / "rollout-gone.jsonl")])
    assert discover_mod.held_threads(1) == []

    def _denied():
        raise PermissionError("denied")
    proc = types.SimpleNamespace(open_files=_denied)
    monkeypatch.setattr(discover_mod, "_ps", lambda: types.SimpleNamespace(Process=lambda pid: proc))
    assert discover_mod.held_threads(1) == []


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
    monkeypatch.setattr(discover_mod, "held_threads", lambda pid: ["T1"] if pid == 20 else [])

    assert relaunch_mod._resume_command(_Session(ARGV), "codex") == (
        "codex resume --no-daemon T1 --dangerously-bypass-approvals-and-sandbox")


def test_an_id_its_process_does_not_hold_is_not_taken(monkeypatch):
    """Inherited from some other Codex - a variable alone says nothing about
    whose it is. The restart resumes as the command does, as before."""
    monkeypatch.setenv("CODEX_SESSION_ID", "T-other")
    monkeypatch.setattr(discover_mod, "held_threads", lambda pid: ["T1"])

    assert relaunch_mod._resume_command(_Session(ARGV), "codex") == (
        "codex resume --no-daemon --last --dangerously-bypass-approvals-and-sandbox")


def test_claude_still_needs_its_own_pid_beside_the_id(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "S1")
    monkeypatch.setenv("CLAUDE_PID", "999")
    argv = ["claude.exe", "--continue"]

    assert "--resume" not in relaunch_mod._resume_command(_Session(argv), "claude")
    monkeypatch.setenv("CLAUDE_PID", "20")
    assert relaunch_mod._resume_command(_Session(argv), "claude").endswith("--resume S1")
