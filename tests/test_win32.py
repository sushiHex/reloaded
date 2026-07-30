from __future__ import annotations

import ctypes

import reloaded.win32 as win32_mod
from reloaded.win32 import (
    RECT,
    WINDOWPLACEMENT,
    close_window,
    get_geometry,
    set_foreground,
    set_geometry,
    verify_and_fix_geometry,
)


def _fake_get_window_placement(showcmd, normal_rect):
    """A GetWindowPlacement replacement that writes real values into the
    caller's WINDOWPLACEMENT struct via its byref, the way the real Win32
    call does - a plain True/False stub can't exercise get_geometry's field
    reads."""
    l, t, w, h = normal_rect

    def fake(hwnd, ref):
        wp = ctypes.cast(ref, ctypes.POINTER(WINDOWPLACEMENT)).contents
        wp.showCmd = showcmd
        wp.rcNormalPosition = RECT(l, t, l + w, t + h)
        return True

    return fake


def _fake_get_window_rect(rect):
    l, t, w, h = rect

    def fake(hwnd, ref):
        r = ctypes.cast(ref, ctypes.POINTER(RECT)).contents
        r.left, r.top, r.right, r.bottom = l, t, l + w, t + h
        return True

    return fake


def test_get_geometry_uses_actual_bounds_for_a_normal_state_window(monkeypatch):
    """The regression this guards: a window Aero-Snapped to half a monitor
    reports showCmd as ordinary SW_SHOWNORMAL, identical to a floating
    window, but GetWindowPlacement's rcNormalPosition for it is the size it
    would return to if un-snapped - not its current half-screen bounds.
    Capturing that instead of GetWindowRect's actual bounds silently drops
    a snap-managed layout on restore."""
    monkeypatch.setattr(
        win32_mod._u32,
        "GetWindowPlacement",
        _fake_get_window_placement(win32_mod.SW_NORMAL, [999, 999, 111, 111]),
    )
    monkeypatch.setattr(
        win32_mod._u32, "GetWindowRect", _fake_get_window_rect([1920, 0, 1920, 2088])
    )
    monkeypatch.setattr(win32_mod._u32, "MonitorFromWindow", lambda hwnd, flags: 0)
    monkeypatch.setattr(win32_mod._u32, "GetMonitorInfoW", lambda hmon, ref: False)

    rect, state, _device, _dpi = get_geometry(12345)
    assert state == "normal"
    assert rect == [1920, 0, 1920, 2088]  # GetWindowRect's actual bounds, not rcNormalPosition


def test_get_geometry_uses_restore_rect_for_a_maximized_window(monkeypatch):
    """The opposite case: GetWindowRect for a maximized window returns the
    full-screen covering rect, not the size it should restore to -
    rcNormalPosition must be used here instead, or "maximized" could never
    be restored to its pre-maximize size."""
    monkeypatch.setattr(
        win32_mod._u32,
        "GetWindowPlacement",
        _fake_get_window_placement(win32_mod.SW_MAXIMIZED, [100, 100, 800, 600]),
    )

    def boom(*a, **k):
        raise AssertionError("must not call GetWindowRect for a maximized window")

    monkeypatch.setattr(win32_mod._u32, "GetWindowRect", boom)
    monkeypatch.setattr(win32_mod._u32, "MonitorFromWindow", lambda hwnd, flags: 0)
    monkeypatch.setattr(win32_mod._u32, "GetMonitorInfoW", lambda hmon, ref: False)

    rect, state, _device, _dpi = get_geometry(12345)
    assert state == "maximized"
    assert rect == [100, 100, 800, 600]  # rcNormalPosition, the restore-to size


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


