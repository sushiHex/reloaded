from __future__ import annotations

import dataclasses
import json

import pytest

from reloaded.layout import (
    LAYOUT_VERSION,
    Layout,
    Monitor,
    Tab,
    Window,
    clamp_rect,
    load,
    save,
    tab_flags,
    window_header,
    window_id,
)

TWO_MONITORS = [
    Monitor(device=r"\\.\DISPLAY1", primary=True, work=[0, 0, 2560, 1392], dpi=96),
    Monitor(device=r"\\.\DISPLAY2", primary=False, work=[2560, 0, 5120, 1392], dpi=96),
]


def _layout() -> Layout:
    return Layout(
        version=LAYOUT_VERSION,
        saved_ts="2026-07-20T07:30:00Z",
        monitors=list(TWO_MONITORS),
        windows=[
            Window(
                monitor=r"\\.\DISPLAY1",
                rect=[221, 228, 1168, 624],
                state="normal",
                dpi=96,
                tabs=[
                    Tab(cwd=r"C:\Users\k\repos\demo-app", title="demo-app"),
                    Tab(cwd=r"C:\Users\k\repos\widget-cli", title="widget-cli"),
                ],
            )
        ],
    )


def test_layout_roundtrips_through_json(tmp_path):
    original = _layout()
    p = tmp_path / "default.json"
    save(original, p)
    restored = load(p)
    assert restored.to_dict() == original.to_dict()


def test_pinned_flag_survives_a_roundtrip(tmp_path):
    lo = _layout()
    lo.windows[0].tabs.append(
        Tab(cwd=r"C:\Users\k\repos\sample-repo", title="sample-repo", pinned=True)
    )
    p = tmp_path / "default.json"
    save(lo, p)
    restored = load(p)
    assert restored.windows[0].tabs[-1].pinned is True
    assert restored.windows[0].tabs[0].pinned is False


