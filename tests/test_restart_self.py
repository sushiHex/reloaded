"""`restart --self`: the one restart a session can ask for on its own behalf.

Every other path drives a session from outside it - select its tab, type the
quit keys, wait for it to come back. None of that works on yourself. The
command would type into its own tab and then die with the session it just
ended, before it could watch for the return or report anything.

So it does not try. The tab's shell already runs its agent inside a loop that
checks for a marker each time the agent exits. "Restart me" is: leave the
marker, get out of the way, and let the user quit cleanly through the session's
own UI.

That makes the refusals as important as the arming. A marker left where no loop
will read it is a file on disk that nobody collects, and a command that says
"armed" about it has lied.
"""
from __future__ import annotations

import types

import pytest

import reloaded.__main__ as main_mod
import reloaded.discover as discover_mod

CWD = r"C:\repos\app"


def _args(**kw):
    d = {"layout": "default", "repos_root": r"C:\repos", "unattended": False,
         "dry_run": False, "repos": [], "self_": True, "cancel": False,
         "arm_only": True, "after": 0.0}
    d.update(kw)
    return types.SimpleNamespace(**d)


@pytest.fixture
def session(monkeypatch, tmp_path):
    """A reloaded-launched Claude session above this process, unless a test
    says otherwise."""
    state = {"found": (111, CWD, "claude"),
             "launcher": discover_mod.RELOADED,
             "marker": tmp_path / "m.marker"}

    monkeypatch.setattr(discover_mod, "owning_session", lambda: state["found"])
    monkeypatch.setattr(discover_mod, "launcher_kind",
                        lambda pid: state["launcher"])
    monkeypatch.setattr(main_mod, "restart_marker", lambda cwd: state["marker"])
    return state


def test_it_arms_the_session_it_is_called_from(session, capsys):
    rc = main_mod.cmd_restart(_args())

    assert rc == 0
    assert session["marker"].read_text(encoding="utf-8") == "restart"


def test_it_says_how_to_finish_the_job(session, capsys):
    """Arming alone does nothing visible. A command that returns 0 and changes
    nothing on screen reads as "done"."""
    main_mod.cmd_restart(_args())

    out = capsys.readouterr().out
    assert "/exit" in out, "it never says how to quit"
    assert "2 minutes" in out, "it never says the marker expires"


def test_it_says_what_missing_the_window_costs(session, capsys):
    """It used to say the stale marker was "ignored", which reads as "nothing
    happens". Something happens: the loop breaks and the tab closes, exactly
    like an ordinary quit. The slot is gone and the session comes back at the
    end of the strip.

    A deadline is only a deadline if the reader knows the penalty.
    """
    main_mod.cmd_restart(_args())

    out = capsys.readouterr().out
    assert "closes the tab" in out, "it never says the tab closes"
    # The full path, not the basename it used to print. This line is meant to
    # be copied and run, and a bare name routes through whatever --repos-root
    # the next shell happens to default to - which is the same mistake the
    # dispatch above was making.
    assert f'reloaded open "{CWD}"' in out, "it never says how to get it back"


def test_a_codex_session_is_told_to_use_its_own_quit_keys(session, capsys):
    """`/exit` does nothing in Codex. Printing it would send the user off to
    type something that cannot work."""
    session["found"] = (111, CWD, "codex")

    main_mod.cmd_restart(_args())

    out = capsys.readouterr().out
    assert "Ctrl+C" in out
    assert "/exit" not in out


def test_outside_a_session_it_refuses_and_says_what_to_run(capsys, monkeypatch):
    monkeypatch.setattr(discover_mod, "owning_session", lambda: None)

    rc = main_mod.cmd_restart(_args())

    assert rc == 1
    assert "restart <repo>" in capsys.readouterr().out


