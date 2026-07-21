from __future__ import annotations

import types

import reloaded.__main__ as main_mod
from reloaded.tabs import UIAUnavailable


def _args(**kw):
    defaults = {"layout": "default", "repos_root": r"C:\repos"}
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def test_cmd_capture_reports_a_missing_uiautomation_instead_of_a_traceback(tmp_path, monkeypatch, capsys):
    """The regression this guards: capture_live's UIAUnavailable propagated
    straight out of main() as a raw traceback on a fresh install without the
    uiautomation package -- psutil already degraded gracefully, this did not."""
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")

    def raises(*a, **k):
        raise UIAUnavailable("uiautomation is not installed")

    monkeypatch.setattr(main_mod.capture_mod, "capture_live", raises)
    rc = main_mod.cmd_capture(_args())
    assert rc == 1
    out = capsys.readouterr().out
    assert "uiautomation is not installed" in out
    assert "pip install uiautomation" in out


def test_cmd_edit_reports_a_missing_uiautomation_instead_of_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")

    def raises(*a, **k):
        raise UIAUnavailable("uiautomation is not installed")

    monkeypatch.setattr(main_mod.capture_mod, "capture_live", raises)
    rc = main_mod.cmd_edit(_args())
    assert rc == 1
    out = capsys.readouterr().out
    assert "pip install uiautomation" in out
