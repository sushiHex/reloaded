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
    is that the session is exited now and not brought back, which is a
    different sentence and the one worth reading."""
    main_mod.cmd_restart(_args())

    out = capsys.readouterr().out
    assert "not come back" in out or "left closed" in out


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


def test_a_named_restart_never_reaches_the_check(restart, monkeypatch):
    """`restart <repo>` does not capture, so it has nothing to refuse."""
    called = []
    monkeypatch.setattr(main_mod, "cmd_restart_one",
                        lambda args, repos: called.append(repos) or 0)

    assert main_mod.cmd_restart(_args(repos=["app"])) == 0
    assert called == [["app"]]
    assert restart["saved"] == []
