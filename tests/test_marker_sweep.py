"""_sweep_stale_markers — housekeeping for markers that never reached a tab.

`restart` deliberately never deletes a marker it just wrote: the shell reads it
moments after its session exits, and deleting eagerly is a race the writer
loses. deploy's TTL already makes an old marker inert, so this only stops the
directory growing. It must never touch a marker that could still be read.
"""
from __future__ import annotations

import os
import time

import pytest

import reloaded.__main__ as main_mod
from reloaded.deploy import RESTART_MARKER_TTL_SECONDS


@pytest.fixture
def markers(monkeypatch, tmp_path):
    d = tmp_path / "restart"
    d.mkdir()
    monkeypatch.setattr(main_mod, "restart_marker_dir", lambda: d)

    def write(name: str, age_seconds: float):
        p = d / name
        p.write_text("restart", encoding="utf-8")
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(p, (old, old))
        return p

    return d, write


def test_a_marker_just_written_is_left_alone(markers):
    """The one that matters. Swept, a restart would silently not happen."""
    _d, write = markers
    fresh = write("fresh.marker", 0)

    main_mod._sweep_stale_markers()

    assert fresh.exists()


def test_a_marker_past_the_ttl_is_removed(markers):
    _d, write = markers
    old = write("old.marker", RESTART_MARKER_TTL_SECONDS + 60)

    main_mod._sweep_stale_markers()

    assert not old.exists()


def test_a_marker_just_inside_the_ttl_survives(markers):
    """The sweep must agree with the shell about what is still good, or one
    would obey a marker the other had already thrown away."""
    _d, write = markers
    edge = write("edge.marker", RESTART_MARKER_TTL_SECONDS - 30)

    main_mod._sweep_stale_markers()

    assert edge.exists()


def test_the_sweep_ignores_files_that_are_not_markers(markers):
    _d, write = markers
    other = write("notes.txt", RESTART_MARKER_TTL_SECONDS + 60)

    main_mod._sweep_stale_markers()

    assert other.exists()


def test_the_sweep_survives_a_missing_directory(monkeypatch, tmp_path):
    """First run on a fresh install, or the directory removed underneath us."""
    monkeypatch.setattr(main_mod, "restart_marker_dir", lambda: tmp_path / "gone")

    main_mod._sweep_stale_markers()  # must not raise
