"""A tab that opened but never started a session is reported, not clicked through.

Codex asks whether to trust a directory it has not seen before, and waits. An
unattended `up` into a fresh repo opens the tab and sits at that prompt, with
nothing on screen to say why the session never appeared.
"""
from __future__ import annotations

from conftest import make_layout, make_window
import reloaded.__main__ as main_mod
from reloaded.layout import Tab
from reloaded.paths import norm

CWD_A = r"C:\repos\alpha"
CWD_B = r"C:\repos\beta"


def _layout(*cwds, agent="codex"):
    tabs = [Tab(cwd=c, title=c.rsplit("\\", 1)[-1], agent=agent) for c in cwds]
    return make_layout([make_window([0, 0, 100, 100], tabs)])


def test_a_tab_with_a_live_session_is_not_reported():
    assert main_mod._never_started(_layout(CWD_A), {norm(CWD_A): 1}) == []


def test_a_tab_that_never_started_is_reported():
    assert main_mod._never_started(_layout(CWD_A), {}) == [CWD_A]


def test_every_silent_tab_is_named():
    assert main_mod._never_started(_layout(CWD_A, CWD_B), {}) == [CWD_A, CWD_B]


def test_a_claude_tab_that_never_started_is_reported_too():
    """Not Codex-specific. A session that failed to start is worth saying so
    about whatever it was."""
    assert main_mod._never_started(_layout(CWD_A, agent="claude"), {}) == [CWD_A]


def test_an_empty_layout_reports_nothing():
    assert main_mod._never_started(make_layout([]), {}) == []


def test_a_partly_started_layout_names_only_the_silent_one():
    lo = _layout(CWD_A, CWD_B)

    assert main_mod._never_started(lo, {norm(CWD_A): 1}) == [CWD_B]
