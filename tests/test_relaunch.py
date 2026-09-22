"""`/relaunch`: end the session by pid and let its tab bring it back.

The design this replaced asked the session to quit, by stealing keyboard focus
and typing `/exit` into it from a detached helper. Both halves failed for real:
Windows refused the focus the moment the user looked at another window, and a
busy session - one running an auto-compaction - swallowed the keystroke.

Nothing here types into a session. The session is ended, exactly, and its tab
brings it back: a looping tab from the restart marker, a plain shell from a
command written into its console's input buffer once the session is gone.
"""
from __future__ import annotations

import os
import types

import pytest

import reloaded.agents as agents_mod
import reloaded.discover as discover_mod
import reloaded.relaunch as relaunch_mod

CWD = r"C:\repos\app"


class _Proc:
    def __init__(self, pid, name="claude.exe", parent=None, cmdline=(),
                 children=(), created=100.0):
        self.pid = pid
        self._name = name
        self._parent = parent
        self._cmdline = list(cmdline)
        self._children = list(children)
        self._created = created
        self.ended = False

    def parent(self):
        return self._parent

    def parents(self):
        line, p = [], self._parent
        while p is not None:
            line.append(p)
            p = p._parent
        return line

    def name(self):
        return self._name

    def cmdline(self):
        return list(self._cmdline)

    def children(self, recursive=False):
        return list(self._children)

    def create_time(self):
        return self._created

    def terminate(self):
        self.ended = True


@pytest.fixture
def desk(monkeypatch, tmp_path):
    """A Claude session in a PowerShell tab, with this process running inside
    it the way a `!` step does: session -> bash -> us."""
    shell = _Proc(10, "pwsh.exe")
    session = _Proc(20, "claude.exe", parent=shell,
                    cmdline=["claude", "--dangerously-skip-permissions",
                             "--continue"])
    bash = _Proc(30, "bash.exe", parent=session)
    me = _Proc(os.getpid(), "python.exe", parent=bash)
    mcp = _Proc(40, "node.exe", parent=session)
    helper = _Proc(50, "python.exe", parent=me)
    # From a venv, python.exe is a redirector: the helper's real work runs in
    # its child, whose pid nobody was handed.
    helper_child = _Proc(60, "python.exe", parent=helper)
    mcp_child = _Proc(70, "python.exe", parent=mcp)
    session._children = [bash, me, mcp, helper, helper_child, mcp_child]
    table = {p.pid: p for p in (shell, session, bash, me, mcp, helper,
                                helper_child, mcp_child)}

    def _process(pid):
        if pid not in table:
            raise LookupError(pid)
        return table[pid]

    monkeypatch.setattr(discover_mod, "_ps",
                        lambda: types.SimpleNamespace(Process=_process))
    monkeypatch.setattr(discover_mod, "launcher_kind",
                        lambda pid: discover_mod.RELOADED)
    marker = tmp_path / "m.marker"
    monkeypatch.setattr(relaunch_mod, "restart_marker", lambda cwd: marker)
    spawned = []
    monkeypatch.setattr(
        relaunch_mod, "_spawn_helper",
        lambda s, sh, command, m: spawned.append(command) or helper.pid)
    monkeypatch.delenv("CLAUDE_PID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    return types.SimpleNamespace(shell=shell, session=session, bash=bash, me=me,
                                 mcp=mcp, mcp_child=mcp_child, helper=helper,
                                 helper_child=helper_child, marker=marker,
                                 spawned=spawned)


def _run(desk, **kw):
    return relaunch_mod.relaunch(desk.session.pid, CWD, "claude",
                                 say=lambda *_: None, **kw)


# ── what gets ended ──────────────────────────────────────────────────────


def test_it_arms_the_marker_hands_off_and_ends_the_session(desk):
    assert _run(desk) == 0
    assert desk.marker.read_text(encoding="utf-8") == "restart"
    assert desk.spawned, "no helper was started"
    assert desk.session.ended


def test_its_children_are_ended_too(desk):
    """Ended by pid, a session does not get to shut its MCP servers down the
    way `/exit` would. Left alone they would outlive it."""
    _run(desk)

    assert desk.mcp.ended
    assert desk.mcp_child.ended


def test_this_process_and_the_helper_are_spared(desk):
    """Both are the session's descendants. Ending this process's own line
    would stop it before the session; ending the helper would leave a plain
    tab at its prompt."""
    _run(desk)

    assert not desk.me.ended
    assert not desk.bash.ended
    assert not desk.helper.ended


def test_the_helpers_own_children_are_spared(desk):
    """The live failure: from a pipx venv the helper's pid is a redirector,
    and the interpreter doing the work is its child. Spared by pid alone,
    that child was ended with the session on every run, silently."""
    _run(desk)

    assert not desk.helper_child.ended


