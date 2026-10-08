"""Restart the agent session this process is running inside: `/relaunch`.

Every other restart drives a session from outside - find its tab, send the
quit keys through UI Automation, wait. Turned on yourself that fails, and it
was measured: the keys need keyboard focus, which Windows refuses a
background process the moment the user looks at another window.

So a detached helper writes the quit keys straight into the session's console
input buffer instead, which needs no focus and cannot land in another window.
Claude Code takes `/exit` even while it is running this command, and this
command waits for it: the session quits before asking the model anything,
the way a user's `/exit` would, and cleans up after itself.

This module first ended the session by pid instead. That was changed after
relaunched sessions came back drawn wrongly - lines stacked and overlaid,
until the window was resized - but the tabs it happened in turned out to be
ones a logon restore had created at 120x30 in the background (see
deploy.wt_argvs), so the kill was probably not the cause. A quit is still the
better way down: the session shuts its MCP servers and its transcript itself.
Ending by pid remains the fallback, for a session that does not quit in time.

The pid is exact either way - `discover.owning_session` walks up from this
process rather than guessing from a directory or a tab title.

Its tab then brings it back, and one detached helper covers both kinds of tab:

- A reloaded tab runs its agent in a loop that relaunches on a fresh restart
  marker. The marker is armed before the session ends, and the loop consumes
  it the moment the agent exits.
- A tab started by hand is a plain shell, which just drops to its prompt. The
  marker is still there, so the helper knows nothing took it, removes it, and
  writes the resume command into that shell's console input buffer. Written,
  not typed: it needs no focus and cannot land in any other window.

Telling the two apart by what happened to the marker, rather than by reading
the shell's command line, is also right for a hand tab that an earlier
`restart <repo>` upgraded to the loop without its command line showing it.

A loop launched before markers carried a command reads only that one exists,
and replays the command its tab was launched with - a bare `--resume` came
back as Claude Code's picker. No file can steer it, but it keeps its tab when
its agent dies on startup (deploy.CLOSE_TAB_IF_STARTED). So when that replay
brings back no conversation at all, the helper ends it as soon as it appears,
and the shell, left at its prompt, is brought back like a plain one. A replay
that resumes one is not killed while it loads it.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import datetime
import os
import pathlib
import subprocess
import sys
import time

from . import agents as agents_mod
from . import discover as discover_mod
from . import win32 as win32_mod
from .deploy import RESTART_MARKER_TTL_SECONDS
from .paths import log_path, restart_marker, shell_marker

# The shells a resume command can be written into. The command is quoted for
# PowerShell (discover._format_command), so nothing else would parse it.
SHELLS = ("pwsh.exe", "powershell.exe")

# How long this command waits for its session to quit before returning. The
# session takes `/exit` while it is still running this command, so it quits
# before asking the model anything - over a large conversation that request
# is the expensive part. Returning sooner only costs that request.
SELF_WAIT_SECONDS = 30.0

# How long the helper waits to be out of the session's process tree before it
# sends the quit keys anyway. Its intermediate exits straight after starting it.
CALLER_WAIT_SECONDS = 10.0

# How long the session gets to quit on the keys before it is ended by pid.
# Well inside the marker's life, and the marker is refreshed before the kill.
QUIT_WAIT_SECONDS = 60.0

# Pause between quit keys. Written in one burst, Claude Code reads a line and
# its Enter as a paste, and the Enter becomes a newline in the prompt.
KEY_PAUSE_SECONDS = 0.5

# How long the helper waits for a killed session to be gone before giving up:
# as long as the marker can still deliver, so that a late exit is never met by
# a disarmed tab. Past it a loop ignores the marker anyway.
EXIT_WAIT_SECONDS = float(RESTART_MARKER_TTL_SECONDS)

# How long a looping tab gets to take the marker before the helper concludes
# there is no loop. The loop takes it on the line straight after the agent
# returns, so this is generous.
LOOP_GRACE_SECONDS = 2.0

# How long an old loop's replay gets to appear, and then to be gone once
# ended. Together well inside deploy.STARTUP_GRACE_SECONDS, which is how long
# the loop keeps its tab for an agent that dies on startup.
REPLAY_WAIT_SECONDS = 3.0

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)


def relaunch(pid: int, cwd: str, kind: str, *, dry_run: bool = False,
             say=print) -> int:
    """Have session `pid` quit and its tab bring it back. 0 on hand-off."""
    ps = discover_mod._ps()
    try:
        session = ps.Process(pid)
        shell = session.parent()
        shell_name = shell.name().lower()
    except Exception as exc:
        say(f"Cannot read session {pid} or the shell it runs in: {exc}")
        return 1

    # Checked before anything is ended: a loop tab needs no shell of ours, but
    # a plain one has to be something the resume command can be written into.
    # Ending a session under, say, cmd.exe would leave it at a prompt that
    # cannot parse what the helper writes.
    looping = discover_mod.launcher_kind(pid) == discover_mod.RELOADED
    if not looping and shell_name not in SHELLS:
        say(f"{cwd} runs directly under {shell.name()}, not a PowerShell "
            "prompt, so there is nothing to bring it back into.")
        say("    Quit it and start it again yourself.")
        return 1

    command = _resume_command(session, kind)
    if dry_run:
        say(f"Would quit {cwd} (pid {pid}, {kind}) and bring it back in the "
            f"same tab as: {command}")
        return 0

    # Addressed to this tab's shell where that shell can read it: a plain one
    # (only the helper looks), or a loop launched since shells had markers of
    # their own. The directory's marker is shared with every tab there, so a
    # sibling exiting first would take this session's command. Only a loop
    # older than that still needs the directory's, the one file it watches.
    try:
        own = not looping or "$rlOwn=" in " ".join(shell.cmdline())
    except Exception:
        own = not looping
    marker = shell_marker(shell.pid) if own else restart_marker(cwd)
    # The command, not just a flag: a loop runs it in place of the one its tab
    # was launched with (deploy.restart_loop), so a looping tab comes back to
    # this conversation too. A loop older than that reads only that it exists
    # and replays its launch command - this session's own. When that brings
    # back no conversation at all - the picker, or an empty one, what
    # `agents.resuming` exists to fix - the helper stops it (see bring_back).
    # One that resumes something is left to: it has a conversation loading.
    replay = _launch_command(session)
    stale = looping and not own and agents_mod.resuming(kind, replay) != replay
    marker.write_text(command, encoding="utf-8")
    try:
        _spawn_helper(session, shell, kind, command, marker, stale)
    except Exception as exc:
        # Nothing has been touched, so nothing is lost. A marker left behind
        # would fire on the user's next deliberate quit instead.
        say(f"Could not start the helper that brings {cwd} back: {exc}")
        say("    Nothing was changed.")
        _disarm(marker, say)
        return 1

    say(f"Restarting {cwd}: this session quits in a moment and comes back in "
        "the same tab.")
    if not _wait_until_gone(session.pid, session.create_time(), SELF_WAIT_SECONDS):
        # Read only by a session that is still here: the model's turn.
        say(f"    It has not quit after {SELF_WAIT_SECONDS:.0f}s. The helper "
            f"ends it within a minute, or calls the restart off; either way "
            f"it says which in {log_path()}.")
    return 0


def _disarm(marker, say) -> None:
    """Remove the marker, or say it is still armed and for how long."""
    try:
        marker.unlink(missing_ok=True)
    except OSError as exc:
        say(f"    The restart marker could not be removed ({exc}): quitting "
            f"within {RESTART_MARKER_TTL_SECONDS}s would relaunch the session.")


def _resume_command(session, kind: str) -> str:
    """What to write into a plain shell to bring this exact session back.

    Claude Code hands its children its own pid and session id. When those are
    this session's, the relaunch names the conversation outright: `--resume`
    with no id opens a picker, and plain `claude` starts an empty one - two of
    the hand-started sessions on the machine this was written on were running
    the first.
    """
    try:
        argv = session.cmdline()
    except Exception:
        argv = []
    session_id = None
    if os.environ.get("CLAUDE_PID") == str(session.pid):
        session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    argv = agents_mod.resume_exactly(kind, argv, session_id)
    # Without the session's id that leaves its argv as it was - possibly a
    # bare `--resume`, no resume at all, or an opening prompt - so it goes
    # through the same rewrite as every launch. A named `--resume <id>` is
    # left as it is.
    return agents_mod.resuming(kind, discover_mod._format_command(argv)
                               or agents_mod.for_kind(kind).launch)


def _launch_command(session) -> str:
    """The command `session` was started with, formatted as `_resume_command`
    formats - what an old loop replays. "" when unreadable, which leaves the
    loop to it, as before."""
    try:
        return discover_mod._format_command(session.cmdline())
    except Exception:
        return ""


def _spawn_helper(session, shell, kind: str, command: str, marker,
                  stale: bool = False) -> None:
    """Start `bring_back` in a process that outlives the session.

    CREATE_BREAKAWAY_FROM_JOB, so that a job this process may be in cannot
    take the helper down with the session. There is no fallback without it:
    CreateProcess refuses breakaway only when this process is in a job that
    forbids it, which is exactly when a helper started without it could die
    with the session. Refusing then ends nothing.

    CREATE_NO_WINDOW, not DETACHED_PROCESS. From a venv, python.exe is a
    redirector whose child - a console program - does the work; detached, that
    child found no console to inherit, was given a new one, and with Windows
    Terminal as the default terminal that opened as a blank window on every
    `/relaunch`. A windowless console is inherited instead. Measured.

    Started through an intermediate that exits at once, so that the helper is
    nobody's descendant by the time the session quits: Claude Code ends the
    process tree of the command it is running when it exits, and the helper
    started directly hung off this process, which is still waiting in that
    command. Measured: it died with the session and logged nothing.

    Bootstrapped through sys.path rather than the `reloaded` shim, which may
    not be on the PATH this process hands down.
    """
    call = (session.pid, session.create_time(), shell.pid, shell.create_time(),
            kind, command, str(marker), stale)
    intermediate = _detached("_start_helper", call)
    try:
        code = intermediate.wait(timeout=30)
    except subprocess.TimeoutExpired:
        intermediate.kill()
        intermediate.wait(timeout=10)  # gone before the marker is disarmed
        raise OSError("the helper's launcher did not finish starting it")
    if code != 0:
        raise OSError(f"the helper's launcher exited {code}")


def _start_helper(*call) -> None:
    """Runs in the intermediate: start the helper, and exit."""
    _detached("bring_back", call)


def _detached(function: str, call: tuple):
    """Run `function(*call)` from this module in a new process: windowless,
    its own process group, out of any job, logging to the log file."""
    package_dir = str(pathlib.Path(__file__).resolve().parents[1])
    bootstrap = (f"import sys; sys.path.insert(0, {package_dir!r}); "
                 f"from reloaded.relaunch import {function}; "
                 f"{function}(*{call!r})")
    flags = 0x08000000 | 0x00000200 | 0x01000000  # NO_WINDOW|NEW_GROUP|BREAKAWAY
    try:
        account = open(log_path(), "a", buffering=1, encoding="utf-8")
    except OSError:
        account = subprocess.DEVNULL
    try:
        return subprocess.Popen([sys.executable, "-u", "-c", bootstrap],
                                creationflags=flags, stdin=subprocess.DEVNULL,
                                stdout=account, stderr=account, close_fds=True)
    finally:
        if account is not subprocess.DEVNULL:
            account.close()


def _end(session) -> None:
    """The fallback: end the session and everything it started, except this
    process's own line and whatever hangs off it.

    Its children too, because a session ended by pid does not get to shut down
    its MCP servers the way `/exit` would, and they would outlive it.

    Spared by ancestry, not by pid: from a venv, `python.exe` is a redirector
    that runs the real interpreter as its child, so the pid Popen returns is
    not the process doing the work. Sparing only that pid once killed the
    helper on every live run.
    """
    ps = discover_mod._ps()
    ours = set()
    p = ps.Process(os.getpid())
    while p is not None and p.pid != session.pid:
        ours.add(p.pid)
        p = p.parent()
    try:
        children = session.children(recursive=True)
    except Exception:
        children = []
    for child in children:
        try:
            if not ({child.pid} | {a.pid for a in child.parents()}) & ours:
                child.terminate()
        except Exception:
            pass
    session.terminate()


def bring_back(session_pid: int, session_created: float, shell_pid: int,
               shell_created: float, kind: str, command: str,
               marker: str, stale: bool = False) -> None:
    """The helper: have the session quit, then see it back into its tab.

    `stale`: the tab's loop is older than marker commands and will replay a
    command that brings back no conversation.

    Runs detached, so it prints to the log rather than to anyone.
    """
    marker_path = pathlib.Path(marker)

    def note(line: str) -> None:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{stamp}  [relaunch] {line}", flush=True)

    # Not before this process is out of the session's tree: the quit ends the
    # tree, and the intermediate that started this may not have exited yet.
    if not _wait_until_outside(session_pid, CALLER_WAIT_SECONDS):
        # Quit now and the session's cleanup could end this too, before it
        # brings a plain tab back. Calling it off loses nothing.
        marker_path.unlink(missing_ok=True)
        note(f"still inside pid {session_pid}'s process tree after "
             f"{CALLER_WAIT_SECONDS:.0f}s - restart called off, left running")
        return
    # The marker is the go-ahead. `relaunch` removes it when it gives up on a
    # helper that was slow to start - one that may yet start, like this - and
    # a session quit with no marker would not come back.
    if not marker_path.exists():
        note(f"pid {session_pid}: the restart was called off - left running")
        return
    try:
        send_keys(session_pid, agents_mod.for_kind(kind).quit_keys)
    except OSError as exc:
        note(f"could not write the quit keys to pid {session_pid}: {exc}")

    if not _wait_until_gone(session_pid, session_created, QUIT_WAIT_SECONDS):
        note(f"pid {session_pid} did not quit in {QUIT_WAIT_SECONDS:.0f}s - "
             "ending it")
        # Fresh, so that the loop still takes it however long the wait was.
        marker_path.write_text(command, encoding="utf-8")
        try:
            _end(discover_mod._ps().Process(session_pid))
        except Exception as exc:
            note(f"could not end pid {session_pid}: {exc}")
        if not _wait_until_gone(session_pid, session_created, EXIT_WAIT_SECONDS):
            marker_path.unlink(missing_ok=True)
            note(f"pid {session_pid} did not end - nothing written to its tab")
            return

    # A loop takes the marker on the line after its agent returns.
    deadline = time.time() + LOOP_GRACE_SECONDS
    while marker_path.exists() and time.time() < deadline:
        time.sleep(0.1)
    # The unlink is the decision, not the check before it: a loop that takes
    # the marker in between must not also get a command typed at its agent.
    try:
        marker_path.unlink()
    except FileNotFoundError:
        if not stale:
            note(f"pid {session_pid} ended; its tab's loop is bringing it back")
            return
        # Ended on startup, the replay leaves the loop at its prompt: no
        # marker, so it breaks, and too young for it to close the tab.
        if not _stop_replay(shell_pid, shell_created, kind):
            note(f"pid {session_pid} ended; its tab's loop predates exact "
                 "relaunch, and its replay could not be stopped in time")
            return
        note(f"pid {session_pid} ended; stopped its tab's old loop replaying "
             "how the tab was launched")
    except OSError as exc:
        # Still there, so nothing took it: bring the session back anyway. The
        # marker expires on its own; the tab would not.
        note(f"could not remove the restart marker ({exc}); it expires in 2 min")

    # Nobody took it, so this is a plain shell sitting at its prompt.
    if not _alive(shell_pid, shell_created):
        note(f"pid {session_pid} ended, but its shell (pid {shell_pid}) went "
             "with it - reopen it by hand")
        return
    try:
        type_into_console(shell_pid, command)
    except OSError as exc:
        note(f"could not write to shell pid {shell_pid}: {exc}")
        return
    note(f"pid {session_pid} ended; started `{command}` in its tab")


def _stop_replay(shell_pid: int, shell_created: float, kind: str) -> bool:
    """End the agent an old loop has just relaunched in `shell_pid`, and
    whatever it started. Whether it is gone.

    The shell's only agent child is the replay: the loop runs nothing else,
    and the session it replaced is already gone.
    """
    ps = discover_mod._ps()
    image = agents_mod.for_kind(kind).process
    deadline = time.time() + REPLAY_WAIT_SECONDS
    while _alive(shell_pid, shell_created):
        try:
            replay = next((c for c in ps.Process(shell_pid).children()
                           if c.name().lower() == image), None)
        except Exception:
            replay = None
        if replay is not None:
            created = replay.create_time()
            _end(replay)
            return _wait_until_gone(replay.pid, created, REPLAY_WAIT_SECONDS)
        if time.time() > deadline:
            return False
        time.sleep(0.1)
    return False


def _wait_until_outside(session_pid: int, seconds: float) -> bool:
    """Whether, within `seconds`, no ancestor of this process is the session."""
    ps = discover_mod._ps()
    deadline = time.time() + seconds
    while True:
        try:
            line = {p.pid for p in ps.Process(os.getpid()).parents()}
        except Exception:
            line = set()
        if session_pid not in line:
            return True
        if time.time() > deadline:
            return False
        time.sleep(0.1)


def _wait_until_gone(pid: int, created: float, seconds: float) -> bool:
    """Whether `pid` is gone within `seconds`."""
    deadline = time.time() + seconds
    while _alive(pid, created):
        if time.time() > deadline:
            return False
        time.sleep(0.1)
    return True


def _alive(pid: int, created: float) -> bool:
    """Whether `pid` is still the process it was - pids are reused.

    Only a missing process counts as gone. Any other failure to look reads as
    alive, which at worst runs out the wait and types nothing.
    """
    ps = discover_mod._ps()
    try:
        return abs(ps.Process(pid).create_time() - created) < 1
    except ps.NoSuchProcess:
        return False
    except Exception:
        return True


class _KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", w.BOOL), ("wRepeatCount", w.WORD),
                ("wVirtualKeyCode", w.WORD), ("wVirtualScanCode", w.WORD),
                ("uChar", w.WCHAR), ("dwControlKeyState", w.DWORD)]


class _EVENT(ctypes.Union):
    _fields_ = [("KeyEvent", _KEY_EVENT_RECORD), ("_pad", ctypes.c_byte * 16)]


class _INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", w.WORD), ("Event", _EVENT)]


_k32.CreateFileW.restype = w.HANDLE
_k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD,
                             w.DWORD, w.HANDLE]
_k32.WriteConsoleInputW.argtypes = [w.HANDLE, ctypes.POINTER(_INPUT_RECORD),
                                    w.DWORD, ctypes.POINTER(w.DWORD)]
# Takes one WCHAR, not a string: undeclared, ctypes passes a pointer and every
# letter comes back -1. Returns a SHORT: virtual key in the low byte, shift
# state in the high.
win32_mod._u32.VkKeyScanW.argtypes = [ctypes.c_wchar]
win32_mod._u32.VkKeyScanW.restype = ctypes.c_short


def type_into_console(shell_pid: int, text: str) -> None:
    """Put `text` and Enter into the input buffer of `shell_pid`'s console.

    Verified against a real Windows Terminal tab: the shell ran it, and no
    window took focus. It queues behind anything already in the buffer, which
    is why it is only ever called once the session reading that buffer is gone.
    """
    _write_input(shell_pid, _key_records(text) + _key_records("{Enter}"))


def send_keys(pid: int, keys) -> None:
    """Write an agent's quit keys (`agents.Agent.quit_keys`) into `pid`'s
    console input, one at a time - see KEY_PAUSE_SECONDS."""
    for i, key in enumerate(keys):
        if i:
            time.sleep(KEY_PAUSE_SECONDS)
        _write_input(pid, _key_records(key))


def _key_records(key: str) -> list:
    """Key-down and key-up records for `key`: text, `{Enter}`, or `{Ctrl}x` -
    the notation `agents.Agent.quit_keys` is written in."""
    if key == "{Enter}":
        presses = [("\r", 0x0D, 0)]
    elif key.startswith("{Ctrl}"):
        letter = key[len("{Ctrl}"):].upper()
        # The control character itself, as a keyboard sends it.
        presses = [(chr(ord(letter) - 0x40), ord(letter), 0x0008)]  # LEFT_CTRL
    else:
        presses = []
        for ch in key:
            scan = win32_mod._u32.VkKeyScanW(ch)
            shift = 0x0010 if (scan >> 8) & 1 else 0  # SHIFT_PRESSED
            presses.append((ch, scan & 0xFF, shift))
    records = []
    for ch, vk, state in presses:
        for down in (True, False):
            rec = _INPUT_RECORD()
            rec.EventType = 0x0001  # KEY_EVENT
            event = rec.Event.KeyEvent
            event.bKeyDown = down
            event.wRepeatCount = 1
            event.wVirtualKeyCode = vk
            event.uChar = ch
            event.dwControlKeyState = state
            records.append(rec)
    return records


def _write_input(pid: int, records: list) -> None:
    """Write `records` into the input buffer of `pid`'s console."""
    _k32.FreeConsole()
    if not _k32.AttachConsole(pid):
        raise OSError(f"AttachConsole failed ({ctypes.get_last_error()})")
    try:
        handle = _k32.CreateFileW("CONIN$", 0xC0000000, 0x3, None, 3, 0, None)
        if handle in (None, w.HANDLE(-1).value):
            raise OSError(f"CONIN$ could not be opened ({ctypes.get_last_error()})")
        try:
            buf = (_INPUT_RECORD * len(records))(*records)
            written = w.DWORD()
            if not _k32.WriteConsoleInputW(handle, buf, len(records),
                                           ctypes.byref(written)):
                raise OSError(f"WriteConsoleInputW failed "
                              f"({ctypes.get_last_error()})")
        finally:
            _k32.CloseHandle(handle)
    finally:
        _k32.FreeConsole()