def test_a_hand_launched_session_cannot_be_armed(session, capsys):
    """Its shell is a plain prompt with no loop in it. Arming would leave a
    file nobody collects, and report success for a restart that cannot
    happen."""
    session["launcher"] = discover_mod.HAND

    rc = main_mod.cmd_restart(_args())

    assert rc == 1
    assert not session["marker"].exists()
    out = capsys.readouterr().out
    assert "started by hand" in out
    assert "marker" in out, "the reason arming is refused is the marker"
    assert "restart <repo>" in out, "no way out is offered"


def test_a_hand_launched_dispatch_is_refused_for_what_it_would_change(
    session, capsys
):
    """Still refused — the rationale in test_the_refusals_still_apply_before_
    dispatching holds — but for the reason that applies to this path.

    Plain `--self` writes no marker; it hands the job to a detached process. So
    "arming would leave a file on disk that no one collects" describes
    something this command would never do, and the one consequence worth
    refusing over goes unsaid: the upgrade permanently rewrites what the tab
    runs. A refusal that gives the wrong reason teaches the wrong lesson.
    """
    session["launcher"] = discover_mod.HAND

    rc = main_mod.cmd_restart(_args(arm_only=False))

    assert rc == 1
    out = capsys.readouterr().out
    assert "marker" not in out, "this path never writes one"
    assert "self-closes" in out, "the permanent change is not named"
    assert "restart <repo>" in out


def test_both_hand_refusals_describe_the_upgrade_identically(session, capsys):
    """One fact, one wording. `_preview_restart` says this for the named path;
    saying it differently here is how two descriptions of the same consequence
    drift apart."""
    session["launcher"] = discover_mod.HAND
    main_mod.cmd_restart(_args(arm_only=False))
    from_self = capsys.readouterr().out

    main_mod._print_hand_upgrade("would be")
    canonical = capsys.readouterr().out

    assert canonical.strip() in from_self


def test_a_reloaded_launched_session_is_not_refused_on_either_path(
    dispatched, session
):
    session["launcher"] = discover_mod.RELOADED

    assert main_mod.cmd_restart(_args(arm_only=False)) == 0
    assert main_mod.cmd_restart(_args(arm_only=True)) == 0
    assert session["marker"].exists()


def test_a_session_whose_directory_cannot_be_read_is_refused(session, capsys):
    """The marker is addressed by a hash of the cwd. Without one there is no
    file to write, and guessing at the directory would arm someone else."""
    session["found"] = (111, "", "claude")

    rc = main_mod.cmd_restart(_args())

    assert rc == 1
    assert not session["marker"].exists()


def test_the_dry_run_writes_nothing(session, capsys):
    rc = main_mod.cmd_restart(_args(dry_run=True))

    assert rc == 0
    assert not session["marker"].exists()
    assert "Dry run" in capsys.readouterr().out


def test_cancel_removes_the_marker(session, capsys):
    """Safe here and almost nowhere else: the session asking is still running,
    so nothing is about to read it."""
    session["marker"].write_text("restart", encoding="utf-8")

    rc = main_mod.cmd_restart(_args(cancel=True))

    assert rc == 0
    assert not session["marker"].exists()
    assert "Disarmed" in capsys.readouterr().out


def test_cancel_with_nothing_armed_says_so(session, capsys):
    rc = main_mod.cmd_restart(_args(cancel=True))

    assert rc == 0
    assert "Nothing was armed" in capsys.readouterr().out


def test_cancel_does_not_check_the_launcher_kind(session):
    """A hand-launched session cannot be armed, but it can have been armed
    before it was one - by an older build, or by a restart that upgraded the
    tab. Refusing to clean up would leave the file forever."""
    session["launcher"] = discover_mod.HAND
    session["marker"].write_text("restart", encoding="utf-8")

    assert main_mod.cmd_restart(_args(cancel=True)) == 0
    assert not session["marker"].exists()


