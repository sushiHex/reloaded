"""Guards added after a max-effort review found silent, damaging failures.

Each test names the failure it prevents, not the code shape it asserts.
"""
from __future__ import annotations

import threading
import time

import pytest

import reloaded.tabs as tabs_mod
from reloaded.__main__ import _capture_shrinkage, _unresolved_live_repos
from reloaded.capture import merge_pinned
from reloaded.layout import Layout, Tab, Window
from reloaded.paths import norm

CWD_A = "C:\\repos\\alpha"
CWD_B = "C:\\repos\\beta"


def _lo(*cwds_per_window, pinned=()):
    windows = []
    for cwds in cwds_per_window:
        windows.append(
            Window(
                monitor="\\\\.\\DISPLAY1",
                rect=[0, 0, 100, 100],
                state="normal",
                dpi=96,
                inset=[0, 0, 0, 0],
                tabs=[Tab(cwd=c, title=c, pinned=(c in pinned)) for c in cwds],
            )
        )
    return Layout(version=3, saved_ts="now", monitors=[], windows=windows)


# ── the capture must not quietly shrink the layout ──────────────────────


def test_a_still_running_session_missing_from_a_capture_blocks_the_save(tmp_path):
    """A UIA timeout drops a whole window, and the reconcile task then wrote
    that over a good layout. The session is still live, so it did not go
    away - the capture failed to see it."""
    path = tmp_path / "layout.json"
    from reloaded.layout import save

    save(_lo([CWD_A, CWD_B]), path)

    fresh = _lo([CWD_A])
    live = {norm(CWD_A): 1, norm(CWD_B): 2}

    detail = _capture_shrinkage(fresh, path, live)
    assert "beta" in detail, detail


def test_a_session_the_user_closed_does_not_block_the_save(tmp_path):
    """The mirror case. Refusing every shrink would wedge the reconcile task
    against a layout that could only ever grow."""
    path = tmp_path / "layout.json"
    from reloaded.layout import save

    save(_lo([CWD_A, CWD_B]), path)

    fresh = _lo([CWD_A])
    live = {norm(CWD_A): 1}  # beta genuinely exited

    assert _capture_shrinkage(fresh, path, live) == ""


def test_no_saved_layout_never_blocks_a_first_capture(tmp_path):
    assert _capture_shrinkage(_lo([CWD_A]), tmp_path / "missing.json", {}) == ""


# ── an unrecognised title must be visible, not silent ───────────────────


def test_a_live_session_with_no_matching_tab_is_named():
    """strip_glyph leaves an unfamiliar spinner attached, so the tab resolves
    to nothing and is dropped. Safe, but silent - which is how the last stale
    frame list went unnoticed."""
    out = _unresolved_live_repos(_lo([CWD_A]), {norm(CWD_A): 1, norm(CWD_B): 2})
    assert out == ["beta"]


def test_nothing_is_reported_when_every_session_was_captured():
    assert _unresolved_live_repos(_lo([CWD_A]), {norm(CWD_A): 1}) == []


# ── a pin outlives the session it launched ──────────────────────────────


def test_a_pin_survives_a_capture_taken_while_that_repo_is_running():
    """The pin used to last exactly until its first successful launch: a
    capture saw an ordinary running tab, merge_pinned skipped it, and the repo
    vanished for good the next time the session closed."""
    previous = _lo([CWD_A, CWD_B], pinned={CWD_B})
    fresh = _lo([CWD_A, CWD_B])  # beta is running now, so not marked pinned

    merged = merge_pinned(fresh, previous)

    beta = [t for w in merged.windows for t in w.tabs if norm(t.cwd) == norm(CWD_B)]
    assert len(beta) == 1, "the pinned repo was duplicated"
    assert beta[0].pinned is True, "the pin was dropped once the repo was running"


# ── the tab-read timeout must not hold the process open ─────────────────


def test_a_hung_tab_read_does_not_keep_the_interpreter_alive(monkeypatch):
    """shutdown(wait=False) does not abandon a worker: threading._shutdown()
    joins every live non-daemon thread before any atexit hook, so a hung COM
    call kept the whole command alive long past its timeout."""
    started = threading.Event()

    def hangs(hwnd):
        started.set()
        time.sleep(30)

    monkeypatch.setattr(tabs_mod, "_list_tab_items_uia", hangs)

    assert tabs_mod.list_tab_items(1, timeout=0.2) == []
    assert started.wait(5), "the worker never ran"

    worker = next(
        (t for t in threading.enumerate() if t.name.startswith("reloaded-uia-")),
        None,
    )
    assert worker is not None, "worker thread not found"
    assert worker.daemon is True, "a non-daemon worker is joined at exit"
