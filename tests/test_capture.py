from __future__ import annotations

import time

from conftest import MONITORS, make_layout as _lo, make_window as _win

import reloaded.capture as capture_mod
from reloaded.capture import _tab_titles_bounded, build_layout, merge_pinned, resolve_tab
from reloaded.layout import Tab
from reloaded.paths import norm

REPOS = r"C:\Users\k\repos"


def test_tab_titles_bounded_returns_the_real_result_when_fast(monkeypatch):
    monkeypatch.setattr(capture_mod.tabs, "tab_titles", lambda hwnd: ["demo-app", "sample-bot"])
    assert _tab_titles_bounded(123, timeout=2) == ["demo-app", "sample-bot"]


def test_tab_titles_bounded_returns_empty_on_timeout_instead_of_blocking(monkeypatch):
    """The regression this guards: tab_titles' underlying UIA calls have no
    timeout of their own, so a hung WT process previously blocked the whole
    5-minute reconcile task indefinitely."""

    def hangs(hwnd):
        time.sleep(5)
        return ["should never get here"]

    monkeypatch.setattr(capture_mod.tabs, "tab_titles", hangs)
    start = time.monotonic()
    result = _tab_titles_bounded(123, timeout=0.2)
    elapsed = time.monotonic() - start
    assert result == []
    assert elapsed < 1.0, f"must return promptly at the timeout, took {elapsed:.2f}s"


def test_tab_titles_bounded_still_propagates_a_real_failure(monkeypatch):
    """A genuine UIA failure (not a hang) must keep surfacing normally --
    only the "never returned" case degrades to an empty list."""

    def raises(hwnd):
        raise RuntimeError("UIA genuinely unavailable")

    monkeypatch.setattr(capture_mod.tabs, "tab_titles", raises)
    try:
        _tab_titles_bounded(123, timeout=2)
        assert False, "expected the exception to propagate"
    except RuntimeError as exc:
        assert "UIA genuinely unavailable" in str(exc)


def test_resolve_tab_prefers_the_transcript_title_map():
    title_map = {"Resume editor-app project": r"C:\Users\k\repos\editor-app"}
    live = {norm(r"C:\Users\k\repos\editor-app"): 1234}
    assert resolve_tab("Resume editor-app project", title_map, live, REPOS) == (
        r"C:\Users\k\repos\editor-app",
        False,
    )


def test_resolve_tab_falls_back_to_basename_under_repos_root():
    live = {norm(r"C:\Users\k\repos\demo-app"): 99}
    cwd, low = resolve_tab("demo-app", {}, live, REPOS)
    assert norm(cwd) == norm(r"C:\Users\k\repos\demo-app")
    assert low is True, "basename guesses must be flagged low confidence"


def test_resolve_tab_returns_none_for_a_non_claude_tab():
    # A plain shell tab resolves to nothing live -> not managed.
    assert resolve_tab("Windows PowerShell", {}, {norm(r"C:\x"): 1}, REPOS) is None


def test_resolve_tab_rejects_a_title_map_hit_with_no_live_session():
    title_map = {"editor-app": r"C:\Users\k\repos\editor-app"}
    assert resolve_tab("editor-app", title_map, {}, REPOS) is None


def test_build_layout_preserves_window_grouping_and_tab_order():
    live = {
        norm(r"C:\Users\k\repos\demo-app"): 1,
        norm(r"C:\Users\k\repos\widget-cli"): 2,
        norm(r"C:\Users\k\repos\editor-app"): 3,
    }
    title_map = {
        "demo-app": r"C:\Users\k\repos\demo-app",
        "widget-cli": r"C:\Users\k\repos\widget-cli",
        "editor-app": r"C:\Users\k\repos\editor-app",
    }
    windows_data = [
        {
            "rect": [221, 228, 1168, 624],
            "state": "normal",
            "monitor": r"\\.\DISPLAY1",
            "dpi": 96,
            "titles": ["demo-app", "widget-cli"],
        },
        {
            "rect": [1400, 25, 1168, 624],
            "state": "maximized",
            "monitor": r"\\.\DISPLAY1",
            "dpi": 96,
            "titles": ["editor-app"],
        },
    ]
    lo = build_layout(windows_data, MONITORS, title_map, live, REPOS, "2026-07-20T00:00:00Z")
    assert len(lo.windows) == 2
    assert [t.title for t in lo.windows[0].tabs] == ["demo-app", "widget-cli"]
    assert lo.windows[0].rect == [221, 228, 1168, 624]
    assert lo.windows[1].state == "maximized"
    assert lo.windows[1].tabs[0].cwd == r"C:\Users\k\repos\editor-app"


def test_build_layout_drops_windows_with_no_claude_tabs():
    windows_data = [
        {
            "rect": [0, 0, 800, 600],
            "state": "normal",
            "monitor": r"\\.\DISPLAY1",
            "dpi": 96,
            "titles": ["Windows PowerShell"],
        }
    ]
    lo = build_layout(windows_data, MONITORS, {}, {}, REPOS, "2026-07-20T00:00:00Z")
    assert lo.windows == []


def test_merge_pinned_carries_a_hand_added_tab_into_a_fresh_capture():
    previous = _lo([
        _win([0, 0, 800, 600], [
            Tab(cwd=r"C:\Users\k\repos\demo-app", title="demo-app"),
            Tab(cwd=r"C:\Users\k\repos\sample-repo", title="sample-repo", pinned=True),
        ])
    ])
    # `sample-repo` is not running, so a fresh capture cannot see it.
    fresh = _lo([
        _win([10, 10, 800, 600], [Tab(cwd=r"C:\Users\k\repos\demo-app", title="demo-app")])
    ])
    merged = merge_pinned(fresh, previous)
    titles = [t.title for t in merged.windows[0].tabs]
    assert titles == ["demo-app", "sample-repo"]
    assert merged.windows[0].rect == [10, 10, 800, 600], "fresh geometry still wins"


def test_merge_pinned_does_not_duplicate_a_pinned_tab_that_is_now_running():
    previous = _lo([
        _win([0, 0, 800, 600],
             [Tab(cwd=r"C:\Users\k\repos\sample-repo", title="sample-repo", pinned=True)])
    ])
    fresh = _lo([
        _win([0, 0, 800, 600], [Tab(cwd=r"C:\Users\k\repos\sample-repo", title="sample-repo")])
    ])
    merged = merge_pinned(fresh, previous)
    assert len(merged.windows[0].tabs) == 1


def test_merge_pinned_handles_no_previous_layout():
    fresh = _lo([])
    assert merge_pinned(fresh, None) is fresh


def test_build_layout_drops_only_the_non_claude_tabs_from_a_mixed_window():
    live = {norm(r"C:\Users\k\repos\demo-app"): 1}
    windows_data = [
        {
            "rect": [0, 0, 800, 600],
            "state": "normal",
            "monitor": r"\\.\DISPLAY1",
            "dpi": 96,
            "titles": ["Windows PowerShell", "demo-app"],
        }
    ]
    lo = build_layout(windows_data, MONITORS, {"demo-app": r"C:\Users\k\repos\demo-app"}, live, REPOS, "t")
    assert len(lo.windows) == 1
    assert [t.title for t in lo.windows[0].tabs] == ["demo-app"]
