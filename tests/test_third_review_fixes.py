"""Four fixes from a third review pass, each pinned where it can be seen.

Grouped because they share a shape rather than a module: every one is a place
where this package answered a question from a stale or second-hand source when
a first-hand one was in reach.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
import reloaded.discover as discover_mod
import reloaded.teardown as teardown_mod
import reloaded.layout as layout_mod
import reloaded.tabs as tabs_mod
from conftest import make_layout, make_window
from reloaded.layout import Tab
from reloaded.paths import norm

CWD = r"C:\repos\app"


class _Item:
    pass


# ── the quit keys come from the target's own pid ─────────────────────────


def _plans(*targets):
    return [main_mod.teardown_mod.WindowPlan(
        hwnd=1, total_tabs=len(targets), targets=list(targets))]


def test_the_quit_kind_is_read_from_the_targets_own_process(monkeypatch):
    """`live_agents()` is a separate sweep of every process on the machine,
    keyed by directory. A target it misses falls back to Claude Code, and a
    Codex tab gets `/exit` typed into it. The pid is right there in the plan."""
    monkeypatch.setattr(discover_mod, "session_launch",
                        lambda pid: ("codex", "codex resume"))
    monkeypatch.setattr(discover_mod, "live_agents", lambda: {})

    kinds = teardown_mod.agent_kinds(_plans(
        main_mod.teardown_mod.Target("app", CWD, 111, _Item())))

    assert kinds == {norm(CWD): "codex"}


def test_the_sweep_is_the_fallback_for_a_pid_that_will_not_answer(monkeypatch):
    monkeypatch.setattr(discover_mod, "session_launch", lambda pid: ("", ""))
    monkeypatch.setattr(discover_mod, "live_agents",
                        lambda: {norm(CWD): "codex"})

    kinds = teardown_mod.agent_kinds(_plans(
        main_mod.teardown_mod.Target("app", CWD, 111, _Item())))

    assert kinds == {norm(CWD): "codex"}


def test_nothing_answering_falls_back_to_the_default_kind(monkeypatch):
    monkeypatch.setattr(discover_mod, "session_launch", lambda pid: ("", ""))
    monkeypatch.setattr(discover_mod, "live_agents", lambda: {})

    kinds = teardown_mod.agent_kinds(_plans(
        main_mod.teardown_mod.Target("app", CWD, 111, _Item())))

    assert kinds == {norm(CWD): main_mod.agents_mod.DEFAULT_KIND}


def test_the_sweep_is_not_run_when_every_pid_answers(monkeypatch):
    """It walks every process on the machine. Paying for that to answer a
    question the pids already answered is pure cost."""
    swept = []
    monkeypatch.setattr(discover_mod, "session_launch",
                        lambda pid: ("codex", "codex resume"))
    monkeypatch.setattr(discover_mod, "live_agents",
                        lambda: swept.append(1) or {})

    teardown_mod.agent_kinds(_plans(
        main_mod.teardown_mod.Target("app", CWD, 111, _Item())))

    assert swept == []


# ── the site that pass missed: a bare restart ────────────────────────────


def _codex_tab_plan():
    return main_mod.teardown_mod.WindowPlan(
        hwnd=1, total_tabs=1,
        targets=[main_mod.teardown_mod.Target("app", CWD, 222, _TabItem())],
    )


class _TabItem:
    def GetChildren(self):
        return []


def test_a_bare_restart_reads_the_quit_kind_from_the_targets_own_process(
    tmp_path, monkeypatch
):
    """`cmd_down` was given `_agent_kinds`; `cmd_restart` was not, and kept
    handing `execute_down` the `live_agents()` sweep directly.

    A Codex session the sweep misses is treated as Claude Code, so `/exit` and
    Enter are typed into a TUI that ignores them. The resend types them again,
    the pid never goes away, and twenty seconds later the session is reported
    as "did not exit in time" — neither quit nor restarted, with nothing on
    screen saying the wrong keys were sent.
    """
    sent = []
    monkeypatch.setattr(main_mod, "layout_path", lambda name: tmp_path / "default.json")
    monkeypatch.setattr(main_mod.capture_mod, "capture_live", lambda *a, **k: make_layout(
        [make_window([0, 0, 800, 600], [Tab(cwd=CWD, title="app")])]))
    monkeypatch.setattr(main_mod.layout_mod, "save", lambda layout, path: None)
    monkeypatch.setattr(main_mod.teardown_mod, "plan_down",
                        lambda repos_root, **kw: [_codex_tab_plan()])
    monkeypatch.setattr(main_mod, "_deploy_layout", lambda layout, args: 0)

    # The tab is Codex, and the machine-wide sweep does not know about it.
    monkeypatch.setattr(discover_mod, "session_launch", lambda pid: ("codex", "codex resume"))
    monkeypatch.setattr(discover_mod, "live_agents", lambda: {})

    _stub_the_desktop(monkeypatch, sent)

    main_mod.cmd_restart(types.SimpleNamespace(
        layout="default", repos_root=r"C:\repos", unattended=False,
        dry_run=False, repos=[]))

    assert sent == [("{Ctrl}c", "{Ctrl}c")]


def _stub_the_desktop(monkeypatch, sent):
    """Let the real execute_down run, with every desktop effect replaced.

    Stubbing `execute_down` itself would assert that a particular argument was
    passed, which is a claim about plumbing. What matters is which keystrokes
    reach the tab, so the keystroke sender is the only thing faked.
    """
    import psutil

    monkeypatch.setattr(main_mod.teardown_mod, "EXIT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(main_mod.teardown_mod, "EXIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(tabs_mod, "select_tab", lambda hwnd, item: True)
    monkeypatch.setattr(tabs_mod, "send_quit_keystrokes",
                        lambda keys, **kw: sent.append(tuple(keys)))
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    monkeypatch.setattr(main_mod.teardown_mod.win32, "close_window", lambda hwnd: True)


# ── an executable name reads the same in either case ─────────────────────


def test_an_uppercase_command_still_names_its_agent(monkeypatch):
    """Windows paths are case-insensitive, so argv[0] arrives in whatever case
    the launcher used. A case-sensitive miss does not give a wrong answer - it
    gives none, and the relaunch then comes from a default instead of from
    what was running."""
    class _Proc:
        def name(self):
            raise OSError("denied")

        def cmdline(self):
            return [r"C:\x\CODEX.EXE", "resume"]

    class _Fake:
        Process = staticmethod(lambda pid: _Proc())

    import sys
    monkeypatch.setitem(sys.modules, "psutil", _Fake)

    assert discover_mod.session_launch(7)[0] == "codex"


# ── a failed marker delete is not a shrug ────────────────────────────────


def test_a_marker_that_will_not_delete_stops_the_reopen(monkeypatch, tmp_path, capsys):
    """The tab opened by this path DOES carry the restart loop. A marker that
    survives outlives the tab that ignored it and fires on the user's next
    /exit, restarting a session they meant to close. The docstring says
    deleting it is necessary, so a failed delete has to be treated as that."""
    launched = []
    monkeypatch.setattr(main_mod, "_launch_single_tab", launched.append)

    class _Marker:
        def unlink(self, missing_ok=False):
            raise OSError("in use by another process")

        def __str__(self):
            return str(tmp_path / "restart.marker")

    monkeypatch.setattr(main_mod, "restart_marker", lambda cwd: _Marker())

    s = main_mod._Restarting(hwnd=1, item=_Item(), cwd=CWD, pid=111,
                             launcher=main_mod.discover_mod.RELOADED,
                             agent="claude", command="", size_bytes=0)
    ok = main_mod._reopen_in_a_new_tab(s)

    assert ok is False
    assert launched == [], "a tab was opened that will inherit the marker"
    out = capsys.readouterr().out
    assert "could not clear" in out
    assert "restart on your next /exit" in out


# ── two writers do not share one temp file ───────────────────────────────


def test_the_layout_temp_file_is_per_process(tmp_path):
    """The reconcile task saves every five minutes forever; capture, restart
    and the editor save on demand. Sharing one `default.json.tmp` meant two of
    them could write into the same handle and rename a half-written layout
    into place."""
    import os

    written = []
    path = tmp_path / "default.json"
    lo = make_layout([make_window([0, 0, 800, 600], [Tab(cwd=CWD, title="app")])])

    real_replace = layout_mod.pathlib.Path.replace

    def spy(self, target):
        written.append(self.name)
        return real_replace(self, target)

    layout_mod.pathlib.Path.replace = spy
    try:
        layout_mod.save(lo, path)
    finally:
        layout_mod.pathlib.Path.replace = real_replace

    assert written == [f"default.json.{os.getpid()}.tmp"]
    assert layout_mod.load(path).windows[0].tabs[0].cwd == CWD


def test_no_temp_file_is_left_behind(tmp_path):
    path = tmp_path / "default.json"
    lo = make_layout([make_window([0, 0, 800, 600], [Tab(cwd=CWD, title="app")])])

    layout_mod.save(lo, path)

    assert [p.name for p in tmp_path.iterdir()] == ["default.json"]


# ── the close button does not have to speak English ──────────────────────


class _Button:
    def __init__(self, name, invoked):
        self.ControlTypeName = "ButtonControl"
        self.Name = name
        self._invoked = invoked

    def GetInvokePattern(self):
        button = self

        class _P:
            def Invoke(_self):
                button._invoked.append(button.Name)

        return _P()


class _Label:
    def __init__(self, name):
        self.ControlTypeName = "TextControl"
        self.Name = name


class _TabWith:
    def __init__(self, *children):
        self._children = children

    def GetChildren(self):
        return list(self._children)


def test_an_english_close_button_is_still_matched_by_name():
    invoked = []

    assert tabs_mod.close_tab(_TabWith(_Button("Close Tab", invoked))) is True
    assert invoked == ["Close Tab"]


def test_a_localized_close_button_is_still_found():
    """Windows Terminal localizes the name. The English match finds nothing on
    a German install, and a hand-launched tab is left dead in the strip with
    no warning at all."""
    invoked = []

    assert tabs_mod.close_tab(
        _TabWith(_Button("Registerkarte schließen", invoked))) is True
    assert invoked == ["Registerkarte schließen"]


def test_a_label_is_never_invoked():
    """A tooltip reading "Close Tab" is not the button."""
    invoked = []

    assert tabs_mod.close_tab(
        _TabWith(_Label("Close Tab"), _Button("schließen", invoked))) is True
    assert invoked == ["schließen"]


def test_two_unnamed_buttons_are_refused():
    """Guessing which of them closes a tab is not the kind of guess this
    package makes."""
    invoked = []

    assert tabs_mod.close_tab(
        _TabWith(_Button("eins", invoked), _Button("zwei", invoked))) is False
    assert invoked == []


def test_the_named_one_wins_when_there_are_several():
    invoked = []

    assert tabs_mod.close_tab(
        _TabWith(_Button("New Tab", invoked), _Button("Close Tab", invoked))) is True
    assert invoked == ["Close Tab"]
