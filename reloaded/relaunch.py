"""Restart the agent session this process is running inside: `/relaunch`.

Every other restart drives a session from outside - find its tab, type the
quit keys, wait. Turned on yourself that fails twice over, and both were
measured. Typing needs keyboard focus, which Windows refuses a background
process the moment the user looks at another window. And the session being
typed at is busy, because it is running this very command: an
auto-compaction swallowed a `/exit` whole.

So this does not ask the session to quit. It ends it, by pid. The pid is
exact - `discover.owning_session` walks up from this process rather than
guessing from a directory or a tab title - and at this instant the session is
waiting on this command rather than working. Its transcript is not flushed
first; the live runs resumed intact, which is evidence, not a guarantee.

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
from .paths import log_path, restart_marker

# The shells a resume command can be written into. The command is quoted for
# PowerShell (discover._format_command), so nothing else would parse it.
SHELLS = ("pwsh.exe", "powershell.exe")

# How long the helper waits for the session to be gone before giving up: as
# long as the marker can still deliver, so that a late exit is never met by a
# disarmed tab. Past it a loop ignores the marker anyway, and removing it
# costs nothing.
EXIT_WAIT_SECONDS = float(RESTART_MARKER_TTL_SECONDS)

# How long a looping tab gets to take the marker before the helper concludes
# there is no loop. The loop takes it on the line straight after the agent
# returns, so this is generous.
LOOP_GRACE_SECONDS = 2.0

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)


def relaunch(pid: int, cwd: str, kind: str, *, dry_run: bool = False,
             say=print) -> int:
    """End session `pid` and have its tab bring it back. 0 on hand-off."""
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
        say(f"Would end {cwd} (pid {pid}, {kind}) and bring it back in the "
            f"same tab{'' if looping else f' as: {command}'}.")
        return 0

    marker = restart_marker(cwd)
    marker.write_text("restart", encoding="utf-8")
    try:
        _spawn_helper(session, shell, command, marker)
    except Exception as exc:
        # Nothing has been ended, so nothing is lost. A marker left behind
        # would fire on the user's next deliberate quit instead.
        marker.unlink(missing_ok=True)
        say(f"Could not start the helper that brings {cwd} back: {exc}")
        say("    Nothing was ended.")
        return 1

    say(f"Restarting {cwd}.")
    try:
        _end(session)
    except Exception as exc:
        # The helper times out on its own and types nothing; the marker has
        # to go now, or it would fire on the user's next deliberate quit.
        marker.unlink(missing_ok=True)
        say(f"Could not end session {pid}: {exc}")
        return 1
    return 0


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
    return (discover_mod._format_command(argv)
            or agents_mod.for_kind(kind).launch)


def _spawn_helper(session, shell, command: str, marker) -> None:
    """Start `bring_back` in a process that outlives the session.

    DETACHED_PROCESS with CREATE_BREAKAWAY_FROM_JOB, so that a job this
    process may be in cannot take the helper down with the session. There is
    no fallback without it: CreateProcess refuses breakaway only when this
    process is in a job that forbids it, which is exactly when a helper
    started without it could die with the session. Refusing then ends nothing.

    Bootstrapped through sys.path rather than the `reloaded` shim, which may
    not be on the PATH this process hands down.
    """
    package_dir = str(pathlib.Path(__file__).resolve().parents[1])
    call = (session.pid, session.create_time(), shell.pid, shell.create_time(),
            command, str(marker))
    bootstrap = (f"import sys; sys.path.insert(0, {package_dir!r}); "
                 f"from reloaded.relaunch import bring_back; "
                 f"bring_back(*{call!r})")
    base = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        account = open(log_path(), "a", buffering=1, encoding="utf-8")
    except OSError:
        account = subprocess.DEVNULL
    kw = dict(stdin=subprocess.DEVNULL, stdout=account, stderr=account,
              close_fds=True)
    argv = [sys.executable, "-u", "-c", bootstrap]
    try:
        subprocess.Popen(argv, creationflags=base | 0x01000000, **kw)
    finally:
        if account is not subprocess.DEVNULL:
            account.close()


def _end(session) -> None:
    """End the session and everything it started, except this process's own
    line and whatever hangs off it - the helper among them.

    Its children too, because a session ended by pid does not get to shut down
    its MCP servers the way `/exit` would, and they would outlive it. Children
    first, so that if the session's job takes this process down with it, what
    is left is nothing rather than orphans.

    Spared by ancestry, not by pid: from a venv, `python.exe` is a redirector
    that runs the real interpreter as its child, so the pid Popen returns is
    not the process doing the work. Sparing only that pid killed the helper
    on every live run.
    """
    ps = discover_mod._ps()
    ours = set()
    p = ps.Process(os.getpid())
    while p is not None and p.pid != session.pid:
        ours.add(p.pid)
        p = p.parent()
    if p is None:
        # Without the whole line there is no telling what is safe to end.
        raise RuntimeError("this process is not running inside it")
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
               shell_created: float, command: str, marker: str) -> None:
    """The helper: once the session is gone, see it back into its tab.

    Runs detached, so it prints to the log rather than to anyone.
    """
    marker_path = pathlib.Path(marker)

    def note(line: str) -> None:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{stamp}  [relaunch] {line}", flush=True)

    deadline = time.time() + EXIT_WAIT_SECONDS
    while _alive(session_pid, session_created):
        if time.time() > deadline:
            marker_path.unlink(missing_ok=True)
            note(f"pid {session_pid} did not end - nothing written to its tab")
            return
        time.sleep(0.1)

    # A loop takes the marker on the line after its agent returns.
    deadline = time.time() + LOOP_GRACE_SECONDS
    while marker_path.exists() and time.time() < deadline:
        time.sleep(0.1)
    # The unlink is the decision, not the check before it: a loop that takes
    # the marker in between must not also get a command typed at its agent.
    try:
        marker_path.unlink()
    except FileNotFoundError:
        note(f"pid {session_pid} ended; its tab's loop is bringing it back")
        return
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
    records = []
    for ch in text + "\r":
        if ch == "\r":
            vk, shift = 0x0D, False
        else:
            scan = win32_mod._u32.VkKeyScanW(ch)
            vk, shift = scan & 0xFF, bool((scan >> 8) & 1)
        for down in (True, False):
            rec = _INPUT_RECORD()
            rec.EventType = 0x0001  # KEY_EVENT
            key = rec.Event.KeyEvent
            key.bKeyDown = down
            key.wRepeatCount = 1
            key.wVirtualKeyCode = vk
            key.uChar = ch
            key.dwControlKeyState = 0x0010 if shift else 0  # SHIFT_PRESSED
            records.append(rec)

    _k32.FreeConsole()
    if not _k32.AttachConsole(shell_pid):
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
