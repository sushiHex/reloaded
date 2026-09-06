"""Which commands bring a conversation back, and saying so before it is lost.

`restart` replays the command a session was actually running, because the flags
are the user's choice. That is right, and it has a sharp edge: a tab started by
typing plain `codex` comes back as plain `codex` — a NEW thread, at the same
directory, with none of the conversation in it. Nothing errors. The run reports
a successful restart for having discarded the thing it exists to preserve.

Found by asking Codex what `resume --last` actually does. It is arguably a
larger hole than anything in the launch defaults, because it silently costs
real work.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
import reloaded.agents as agents_mod


# ── what counts as resuming ──────────────────────────────────────────────


@pytest.mark.parametrize("kind,command,expected", [
    # Empty means the kind's own default, and both defaults resume.
    ("codex", "", True),
    ("claude", "", True),
    # The defaults themselves.
    ("codex", "codex resume --last --dangerously-bypass-approvals-and-sandbox", True),
    ("claude", "claude --dangerously-skip-permissions --continue", True),
    # The ones that quietly start over.
    ("codex", "codex", False),
    ("codex", "codex --dangerously-bypass-approvals-and-sandbox", False),
    ("claude", "claude", False),
    ("claude", "claude --dangerously-skip-permissions", False),
    # Other ways of asking for it.
    ("claude", "claude -c", True),
    ("claude", "claude --resume", True),
    ("codex", "codex resume 01a04603-b178-7272-ab41-c7c9474afa8a", True),
], ids=["codex-default", "claude-default", "codex-resume-last",
        "claude-continue", "codex-bare", "codex-flags-only", "claude-bare",
        "claude-flags-only", "claude-dash-c", "claude-resume", "codex-by-id"])
def test_resumes(kind, command, expected):
    assert agents_mod.resumes(kind, command) is expected


def test_a_token_inside_another_word_does_not_count():
    """`-c` is a resume flag; `--config` is not, and neither is a path with
    the word resume in it."""
    assert agents_mod.resumes("claude", "claude --concurrency 4") is False
    assert agents_mod.resumes("codex", "codex --cwd C:/resumes") is False


def test_an_unknown_kind_is_judged_as_claude():
    """Same defaulting as everywhere else - an unknown kind is what every
    layout meant before a second kind existed."""
    assert agents_mod.resumes("gemini", "claude --continue") is True


# ── saying so before anything is exited ──────────────────────────────────


def _session(**kw):
    fields = dict(hwnd=1, item=object(), cwd=r"C:\repos\app", pid=111,
                  launcher=main_mod.discover_mod.RELOADED, agent="codex",
                  command="codex", size_bytes=0)
    fields.update(kw)
    return main_mod._Restarting(**fields)


def test_it_names_the_sessions_that_will_come_back_empty(capsys):
    main_mod._warn_about_empty_relaunches([_session()])

    out = capsys.readouterr().out
    assert "EMPTY" in out
    assert r"C:\repos\app" in out
    assert "running: codex" in out, "it never shows the command at fault"


def test_it_says_what_to_do_about_it(capsys):
    """A warning with no way out is just an apology."""
    main_mod._warn_about_empty_relaunches([_session()])

    out = capsys.readouterr().out.lower()
    assert "ctrl+c now" in out
    assert "resume flag" in out


def test_a_resuming_session_produces_no_noise(capsys):
    main_mod._warn_about_empty_relaunches([
        _session(command="codex resume --last"),
        _session(agent="claude", command="claude --continue"),
    ])

    assert capsys.readouterr().out == ""


def test_a_captured_empty_command_is_not_warned_about(capsys):
    """"" means the kind's default, which resumes. Warning here would fire on
    every session whose command line could not be read."""
    main_mod._warn_about_empty_relaunches([_session(command="")])

    assert capsys.readouterr().out == ""


def test_the_warning_comes_before_anything_is_exited(monkeypatch, capsys):
    """The point of it. Said afterwards it is a post-mortem, and the choice it
    exists to offer is already gone."""
    order = []
    monkeypatch.setattr(main_mod, "_warn_about_empty_relaunches",
                        lambda s: order.append("warned"))
    monkeypatch.setattr(main_mod.teardown_mod, "execute_down",
                        lambda *a, **k: order.append("exited") or {
                            "exited": [], "timed_out": [], "closed": [],
                            "left_open": []})
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down",
                        lambda *a, **k: [main_mod.teardown_mod.WindowPlan(
                            hwnd=1, total_tabs=1,
                            targets=[main_mod.teardown_mod.Target(
                                "app", r"C:\repos\app", 111, object())])])
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions",
                        lambda: {main_mod.norm(r"C:\repos\app"): 111})
    monkeypatch.setattr(main_mod, "_snapshot_restarts", lambda p, a: [_session()])
    monkeypatch.setattr(main_mod, "_sweep_stale_markers", lambda: None)
    monkeypatch.setattr(main_mod, "resolve_repo", lambda r, root: r"C:\repos\app")

    main_mod.cmd_restart(types.SimpleNamespace(
        layout="default", repos_root=r"C:\repos", unattended=False,
        dry_run=False, repos=["app"], self_=False, cancel=False,
        arm_only=False, after=0.0))

    assert order[:2] == ["warned", "exited"]


# ── CODEX_HOME ───────────────────────────────────────────────────────────


def test_the_codex_state_directory_follows_codex_home(monkeypatch):
    """A session started under a different CODEX_HOME has a different thread
    store and a different name index entirely."""
    monkeypatch.setenv("CODEX_HOME", r"D:\elsewhere\.codex")

    assert agents_mod._codex_home() == r"D:\elsewhere\.codex"


def test_it_falls_back_to_the_home_directory(monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)

    assert agents_mod._codex_home().endswith(".codex")
