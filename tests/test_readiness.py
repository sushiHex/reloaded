from __future__ import annotations

import os
import subprocess

from reloaded.layout import LAYOUT_VERSION, Layout, Monitor, Tab, Window
from reloaded.readiness import check_wt_version, wait_for_ready, wt_version
import reloaded.readiness as readiness_mod

MONITOR = Monitor(device=r"\\.\DISPLAY1", primary=True, work=[0, 0, 2560, 1392], dpi=96)


def _layout(cwd: str) -> Layout:
    return Layout(
        version=LAYOUT_VERSION,
        saved_ts="t",
        monitors=[MONITOR],
        windows=[
            Window(
                monitor=MONITOR.device, rect=[0, 0, 800, 600], state="normal", dpi=96,
                tabs=[Tab(cwd=cwd, title="t")],
            )
        ],
    )


def _make_ready(monkeypatch, monitors=(MONITOR,)):
    monkeypatch.setattr(readiness_mod.shutil, "which", lambda name: r"C:\wt.exe")
    monkeypatch.setattr(readiness_mod.win32, "list_monitors", lambda: list(monitors))


def test_missing_repo_does_not_block_readiness(tmp_path, monkeypatch):
    """A repo directory that doesn't exist must not force the full timeout —
    a pinned tab for a deleted repo persists forever, so this would otherwise
    be a permanent 120s delay on every boot."""
    _make_ready(monkeypatch)
    lo = _layout(str(tmp_path / "does-not-exist"))
    ready, reason = wait_for_ready(lo, timeout=5, poll=0.1)
    assert ready is True
    assert "missing" in reason.lower()
    assert "does-not-exist" in reason


def test_missing_repo_is_reported_but_ready_when_it_exists(tmp_path, monkeypatch):
    _make_ready(monkeypatch)
    lo = _layout(str(tmp_path))
    ready, reason = wait_for_ready(lo, timeout=5, poll=0.1)
    assert ready is True
    assert reason == "ready"


def test_missing_monitor_still_blocks_readiness_until_timeout(tmp_path, monkeypatch):
    """Monitors, unlike repos, are expected to appear during boot — this must
    still be a real gate, not weakened by the repo fix."""
    _make_ready(monkeypatch, monitors=())  # the saved monitor never shows up
    lo = _layout(str(tmp_path))
    ready, reason = wait_for_ready(lo, timeout=0.3, poll=0.1)
    assert ready is False
    assert "monitor" in reason.lower()


def test_wt_not_on_path_blocks_readiness_until_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(readiness_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(readiness_mod.win32, "list_monitors", lambda: [MONITOR])
    lo = _layout(str(tmp_path))
    ready, reason = wait_for_ready(lo, timeout=0.3, poll=0.1)
    assert ready is False
    assert "wt.exe" in reason


def test_claude_not_on_path_blocks_readiness_until_timeout(tmp_path, monkeypatch):
    """A tab that opens but can never run `claude` is just as broken as one
    that can't open at all -- wait_for_ready must catch it the same way."""
    monkeypatch.setattr(
        readiness_mod.shutil, "which",
        lambda name: r"C:\wt.exe" if name == "wt" else None,
    )
    monkeypatch.setattr(readiness_mod.win32, "list_monitors", lambda: [MONITOR])
    lo = _layout(str(tmp_path))
    ready, reason = wait_for_ready(lo, timeout=0.3, poll=0.1)
    assert ready is False
    assert "claude" in reason


def _mock_appx_version(monkeypatch, stdout: str, returncode: int = 0):
    def fake_run(argv, capture_output=True, text=True, timeout=None):
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(readiness_mod.subprocess, "run", fake_run)


def test_wt_version_parses_the_appx_version_string(monkeypatch):
    _mock_appx_version(monkeypatch, "1.24.11911.0\n")
    assert wt_version() == (1, 24, 11911, 0)


def test_wt_version_returns_none_when_package_not_found(monkeypatch):
    _mock_appx_version(monkeypatch, "")  # Get-AppxPackage found nothing
    assert wt_version() is None


def test_wt_version_returns_none_on_powershell_failure(monkeypatch):
    def raises(*a, **k):
        raise OSError("powershell not found")

    monkeypatch.setattr(readiness_mod.subprocess, "run", raises)
    assert wt_version() is None


def test_check_wt_version_passes_at_the_verified_baseline(monkeypatch):
    _mock_appx_version(monkeypatch, "1.24.11911.0\n")
    ok, detail = check_wt_version()
    assert ok is True
    assert "1.24.11911.0" in detail


def test_check_wt_version_warns_below_the_baseline(monkeypatch):
    """This must warn, not silently pass -- the whole point is visibility
    into an untested failure mode (wt could error, or could route tabs into
    an existing window instead of opening a new one)."""
    _mock_appx_version(monkeypatch, "1.18.3231.0\n")
    ok, detail = check_wt_version()
    assert ok is False
    assert "1.18.3231.0" in detail


def test_check_wt_version_does_not_fail_when_version_is_undeterminable(monkeypatch):
    """Unable to determine != known to be too old -- must not be treated as
    a failure, or a machine with WT installed some other way (not via the
    Store/winget) would always warn incorrectly."""
    _mock_appx_version(monkeypatch, "")
    ok, detail = check_wt_version()
    assert ok is True
