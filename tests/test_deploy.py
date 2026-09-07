from __future__ import annotations

import pytest

import reloaded.deploy as deploy_mod
from reloaded.deploy import (
    CHILD_SESSION_CLEAR,
    CLAUDE_STARTED_AT,
    CLOSE_TAB_IF_STARTED,
    RESUME_SUPPRESSOR,
    LaunchResult,
    PlanEntry,
    _pick_window,
    execute,
    launch_window,
    launcher_command,
    plan_deploy,
    shell_executable,
    wt_argv,
    wt_argv_single_tab,
)
from conftest import MONITORS, make_layout as _layout, make_window as _window

from reloaded.layout import LAYOUT_VERSION, Layout, Monitor, Tab, Window
from reloaded.paths import norm


@pytest.fixture(autouse=True)
def _repos_exist_by_default(monkeypatch):
    """Every fixture in this file uses fabricated paths like C:\\repos\\a that
    are never real directories. Without this, plan_deploy's missing-repo check
    would reclassify every tab in every other test as "missing" instead of
    "to_launch". Tests for the missing-repo behavior itself override this."""
    monkeypatch.setattr(deploy_mod.os.path, "isdir", lambda p: True)


@pytest.fixture(autouse=True)
def _pin_shell_to_pwsh(monkeypatch):
    """shell_executable() depends on whether pwsh is actually installed on
    the machine running the tests — pin it so test output doesn't vary by
    environment. The fallback logic itself is tested separately, unpinned."""
    monkeypatch.setattr(deploy_mod, "shell_executable", lambda: "pwsh")


def test_launcher_sets_the_resume_suppressor_before_launching():
    cmd = launcher_command(r"C:\repos\editor-app", 0, 1024)
    assert RESUME_SUPPRESSOR in cmd
    assert cmd.index(RESUME_SUPPRESSOR) < cmd.index("claude ")


def test_launcher_clears_the_inherited_child_session_marker():
    """`up`/`restart` are normally run from inside a Claude Code session, which
    marks the shells it spawns with CLAUDE_CODE_CHILD_SESSION. Inherited, it
    makes the relaunched session disable transcript saving - healthy-looking
    tabs that quietly record nothing, so the next --continue finds nothing."""
    cmd = launcher_command(r"C:\repos\editor-app", 0, 1024)
    assert CHILD_SESSION_CLEAR in cmd
    assert cmd.index(CHILD_SESSION_CLEAR) < cmd.index("claude ")


def test_launcher_closes_its_tab_once_the_session_ends():
    """The tab hosts exactly one session, so it should not outlive it at a bare
    shell prompt - that is what stranded tabs in windows kept open by a single
    holdout."""
    cmd = launcher_command(r"C:\repos\editor-app", 0, 1024)
    assert cmd.index(CLAUDE_STARTED_AT) < cmd.index("claude ")
    assert cmd.index(CLOSE_TAB_IF_STARTED) > cmd.index("claude ")


def test_launcher_keeps_a_failed_launch_on_screen():
    """The self-close is gated on elapsed time so a claude that dies instantly
    leaves its tab (and its error) visible, which is why -NoExit exists."""
    cmd = launcher_command(r"C:\repos\editor-app", 0, 1024)
    assert f"-gt {deploy_mod.STARTUP_GRACE_SECONDS}" in cmd
    assert deploy_mod.STARTUP_GRACE_SECONDS > 0


def test_launcher_self_close_survives_wt_argument_escaping():
    """wt treats ';' inside an argument as a subcommand separator, so an
    unescaped statement would be torn into extra tabs rather than run."""
    cmd = launcher_command(r"C:\repos\editor-app", 0, 1024)
    escaped = wt_argv_single_tab(r"C:\repos\editor-app", 1024)[-1]
    assert "{ exit }" in escaped
    unescaped = [
        i for i, ch in enumerate(escaped) if ch == ";" and (i == 0 or escaped[i - 1] != "\\")
    ]
    assert unescaped == []
    assert cmd.count(";") == escaped.count("\\;")


def test_launcher_uses_continue_and_never_a_fresh_session():
    cmd = launcher_command(r"C:\repos\editor-app", 0, 1024)
    assert "claude --dangerously-skip-permissions --continue" in cmd
    assert "--resume" not in cmd