def test_a_plain_powershell_tab_is_not_refused(desk, monkeypatch):
    """What the old design refused, for the sake of not upgrading the tab.
    Nothing is upgraded now: the helper writes the resume command into the
    plain shell and it stays a plain shell."""
    monkeypatch.setattr(discover_mod, "launcher_kind",
                        lambda pid: discover_mod.HAND)

    assert _run(desk) == 0
    assert desk.session.ended


def test_a_plain_tab_under_something_else_is_refused_first(desk, monkeypatch):
    """The resume command is quoted for PowerShell, so a session under any
    other parent would be ended into a prompt that cannot run it back."""
    monkeypatch.setattr(discover_mod, "launcher_kind",
                        lambda pid: discover_mod.HAND)
    desk.shell._name = "cmd.exe"

    assert _run(desk) == 1
    assert not desk.session.ended
    assert not desk.marker.exists()


def test_a_looping_tab_is_fine_under_any_parent(desk):
    desk.shell._name = "cmd.exe"

    assert _run(desk) == 0


def test_the_dry_run_changes_nothing(desk):
    assert _run(desk, dry_run=True) == 0
    assert not desk.marker.exists()
    assert not desk.spawned
    assert not desk.session.ended


def test_a_helper_that_cannot_start_ends_nothing(desk, monkeypatch):
    """Nothing is lost if nothing is ended. The marker goes too: left, it
    would fire on the user's next deliberate quit."""
    def _boom(*a):
        raise OSError("no")

    monkeypatch.setattr(relaunch_mod, "_spawn_helper", _boom)

    assert _run(desk) == 1
    assert not desk.session.ended
    assert not desk.marker.exists()


def test_a_marker_that_will_not_go_is_reported_not_raised(desk, monkeypatch):
    """Still armed, it would relaunch the session on a quit the user meant.
    That is theirs to know, and the reason nothing was ended still is."""
    def _boom(*a):
        raise OSError("no")

    def _locked(self, missing_ok=False):
        raise PermissionError("in use")

    said = []
    monkeypatch.setattr(relaunch_mod, "_spawn_helper", _boom)
    monkeypatch.setattr(type(desk.marker), "unlink", _locked)

    assert relaunch_mod.relaunch(desk.session.pid, CWD, "claude",
                                 say=said.append) == 1
    assert any("Nothing was ended" in s for s in said)
    assert any("would relaunch the session" in s for s in said)


def test_a_helper_is_never_started_without_breakaway(monkeypatch, tmp_path):
    """Windows refuses breakaway only to a process in a job that forbids it -
    exactly where a helper started without it could die with the session and
    strand the tab. So there is no fallback: the refusal ends nothing."""
    calls = []

    def _popen(argv, creationflags=0, **kw):
        calls.append(creationflags)
        raise OSError("breakaway refused")

    monkeypatch.setattr(relaunch_mod.subprocess, "Popen", _popen)
    monkeypatch.setattr(relaunch_mod, "log_path", lambda: tmp_path / "log")
    proc = _Proc(20)

    with pytest.raises(OSError):
        relaunch_mod._spawn_helper(proc, _Proc(10), "claude", tmp_path / "m")
    assert len(calls) == 1 and calls[0] & 0x01000000


def test_the_helper_opens_no_window(monkeypatch, tmp_path):
    """Detached, a venv redirector's child was given a console of its own,
    which Windows Terminal showed as a blank window on every `/relaunch`. A
    windowless console is inherited instead; the two flags are exclusive."""
    calls = []
    monkeypatch.setattr(relaunch_mod.subprocess, "Popen",
                        lambda argv, creationflags=0, **kw: calls.append(creationflags))
    monkeypatch.setattr(relaunch_mod, "log_path", lambda: tmp_path / "log")

    relaunch_mod._spawn_helper(_Proc(20), _Proc(10), "claude", tmp_path / "m")

    assert calls[0] & 0x08000000, "CREATE_NO_WINDOW"
    assert not calls[0] & 0x00000008, "DETACHED_PROCESS"


def test_nothing_is_ended_if_this_process_is_not_inside_the_session(desk):
    """Without the whole line from here to the session there is no telling
    which descendants are safe to end - this process and the helper among
    them - so nothing is, and the marker goes."""
    desk.me._parent = None

    assert _run(desk) == 1
    assert not desk.session.ended
    assert not desk.mcp.ended
    assert not desk.marker.exists()


# ── what a plain tab gets back ───────────────────────────────────────────


def test_the_relaunch_names_this_conversation(desk, monkeypatch):
    """Claude Code hands its children its pid and session id. Bare `--resume`
    opens a picker and plain `claude` starts empty; neither is a restart."""
    monkeypatch.setenv("CLAUDE_PID", str(desk.session.pid))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc-123")

    _run(desk)

    assert desk.spawned == [
        "claude --dangerously-skip-permissions --resume abc-123"]


