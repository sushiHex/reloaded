"""Shared test setup and model factories."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from reloaded.layout import LAYOUT_VERSION, Layout, Monitor, Window  # noqa: E402

PRIMARY = r"\\.\DISPLAY1"
SECONDARY = r"\\.\DISPLAY2"

# One 2560x1392 primary. Tests that need a different display topology build
# their own Monitor list rather than mutating this.
MONITORS = [Monitor(device=PRIMARY, primary=True, work=[0, 0, 2560, 1392], dpi=96)]


def make_window(rect, tabs, state="normal", monitor=PRIMARY, dpi=96) -> Window:
    return Window(monitor=monitor, rect=rect, state=state, dpi=dpi, tabs=tabs)


def make_layout(windows, monitors=None, saved_ts="t") -> Layout:
    return Layout(
        version=LAYOUT_VERSION,
        saved_ts=saved_ts,
        monitors=list(MONITORS if monitors is None else monitors),
        windows=windows,
    )
