"""Which file extensions count as "this binary is installed".

The persisted-PATH check exists because a long-lived process keeps the PATH it
started with, so an agent installed after it started reads as missing. The same
staleness applies to PATHEXT, and the old fallback made it worse: absent
PATHEXT meant `.EXE` only, which is wrong for exactly the binaries this checks.
An npm-installed `claude` is a `claude.cmd` shim. A readiness check that misses
it makes the logon deploy wait out its whole timeout for something that is
sitting right there.
"""
from __future__ import annotations

import os

import pytest

import reloaded.readiness as readiness_mod


@pytest.fixture
def path_dirs(monkeypatch, tmp_path):
    """A persisted PATH holding one directory, and nothing on the real one."""
    monkeypatch.setattr(readiness_mod.shutil, "which", lambda b: None)
    monkeypatch.setattr(readiness_mod, "_persisted_path_dirs",
                        lambda: [str(tmp_path)])
    return tmp_path


def test_an_exe_is_found(path_dirs):
    (path_dirs / "codex.exe").write_text("", encoding="utf-8")

    assert readiness_mod._on_path("codex")


def test_a_cmd_shim_is_found_with_no_pathext_at_all(path_dirs, monkeypatch):
    """The regression: `.EXE` was the entire fallback, so an npm shim read as
    not installed."""
    monkeypatch.delenv("PATHEXT", raising=False)
    (path_dirs / "claude.cmd").write_text("", encoding="utf-8")

    assert readiness_mod._on_path("claude")


def test_a_powershell_shim_is_found(path_dirs, monkeypatch):
    monkeypatch.delenv("PATHEXT", raising=False)
    (path_dirs / "claude.ps1").write_text("", encoding="utf-8")

    assert readiness_mod._on_path("claude")


def test_a_stale_pathext_does_not_hide_a_binary(path_dirs, monkeypatch):
    """This process's PATHEXT is exactly as old as its PATH. A shorter one
    than the machine now has must not shrink the search."""
    monkeypatch.setenv("PATHEXT", ".COM")
    (path_dirs / "codex.cmd").write_text("", encoding="utf-8")

    assert readiness_mod._on_path("codex")


def test_an_extension_only_this_machine_knows_is_honoured(path_dirs, monkeypatch):
    """The environment adds to the defaults rather than being ignored."""
    monkeypatch.setenv("PATHEXT", os.pathsep.join([".EXE", ".WSF"]))
    (path_dirs / "codex.wsf").write_text("", encoding="utf-8")

    assert readiness_mod._on_path("codex")


def test_a_binary_that_is_not_there_is_still_not_there(path_dirs):
    assert not readiness_mod._on_path("codex")
