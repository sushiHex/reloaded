"""A tab remembers which agent it ran and how it was launched.

The compatibility requirement runs through all of this: a layout written
before a second kind existed carries neither field, and must keep working with
no migration and no rewrite.
"""
from __future__ import annotations

import json

from conftest import make_layout, make_window
from reloaded.layout import Tab, load, save


def _write(path, tab_json):
    path.write_text(json.dumps({
        "version": 1,
        "saved_ts": "t",
        "monitors": [],
        "windows": [{
            "monitor": r"\\.\DISPLAY1", "rect": [0, 0, 100, 100],
            "state": "normal", "dpi": 96, "inset": [0, 0, 0, 0],
            "tabs": [tab_json],
        }],
    }), encoding="utf-8")


def test_a_tab_defaults_to_claude():
    assert Tab(cwd="c:/a", title="a").agent == "claude"


def test_a_tab_defaults_to_no_captured_command():
    assert Tab(cwd="c:/a", title="a").command == ""


def test_the_fields_round_trip_through_a_saved_layout(tmp_path):
    path = tmp_path / "l.json"
    save(make_layout([make_window([0, 0, 100, 100], [
        Tab(cwd=r"C:\repos\beta", title="beta", agent="codex",
            command="codex resume --last"),
    ])]), path)

    tab = load(path).windows[0].tabs[0]

    assert tab.agent == "codex"
    assert tab.command == "codex resume --last"


def test_a_layout_written_before_kinds_existed_still_loads(tmp_path):
    path = tmp_path / "old.json"
    _write(path, {"cwd": r"C:\repos\alpha", "title": "alpha"})

    tab = load(path).windows[0].tabs[0]

    assert tab.agent == "claude"
    assert tab.command == ""


def test_an_explicit_null_reads_as_the_default(tmp_path):
    """A key present but null would otherwise defeat the dataclass default
    and put None where a string is expected."""
    path = tmp_path / "null.json"
    _write(path, {"cwd": r"C:\repos\alpha", "title": "alpha",
                  "agent": None, "command": None})

    tab = load(path).windows[0].tabs[0]

    assert tab.agent == "claude"
    assert tab.command == ""


def test_the_existing_flags_are_untouched():
    """agent and command are additions, not replacements."""
    tab = Tab(cwd="c:/a", title="a", low_confidence=True, pinned=True,
              agent="codex")

    assert (tab.low_confidence, tab.pinned, tab.agent) == (True, True, "codex")
