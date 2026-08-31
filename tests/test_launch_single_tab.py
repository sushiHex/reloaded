"""Opening one repo as a tab must open the right CLI in it.

Same class of bug as _type_relaunch's: a function grew agent/command
parameters and a call site kept passing neither, so a Codex repo came back as
Claude. Found by auditing the call sites, not by a failing test.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
from reloaded.layout import Tab

CWD = r"C:\repos\constructicon"


def _session(**kw) -> main_mod._Restarting:
    """A reloaded-launched Claude session, unless a test says otherwise."""
    fields = dict(hwnd=1, item=object(), cwd=CWD,
                  pid=111, launcher=main_mod.discover_mod.RELOADED,
                  agent="claude", command="", size_bytes=0)
    fields.update(kw)
    return main_mod._Restarting(**fields)


@pytest.fixture
def spawned(monkeypatch, tmp_path):
    """Record the wt argv, without spawning anything."""
    argvs = []

    monkeypatch.setattr(main_mod.discover_mod, "transcript_index",
                        lambda *a, **k: {})
    monkeypatch.setattr(main_mod.deploy_mod, "shell_executable", lambda: "pwsh")

    import subprocess

    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: argvs.append(argv) or None)
    monkeypatch.setattr(main_mod, "layout_path", lambda n: tmp_path / "l.json")
    return argvs


def _command(argv):
    """The PowerShell command wt was told to run."""
    return argv[-1]


def test_a_codex_repo_is_reopened_with_codex(spawned):
    main_mod._launch_single_tab(_session(agent="codex"))

    assert "codex resume --last" in _command(spawned[0])
    assert "claude --dangerously-skip-permissions" not in _command(spawned[0])


def test_a_captured_command_is_used(spawned):
    main_mod._launch_single_tab(_session(agent="codex", command="codex --profile fast"))

    assert "codex --profile fast" in _command(spawned[0])


def test_a_claude_repo_is_unchanged(spawned):
    main_mod._launch_single_tab(_session())

    assert "claude --dangerously-skip-permissions" in _command(spawned[0])


# ── `reloaded open` ──────────────────────────────────────────────────────


def _args(repo, tmp_path, **kw):
    d = {"repo": repo, "repos_root": r"C:\repos", "dry_run": False,
         "layout": "default"}
    d.update(kw)
    return types.SimpleNamespace(**d)


def _save_layout(tmp_path, agent, command=""):
    from conftest import make_layout, make_window
    from reloaded import layout as layout_mod

    lo = make_layout([make_window([0, 0, 100, 100], [
        Tab(cwd=CWD, title="constructicon", agent=agent, command=command),
    ])])
    layout_mod.save(lo, tmp_path / "l.json")


def test_open_uses_the_kind_the_layout_recorded(spawned, monkeypatch, tmp_path):
    """`reloaded open constructicon` should bring back a Codex session, not
    replace it with a Claude one."""
    _save_layout(tmp_path, "codex", "codex resume --last")
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    monkeypatch.setattr(main_mod.os.path, "isdir", lambda p: True)

    main_mod.cmd_open(_args("constructicon", tmp_path))

    assert "codex resume --last" in _command(spawned[0])


def test_open_falls_back_to_claude_with_no_layout(spawned, monkeypatch, tmp_path):
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    monkeypatch.setattr(main_mod.os.path, "isdir", lambda p: True)

    main_mod.cmd_open(_args("constructicon", tmp_path))

    assert "claude --dangerously-skip-permissions" in _command(spawned[0])


def test_open_falls_back_to_claude_for_a_repo_not_in_the_layout(spawned, monkeypatch, tmp_path):
    _save_layout(tmp_path, "codex")
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    monkeypatch.setattr(main_mod.os.path, "isdir", lambda p: True)

    main_mod.cmd_open(_args("somewhere-else", tmp_path))

    assert "claude --dangerously-skip-permissions" in _command(spawned[0])
