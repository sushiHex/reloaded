"""Tests for reloaded.tabs' selection/keystroke helpers used by `down`, plus
list_tab_items' timeout bound.

The real UIA walk (_list_tab_items_uia) is COM interop and, matching the
rest of this codebase's convention (see test_capture.py), is exercised
through a monkeypatched boundary rather than against real COM interop.
"""
from __future__ import annotations

import time

import reloaded.tabs as tabs_mod


class _FakeSelectionPattern:
    def __init__(self):
        self.selected = False

    def Select(self):
        self.selected = True


class _FakeTabItem:
    def __init__(self):
        self.pattern = _FakeSelectionPattern()

    def GetSelectionItemPattern(self):
        return self.pattern


def test_select_tab_selects_then_foregrounds(monkeypatch):
    item = _FakeTabItem()
    monkeypatch.setattr(tabs_mod.win32, "set_foreground", lambda hwnd: True)
    assert tabs_mod.select_tab(12345, item) is True
    assert item.pattern.selected is True


def test_select_tab_survives_a_stale_control(monkeypatch):
    """A tab that can't be selected via UIA (stale control, closed tab)
    should not abort the call — the caller decides what to do based on
    whether the window actually ended up foreground, not on Select()."""

    class _BoomItem:
        def GetSelectionItemPattern(self):
            raise RuntimeError("stale control")

    monkeypatch.setattr(tabs_mod.win32, "set_foreground", lambda hwnd: True)
    assert tabs_mod.select_tab(12345, _BoomItem()) is True


def test_select_tab_reports_foreground_failure(monkeypatch):
    item = _FakeTabItem()
    monkeypatch.setattr(tabs_mod.win32, "set_foreground", lambda hwnd: False)
    assert tabs_mod.select_tab(12345, item) is False


def test_list_tab_items_returns_the_real_result_when_fast(monkeypatch):
    monkeypatch.setattr(
        tabs_mod, "_list_tab_items_uia", lambda hwnd: [("demo-app", object())]
    )
    result = tabs_mod.list_tab_items(123, timeout=2)
    assert [title for title, _item in result] == ["demo-app"]


def test_list_tab_items_returns_empty_on_timeout_instead_of_blocking(monkeypatch):
    """The regression this guards: the underlying UIA calls have no timeout
    of their own, so a hung WT window previously blocked the caller
    indefinitely — both the 5-minute reconcile task (via capture -> tab_titles)
    and down/restart's interactive teardown path (via plan_down)."""

    def hangs(hwnd):
        time.sleep(5)
        return [("should never get here", object())]

    monkeypatch.setattr(tabs_mod, "_list_tab_items_uia", hangs)
    start = time.monotonic()
    result = tabs_mod.list_tab_items(123, timeout=0.2)
    elapsed = time.monotonic() - start
    assert result == []
    assert elapsed < 1.0, f"must return promptly at the timeout, took {elapsed:.2f}s"


def test_list_tab_items_still_propagates_a_real_failure(monkeypatch):
    """A genuine UIA failure (not a hang) must keep surfacing normally --
    only the "never returned" case degrades to an empty list."""

    def raises(hwnd):
        raise RuntimeError("UIA genuinely unavailable")

    monkeypatch.setattr(tabs_mod, "_list_tab_items_uia", raises)
    try:
        tabs_mod.list_tab_items(123, timeout=2)
        assert False, "expected the exception to propagate"
    except RuntimeError as exc:
        assert "UIA genuinely unavailable" in str(exc)
