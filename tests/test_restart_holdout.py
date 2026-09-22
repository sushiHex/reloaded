"""What a named restart tells you about a session that never exited.

Its marker is left armed on purpose - "did not exit in 20s" is not "will never
exit" - so the report is about that marker: how much of it is left, read off the
file, rather than the TTL quoted as though none had passed.
"""
from __future__ import annotations

import os
import time

import reloaded.__main__ as main_mod
import reloaded.deploy as deploy_mod

CWD = r"C:\repos\app"


def _holdout(launcher=main_mod.discover_mod.RELOADED):
    return main_mod._Restarting(
        hwnd=1, item=object(), cwd=CWD, pid=111, launcher=launcher,
        agent="claude", command="", size_bytes=0)


def test_a_holdout_is_told_what_is_really_left(capsys):
    main_mod.restart_marker(CWD).write_text("restart", encoding="utf-8")

    main_mod._report_never_exited(_holdout())

    out = capsys.readouterr().out
    assert "still armed" in out
    assert "2 min" not in out, "it is quoting the TTL as a constant"


def test_an_expired_marker_is_not_described_as_armed(capsys):
    path = main_mod.restart_marker(CWD)
    path.write_text("restart", encoding="utf-8")
    stale = time.time() - deploy_mod.RESTART_MARKER_TTL_SECONDS - 1
    os.utime(path, (stale, stale))

    main_mod._report_never_exited(_holdout())

    out = capsys.readouterr().out
    assert "still armed" not in out
    assert "closes the tab" in out, "it never says what quitting now does"


def test_a_missing_marker_is_not_described_as_armed(capsys):
    main_mod._report_never_exited(_holdout())

    assert "still armed" not in capsys.readouterr().out


def test_a_hand_started_holdout_is_promised_nothing(capsys):
    """It is armed with nothing by design, so there is no window to quote."""
    main_mod.restart_marker(CWD).write_text("restart", encoding="utf-8")

    main_mod._report_never_exited(_holdout(main_mod.discover_mod.HAND))

    assert "armed" not in capsys.readouterr().out