def test_set_geometry_applies_exactly_once_no_settle_no_retry(monkeypatch):
    """set_geometry is the immediate, one-shot placement only now - waiting
    for WT to settle and reapplying if it drifted is verify_and_fix_geometry's
    job, called separately (and batched across windows) by deploy.execute."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: win32_mod.WT_CLASS)
    calls = []
    monkeypatch.setattr(
        win32_mod._u32, "GetWindowPlacement", lambda hwnd, ref: calls.append("get") or True
    )
    monkeypatch.setattr(
        win32_mod._u32, "SetWindowPlacement", lambda hwnd, ref: calls.append("set") or True
    )
    assert set_geometry(12345, [0, 0, 100, 100], "normal") is True
    assert calls == ["get", "set"]


def test_verify_and_fix_geometry_refuses_a_hwnd_that_is_no_longer_a_wt_window(monkeypatch):
    """Same HWND-reuse hazard set_geometry itself guards against: real time
    has passed (the caller's settle wait) since the original placement, so
    the class must be re-checked here too, not just once in set_geometry."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: "SomeUnrelatedApp")

    def boom(*a, **k):
        raise AssertionError("get_geometry must not be called")

    monkeypatch.setattr(win32_mod, "get_geometry", boom)
    assert verify_and_fix_geometry(12345, [0, 0, 100, 100], "normal") is False


def test_verify_and_fix_geometry_does_nothing_when_placement_already_stuck(monkeypatch):
    """The common case: no drift, no reapply, no maximize/minimize flicker,
    and no risk of a transient second-call failure masking the real
    success."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: win32_mod.WT_CLASS)
    monkeypatch.setattr(
        win32_mod, "get_geometry", lambda hwnd: ([0, 0, 100, 100], "normal", "\\\\.\\DISPLAY1", 96)
    )

    def boom(*a, **k):
        raise AssertionError("must not reapply when nothing drifted")

    monkeypatch.setattr(win32_mod._u32, "SetWindowPlacement", boom)
    assert verify_and_fix_geometry(12345, [0, 0, 100, 100], "normal") is True


def test_verify_and_fix_geometry_reapplies_when_rect_drifted(monkeypatch):
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: win32_mod.WT_CLASS)
    monkeypatch.setattr(
        win32_mod, "get_geometry", lambda hwnd: ([9, 9, 9, 9], "normal", "\\\\.\\DISPLAY1", 96)
    )
    calls = []
    monkeypatch.setattr(
        win32_mod._u32, "GetWindowPlacement", lambda hwnd, ref: calls.append("get") or True
    )
    monkeypatch.setattr(
        win32_mod._u32, "SetWindowPlacement", lambda hwnd, ref: calls.append("set") or True
    )
    assert verify_and_fix_geometry(12345, [0, 0, 100, 100], "normal") is True
    assert calls == ["get", "set"]


def test_verify_and_fix_geometry_reapplies_when_state_drifted(monkeypatch):
    """Rect alone isn't enough to verify a maximized/minimized target - state
    must be checked too, since rcNormalPosition (what the rect comparison
    reads) stays the same restore-to rect regardless of current state."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: win32_mod.WT_CLASS)
    monkeypatch.setattr(
        win32_mod, "get_geometry", lambda hwnd: ([0, 0, 100, 100], "normal", "\\\\.\\DISPLAY1", 96)
    )
    calls = []
    monkeypatch.setattr(win32_mod._u32, "GetWindowPlacement", lambda hwnd, ref: True)
    monkeypatch.setattr(
        win32_mod._u32, "SetWindowPlacement", lambda hwnd, ref: calls.append("set") or True
    )
    monkeypatch.setattr(
        win32_mod._u32, "ShowWindow", lambda hwnd, cmd: calls.append(("show", cmd))
    )
    assert verify_and_fix_geometry(12345, [0, 0, 100, 100], "maximized") is True
    assert calls == ["set", ("show", win32_mod.SW_MAXIMIZED)]


def test_set_foreground_true_only_when_the_os_actually_moved_focus(monkeypatch):
    """SetForegroundWindow can be silently refused by Windows depending on
    which process last had input focus — the call succeeding is not proof.
    A caller about to send real keystrokes must be able to tell the
    difference, so this verifies via GetForegroundWindow rather than trusting
    the request call's own return value."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: win32_mod.WT_CLASS)
    monkeypatch.setattr(win32_mod._u32, "SetForegroundWindow", lambda hwnd: True)
    monkeypatch.setattr(win32_mod._u32, "GetForegroundWindow", lambda: 12345)
    assert set_foreground(12345) is True
    assert set_foreground(99999) is False


def test_set_foreground_correctly_matches_a_handle_with_the_sign_bit_set(monkeypatch):
    """GetForegroundWindow.restype must be HWND, not left as ctypes' default
    signed c_int — otherwise a handle with bit 31 set reads back negative
    and never compares equal to the positive hwnd passed in, so this would
    always report failure (and skip sending /exit) for such a handle."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: win32_mod.WT_CLASS)
    monkeypatch.setattr(win32_mod._u32, "SetForegroundWindow", lambda hwnd: True)
    high_bit_hwnd = 0x80010203
    monkeypatch.setattr(win32_mod._u32, "GetForegroundWindow", lambda: high_bit_hwnd)
    assert set_foreground(high_bit_hwnd) is True


def test_set_foreground_refuses_a_hwnd_that_is_no_longer_a_wt_window(monkeypatch):
    """Same HWND-reuse hazard as set_geometry: teardown's per-tab loop can
    take up to EXIT_TIMEOUT_SECONDS per holdout, real time for a closed
    window's HWND to be reused by an unrelated app before this is reached."""
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: "SomeUnrelatedApp")

    def boom(*a, **k):
        raise AssertionError("SetForegroundWindow must not be called")

    monkeypatch.setattr(win32_mod._u32, "SetForegroundWindow", boom)
    assert set_foreground(12345) is False


def test_close_window_sends_wm_close(monkeypatch):
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: win32_mod.WT_CLASS)
    calls = []

    def fake_post(hwnd, msg, w, l):
        calls.append((hwnd.value, msg))
        return True

    monkeypatch.setattr(win32_mod._u32, "PostMessageW", fake_post)
    assert close_window(12345) is True
    assert calls == [(12345, win32_mod.WM_CLOSE)]


def test_close_window_reports_failure(monkeypatch):
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: win32_mod.WT_CLASS)
    monkeypatch.setattr(win32_mod._u32, "PostMessageW", lambda hwnd, msg, w, l: False)
    assert close_window(12345) is False


def test_close_window_refuses_a_hwnd_that_is_no_longer_a_wt_window(monkeypatch):
    monkeypatch.setattr(win32_mod, "_class_name", lambda hwnd: "SomeUnrelatedApp")

    def boom(*a, **k):
        raise AssertionError("PostMessageW must not be called")

    monkeypatch.setattr(win32_mod._u32, "PostMessageW", boom)
    assert close_window(12345) is False