def test_launcher_never_disables_auto_compact():
    cmd = launcher_command(r"C:\repos\editor-app", 0, 1024)
    assert "DISABLE_AUTO_COMPACT" not in cmd


def test_launcher_includes_a_sleep_only_when_delay_is_positive():
    assert "Start-Sleep" not in launcher_command(r"C:\repos\a", 0, 10)
    assert "Start-Sleep 8" in launcher_command(r"C:\repos\a", 8, 10)


def test_launcher_warns_on_an_oversized_transcript_but_still_continues():
    big = 483 * 1024 * 1024
    cmd = launcher_command(r"C:\repos\editor-app", 0, big)
    assert "483 MB" in cmd
    assert "claude --dangerously-skip-permissions --continue" in cmd


def test_launcher_escapes_single_quotes_in_the_banner():
    cmd = launcher_command(r"C:\repos\it's", 0, 10)
    # A raw apostrophe would terminate the PowerShell single-quoted string.
    assert "it''s" in cmd


def test_wt_argv_places_the_window_and_orders_tabs():
    tabs = [Tab(cwd=r"C:\repos\demo-app", title="demo-app"), Tab(cwd=r"C:\repos\lb", title="lb")]
    argv = wt_argv([221, 228, 1168, 624], tabs, [0, 4], [10, 10])
    assert argv[:4] == ["wt", "-w", "-1", "--pos=221,228"]
    assert argv.count("new-tab") == 2
    assert argv.count(";") == 1
    first = argv.index("new-tab")
    assert argv[first + 1:first + 4] == [
        "--title", "demo-app", "--suppressApplicationTitle"]
    assert argv[first + 4] == "-d"
    assert argv[first + 5] == r"C:\repos\demo-app"
    # Second tab's directory comes after the separator, preserving order.
    sep = argv.index(";")
    assert r"C:\repos\lb" in argv[sep:]


def test_wt_argv_golden_string_for_a_single_tab():
    tabs = [Tab(cwd=r"C:\repos\demo-app", title="demo-app")]
    argv = wt_argv([10, 20, 800, 600], tabs, [0], [1024])
    assert argv[:11] == [
        "wt", "-w", "-1", "--pos=10,20",
        "new-tab",
        # Named at launch. A tab reloaded did not name shows the running
        # program - `claude` - which matches no repo and no recorded title, so
        # the tab it just opened is one it cannot find again.
        "--title", "demo-app", "--suppressApplicationTitle",
        "-d", r"C:\repos\demo-app", "pwsh",
    ]
    assert argv[11:13] == ["-NoExit", "-Command"]
    assert len(argv) == 14


def test_wt_argv_uses_the_equals_form_for_pos_so_a_negative_x_is_not_misparsed():
    """A monitor left of the primary has negative virtual-desktop coordinates.
    `--pos -1920,0` would read as two options to wt's parser; `--pos=-1920,0`
    cannot."""
    tabs = [Tab(cwd=r"C:\repos\demo-app", title="demo-app")]
    argv = wt_argv([-1920, 0, 800, 600], tabs, [0], [0])
    assert "--pos=-1920,0" in argv
    assert "--pos" not in argv, "must not appear as a separate token"


def test_plan_skips_tabs_whose_session_is_already_running():
    tabs = [Tab(cwd=r"C:\repos\demo-app", title="demo-app"), Tab(cwd=r"C:\repos\lb", title="lb")]
    lo = _layout([_window([0, 0, 800, 600], tabs)])
    live = {norm(r"C:\repos\demo-app"): 42}
    plan = plan_deploy(lo, live, MONITORS, {})
    assert len(plan) == 1
    assert [t.title for t in plan[0].tabs] == ["lb"]
    assert [t.title for t in plan[0].skipped] == ["demo-app"]


def test_plan_omits_a_window_entirely_when_every_tab_is_live():
    tabs = [Tab(cwd=r"C:\repos\demo-app", title="demo-app")]
    lo = _layout([_window([0, 0, 800, 600], tabs)])
    plan = plan_deploy(lo, {norm(r"C:\repos\demo-app"): 1}, MONITORS, {})
    assert plan == []


