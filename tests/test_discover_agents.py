"""Discovery sees both kinds, and only the ones that are actually tabs.

An agent process is a managed session when it IS a terminal tab. Two shapes
are not, and both were live on this machine at once:

  the Codex desktop app hosts its own codex.exe, under ChatGPT.exe;

  an agent running a tool call spawns another agent - `ask_codex` from a
  Claude Code session is a `codex exec` three hops below claude.exe.

Neither has a tab. The rule that covers both is ancestry: an agent nested
inside another program is that program's, not a session of its own.

Measured, not reasoned about. Of five live codex.exe processes here, three
were ephemeral exec runs under Claude sessions, and discovery reported all
three as Codex sessions in repos with no Codex tab. One shared a directory
with a real Claude session, where keying by cwd let last-writer-wins decide
what kind that directory was — and a Codex verdict there sends Ctrl+C to a
Claude session.
"""
from __future__ import annotations

import sys
import types

import reloaded.discover as discover_mod
import reloaded.win32 as win32_mod
from reloaded.paths import norm
from reloaded.win32 import Proc

REPOS = r"C:\Users\k\repos"


class _Proc(types.SimpleNamespace):
    """One process and its ancestry, named outward: the immediate parent
    first. A tab's agent has its shell there and nothing above it that
    matters."""

    def __init__(self, pid, name, cwd, ancestors=("pwsh.exe",)):
        super().__init__(pid=pid, name=name, cwd=cwd, ancestors=ancestors)


def _stub(monkeypatch, procs, table=None):
    """The machine's process table, built from each process's ancestry: every
    ancestor is older than the one below it, as a real parent is."""
    table = dict(table or {})
    for p in procs:
        line = [p.pid] + [p.pid * 100 + hop for hop in range(1, len(p.ancestors) + 1)]
        names = [p.name, *p.ancestors]
        for hop, pid in enumerate(line):
            parent = line[hop + 1] if hop + 1 < len(line) else 0
            table[pid] = Proc(parent, names[hop], float(-hop))
    cwds = {p.pid: p.cwd for p in procs}

    def cwd(pid):
        if cwds.get(pid) is None:
            raise PermissionError("denied")
        return cwds[pid]

    monkeypatch.setattr(win32_mod, "process_table", lambda: table)
    _psutil(monkeypatch, cwd, lambda pid: table[pid].created)


def _psutil(monkeypatch, cwd, started):
    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(
        Process=lambda pid: types.SimpleNamespace(
            cwd=lambda: cwd(pid), create_time=lambda: started(pid))))


def test_a_claude_session_is_found(monkeypatch):
    _stub(monkeypatch, [_Proc(1, "claude.exe", REPOS + r"\alpha")])

    assert discover_mod.live_sessions() == {norm(REPOS + r"\alpha"): 1}


def test_a_codex_session_is_found(monkeypatch):
    """The gap this closes: a Codex tab was invisible to every command."""
    _stub(monkeypatch, [_Proc(2, "codex.exe", REPOS + r"\beta")])

    assert discover_mod.live_sessions() == {norm(REPOS + r"\beta"): 2}


def test_both_kinds_are_reported_together(monkeypatch):
    _stub(monkeypatch, [
        _Proc(1, "claude.exe", REPOS + r"\alpha"),
        _Proc(2, "codex.exe", REPOS + r"\beta"),
    ])

    assert discover_mod.live_agents() == {
        norm(REPOS + r"\alpha"): "claude",
        norm(REPOS + r"\beta"): "codex",
    }


def test_a_tab_under_windows_terminal_is_kept(monkeypatch):
    """The real shape: agent, its shell, the terminal. Nothing above the shell
    is an agent, so nothing about this is nested."""
    _stub(monkeypatch, [
        _Proc(1, "codex.exe", REPOS + r"\beta",
              ancestors=("pwsh.exe", "WindowsTerminal.exe", "explorer.exe")),
    ])

    assert discover_mod.live_sessions() == {norm(REPOS + r"\beta"): 1}


# ── the two things that are not tabs ─────────────────────────────────────


def test_the_desktop_app_codex_is_excluded(monkeypatch):
    """It is not a terminal tab. Relaunching it into one would be wrong."""
    _stub(monkeypatch, [
        _Proc(3, "codex.exe",
              r"C:\Program Files\WindowsApps\OpenAI.Codex\app",
              ancestors=("ChatGPT.exe",)),
    ])

    assert discover_mod.live_sessions() == {}
    assert discover_mod.live_agents() == {}


def test_a_terminal_codex_beside_a_desktop_one_is_still_found(monkeypatch):
    """Both run at once here. Excluding by image name would lose the real tab
    along with the desktop app."""
    _stub(monkeypatch, [
        _Proc(3, "codex.exe", r"C:\Program Files\WindowsApps\OpenAI.Codex\app",
              ancestors=("ChatGPT.exe",)),
        _Proc(4, "codex.exe", REPOS + r"\beta"),
    ])

    assert discover_mod.live_sessions() == {norm(REPOS + r"\beta"): 4}


def test_an_agent_running_inside_another_agent_is_not_a_session(monkeypatch):
    """`ask_codex` from a Claude Code session. The exact chain measured here:
    codex.exe <- python.exe <- hardline-mcp.exe <- claude.exe <- pwsh.exe.

    It has no tab, it lives for one question, and reporting it made three
    repos look like they held Codex sessions."""
    _stub(monkeypatch, [
        _Proc(5, "codex.exe", REPOS + r"\gamma",
              ancestors=("python.exe", "hardline-mcp.exe", "claude.exe",
                         "pwsh.exe", "WindowsTerminal.exe")),
    ])

    assert discover_mod.live_sessions() == {}