def test_another_sessions_id_is_not_used(desk, monkeypatch):
    """The variables belong to whichever session spawned this process. If that
    is not the one being relaunched, its id would resume someone else."""
    monkeypatch.setenv("CLAUDE_PID", "999")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "someone-else")

    _run(desk)

    assert "someone-else" not in desk.spawned[0]


@pytest.mark.parametrize("argv, expected", [
    (["claude", "--continue"], ["claude", "--resume", "id"]),
    (["claude", "-c", "--model", "x"], ["claude", "--model", "x", "--resume", "id"]),
    (["claude", "--resume"], ["claude", "--resume", "id"]),
    (["claude", "--resume", "old"], ["claude", "--resume", "id"]),
    (["claude", "-r", "old", "--verbose"], ["claude", "--verbose", "--resume", "id"]),
    (["claude", "--resume=old"], ["claude", "--resume", "id"]),
    (["claude"], ["claude", "--resume", "id"]),
    # A fork is a new conversation, and that is not a restart.
    (["claude", "-c", "--fork-session"], ["claude", "--resume", "id"]),
    # After `--` is the opening prompt; resuming would send it again.
    (["claude", "--verbose", "--", "fix it"], ["claude", "--verbose", "--resume", "id"]),
    (["claude", "--", "-r", "x"], ["claude", "--resume", "id"]),
    # A bare word no option claims is the opening prompt: resuming would send
    # it again, into a session that may already have done it.
    (["claude", "fix the tests"], ["claude", "--resume", "id"]),
    (["claude", "--continue", "fix the tests"], ["claude", "--resume", "id"]),
    (["claude", "--dangerously-skip-permissions", "fix"],
     ["claude", "--dangerously-skip-permissions", "--resume", "id"]),
    (["claude", "--model", "opus", "fix"], ["claude", "--model", "opus", "--resume", "id"]),
    (["claude", "--model=opus", "fix"], ["claude", "--model=opus", "--resume", "id"]),
    # ...but an option's values stay, all of them for a variadic one.
    (["claude", "--add-dir", "a", "b", "--verbose"],
     ["claude", "--add-dir", "a", "b", "--verbose", "--resume", "id"]),
    # An option newer than the table keeps its value rather than lose it.
    (["claude", "--brand-new", "x"], ["claude", "--brand-new", "x", "--resume", "id"]),
    # Unreadable argv: the default launch, still aimed at this conversation.
    ([], ["claude", "--dangerously-skip-permissions", "--resume", "id"]),
])
def test_whatever_resumed_before_is_replaced_by_the_id(argv, expected):
    assert agents_mod.resume_exactly("claude", argv, "id") == expected


def test_a_kind_that_cannot_name_a_session_is_left_alone():
    argv = ["codex", "resume", "--last"]

    assert agents_mod.resume_exactly("codex", argv, "id") == argv


def test_no_id_means_no_rewrite():
    argv = ["claude", "--continue"]

    assert agents_mod.resume_exactly("claude", argv, None) == argv


# ── the helper ───────────────────────────────────────────────────────────


@pytest.fixture
def helper(monkeypatch, tmp_path):
    """bring_back against a controllable world and clock."""
    state = {"session_alive": True, "shell_alive": True, "typed": [],
             "now": 0.0, "loop_takes_marker": False}
    marker = tmp_path / "m.marker"
    marker.write_text("restart", encoding="utf-8")

    def _alive(pid, created):
        return state["session_alive"] if pid == 20 else state["shell_alive"]

    def _sleep(seconds):
        state["now"] += seconds
        # The session ends as soon as anything waits on it; a loop, when
        # there is one, takes the marker a moment later.
        state["session_alive"] = False
        if state["loop_takes_marker"] and state["now"] > 0.3:
            marker.unlink(missing_ok=True)

    monkeypatch.setattr(relaunch_mod, "_alive", _alive)
    monkeypatch.setattr(relaunch_mod, "time", types.SimpleNamespace(
        time=lambda: state["now"], sleep=_sleep))
    monkeypatch.setattr(relaunch_mod, "type_into_console",
                        lambda pid, text: state["typed"].append((pid, text)))
    state["marker"] = marker
    return state


def _bring_back(helper):
    relaunch_mod.bring_back(20, 1.0, 10, 1.0, "claude --resume id",
                            str(helper["marker"]))


def test_a_plain_shell_gets_the_command_written_into_it(helper, capsys):
    _bring_back(helper)

    assert helper["typed"] == [(10, "claude --resume id")]
    assert not helper["marker"].exists(), "left to fire on a later quit"