def test_plan_staggers_delays_globally_across_windows():
    lo = _layout([
        _window([0, 0, 800, 600], [
            Tab(cwd=r"C:\repos\a", title="a"), Tab(cwd=r"C:\repos\b", title="b")]),
        _window([900, 0, 800, 600], [Tab(cwd=r"C:\repos\c", title="c")]),
    ])
    plan = plan_deploy(lo, {}, MONITORS, {})
    assert plan[0].delays == [0, 4]
    assert plan[1].delays == [8], "the index must continue across windows, not reset"


def test_plan_skips_a_tab_whose_directory_no_longer_exists(monkeypatch):
    """readiness.wait_for_ready deliberately never blocks on a missing repo
    (a pinned tab for a deleted repo would otherwise delay every boot by
    120s), so plan_deploy is the actual place that must not try to launch it."""
    monkeypatch.setattr(deploy_mod.os.path, "isdir", lambda p: p != r"C:\repos\gone")
    tabs = [Tab(cwd=r"C:\repos\demo-app", title="demo-app"), Tab(cwd=r"C:\repos\gone", title="gone")]
    lo = _layout([_window([0, 0, 800, 600], tabs)])
    plan = plan_deploy(lo, {}, MONITORS, {})
    assert [t.title for t in plan[0].tabs] == ["demo-app"]
    assert [t.title for t in plan[0].missing] == ["gone"]


def test_plan_omits_a_window_entirely_when_its_only_tab_is_missing(monkeypatch):
    monkeypatch.setattr(deploy_mod.os.path, "isdir", lambda p: False)
    lo = _layout([_window([0, 0, 800, 600], [Tab(cwd=r"C:\repos\gone", title="gone")])])
    assert plan_deploy(lo, {}, MONITORS, {}) == []


def test_plan_clamps_a_rect_whose_monitor_disappeared():
    lo = Layout(
        version=LAYOUT_VERSION, saved_ts="t",
        monitors=[Monitor(device=r"\\.\DISPLAY2", primary=False, work=[2560, 0, 5120, 1392], dpi=96)],
        windows=[Window(monitor=r"\\.\DISPLAY2", rect=[2578, 25, 1168, 624],
                        state="normal", dpi=96,
                        tabs=[Tab(cwd=r"C:\repos\a", title="a")])],
    )
    plan = plan_deploy(lo, {}, MONITORS, {})  # only DISPLAY1 available now
    x, y, w, h = plan[0].rect
    assert x + w <= 2560, "must be pulled onto the surviving monitor"


def test_wt_argv_escapes_semicolons_inside_the_command():
    """wt splits on `;` inside arguments, so an unescaped launcher becomes one
    tab per statement. Observed live: a 2-tab window opened with 7 tabs."""
    tabs = [Tab(cwd=r"C:\repos\demo-app", title="demo-app")]
    argv = wt_argv([0, 0, 800, 600], tabs, [4], [10])
    command = argv[-1]
    assert ";" in command, "sanity: the command really does contain separators"
    # Every semicolon in the command must be backslash-escaped.
    for i, ch in enumerate(command):
        if ch == ";":
            assert command[i - 1] == "\\", f"unescaped semicolon at {i}: {command!r}"


def test_wt_argv_keeps_the_structural_separator_unescaped():
    tabs = [Tab(cwd=r"C:\repos\a", title="a"), Tab(cwd=r"C:\repos\b", title="b")]
    argv = wt_argv([0, 0, 800, 600], tabs, [0, 4], [10, 10])
    assert ";" in argv, "the between-tabs separator must remain a bare ';' element"
    assert argv.count(";") == 1


def test_wt_argv_escapes_a_semicolon_in_a_repo_path():
    tabs = [Tab(cwd=r"C:\repos\weird;name", title="weird")]
    argv = wt_argv([0, 0, 800, 600], tabs, [0], [10])
    assert r"C:\repos\weird\;name" in argv


def test_single_tab_argv_targets_the_current_window_and_escapes():
    """`launch` builds its argv through the same primitive as `up`, so escaping
    cannot be forgotten at one call site and applied at the other."""
    argv = wt_argv_single_tab(r"C:\repos\demo-app", 0)
    assert argv[:3] == ["wt", "-w", "0"], "must target the current window"
    assert argv[3] == "new-tab"
    command = argv[-1]
    for i, ch in enumerate(command):
        if ch == ";":
            assert command[i - 1] == "\\", f"unescaped semicolon: {command!r}"


