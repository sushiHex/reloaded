from __future__ import annotations

import reloaded.win32 as win32_mod
from reloaded.win32 import set_geometry


def test_set_geometry_refuses_a_hwnd_that_is_no_longer_a_wt_window(monkeypatch):
    """Guard against HWND reuse: launch_window identifies a window, and up to
    ~2.5s can pass (while disambiguating multiple candidates) before
    set_geometry is called on it. If the original window closed in that gap,
    Windows can hand that same numeric HWND to an unrelated window — this
    must refuse to move/resize whatever it now points at."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: "SomeUnrelatedApp")
    assert set_geometry(12345, [0, 0, 100, 100], "normal") is False


def test_set_geometry_class_check_runs_before_any_placement_call(monkeypatch):
    """The class check must short-circuit before touching the window at all —
    not just refuse afterward — so a call that would crash on a bogus handle
    is never reached."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: "")

    def boom(*a, **k):
        raise AssertionError("GetWindowPlacement must not be called")

    monkeypatch.setattr(win32_mod._u32, "GetWindowPlacement", boom)
    assert set_geometry(12345, [0, 0, 100, 100], "normal") is False
