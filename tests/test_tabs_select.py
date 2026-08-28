"""select_tab must confirm the TAB took focus, not just the window.

The bug this pins cost two days of wrong explanations. select_tab called
Select() inside a try/except that swallowed failure, then returned whether the
WINDOW reached the foreground - so a silently failed Select() sent every
keystroke to whichever tab was already active. Measured: a tab read
selected=False immediately after select_tab returned True, and the same
{Enter} that had done nothing three times landed as soon as selection was
verified.
"""
from __future__ import annotations

import pytest

import reloaded.tabs as tabs_mod


class _Pattern:
    def __init__(self, takes_after: int | None):
        self._takes_after = takes_after
        self.selects = 0
        self.IsSelected = False

    def Select(self):
        self.selects += 1
        if self._takes_after is not None and self.selects >= self._takes_after:
            self.IsSelected = True


class _Item:
    def __init__(self, takes_after=1, raises=False):
        self._pattern = _Pattern(takes_after)
        self._raises = raises

    def GetSelectionItemPattern(self):
        if self._raises:
            raise RuntimeError("no selection pattern")
        return self._pattern


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)


@pytest.fixture
def foreground(monkeypatch):
    monkeypatch.setattr(tabs_mod.win32, "set_foreground", lambda hwnd: True)


def test_a_tab_that_selects_immediately_succeeds(foreground):
    assert tabs_mod.select_tab(1, _Item(takes_after=1)) is True


def test_a_tab_that_selects_on_a_later_attempt_succeeds(foreground):
    item = _Item(takes_after=3)

    assert tabs_mod.select_tab(1, item) is True
    assert item._pattern.selects >= 3


def test_a_tab_that_never_selects_fails(foreground):
    """The bug. Returning True here sends keystrokes to whichever tab IS
    active - which is how /exit reached the wrong session for two days."""
    assert tabs_mod.select_tab(1, _Item(takes_after=None)) is False


def test_a_tab_whose_pattern_raises_fails(foreground):
    """Nothing here can be confirmed, and typing blind is what this function
    exists to prevent."""
    assert tabs_mod.select_tab(1, _Item(raises=True)) is False


def test_a_window_that_will_not_foreground_fails(monkeypatch):
    monkeypatch.setattr(tabs_mod.win32, "set_foreground", lambda hwnd: False)

    assert tabs_mod.select_tab(1, _Item(takes_after=1)) is False


def test_selection_is_not_retried_once_it_has_taken(foreground):
    item = _Item(takes_after=1)

    tabs_mod.select_tab(1, item)

    assert item._pattern.selects == 1
