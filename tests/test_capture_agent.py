"""Capture records which agent a tab held, and how it was launched."""
from __future__ import annotations

import reloaded.capture as capture_mod
from reloaded.layout import Monitor, Tab
from reloaded.paths import norm

REPOS = r"C:\repos"
CWD_A = r"C:\repos\alpha"
CWD_B = r"C:\repos\beta"
MONITORS = [Monitor(device=r"\\.\DISPLAY1", primary=True,
                    work=[0, 0, 2560, 1392], dpi=96)]


def _windows(*titles):
    return [{"titles": list(titles), "monitor": r"\\.\DISPLAY1",
             "rect": [0, 0, 100, 100], "state": "normal", "dpi": 96,
             "inset": [0, 0, 0, 0]}]


def _build(monkeypatch, titles, live, kinds=None, command=""):
    monkeypatch.setattr(capture_mod.discover, "session_command",
                        lambda pid: command)
    return capture_mod.build_layout(
        _windows(*titles), MONITORS, {}, live, REPOS, "t", kinds=kinds,
    )


def test_a_captured_tab_records_its_kind(monkeypatch):
    lo = _build(
        monkeypatch, ["alpha", "beta"],
        {norm(CWD_A): 1, norm(CWD_B): 2},
        kinds={norm(CWD_A): "claude", norm(CWD_B): "codex"},
    )

    assert [t.agent for t in lo.windows[0].tabs] == ["claude", "codex"]


def test_a_captured_tab_records_its_command(monkeypatch):
    lo = _build(monkeypatch, ["beta"], {norm(CWD_B): 2},
                kinds={norm(CWD_B): "codex"},
                command="codex resume --last")

    assert lo.windows[0].tabs[0].command == "codex resume --last"


def test_a_cwd_with_no_known_kind_captures_as_claude(monkeypatch):
    """Which is what every layout written before kinds existed meant."""
    lo = _build(monkeypatch, ["alpha"], {norm(CWD_A): 1}, kinds={})

    assert lo.windows[0].tabs[0].agent == "claude"


def test_kinds_may_be_omitted_entirely(monkeypatch):
    """Callers that predate agent kinds keep working."""
    lo = _build(monkeypatch, ["alpha"], {norm(CWD_A): 1}, kinds=None)

    assert lo.windows[0].tabs[0].agent == "claude"


def test_a_pinned_tab_keeps_its_kind_and_command():
    """merge_pinned rebuilds pinned tabs. Dropping these would silently revert
    a pinned Codex tab to Claude on the next reconcile - and the reconcile
    runs every five minutes."""
    from conftest import make_layout, make_window

    previous = make_layout([make_window([0, 0, 100, 100], [
        Tab(cwd=CWD_B, title="beta", agent="codex",
            command="codex resume --last", pinned=True),
    ])])
    fresh = make_layout([make_window([0, 0, 100, 100], [])])

    merged = capture_mod.merge_pinned(fresh, previous)

    tab = merged.windows[0].tabs[0]
    assert tab.agent == "codex"
    assert tab.command == "codex resume --last"