def test_single_tab_argv_shares_the_new_tab_shape_with_multi_tab():
    single = wt_argv_single_tab(r"C:\repos\demo-app", 0)
    multi = wt_argv([0, 0, 800, 600], [Tab(cwd=r"C:\repos\demo-app", title="demo-app")], [0], [0])
    # Everything from `new-tab` onward is produced by the same primitive.
    assert single[3:] == multi[4:]


def test_plan_entries_carry_a_position_derived_window_id():
    lo = _layout([
        _window([0, 0, 800, 600], [Tab(cwd=r"C:\repos\a", title="a")]),
        _window([900, 0, 800, 600], [Tab(cwd=r"C:\repos\b", title="b")]),
    ])
    plan = plan_deploy(lo, {}, MONITORS, {})
    assert [e.id for e in plan] == ["w1", "w2"]


def test_pick_window_returns_the_sole_candidate_without_checking_titles():
    calls = []
    assert _pick_window({42}, {"demo-app"}, lambda h: calls.append(h) or []) == 42
    assert calls == [], "must not query titles when there is nothing to disambiguate"


def test_pick_window_returns_none_when_there_are_no_candidates():
    assert _pick_window(set(), {"demo-app"}, lambda h: []) is None


def test_pick_window_picks_the_candidate_whose_tabs_match_best():
    titles = {101: ["Windows PowerShell"], 202: ["demo-app", "sample-bot"]}
    assert _pick_window({101, 202}, {"demo-app"}, lambda h: titles[h]) == 202


def test_pick_window_treats_a_title_lookup_failure_as_zero_score():
    def get_titles(h):
        if h == 101:
            raise RuntimeError("window closed mid-check")
        return titles[h]

    titles = {202: ["demo-app"]}
    assert _pick_window({101, 202}, {"demo-app"}, get_titles) == 202


def test_pick_window_falls_back_to_lowest_hwnd_when_nothing_matches():
    assert _pick_window({202, 101}, {"demo-app"}, lambda h: []) == 101


def test_launch_window_returns_none_instead_of_raising_when_popen_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy_mod.win32, "list_wt_windows", lambda: [])

    def raising_popen(*a, **k):
        raise OSError("wt.exe not found")

    monkeypatch.setattr(deploy_mod.subprocess, "Popen", raising_popen)
    tabs = [Tab(cwd=str(tmp_path), title="t")]
    assert launch_window(["wt"], tabs, timeout=1) is None


def test_shell_executable_prefers_pwsh_when_installed(monkeypatch):
    shell_executable.cache_clear()
    monkeypatch.setattr(deploy_mod.shutil, "which", lambda name: r"C:\pwsh.exe" if name == "pwsh" else None)
    assert shell_executable() == "pwsh"
    shell_executable.cache_clear()


def test_shell_executable_falls_back_to_windows_powershell_without_pwsh(monkeypatch):
    """The regression this guards: pwsh is a separate install most machines
    don't have. Assuming it exists silently kills every launched tab -- the
    shell fails to start, but wt itself opened fine, so `up` still reports
    success."""
    shell_executable.cache_clear()
    monkeypatch.setattr(deploy_mod.shutil, "which", lambda name: None)
    assert shell_executable() == "powershell"
    shell_executable.cache_clear()


def test_new_tab_args_uses_whatever_shell_executable_resolves_to(monkeypatch):
    monkeypatch.setattr(deploy_mod, "shell_executable", lambda: "powershell")
    args = deploy_mod.new_tab_args(r"C:\repos\demo-app", "Write-Host hi")
    assert "powershell" in args
    assert "pwsh" not in args


def test_plan_passes_transcript_size_through_to_the_banner():
    big = 676 * 1024 * 1024
    lo = _layout([_window([0, 0, 800, 600], [Tab(cwd=r"C:\repos\demo-app", title="demo-app")])])
    plan = plan_deploy(lo, {}, MONITORS, {norm(r"C:\repos\demo-app"): big})
    assert "676 MB" in " ".join(plan[0].argv)


