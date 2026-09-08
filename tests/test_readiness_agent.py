"""A layout waits for the binaries it actually needs.

wait_for_ready blocked unconditionally on `claude` being on PATH, so a layout
made only of Codex tabs would have waited out its whole timeout for a binary it
never uses - then reported that as the reason it was not ready.
"""
from __future__ import annotations

from conftest import make_layout, make_window
from reloaded.layout import Tab
import reloaded.readiness as readiness_mod


def _layout(*kinds):
    tabs = [Tab(cwd=f"c:/repos/{k}{i}", title=f"{k}{i}", agent=k)
            for i, k in enumerate(kinds)]
    return make_layout([make_window([0, 0, 100, 100], tabs)])


def test_a_claude_only_layout_needs_claude():
    assert readiness_mod.required_binaries(_layout("claude")) == ["claude"]


def test_a_codex_only_layout_does_not_wait_for_claude():
    """The bug this fixes."""
    assert readiness_mod.required_binaries(_layout("codex")) == ["codex"]


def test_a_mixed_layout_needs_both():
    assert readiness_mod.required_binaries(_layout("claude", "codex")) == \
        ["claude", "codex"]


def test_an_empty_layout_needs_nothing():
    assert readiness_mod.required_binaries(make_layout([])) == []


def test_a_binary_is_listed_once_however_many_tabs_use_it():
    assert readiness_mod.required_binaries(_layout("codex", "codex")) == ["codex"]


def test_an_unknown_kind_is_treated_as_claude():
    """Matching the layout's own back-compat rule."""
    assert readiness_mod.required_binaries(_layout("gemini")) == ["claude"]


def test_readiness_reports_the_binary_that_is_missing(monkeypatch):
    """Naming `claude` when the layout is all Codex sent people looking in the
    wrong place."""
    monkeypatch.setattr(readiness_mod.shutil, "which",
                        lambda name: r"C:\wt.exe" if name == "wt" else None)
    monkeypatch.setattr(readiness_mod, "_persisted_path_dirs", lambda: [])

    ready, reason = readiness_mod.wait_for_ready(_layout("codex"), timeout=0.01,
                                                 poll=0.01)

    assert ready is False
    assert "codex" in reason
    assert "claude" not in reason


def test_a_binary_on_the_persisted_path_counts_as_present(monkeypatch):
    """Found for real: `codex` was on the user PATH but absent from this
    process's inherited copy, because the installer updated PATH after these
    processes started. The tabs this launches get a fresh environment and would
    resolve it fine, so blocking on the stale copy costs a 120s wait at every
    logon for a binary that is installed.
    """
    monkeypatch.setattr(readiness_mod.shutil, "which",
                        lambda name: r"C:\wt.exe" if name == "wt" else None)
    monkeypatch.setattr(readiness_mod, "_persisted_path_dirs",
                        lambda: [r"C:\Users\k\AppData\Local\Programs\OpenAI\Codex\bin"])
    monkeypatch.setattr(readiness_mod.os.path, "isfile",
                        lambda p: p.lower().endswith("codex.exe"))
    monkeypatch.setattr(readiness_mod, "_missing_monitors", lambda lo: [])

    ready, _reason = readiness_mod.wait_for_ready(_layout("codex"), timeout=0.01,
                                                  poll=0.01)

    assert ready is True


def test_a_binary_on_neither_path_is_still_missing(monkeypatch):
    monkeypatch.setattr(readiness_mod.shutil, "which",
                        lambda name: r"C:\wt.exe" if name == "wt" else None)
    monkeypatch.setattr(readiness_mod, "_persisted_path_dirs", lambda: [])

    ready, reason = readiness_mod.wait_for_ready(_layout("codex"), timeout=0.01,
                                                 poll=0.01)

    assert ready is False
    assert "codex" in reason


def test_readiness_passes_when_the_needed_binary_is_present(monkeypatch):
    monkeypatch.setattr(readiness_mod.shutil, "which", lambda name: r"C:\x.exe")
    monkeypatch.setattr(readiness_mod, "_missing_monitors", lambda lo: [])

    ready, _reason = readiness_mod.wait_for_ready(_layout("codex"), timeout=0.01,
                                                 poll=0.01)

    assert ready is True