def test_a_marker_that_cannot_be_written_is_reported(session, capsys, monkeypatch):
    class _Unwritable:
        def exists(self):
            return False

        def write_text(self, *a, **k):
            raise OSError("read-only")

    monkeypatch.setattr(main_mod, "restart_marker", lambda cwd: _Unwritable())

    rc = main_mod.cmd_restart(_args())

    assert rc == 1
    assert "could not arm" in capsys.readouterr().out


def test_naming_a_repo_alongside_self_is_refused(session, capsys):
    """`--self` is checked first, so naming both would arm the calling session
    and ignore what was named - a different restart than the one asked for,
    reported as success.

    Not hypothetical: a slash command passes its arguments through, and an
    argument that fails to interpolate arrives as the literal `$ARGUMENTS`.
    Refusing turns that into a message; picking would turn it into a restart
    nobody asked for.
    """
    rc = main_mod.cmd_restart(_args(repos=["other-repo"]))

    assert rc == 1
    assert not session["marker"].exists()
    out = capsys.readouterr().out
    assert "other-repo" in out
    assert "cannot also take a repo name" in out


def test_naming_a_repo_still_takes_the_normal_path(session, monkeypatch):
    """`--self` is a different command wearing the same verb. It must not
    swallow the one people already use."""
    called = []
    monkeypatch.setattr(main_mod, "cmd_restart_one",
                        lambda args, repos: called.append(repos) or 0)

    main_mod.cmd_restart(_args(self_=False, repos=["app"]))

    assert called == [["app"]]


# ── dispatching, which is what --self does by default ────────────────────
#
# The whole difficulty of restarting yourself is that the command doing it dies
# with the session it ends. So it does not do it. It hands the job to a process
# that is not inside the session at all, and that process runs the ordinary
# named-restart path - the same one used from another tab, with the same
# guards on where its keystrokes land.


@pytest.fixture
def dispatched(monkeypatch, session):
    """Capture the delegate instead of spawning it."""
    calls = []
    monkeypatch.setattr(main_mod, "_dispatch_restart",
                        lambda repo, layout, after, repos_root:
                        calls.append(
                                (repo, layout, after, repos_root)) or 4242)
    return calls


def test_by_default_it_hands_the_job_to_a_detached_process(dispatched, session):
    assert main_mod.cmd_restart(_args(arm_only=False)) == 0
    # The length matters as much as the contents: dispatch happens exactly once,
    # and a second helper would race the first for the same tab.
    assert dispatched == [
        (CWD, "default", main_mod.SELF_RESTART_DELAY_SECONDS, r"C:\repos")]


def test_a_session_outside_the_default_root_dispatches_its_own_path(
    dispatched, session
):
    session["found"] = (111, r"D:\work\app", "claude")

    main_mod.cmd_restart(_args(arm_only=False, repos_root=r"D:\work"))

    assert dispatched[0][0] == r"D:\work\app"


def test_two_repos_sharing_a_basename_cannot_be_confused(dispatched, session):
    """`C:\\repos\\app` and `D:\\work\\app` are both "app". The old dispatch
    sent that name to a helper resolving under its own default root, so a
    self-restart in one could restart the other."""
    session["found"] = (111, r"D:\work\app", "claude")

    main_mod.cmd_restart(_args(arm_only=False, repos_root=r"D:\work"))

    assert dispatched[0][0] != "app"
    assert dispatched[0][0] == r"D:\work\app"


def test_the_helper_gets_a_root_that_resolves_the_session_tab(dispatched, session):
    """Routing is settled by the absolute path, but the helper still has to
    find the session's TAB, and `resolve_tab` falls back to matching a title
    against a root. A Codex tab is never in the transcript title map, so that
    fallback is its only route.

    The root therefore has to be the session's own parent, not the dispatching
    process's `--repos-root`."""
    main_mod.cmd_restart(_args(arm_only=False, repos_root=r"D:\work"))

    assert dispatched[0][3] == r"C:\repos"


