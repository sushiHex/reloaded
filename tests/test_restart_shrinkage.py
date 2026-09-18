"""A full restart refuses a capture that lost sessions still running.

`capture` has always refused this: `tabs.list_tab_items` degrades a per-window
UIA timeout to an empty list, `build_layout` drops that window entirely, and
what looks like a capture is a failed read. Saving it would drop those repos
from `up`.

A full `restart` performed the same capture and saved it with no such check —
and the cost there is higher, not lower. Teardown works from live discovery,
not from the layout, so every running session is exited; the relaunch then
works from the layout, so the ones the capture missed are closed and never come
back. The command that exists to put everything back exactly as it was is the
one that could take it away.
"""
from __future__ import annotations

import types

import pytest

from conftest import make_layout, make_window
import reloaded.__main__ as main_mod
from reloaded.layout import Tab
from reloaded.paths import norm


def _args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "unattended": False,
         "dry_run": False, "repos": [], "self_": False, "force": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


@pytest.fixture
def restart(monkeypatch, tmp_path):
    """`cmd_restart` with the desktop replaced, and every destructive step
    recorded rather than performed."""
    log = tmp_path / "reloaded.log"
    did = {"saved": [], "exited": False, "deployed": False, "log": log}

    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")
    monkeypatch.setattr(main_mod, "log_path", lambda: log)
    monkeypatch.setattr(main_mod.capture_mod, "capture_live", lambda *a, **k: make_layout(
        [make_window([0, 0, 800, 600], [Tab(cwd=r"C:\repos\app", title="app")])]))
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions", lambda: {})
    monkeypatch.setattr(main_mod.discover_mod, "title_to_cwd", lambda idx: {})
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda: {})
    monkeypatch.setattr(main_mod.layout_mod, "save",
                        lambda lo, path: did["saved"].append(lo))
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", lambda *a, **k: [])

    def _exited(*a, **k):
        did["exited"] = True
        return {"exited": [], "timed_out": [], "closed": [], "left_open": []}

    monkeypatch.setattr(main_mod.teardown_mod, "execute_down", _exited)
    monkeypatch.setattr(main_mod, "_deploy_layout",
                        lambda lo, args: did.update(deployed=True) or 0)

    # The capture lost a repo that is still running — a window that read as
    # empty, not a session that went away.
    monkeypatch.setattr(main_mod, "_capture_shrinkage",
                        lambda fresh, path, live: "1 running session(s) missing from this capture — beta")
    return did


def test_a_restart_refuses_a_capture_that_lost_a_running_session(restart, capsys):
    assert main_mod.cmd_restart(_args()) == 1

    out = capsys.readouterr().out
    assert "refusing to overwrite the layout" in out
    assert "beta" in out


def test_nothing_is_saved_exited_or_relaunched_on_a_refusal(restart, capsys):
    """The refusal has to land before the save, or it has already destroyed the
    thing it exists to protect — and before teardown, or the sessions are gone
    whatever the layout says."""
    main_mod.cmd_restart(_args())

    assert restart["saved"] == []
    assert restart["exited"] is False
    assert restart["deployed"] is False


def test_the_refusal_says_what_proceeding_would_cost(restart, capsys):
    """`capture`'s cost is that `up` stops knowing about the repo. A restart's
    is larger and conditional, and the wording has to carry both halves.

    `plan_down` builds its targets from `tabs.list_tab_items` — the same call
    that timed out during the capture — so a window still wedged at teardown
    yields no targets and its sessions survive; one that recovers in between is
    exited and then not relaunched. The dropped layout entry is certain, the
    exit is a race, and saying either alone would be wrong.
    """
    main_mod.cmd_restart(_args())

    out = capsys.readouterr().out
    assert "dropped from the layout" in out
    assert "if its window recovers before teardown" in out


def test_the_refusal_names_the_door_that_is_not_force(restart, capsys):
    """`--force` here *is* the outcome the check exists to prevent. Naming it
    as the only way past would funnel the user straight into it, and there is a
    safe one: a named restart takes no capture at all."""
    main_mod.cmd_restart(_args())

    out = capsys.readouterr().out
    assert "reloaded restart <repo>" in out
    assert out.index("reloaded restart <repo>") < out.index("--force")


def test_force_proceeds(restart, capsys):
    """The escape `capture --force` already gives, at the site that needs it
    more."""
    main_mod.cmd_restart(_args(force=True))

    assert restart["saved"], "a forced restart did not save its capture"
    assert restart["deployed"] is True


