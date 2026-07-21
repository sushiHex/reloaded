from __future__ import annotations

from conftest import make_layout as _lo, make_window as _win

import reloaded.editor as editor_mod
from reloaded.layout import Tab
from reloaded.tabs import UIAUnavailable


def test_recapture_reports_a_missing_uiautomation_and_keeps_the_current_layout(monkeypatch, tmp_path, capsys):
    """The regression this guards: the editor's `c` (recapture) command let
    UIAUnavailable propagate straight out of the interactive loop and crash
    it, instead of reporting the problem and letting the user keep editing."""
    lo = _lo([_win([0, 0, 800, 600], [Tab(cwd=r"C:\repos\demo-app", title="demo-app")])])

    def raises(*a, **k):
        raise UIAUnavailable("uiautomation is not installed")

    monkeypatch.setattr(editor_mod.capture_mod, "capture_live", raises)

    commands = iter(["c", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(commands))

    rc = editor_mod.run(lo, tmp_path / "default.json", r"C:\repos")

    assert rc == 1  # quit without saving, since nothing changed
    out = capsys.readouterr().out
    assert "uiautomation is not installed" in out
    assert "pip install uiautomation" in out