def test_a_nested_agent_does_not_steal_a_directory_from_a_real_one(monkeypatch):
    """The damaging case, and the one that was live. Sessions are keyed by
    cwd, so a tool call in the same directory as a real session could win on
    last-writer and flip that directory's kind. Ctrl+C then goes to Claude."""
    _stub(monkeypatch, [
        _Proc(1, "claude.exe", REPOS + r"\delta"),
        _Proc(5, "codex.exe", REPOS + r"\delta",
              ancestors=("python.exe", "hardline-mcp.exe", "claude.exe")),
    ])

    assert discover_mod.live_agents() == {norm(REPOS + r"\delta"): "claude"}
    assert discover_mod.live_sessions() == {norm(REPOS + r"\delta"): 1}


def test_a_claude_inside_a_claude_is_also_excluded(monkeypatch):
    """Not Codex-specific. A subagent, or any Claude spawned by a tool call,
    is that session's, not a tab."""
    _stub(monkeypatch, [
        _Proc(6, "claude.exe", REPOS + r"\eps",
              ancestors=("pwsh.exe", "claude.exe", "pwsh.exe")),
    ])

    assert discover_mod.live_sessions() == {}


def test_the_walk_is_bounded(monkeypatch):
    """A chain longer than any real one, or a pid loop. Answering is better
    than hanging, and a deep chain with no agent in reach is a tab."""
    deep = tuple(["pwsh.exe"] * 40)
    _stub(monkeypatch, [_Proc(7, "codex.exe", REPOS + r"\zeta", ancestors=deep)])

    assert discover_mod.live_sessions() == {norm(REPOS + r"\zeta"): 7}


def test_an_agent_past_the_search_depth_is_not_a_host(monkeypatch):
    """Eight ancestors is the reach. The observed nesting is three."""
    within = tuple(["pwsh.exe"] * 7 + ["claude.exe"])
    beyond = tuple(["pwsh.exe"] * 8 + ["claude.exe"])
    _stub(monkeypatch, [_Proc(7, "codex.exe", REPOS + r"\zeta", ancestors=within),
                        _Proc(8, "codex.exe", REPOS + r"\eta", ancestors=beyond)])

    assert discover_mod.live_sessions() == {norm(REPOS + r"\eta"): 8}


# ── failures that must not drop a real session ───────────────────────────


def test_a_reused_parent_pid_does_not_drop_the_session(monkeypatch):
    """Its recorded parent exited, and Windows gave that pid to a Claude
    session started later. Believing the table would make a real tab look
    nested and drop it; a parent younger than its child is not its parent."""
    _stub(monkeypatch, [_Proc(8, "codex.exe", REPOS + r"\eta", ancestors=())],
          table={0: Proc(0, "claude.exe", 5.0)})

    assert discover_mod.live_sessions() == {norm(REPOS + r"\eta"): 8}


def test_a_parent_as_old_as_its_child_is_still_its_parent(monkeypatch):
    """The other side of that rule, which is psutil's: equal is a parent."""
    _stub(monkeypatch, [_Proc(8, "codex.exe", REPOS + r"\eta", ancestors=())],
          table={0: Proc(0, "claude.exe", 0.0)})

    assert discover_mod.live_sessions() == {}


def test_a_pid_loop_ends_the_walk(monkeypatch):
    _stub(monkeypatch, [], table={
        8: Proc(9, "codex.exe", 0.0), 9: Proc(8, "pwsh.exe", 0.0)})
    _psutil(monkeypatch, lambda pid: REPOS + r"\eta", lambda pid: 0.0)

    assert discover_mod.live_sessions() == {norm(REPOS + r"\eta"): 8}


def test_a_session_pid_reused_since_the_snapshot_is_not_read(monkeypatch):
    """The table named a Codex session at that pid; the process there now
    started later and is something else. Its cwd would be filed under the
    old session's kind."""
    _stub(monkeypatch, [_Proc(8, "codex.exe", REPOS + r"\eta")])
    _psutil(monkeypatch, lambda pid: REPOS + r"\eta", lambda pid: 5.0)

    assert discover_mod.live_sessions() == {}


def test_a_process_with_no_parent_at_all_is_kept(monkeypatch):
    _stub(monkeypatch, [_Proc(9, "codex.exe", REPOS + r"\theta", ancestors=())])

    assert discover_mod.live_sessions() == {norm(REPOS + r"\theta"): 9}


def test_a_process_with_no_readable_cwd_is_skipped(monkeypatch):
    _stub(monkeypatch, [_Proc(10, "codex.exe", None)])

    assert discover_mod.live_sessions() == {}


def test_an_unrelated_process_is_ignored(monkeypatch):
    _stub(monkeypatch, [_Proc(11, "notepad.exe", REPOS + r"\iota")])

    assert discover_mod.live_sessions() == {}


def test_live_sessions_still_returns_plain_pids(monkeypatch):
    """Nine call sites depend on dict[str, int]; the kind travels separately
    rather than changing this signature."""
    _stub(monkeypatch, [_Proc(12, "codex.exe", REPOS + r"\kappa")])

    assert all(isinstance(v, int) for v in discover_mod.live_sessions().values())