def test_the_forwarded_root_does_not_depend_on_the_callers_flag(dispatched, session):
    """The whole bug report is a session outside the default root - and someone
    in that position has no reason to have typed `--repos-root`, because they
    are not naming a repo. Forwarding the caller's flag hands the helper
    `~/repos` in exactly the case the fix exists for: the target is then
    unambiguous, the tab is unfindable, `_no_tab_to_type_into` refuses the
    batch, and the refusal prints to the helper's DEVNULL while the dispatching
    session has already said "Nothing further to do."

    Deriving the root from the path turns "restarts the wrong app" into
    "restarts the right one" rather than into "silently does nothing"."""
    session["found"] = (111, r"D:\work\app", "claude")

    main_mod.cmd_restart(_args(arm_only=False))  # no --repos-root, as reported

    assert dispatched[0][3] == r"D:\work"


def test_the_dry_run_names_the_full_target_path(session, capsys):
    """The preview is the last chance to notice the helper was aimed at the
    wrong "app". A basename cannot show that."""
    main_mod.cmd_restart(_args(arm_only=False, dry_run=True))

    assert CWD in capsys.readouterr().out


# ── what the detached process is actually told ───────────────────────────


@pytest.fixture
def bootstrap(monkeypatch):
    """The argv of the real spawn, without spawning it.

    `sys.executable` is not on conftest's forbidden list - nothing stops this
    from starting a real detached python that outlives the suite - so Popen is
    replaced here rather than relied upon to refuse.
    """
    import subprocess

    argv = []

    class _Popen:
        def __init__(self, a, **kw):
            argv.append(a)
            self.pid = 4242

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    return argv


def _helper_argv(spawned):
    """The argv the helper will really see.

    Asserting on the bootstrap's *text* would be asserting on repr escaping -
    a path arrives there as `D:\\\\work\\\\app` and matching that proves
    nothing about what the helper parses. The list is read back the way the
    interpreter will read it.
    """
    import ast

    # Found by the flag rather than by index: the interpreter's own options
    # sit in front of it and gaining one (`-u`, so the account is not lost in
    # a buffer when the helper is killed) should not break this reader.
    argv = spawned[0]
    source = argv[argv.index("-c") + 1]
    start = source.index("[", source.index("main("))
    return ast.literal_eval(source[start:source.rindex("]") + 1])


def test_the_bootstrap_carries_the_absolute_path_and_the_root(bootstrap):
    main_mod._dispatch_restart(r"D:\work\app", "work", 7.0, r"D:\work")

    assert _helper_argv(bootstrap) == [
        "--layout", "work", "--repos-root", r"D:\work",
        "restart", r"D:\work\app", "--after", "7.0", "--dispatched",
    ]


def test_the_helper_is_told_it_was_dispatched(bootstrap):
    """The marker-long patience belongs to a restart nobody is watching, and
    `--after` cannot stand in for that: it is a public option a named restart
    accepts alongside any number of repos."""
    main_mod._dispatch_restart(r"D:\work\app", "work", 7.0, r"D:\work")

    assert main_mod.build_parser().parse_args(
        _helper_argv(bootstrap)).dispatched is True


def test_a_hand_typed_restart_is_not_dispatched(bootstrap):
    assert main_mod.build_parser().parse_args(
        ["restart", "app", "--after", "5"]).dispatched is False


def test_the_helper_does_not_buffer_its_account(bootstrap):
    """It is a process that gets killed rather than closed, and the lines
    worth keeping are the last ones it writes."""
    main_mod._dispatch_restart(r"D:\work\app", "work", 7.0, r"D:\work")

    assert "-u" in bootstrap[0]


