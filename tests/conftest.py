"""Shared test setup and model factories.

Also the blast door. This package drives a real desktop - it synthesizes
keystrokes into whatever window has focus, and closes windows and tabs. A test
that reaches those functions for real does not fail; it types into the user's
live sessions.

That is not hypothetical. A commit renamed send_exit_keystrokes to
send_quit_keystrokes while several tests still stubbed the old name, so those
tests called the REAL sender. It typed `/exit` + Enter into whatever was
focused, over and over, and killed six live Claude Code sessions inside 31
seconds - the transcripts all stop between 15:04:06 and 15:04:37. The only
symptom at the time was the suite getting slower, which was written off as the
real function's sleeps.

Every entry point that touches the desktop is therefore blocked at the module
boundary for the whole suite. A test that needs one must stub it deliberately,
and a test that forgets gets a loud error instead of a wrecked desktop.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from reloaded.layout import LAYOUT_VERSION, Layout, Monitor, Window  # noqa: E402

class _PoisonedUIAutomation:
    """Stands in for the real uiautomation package during tests.

    Guarding at the import rather than at each wrapper is what makes this hold:
    a function can be renamed out from under a stub - which is exactly what
    happened - but everything that reaches the desktop reaches it through this
    module, and it cannot be renamed away. A test that genuinely exercises the
    keystroke path substitutes its own fake into sys.modules, which takes
    precedence over this.
    """

    def __getattr__(self, name):
        raise AssertionError(
            f"uiautomation.{name} was called for real during a test. "
            "SendKeys goes to whatever window has focus, so this types into "
            "the user's live sessions. Substitute a fake uiautomation module "
            "with monkeypatch.setitem(sys.modules, 'uiautomation', fake)."
        )


@pytest.fixture(autouse=True)
def _no_real_desktop_effects(monkeypatch):
    """Keep the suite off the real desktop. autouse, so it cannot be forgotten.

    Two things are blocked, for two different reasons:

    `uiautomation` - everything that synthesizes input goes through it, and
    SendKeys targets whatever has focus rather than anything the caller named.
    No fake argument can make that safe.

    `win32.set_foreground` - moves the real focus via ctypes, without going
    through uiautomation at all.

    Not blocked: select_tab, close_tab, close_window. Those act on the UIA
    element or hwnd they are handed, so a test passing a fake acts on the fake.
    Blocking them would put ceremony on ~40 tests that were never dangerous,
    and a guard that cries wolf stops being read.
    """
    monkeypatch.setitem(sys.modules, "uiautomation", _PoisonedUIAutomation())

    import reloaded.win32 as win32_mod

    def _blocked_foreground(hwnd):
        raise AssertionError(
            "win32.set_foreground() was called for real during a test. It moves "
            "the desktop's focus, which is what makes a stray SendKeys land on "
            "a live session. Stub it."
        )

    monkeypatch.setattr(win32_mod, "set_foreground", _blocked_foreground)

PRIMARY = r"\\.\DISPLAY1"
SECONDARY = r"\\.\DISPLAY2"

# One 2560x1392 primary. Tests that need a different display topology build
# their own Monitor list rather than mutating this.
MONITORS = [Monitor(device=PRIMARY, primary=True, work=[0, 0, 2560, 1392], dpi=96)]


def make_window(rect, tabs, state="normal", monitor=PRIMARY, dpi=96) -> Window:
    return Window(monitor=monitor, rect=rect, state=state, dpi=dpi, tabs=tabs)


def make_layout(windows, monitors=None, saved_ts="t") -> Layout:
    return Layout(
        version=LAYOUT_VERSION,
        saved_ts=saved_ts,
        monitors=list(MONITORS if monitors is None else monitors),
        windows=windows,
    )
