"""A tab that opened but never started a session is reported, not clicked through.

Codex asks whether to trust a directory it has not seen before, and waits. An
unattended `up` into a fresh repo opens the tab and sits at that prompt, with
nothing on screen to say why the session never appeared.

Scoped to the tabs THIS deploy opened. It used to take the whole layout, so a
repo whose directory is missing — which plan_deploy had already skipped and
reported correctly — came back here as "opened but no session started", under
an explanation about a trust prompt that had nothing to do with it. A tab that
was never opened cannot have failed to start.
"""
from __future__ import annotations

import types

import reloaded.__main__ as main_mod
from reloaded.layout import Tab
from reloaded.paths import norm

CWD_A = r"C:\repos\alpha"
CWD_B = r"C:\repos\beta"
CWD_GONE = r"C:\repos\deleted-last-week"


def _tab(cwd, agent="codex"):
    return Tab(cwd=cwd, title=cwd.rsplit("\\", 1)[-1], agent=agent)


def _plan(launched=(), skipped=(), missing=()):
    """One planned window. `launched` are the tabs the deploy actually opens;
    the other two are what it decided not to."""
    return [types.SimpleNamespace(
        id="w1", rect=[0, 0, 100, 100], state="normal",
        tabs=[_tab(c) for c in launched],
        delays=[0] * len(launched),
        skipped=[_tab(c) for c in skipped],
        missing=[_tab(c) for c in missing],
        argv=["wt"],
    )]


def test_a_tab_with_a_live_session_is_not_reported():
    assert main_mod._never_started(_plan([CWD_A]), {norm(CWD_A): 1}) == []


def test_a_tab_that_never_started_is_reported():
    assert main_mod._never_started(_plan([CWD_A]), {}) == [CWD_A]


def test_every_silent_tab_is_named():
    assert main_mod._never_started(_plan([CWD_A, CWD_B]), {}) == [CWD_A, CWD_B]


def test_a_claude_tab_that_never_started_is_reported_too():
    """Not Codex-specific. A session that failed to start is worth saying so
    about whatever it was."""
    plan = _plan([CWD_A])
    plan[0].tabs = [_tab(CWD_A, agent="claude")]

    assert main_mod._never_started(plan, {}) == [CWD_A]


def test_an_empty_plan_reports_nothing():
    assert main_mod._never_started([], {}) == []


def test_a_partly_started_plan_names_only_the_silent_one():
    assert main_mod._never_started(_plan([CWD_A, CWD_B]), {norm(CWD_A): 1}) == [CWD_B]


# ── what the layout holds but the deploy did not open ────────────────────


def test_a_repo_whose_directory_is_missing_is_not_blamed_on_a_trust_prompt():
    """The regression. plan_deploy reports it as missing, correctly and with
    the right reason. Reporting it a second time as "opened but no session
    started" sends the user looking for a prompt in a tab that does not
    exist."""
    assert main_mod._never_started(_plan(missing=[CWD_GONE]), {}) == []


def test_a_repo_that_was_already_running_is_not_reported():
    """It was skipped precisely BECAUSE it has a session. Reporting it as
    having none inverts the fact that caused the skip."""
    assert main_mod._never_started(_plan(skipped=[CWD_A]), {}) == []


def test_only_the_launched_tab_is_named_among_all_three():
    plan = _plan(launched=[CWD_B], skipped=[CWD_A], missing=[CWD_GONE])

    assert main_mod._never_started(plan, {}) == [CWD_B]