def test_a_dry_run_says_the_real_run_would_refuse(restart, capsys):
    """A preview whose only job is to say what would happen must not omit the
    one thing that would."""
    rc = main_mod.cmd_restart(_args(dry_run=True))

    out = capsys.readouterr().out
    assert "would refuse" in out
    assert "beta" in out
    assert rc == 1, "the preview of a run that would stop should not report success"


def test_a_dry_run_still_says_it_changed_nothing(restart, capsys):
    main_mod.cmd_restart(_args(dry_run=True))

    assert "nothing captured, sent, closed, or relaunched" in capsys.readouterr().out


def test_a_dry_run_leaves_no_trace_in_the_log(restart):
    """A dry run must leave nothing behind, and the log is something behind."""
    main_mod.cmd_restart(_args(dry_run=True))

    assert not restart["log"].exists() or restart["log"].read_text(encoding="utf-8") == ""


def test_a_real_refusal_is_recorded(restart):
    """Unattended it is the only trace: the reconcile has no console, and this
    is the outcome where the layout stops matching what is running."""
    main_mod.cmd_restart(_args())

    assert "refused to overwrite the layout" in restart["log"].read_text(encoding="utf-8")


# ── through the real predicate ───────────────────────────────────────────
#
# Everything above stubs `_capture_shrinkage` to pin the wiring. A guard wired
# perfectly to a predicate that never fires, or fires on everything, is still
# broken, so these two go through the real one.


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """A real saved layout on disk, and a capture that came back short."""
    path = tmp_path / "default.json"
    did = {"saved": [], "exited": False, "path": path}

    monkeypatch.setattr(main_mod, "layout_path", lambda name: path)
    monkeypatch.setattr(main_mod, "log_path", lambda: tmp_path / "reloaded.log")
    main_mod.layout_mod.save(make_layout([make_window(
        [0, 0, 800, 600],
        [Tab(cwd=r"C:\repos\app", title="app"), Tab(cwd=r"C:\repos\beta", title="beta")],
    )]), path)

    # The capture only read one of the two windows.
    monkeypatch.setattr(main_mod.capture_mod, "capture_live", lambda *a, **k: make_layout(
        [make_window([0, 0, 800, 600], [Tab(cwd=r"C:\repos\app", title="app")])]))
    monkeypatch.setattr(main_mod.discover_mod, "title_to_cwd", lambda idx: {})
    monkeypatch.setattr(main_mod.discover_mod, "transcript_index", lambda: {})
    monkeypatch.setattr(main_mod.layout_mod, "save",
                        lambda lo, p: did["saved"].append(lo))
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down", lambda *a, **k: [])
    monkeypatch.setattr(main_mod.teardown_mod, "execute_down",
                        lambda *a, **k: did.update(exited=True) or {
                            "exited": [], "timed_out": [], "closed": [], "left_open": []})
    monkeypatch.setattr(main_mod, "_deploy_layout", lambda lo, args: 0)
    return did


def test_a_window_that_read_as_empty_stops_the_restart(wired, monkeypatch, capsys):
    """`beta` is still running, so the capture that missed it failed to read
    it. Restarting would exit it and never bring it back."""
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions",
                        lambda: {norm(r"C:\repos\app"): 1, norm(r"C:\repos\beta"): 2})

    assert main_mod.cmd_restart(_args()) == 1

    assert wired["saved"] == []
    assert wired["exited"] is False
    assert "beta" in capsys.readouterr().out


def test_a_session_closed_on_purpose_does_not_stop_the_restart(wired, monkeypatch):
    """The mirror case, and the one that decides whether this guard is usable.

    `beta` is gone from the layout because the user closed it, not because the
    scan missed it — it is not running. Refusing here would mean a restart
    fails for good the first time anybody closes a tab.
    """
    monkeypatch.setattr(main_mod.discover_mod, "live_sessions",
                        lambda: {norm(r"C:\repos\app"): 1})

    assert main_mod.cmd_restart(_args()) == 0

    assert wired["saved"], "a legitimate restart was refused"
    assert wired["exited"] is True


def test_a_named_restart_never_reaches_the_check(restart, monkeypatch):
    """`restart <repo>` does not capture, so it has nothing to refuse."""
    called = []
    monkeypatch.setattr(main_mod, "cmd_restart_one",
                        lambda args, repos: called.append(repos) or 0)

    assert main_mod.cmd_restart(_args(repos=["app"])) == 0
    assert called == [["app"]]
    assert restart["saved"] == []