def test_saved_layout_is_human_readable_json(tmp_path):
    p = tmp_path / "default.json"
    save(_layout(), p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["windows"][0]["tabs"][1]["title"] == "widget-cli"
    assert raw["version"] == LAYOUT_VERSION


def test_clamp_leaves_rect_untouched_when_monitor_present_and_dpi_matches():
    out = clamp_rect([221, 228, 1168, 624], 96, r"\\.\DISPLAY1", TWO_MONITORS)
    assert out == [221, 228, 1168, 624]


def test_clamp_reanchors_to_primary_when_saved_monitor_is_gone():
    # Window was on DISPLAY2 at x=2578; DISPLAY2 no longer exists.
    only_primary = [TWO_MONITORS[0]]
    out = clamp_rect([2578, 25, 1168, 624], 96, r"\\.\DISPLAY2", only_primary)
    x, y, w, h = out
    assert 0 <= x and x + w <= 2560, "must be fully inside the primary work area"
    assert 0 <= y and y + h <= 1392
    assert (w, h) == (1168, 624), "size is preserved when only the monitor changed"


def test_clamp_rescales_when_target_dpi_differs():
    hidpi = [Monitor(device=r"\\.\DISPLAY1", primary=True, work=[0, 0, 5120, 2784], dpi=192)]
    out = clamp_rect([100, 100, 1000, 600], 96, r"\\.\DISPLAY1", hidpi)
    assert out[2] == 2000
    assert out[3] == 1200


def test_clamp_shrinks_a_window_larger_than_the_work_area():
    small = [Monitor(device=r"\\.\DISPLAY1", primary=True, work=[0, 0, 1280, 720], dpi=96)]
    out = clamp_rect([0, 0, 1920, 1080], 96, r"\\.\DISPLAY1", small)
    assert out[2] <= 1280 and out[3] <= 720


def test_clamp_pulls_a_window_back_from_past_the_right_edge():
    out = clamp_rect([2500, 100, 800, 600], 96, r"\\.\DISPLAY1", TWO_MONITORS)
    assert out[0] + out[2] <= 2560


# Real values measured on a 2x 3840x2160 @150% setup: work area 3840x2088 per
# screen, taskbar on the primary, Windows Terminal's invisible resize border
# 9px on left/right/bottom and 0 on top. Each window is snapped flush to half a
# screen, so its rect legitimately pokes 9px past the work area on both sides.
SNAP_MONITORS = [
    Monitor(device=r"\\.\DISPLAY1", primary=True, work=[0, 0, 3840, 2088], dpi=144),
    Monitor(device=r"\\.\DISPLAY2", primary=False, work=[3840, 0, 7680, 2088], dpi=144),
]
SNAP_INSET = [9, 0, 9, 9]


@pytest.mark.parametrize(
    "monitor,rect",
    [
        (r"\\.\DISPLAY1", [-9, 0, 1938, 2097]),    # left half of the primary
        (r"\\.\DISPLAY1", [1911, 0, 1938, 2097]),  # right half of the primary
        (r"\\.\DISPLAY2", [3831, 0, 1938, 2097]),  # left half of the second
        (r"\\.\DISPLAY2", [5751, 0, 1938, 2097]),  # right half of the second
    ],
)
def test_clamp_leaves_a_flush_snapped_window_exactly_where_it_was(monitor, rect):
    """The whole point of the inset: these rects are correct, not off-screen.

    Clamped to the bare work area they came back as 0 / 1902 / 3840 / 5742 with
    the height cut to 2088 - a ~9px gap at the outer screen edges and a ~9px
    overlap down the middle of each monitor.
    """
    assert clamp_rect(rect, 144, monitor, SNAP_MONITORS, SNAP_INSET) == rect


def test_clamp_without_an_inset_still_pulls_a_snapped_window_inward():
    """Guards the migration path rather than the fix: a layout saved before the
    inset field has none, and must behave exactly as it did before."""
    out = clamp_rect([-9, 0, 1938, 2097], 144, r"\\.\DISPLAY1", SNAP_MONITORS)
    assert out == [0, 0, 1938, 2088]


def test_clamp_still_rescues_a_genuinely_offscreen_rect_when_inset_is_present():
    """The inset widens the allowance by a frame, not without limit."""
    out = clamp_rect([9000, 4000, 1938, 2097], 144, r"\\.\DISPLAY1", SNAP_MONITORS, SNAP_INSET)
    x, y, w, h = out
    assert x + w <= 3840 + SNAP_INSET[2]
    assert y + h <= 2088 + SNAP_INSET[3]


def test_clamp_scales_the_inset_with_the_window_on_a_dpi_change():
    """The border is part of the frame, so a rect rescaled for a new DPI needs
    its allowance rescaled too or the window lands short by a fraction of it."""
    hidpi = [Monitor(device=r"\\.\DISPLAY1", primary=True, work=[0, 0, 3840, 2088], dpi=288)]
    out = clamp_rect([-9, 0, 1938, 2097], 144, r"\\.\DISPLAY1", hidpi, SNAP_INSET)
    assert out[0] == -18, "inset doubled with the DPI, so the window may start 18px out"


def test_clamp_handles_empty_monitor_list_without_crashing():
    out = clamp_rect([221, 228, 1168, 624], 96, r"\\.\DISPLAY1", [])
    assert out == [221, 228, 1168, 624]


def _assert_defaulted_fields_are_non_default(obj) -> None:
    """Every field of `obj` that has a default must hold a non-default value.

    Driven by dataclasses.fields() rather than a hardcoded field-name list —
    a hardcoded list is exactly the kind of thing a developer adding a new
    defaulted field would also forget to update, which would let the
    roundtrip guard below pass while covering nothing for that field.
    """
    for f in dataclasses.fields(obj):
        if f.default is not dataclasses.MISSING:
            default_value = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            default_value = f.default_factory()
        else:
            continue  # required field: no default exists to compare against
        actual = getattr(obj, f.name)
        assert actual != default_value, (
            f"{type(obj).__name__}.{f.name} is at its default in this fixture — "
            "set it to a non-default value or the roundtrip guard below cannot "
            "detect a from_dict that forgets to read it back"
        )


def test_every_field_survives_a_roundtrip(tmp_path):
    """Guard for the to_dict/from_dict asymmetry.

    to_dict() is dataclasses.asdict() and picks up new fields automatically;
    from_dict() is hand-enumerated and will not. This builds every dataclass
    with all defaulted fields set away from their defaults, so a field added
    to the model but forgotten in from_dict fails here instead of silently
    reverting to its default on every load.
    """
    monitor = Monitor(device=r"\\.\DISPLAY9", primary=True, work=[1, 2, 3, 4], dpi=192)
    tab = Tab(cwd=r"C:\x", title="t", low_confidence=True, pinned=True,
              agent="codex", command="codex resume --last")
    window = Window(
        monitor=r"\\.\DISPLAY9",
        rect=[5, 6, 7, 8],
        state="maximized",
        dpi=192,
        tabs=[tab],
        inset=[9, 0, 9, 9],
    )
    lo = Layout(version=LAYOUT_VERSION, saved_ts="2026-07-20T00:00:00Z", monitors=[monitor], windows=[window])

    for obj in (monitor, tab, window, lo):
        _assert_defaulted_fields_are_non_default(obj)

    p = tmp_path / "l.json"
    save(lo, p)
    assert load(p).to_dict() == lo.to_dict()


def test_tab_flags_renders_each_combination():
    assert tab_flags(Tab(cwd="c", title="t")) == ""
    assert tab_flags(Tab(cwd="c", title="t", pinned=True)) == "  [pinned]"
    assert tab_flags(Tab(cwd="c", title="t", low_confidence=True)) == "  [basename guess]"
    assert (
        tab_flags(Tab(cwd="c", title="t", low_confidence=True, pinned=True))
        == "  [basename guess, pinned]"
    )


def test_window_header_formats_monitor_and_geometry():
    w = Window(monitor=r"\\.\DISPLAY1", rect=[10, 20, 800, 600], state="maximized", dpi=96, tabs=[])
    assert window_header(w) == r"\\.\DISPLAY1  (10,20 800x600, maximized)"


def test_window_id_is_derived_from_position():
    assert [window_id(i) for i in range(3)] == ["w1", "w2", "w3"]


def test_loading_a_layout_written_before_id_and_focused_tab_were_dropped(tmp_path):
    """Older layouts carry `id` and `focused_tab`; they must still load."""
    p = tmp_path / "old.json"
    p.write_text(
        json.dumps({
            "version": 1,
            "saved_ts": "old",
            "monitors": [],
            "windows": [{
                "id": "w7", "focused_tab": 3,
                "monitor": r"\\.\DISPLAY1", "rect": [1, 2, 3, 4],
                "state": "normal", "dpi": 96,
                "tabs": [{"cwd": r"C:\repos\a", "title": "a"}],
            }],
        }),
        encoding="utf-8",
    )
    lo = load(p)
    assert lo.windows[0].rect == [1, 2, 3, 4]
    assert lo.windows[0].tabs[0].cwd == r"C:\repos\a"