def test_the_global_options_precede_the_subcommand(bootstrap):
    """`--layout` and `--repos-root` are added to the top-level parser before
    `add_subparsers`, so they are not options after `restart`.

    Verified rather than assumed: argparse does not fold them into `repos`
    (`nargs="*"` skips anything starting with a prefix char) - it exits 2 with
    "unrecognized arguments". So the failure mode for a misordered bootstrap is
    a helper that dies instantly into DEVNULL, with the dispatching session
    still reporting a successful handoff. Silent, not wrong."""
    main_mod._dispatch_restart(r"D:\work\app", "work", 7.0, r"D:\work")

    argv = _helper_argv(bootstrap)
    assert argv.index("--layout") < argv.index("restart")
    assert argv.index("--repos-root") < argv.index("restart")


def test_the_bootstrap_argv_is_one_argparse_accepts(bootstrap):
    """The ordering above only matters because of what it prevents, so parse it
    with the real parser rather than trusting the index comparison."""
    main_mod._dispatch_restart(r"D:\work\app", "work", 7.0, r"D:\work")

    parsed = main_mod.build_parser().parse_args(_helper_argv(bootstrap))

    assert parsed.command == "restart"
    assert parsed.repos == [r"D:\work\app"]
    assert parsed.repos_root == r"D:\work"
    assert parsed.layout == "work"


def test_a_path_with_spaces_stays_one_argument(bootstrap):
    """The bootstrap is Python source, not a shell line - the list is embedded
    by repr and read back by the interpreter, so a space is just a character.
    Pinned because the obvious "fix" of building a command string is what
    breaks it."""
    main_mod._dispatch_restart(r"C:\work\my project", "default", 5.0, r"C:\work")

    assert r"C:\work\my project" in _helper_argv(bootstrap)


def test_it_does_not_arm_the_marker_itself(dispatched, session):
    """The delegate runs the ordinary restart, which arms as part of its own
    flow, at the moment it is about to send the quit keys. Arming here as well
    would put a marker on disk early and leave it there if the delegate never
    ran."""
    main_mod.cmd_restart(_args(arm_only=False))

    assert not session["marker"].exists()


def test_it_says_the_helper_outlives_this_session(dispatched, capsys):
    """A user who thinks the restart dies with the session will type /exit to
    "help", which is the one thing that breaks it."""
    main_mod.cmd_restart(_args(arm_only=False))

    out = capsys.readouterr().out
    assert "4242" in out, "the helper pid is not named"
    assert "outside this session" in out
    # It used to end "Nothing further to do." - printed the instant the helper
    # was spawned, by a process about to die, about work not yet attempted. A
    # dispatched restart that failed said nothing anywhere; see
    # tests/test_self_restart_account.py.
    assert "Nothing further to do" not in out


def test_the_delay_is_passed_through(dispatched, session):
    main_mod.cmd_restart(_args(arm_only=False, after=12.0))

    assert dispatched[0][2] == 12.0


def test_a_spawn_that_fails_names_the_fallback(session, capsys, monkeypatch):
    """Losing the delegate silently would leave a user waiting for a restart
    that has no process behind it."""
    def boom(repo, layout, after):
        raise OSError("breakaway refused and detach failed")

    monkeypatch.setattr(main_mod, "_dispatch_restart", boom)

    rc = main_mod.cmd_restart(_args(arm_only=False))

    assert rc == 1
    out = capsys.readouterr().out
    assert "--arm-only" in out, "no way to fall back is offered"


def test_the_dry_run_spawns_nothing(session, capsys, monkeypatch):
    monkeypatch.setattr(main_mod, "_dispatch_restart",
                        lambda *a: pytest.fail("spawned during a dry run"))

    rc = main_mod.cmd_restart(_args(arm_only=False, dry_run=True))

    assert rc == 0
    assert "Dry run" in capsys.readouterr().out


def test_the_refusals_still_apply_before_dispatching(capsys, monkeypatch):
    """Handing a hand-launched session to the delegate is not wrong - the
    named restart handles those by typing the launcher into the idle shell.
    But `--self` refuses it, because that path replaces what the tab runs, and
    doing that to yourself on the strength of one keystroke deserves the
    explicit command."""
    monkeypatch.setattr(discover_mod, "owning_session", lambda: None)
    monkeypatch.setattr(main_mod, "_dispatch_restart",
                        lambda *a: pytest.fail("dispatched with no session"))

    assert main_mod.cmd_restart(_args(arm_only=False)) == 1