def _entry(id_="w1", rect=None, state="normal"):
    return PlanEntry(
        id=id_, state=state, rect=rect or [0, 0, 100, 100],
        tabs=[], skipped=[], missing=[], delays=[], argv=["wt"],
    )


def test_execute_batches_the_settle_wait_once_not_per_window(monkeypatch):
    """N windows must pay GEOMETRY_SETTLE_SECONDS once total, not once each -
    the whole point of moving the wait out of win32.set_geometry."""
    hwnds = iter([100, 200, 300])
    monkeypatch.setattr(deploy_mod, "launch_window", lambda argv, tabs: next(hwnds))
    monkeypatch.setattr(deploy_mod.win32, "set_geometry", lambda hwnd, rect, state: True)
    monkeypatch.setattr(
        deploy_mod.win32, "verify_and_fix_geometry", lambda hwnd, rect, state: True
    )
    sleeps = []
    monkeypatch.setattr(deploy_mod.time, "sleep", lambda s: sleeps.append(s))

    results = execute([_entry("w1"), _entry("w2"), _entry("w3")])

    assert sleeps == [deploy_mod.GEOMETRY_SETTLE_SECONDS]  # one sleep total, not three
    assert [r.placed for r in results] == [True, True, True]


def test_execute_verifies_each_placed_window_with_its_own_target_after_the_wait(monkeypatch):
    monkeypatch.setattr(deploy_mod, "launch_window", lambda argv, tabs: 100)
    monkeypatch.setattr(deploy_mod.win32, "set_geometry", lambda hwnd, rect, state: True)
    verify_calls = []
    monkeypatch.setattr(
        deploy_mod.win32,
        "verify_and_fix_geometry",
        lambda hwnd, rect, state: verify_calls.append((hwnd, rect, state)) or True,
    )
    monkeypatch.setattr(deploy_mod.time, "sleep", lambda s: None)

    entry = _entry("w1", rect=[1, 2, 3, 4], state="maximized")
    results = execute([entry])

    assert verify_calls == [(100, [1, 2, 3, 4], "maximized")]
    assert results[0].placed is True


def test_execute_skips_settle_and_verify_when_launch_fails(monkeypatch):
    monkeypatch.setattr(deploy_mod, "launch_window", lambda argv, tabs: None)

    def boom(*a, **k):
        raise AssertionError("must not sleep/verify/place when nothing launched")

    monkeypatch.setattr(deploy_mod.win32, "set_geometry", boom)
    monkeypatch.setattr(deploy_mod.win32, "verify_and_fix_geometry", boom)
    monkeypatch.setattr(deploy_mod.time, "sleep", boom)

    results = execute([_entry("w1")])
    assert results == [LaunchResult(window_id="w1", hwnd=None, placed=False)]


def test_execute_skips_settle_and_verify_when_initial_placement_fails(monkeypatch):
    monkeypatch.setattr(deploy_mod, "launch_window", lambda argv, tabs: 100)
    monkeypatch.setattr(deploy_mod.win32, "set_geometry", lambda hwnd, rect, state: False)

    def boom(*a, **k):
        raise AssertionError("must not sleep/verify a placement that never succeeded")

    monkeypatch.setattr(deploy_mod.win32, "verify_and_fix_geometry", boom)
    monkeypatch.setattr(deploy_mod.time, "sleep", boom)

    results = execute([_entry("w1")])
    assert results == [LaunchResult(window_id="w1", hwnd=100, placed=False)]


def test_execute_final_placed_reflects_the_verify_step_not_just_the_initial_apply(monkeypatch):
    """Even when the initial set_geometry succeeds, the reported placed
    status is whatever verify_and_fix_geometry ultimately determines - it
    may have had to correct drift, or found the window gone by then."""
    monkeypatch.setattr(deploy_mod, "launch_window", lambda argv, tabs: 100)
    monkeypatch.setattr(deploy_mod.win32, "set_geometry", lambda hwnd, rect, state: True)
    monkeypatch.setattr(
        deploy_mod.win32, "verify_and_fix_geometry", lambda hwnd, rect, state: False
    )
    monkeypatch.setattr(deploy_mod.time, "sleep", lambda s: None)

    results = execute([_entry("w1")])
    assert results[0].placed is False