def test_a_looping_tab_is_left_to_its_loop(helper):
    """The loop takes the marker on its next line. Writing into that console
    as well would put the command into the relaunched session's prompt."""
    helper["loop_takes_marker"] = True

    _bring_back(helper)

    assert helper["typed"] == []


def test_nothing_is_written_while_the_session_is_alive(helper, monkeypatch):
    """The session reads that console. Anything written while it lives goes
    into its prompt, not the shell's."""
    monkeypatch.setattr(relaunch_mod, "_alive", lambda pid, created: True)

    _bring_back(helper)

    assert helper["typed"] == []
    assert not helper["marker"].exists(), "left to fire on a later quit"


def test_the_wait_outlasts_the_marker(helper, monkeypatch):
    """A session that exits late must still find the marker armed. Given up
    on any sooner, it would be ended with nothing to bring it back."""
    exits_at = relaunch_mod.RESTART_MARKER_TTL_SECONDS - 1
    monkeypatch.setattr(relaunch_mod, "_alive", lambda pid, created:
                        pid != 20 or helper["now"] < exits_at)

    _bring_back(helper)

    assert helper["typed"] == [(10, "claude --resume id")]


def test_a_marker_taken_at_the_last_moment_is_the_loops(helper, monkeypatch):
    """The unlink decides, not the check before it. A loop that takes the
    marker in between has relaunched the agent, and a command written now
    would land in that agent's prompt."""
    class _TakenLate:
        def __init__(self, _path):
            pass

        def exists(self):
            return True

        def unlink(self, missing_ok=False):
            if not missing_ok:
                raise FileNotFoundError

    monkeypatch.setattr(relaunch_mod, "pathlib",
                        types.SimpleNamespace(Path=_TakenLate))

    _bring_back(helper)

    assert helper["typed"] == []


def test_a_marker_that_will_not_delete_still_brings_it_back(helper, monkeypatch):
    """Still there means nothing took it, and the session is already gone.
    A locked marker expires on its own; an empty tab would not."""
    class _Locked:
        def __init__(self, _path):
            pass

        def exists(self):
            return True

        def unlink(self, missing_ok=False):
            raise PermissionError("in use")

    monkeypatch.setattr(relaunch_mod, "pathlib",
                        types.SimpleNamespace(Path=_Locked))

    _bring_back(helper)

    assert helper["typed"] == [(10, "claude --resume id")]


class _Gone(Exception):
    pass


@pytest.mark.parametrize("error, alive", [(_Gone, False), (PermissionError, True)])
def test_only_a_missing_process_counts_as_ended(monkeypatch, error, alive):
    """Read as ended, a session that merely could not be inspected would get
    a command written into its prompt. Read as alive, the wait runs out and
    nothing is written."""
    def _process(pid):
        raise error()

    monkeypatch.setattr(discover_mod, "_ps", lambda: types.SimpleNamespace(
        Process=_process, NoSuchProcess=_Gone))

    assert relaunch_mod._alive(20, 1.0) is alive


def test_nothing_is_written_to_a_shell_that_is_gone(helper):
    helper["shell_alive"] = False

    _bring_back(helper)

    assert helper["typed"] == []


# ── what is written ──────────────────────────────────────────────────────


def test_each_character_is_a_key_press_and_release_ending_in_enter(monkeypatch):
    """Written into the input buffer as key events, which PowerShell's line
    editor reads as typing. Verified against a real Windows Terminal tab; this
    pins the encoding that was verified."""
    captured = {}

    def _write(handle, buf, count, written):
        captured["keys"] = [(buf[i].Event.KeyEvent.uChar,
                             buf[i].Event.KeyEvent.bKeyDown,
                             buf[i].Event.KeyEvent.wVirtualKeyCode,
                             buf[i].Event.KeyEvent.dwControlKeyState)
                            for i in range(count)]
        return 1

    k32 = types.SimpleNamespace(
        FreeConsole=lambda: 1, AttachConsole=lambda pid: 1,
        CreateFileW=lambda *a: 5, CloseHandle=lambda h: 1,
        WriteConsoleInputW=_write)
    monkeypatch.setattr(relaunch_mod, "_k32", k32)

    relaunch_mod.type_into_console(10, "aB")

    keys = captured["keys"]
    assert [(c, down) for c, down, _vk, _state in keys] == [
        ("a", True), ("a", False), ("B", True), ("B", False),
        ("\r", True), ("\r", False)]
    assert keys[-1][2] == 0x0D, "Enter is what makes the shell run it"
    # VkKeyScanW takes a WCHAR. Handed a string, it answered -1 for every
    # letter: key 255 with Shift held.
    assert (keys[0][2], keys[0][3]) == (ord("A"), 0)
    assert (keys[2][2], keys[2][3]) == (ord("B"), 0x0010)