def test_cancel_admits_it_cannot_recall_a_dispatched_restart(session, capsys):
    """"Disarmed" would otherwise read as "stopped". The delegate is a separate
    process doing its own arming; nothing in here can call it back."""
    main_mod.cmd_restart(_args(cancel=True))

    out = capsys.readouterr().out
    assert "cannot be called off" in out


# ── finding the session you are inside ───────────────────────────────────


class _Proc:
    def __init__(self, pid, image, cwd=CWD, parent=None, cwd_raises=None):
        self.pid = pid
        self._image = image
        self._cwd = cwd
        self._parent = parent
        self._cwd_raises = cwd_raises

    def name(self):
        return self._image

    def cwd(self):
        if self._cwd_raises:
            raise self._cwd_raises
        return self._cwd

    def parent(self):
        return self._parent


def _chain(monkeypatch, leaf):
    fake = types.SimpleNamespace(Process=lambda pid: leaf)
    monkeypatch.setattr(discover_mod, "_ps", lambda: fake)


def test_the_agent_above_this_process_is_found(monkeypatch):
    """Walking up, not matching directories. A shell a session spawns can be
    anywhere - a subdirectory, a sibling, somewhere else entirely - and the
    parent chain is the only thing that says "I am inside this one"."""
    agent = _Proc(111, "claude.exe", parent=_Proc(1, "WindowsTerminal.exe"))
    shell = _Proc(222, "pwsh.exe", parent=agent)
    _chain(monkeypatch, _Proc(333, "python.exe", parent=shell))

    assert discover_mod.owning_session() == (111, CWD, "claude")


def test_codex_is_recognised_too(monkeypatch):
    agent = _Proc(111, "codex.exe", parent=None)
    _chain(monkeypatch, _Proc(222, "pwsh.exe", parent=agent))

    assert discover_mod.owning_session() == (111, CWD, "codex")


def test_the_image_name_is_matched_case_insensitively(monkeypatch):
    agent = _Proc(111, "CLAUDE.EXE", parent=None)
    _chain(monkeypatch, _Proc(222, "pwsh.exe", parent=agent))

    assert discover_mod.owning_session()[2] == "claude"


def test_no_agent_above_means_none(monkeypatch):
    """Run from an ordinary terminal. Saying "not inside a session" is the
    truthful answer; picking the nearest live session would not be."""
    _chain(monkeypatch, _Proc(222, "pwsh.exe", parent=_Proc(1, "explorer.exe")))

    assert discover_mod.owning_session() is None


def test_the_walk_is_bounded(monkeypatch):
    """A pid loop, or a chain longer than any real one. Hanging on it would be
    worse than answering None."""
    loop = _Proc(1, "pwsh.exe")
    loop._parent = loop
    _chain(monkeypatch, loop)

    assert discover_mod.owning_session() is None


def test_an_unreadable_directory_still_reports_the_session(monkeypatch):
    """Losing the cwd here would be reported as "not inside a session" - a
    confidently wrong answer, when the truth is "inside one I cannot fully
    read"."""
    agent = _Proc(111, "claude.exe", parent=None,
                  cwd_raises=PermissionError("denied"))
    _chain(monkeypatch, _Proc(222, "pwsh.exe", parent=agent))

    assert discover_mod.owning_session() == (111, "", "claude")


def test_an_unreadable_parent_ends_the_walk(monkeypatch):
    class _Blind(_Proc):
        def parent(self):
            raise PermissionError("denied")

    _chain(monkeypatch, _Blind(222, "pwsh.exe"))

    assert discover_mod.owning_session() is None
