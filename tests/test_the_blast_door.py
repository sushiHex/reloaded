"""The guard in conftest, tested rather than assumed.

A safety net nobody has ever seen catch anything is indistinguishable from a
safety net with a hole in it. These tests fall into it on purpose.

The history: a commit renamed send_exit_keystrokes to send_quit_keystrokes
while several tests still stubbed the old name. Those tests called the real
sender, which types into whatever window has focus. Six live sessions died in
31 seconds. The lesson was not "be careful with renames" - it was that a guard
attached to a NAME can be renamed out from under itself, so the guard has to
sit on what cannot be renamed: the module every keystroke goes through, and the
syscalls every window change goes through.
"""
from __future__ import annotations

import sys

import pytest

import reloaded.win32 as win32_mod


def test_nothing_usable_comes_out_of_the_poisoned_module():
    """The guarantee, stated so it holds on every Python this package supports.

    An earlier version asserted that `import uiautomation` itself raises. It
    does on 3.14, where the import machinery probes `__spec__` on a non-module
    entry in sys.modules. It does not on 3.12 - which CI runs, and which caught
    it. That was an assertion about CPython's internals wearing the costume of
    a safety guarantee.

    Either behaviour is safe, and neither is the point. The point is that
    production reaching for this module gets nothing it can type with, whether
    the refusal arrives at the import or at the first attribute.
    """
    poison = sys.modules["uiautomation"]

    for attribute in ("SendKeys", "GetRootControl", "ControlFromHandle"):
        with pytest.raises(AssertionError, match="types into"):
            getattr(poison, attribute)


def test_any_uiautomation_attribute_is_refused():
    """Poisoned at the module, not at a list of known-dangerous functions. A
    name this package does not use today is still refused tomorrow."""
    auto = sys.modules["uiautomation"]

    with pytest.raises(AssertionError, match="types into"):
        auto.SendKeys("/exit")
    with pytest.raises(AssertionError):
        auto.GetRootControl()


def test_closing_a_real_terminal_window_is_refused(monkeypatch):
    """close_window takes a raw HWND and posts WM_CLOSE to it. Its own class
    check stops a bogus number, but not a real terminal window with real
    sessions in it - which is the hwnd a test would get wrong."""
    monkeypatch.setattr(win32_mod, "_class_name",
                        lambda hwnd: win32_mod.WT_CLASS)

    with pytest.raises(AssertionError, match="closes a real window"):
        win32_mod.close_window(12345)


def test_moving_a_window_is_refused():
    with pytest.raises(AssertionError, match="moves and resizes"):
        win32_mod._u32.SetWindowPlacement(12345, None)


def test_showing_a_window_is_refused():
    with pytest.raises(AssertionError, match="minimizes"):
        win32_mod._u32.ShowWindow(12345, 1)


def test_taking_the_foreground_is_refused():
    with pytest.raises(AssertionError, match="focus"):
        win32_mod.set_foreground(12345)


def test_taking_the_foreground_underneath_the_wrapper_is_refused():
    """The wrapper is blocked by name for a clearer message. The syscall is
    blocked too, so renaming the wrapper does not reopen the hole - which is
    exactly how the hole opened last time."""
    with pytest.raises(AssertionError, match="SetForegroundWindow"):
        win32_mod._u32.SetForegroundWindow(12345)


def test_a_test_that_needs_one_can_still_have_it(monkeypatch):
    """The guard is a default, not a wall. monkeypatch inside a test runs
    after the autouse fixture, so a deliberate fake wins - which is what the
    ~40 tests exercising these paths already do."""
    posted = []
    monkeypatch.setattr(win32_mod, "_class_name",
                        lambda hwnd: win32_mod.WT_CLASS)
    monkeypatch.setattr(win32_mod._u32, "PostMessageW",
                        lambda hwnd, msg, w, l: posted.append(int(hwnd.value)) or True)
    # close_window now confirms the window actually went, so the fake has to
    # model a window that goes. Reporting success on a queued message is what
    # it used to do, and what it printed about windows still on screen.
    monkeypatch.setattr(win32_mod, "is_wt_window", lambda hwnd: not posted)

    assert win32_mod.close_window(12345) is True
    assert posted == [12345]


def test_launching_a_terminal_is_refused():
    """This one got out. A test whose wait loop fell through to the reopen
    fallback called Popen on `wt` for real, once per suite run, and opened
    terminal tabs on the user's desktop trying to start a session in a
    fixture's imaginary directory. The user noticed before the suite did."""
    import subprocess

    with pytest.raises(AssertionError, match="opens a window"):
        subprocess.Popen(["wt", "-w", "0", "new-tab"])


def test_launching_an_agent_is_refused():
    import subprocess

    with pytest.raises(AssertionError, match="starts an agent"):
        subprocess.Popen([r"C:\Users\k\bin\claude.exe", "--continue"])


def test_a_full_path_to_a_terminal_is_still_caught():
    """Blocked on the basename, so an absolute path does not slip past."""
    import subprocess

    with pytest.raises(AssertionError):
        subprocess.Popen([r"C:\Program Files\WindowsApps\wt.exe", "-w", "0"])


def test_powershell_is_still_allowed():
    """The suite deliberately runs real PowerShell to exercise the launcher
    loop and the relaunch script. Blocking spawning outright would cost more
    than it saves; what nothing may do is open a window or start an agent."""
    import subprocess

    proc = subprocess.Popen(
        ["cmd.exe", "/c", "exit 0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert proc.wait(timeout=30) == 0


def test_reading_a_window_is_not_blocked():
    """Only the four calls that change something are refused. A guard that
    also blocked reads would put ceremony on tests that were never dangerous,
    and a guard that cries wolf stops being read."""
    assert win32_mod.is_wt_window(12345) is False
