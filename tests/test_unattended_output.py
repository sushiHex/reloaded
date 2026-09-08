"""An unattended deploy has to write everything to the log.

Task Scheduler runs it under pythonw, where stdout has no destination at all.
`print` there is not quieter than `_log` - it is gone. So the choice between
them is a correctness question, and it was being made four separate times
inside one function.

The fourth was wrong. The header of the "no session started" report went to
the log; the lines under it - which repos, and the explanation of Codex's
trust prompt - stayed on `print`. That is the one message worth having in a
log, because the run it describes is the one nobody is watching, and the tabs
it names look perfectly healthy from the outside.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod


@pytest.fixture
def logged(monkeypatch):
    """Capture what would reach the log file."""
    lines = []
    monkeypatch.setattr(main_mod, "_log", lines.append)
    return lines


def _args(unattended):
    return types.SimpleNamespace(unattended=unattended)


def test_an_unattended_run_logs_instead_of_printing(logged, capsys):
    say = main_mod._reporter(_args(True))

    say("something happened")

    assert logged == ["something happened"]
    assert capsys.readouterr().out == ""


def test_an_interactive_run_prints_instead_of_logging(logged, capsys):
    say = main_mod._reporter(_args(False))

    say("something happened")

    assert logged == []
    assert "something happened" in capsys.readouterr().out


def test_a_separating_line_is_a_console_thing_only(logged, capsys):
    """A timestamped empty line is litter in a log file."""
    main_mod._reporter(_args(True))("headline", blank=True)
    assert logged == ["headline"]

    main_mod._reporter(_args(False))("headline", blank=True)
    assert capsys.readouterr().out == "\nheadline\n"


def test_every_line_of_the_no_session_report_reaches_the_log(logged, monkeypatch, capsys):
    """The regression. The header was logged and its details were not, so an
    unattended deploy recorded that something failed without recording what."""
    from conftest import make_layout, make_window
    from reloaded.layout import Tab

    lo = make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=r"C:\repos\untrusted", title="untrusted", agent="codex"),
    ])])

    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    monkeypatch.setattr(main_mod.win32_mod, "list_monitors", lambda: [])
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index",
                        lambda *a, **k: {})
    monkeypatch.setattr(main_mod.deploy_mod, "plan_deploy",
                        lambda *a, **k: [_entry(lo)])
    monkeypatch.setattr(main_mod.deploy_mod, "transcripts_to_repair",
                        lambda *a, **k: [])
    monkeypatch.setattr(main_mod.transcript_mod, "repair_all", lambda paths: [])
    monkeypatch.setattr(main_mod.deploy_mod, "execute", lambda plan: [])

    main_mod._deploy_layout(lo, types.SimpleNamespace(
        unattended=True, dry_run=False))

    joined = "\n".join(logged)
    assert "no session started" in joined
    assert r"C:\repos\untrusted" in joined, "the repo that failed is not in the log"
    assert "trust a directory" in joined, "the explanation is not in the log"


def test_a_failed_placement_reaches_the_log(logged, monkeypatch):
    """A window that launched but could not be identified or placed is the
    other thing worth knowing about an unattended deploy, and it was on bare
    `print` too."""
    lo = _one_tab_layout()
    _stub_deploy(monkeypatch, lo, results=[
        types.SimpleNamespace(window_id="w1", hwnd=None, placed=False),
    ])
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})

    main_mod._deploy_layout(lo, types.SimpleNamespace(
        unattended=True, dry_run=False))

    assert any("could not identify the new window" in line for line in logged)


def test_a_repair_warning_reaches_the_log(logged, monkeypatch):
    """A torn transcript that could not be repaired changes what the session
    resumes with. Silence about it is the worst possible report."""
    lo = _one_tab_layout()
    _stub_deploy(monkeypatch, lo)
    monkeypatch.setattr(main_mod.transcript_mod, "repair_all",
                        lambda paths: [(r"C:\t\a.jsonl", OSError("locked"))])
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})

    main_mod._deploy_layout(lo, types.SimpleNamespace(
        unattended=True, dry_run=False))

    assert any("could not repair" in line for line in logged)


def test_the_plan_itself_reaches_the_log(logged, monkeypatch):
    """What a logon deploy decided to launch, and where. It is the only record
    of the run."""
    lo = _one_tab_layout()
    _stub_deploy(monkeypatch, lo)
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})

    main_mod._deploy_layout(lo, types.SimpleNamespace(
        unattended=True, dry_run=False))

    assert any("untrusted" in line for line in logged)


def _one_tab_layout():
    from conftest import make_layout, make_window
    from reloaded.layout import Tab

    return make_layout([make_window([0, 0, 800, 600], [
        Tab(cwd=r"C:\repos\untrusted", title="untrusted", agent="codex"),
    ])])


def _stub_deploy(monkeypatch, lo, results=()):
    """Everything _deploy_layout reaches for, so only its reporting is under
    test."""
    monkeypatch.setattr(main_mod.win32_mod, "list_monitors", lambda: [])
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index",
                        lambda *a, **k: {})
    monkeypatch.setattr(main_mod.deploy_mod, "plan_deploy",
                        lambda *a, **k: [_entry(lo)])
    monkeypatch.setattr(main_mod.deploy_mod, "transcripts_to_repair",
                        lambda *a, **k: [])
    monkeypatch.setattr(main_mod.transcript_mod, "repair_all", lambda paths: [])
    monkeypatch.setattr(main_mod.deploy_mod, "execute", lambda plan: list(results))


def _entry(lo):
    """One planned window, enough for _deploy_layout to get past the plan."""
    return types.SimpleNamespace(
        id="w1", rect=[0, 0, 800, 600], state="normal",
        tabs=list(lo.windows[0].tabs), delays=[0], skipped=[], missing=[],
        argv=["wt"],
    )
