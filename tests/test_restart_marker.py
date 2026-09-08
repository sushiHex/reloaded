"""The marker file that tells a tab's own shell to relaunch instead of exiting."""
from __future__ import annotations

import pathlib

import pytest

from reloaded.paths import norm, restart_marker, restart_marker_dir


@pytest.fixture(autouse=True)
def _state_dir_in_tmp(monkeypatch, tmp_path):
    """restart_marker writes under the real ~/.reloaded otherwise."""
    monkeypatch.setattr("reloaded.paths.state_dir", lambda: tmp_path)


def test_marker_is_stable_for_the_same_cwd():
    a = restart_marker(r"C:\repos\computers")
    b = restart_marker(r"C:\repos\computers")
    assert a == b


def test_marker_differs_between_repos():
    assert restart_marker(r"C:\repos\computers") != restart_marker(r"C:\repos\reloaded")


def test_marker_ignores_case_and_separator_like_norm():
    """The cwd reaching `restart` comes from a user argument; the cwd the shell
    interpolates came from the layout. They spell the same directory
    differently, and a marker only works if both spellings agree on it."""
    assert norm(r"C:\repos\Computers") == norm(r"c:/repos/computers")
    assert restart_marker(r"C:\repos\Computers") == restart_marker(r"c:/repos/computers")


def test_marker_lives_under_the_state_dir(tmp_path):
    m = restart_marker(r"C:\repos\computers")
    assert isinstance(m, pathlib.Path)
    assert tmp_path in m.parents


def test_markers_share_one_directory_so_a_sweep_can_find_them():
    """`restart` never deletes a marker it wrote - deleting eagerly would race
    the shell's Test-Path - so stale ones are swept later by directory."""
    a = restart_marker(r"C:\repos\computers")
    b = restart_marker(r"C:\repos\reloaded")
    assert a.parent == b.parent == restart_marker_dir()


def test_the_sweep_directory_is_named_rather_than_derived_from_a_dummy_cwd():
    """The sweep needs the directory, not a marker in it. Reaching it via
    restart_marker("x").parent works but reads as though "x" meant something."""
    assert restart_marker_dir() == restart_marker(r"C:\anything").parent


def test_marker_filename_survives_a_path_with_separators_and_spaces():
    """A raw cwd cannot be a filename. Whatever the encoding, it must produce
    one path component - a marker nested inside a directory that does not
    exist would never be written."""
    m = restart_marker(r"C:\my repos\a b\c")
    assert m.name
    assert "\\" not in m.name and "/" not in m.name
