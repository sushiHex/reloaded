from __future__ import annotations

import subprocess

import pytest

import reloaded.readiness as readiness_mod
import reloaded.tasks as tasks_mod
from reloaded.tasks import (
    RECONCILE_EXECUTION_TIME_LIMIT_MINUTES,
    RECONCILE_INTERVAL_MINUTES,
    STARTUP_VBS_NAME,
    TASK_RECONCILE,
    _LEGACY_CMD_NAME,
    _bootstrap_command,
    _build_vbs,
    _launcher_argv,
    _ps_quote,
    _vbs_quote,
    install,
    logon_launcher_installed,
    startup_vbs_path,
    uninstall,
)


ROOT = r"C:\repos"


def test_vbs_quote_wraps_in_double_quotes():
    assert _vbs_quote("hello") == '"hello"'


def test_vbs_quote_escapes_embedded_double_quotes():
    # A raw " would terminate the VBScript string literal early.
    assert _vbs_quote('a"b') == '"a""b"'


def test_ps_quote_wraps_in_single_quotes():
    assert _ps_quote("hello") == "'hello'"


def test_ps_quote_escapes_embedded_single_quotes():
    assert _ps_quote("O'Brien") == "'O''Brien'"


# ── the configured repository root reaches both launchers ────────────────
#
# `--layout` was forwarded into the scheduled commands and `--repos-root` was
# not, so automation installed with a custom root silently ran against the
# default one. The reconcile then resolves tab titles under a directory the
# user never chose: `capture.resolve_tab` falls back to matching a title
# against `repos_root`, and a Codex tab has no transcript title to match on
# first, so that fallback is its only route.
#
# The same omission at a different spawn point as the `--self` helper's
# (fix/self-restart-absolute-target). Both launchers are argv lists embedded in
# Python source, so these assert on the decoded list rather than on the text -
# a root renders there as `D:\\\\work` and matching that proves nothing about
# what the scheduled process parses.


def _scheduled_argv(text: str) -> list[str]:
    """The argv the scheduled process will really see, out of a bootstrap.

    Works on a bare bootstrap and on the VBS that wraps one, because
    `_vbs_quote` doubles `"` while the embedded Python literal uses `'` — the
    two escapes do not collide. It does NOT work on the PowerShell
    registration script: `_ps_quote` doubles `'`, which is the quote the
    literal is built from, so that one has to be un-escaped first. Stated
    rather than handled, because a helper that guessed which encoding it was
    looking at would hide exactly the layer these tests exist to check.
    """
    import ast

    start = text.index("[", text.index("main("))
    return ast.literal_eval(text[start:text.index("]", start) + 1])


def test_the_bootstrap_carries_the_configured_root():
    argv = _scheduled_argv(
        _bootstrap_command(r"C:\pkg", "work", r"D:\work", ["capture"]))

    assert argv == ["--layout", "work", "--repos-root", r"D:\work", "capture"]


def test_the_scheduled_argv_is_one_the_real_parser_accepts():
    """The ordering matters only because of what it prevents, so parse it with
    the parser it will meet rather than comparing indices: `--layout` and
    `--repos-root` sit on the top-level parser, and after the subcommand
    argparse exits 2 on them."""
    import reloaded.__main__ as main_mod

    argv = _scheduled_argv(
        _bootstrap_command(r"C:\pkg", "work", r"D:\work", ["capture"]))
    parsed = main_mod.build_parser().parse_args(argv)

    assert parsed.command == "capture"
    assert parsed.repos_root == r"D:\work"
    assert parsed.layout == "work"


def test_a_root_with_spaces_survives_into_the_logon_launcher():
    """Three encodings between here and the running process - repr into the
    bootstrap, list2cmdline into the pyw command line, then a VBScript string
    literal - and a space is the character that breaks the naive version of
    any of them."""
    vbs = _build_vbs(r"C:\pkg", "default", r"D:\my work")

    assert _scheduled_argv(vbs) == [
        "--layout", "default", "--repos-root", r"D:\my work",
        "up", "--unattended",
    ]


def test_a_root_with_spaces_survives_into_the_reconcile_registration(monkeypatch):
    scripts = []
    monkeypatch.setattr(tasks_mod, "_run_powershell",
                        lambda script: scripts.append(script) or _ok())

    tasks_mod._register_reconcile_task(r"C:\pkg", "default", r"D:\my work")

    # Undo the PowerShell single-quoted literal before reading the Python one
    # inside it. Four encodings deep here, one more than the VBS path.
    unquoted = scripts[0].replace("''", "'")

    assert _scheduled_argv(unquoted) == [
        "--layout", "default", "--repos-root", r"D:\my work", "capture",
    ]


def _ok():
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_install_tasks_hands_over_the_root_it_was_invoked_with(monkeypatch):
    """The seam the bug actually lived at.

    `tasks.install` can thread a root through flawlessly and the command can
    still never give it one — which is what happened: `cmd_install_tasks`
    passed the package directory and the layout, and the root the user typed
    went no further than argparse.
    """
    import types
    import reloaded.__main__ as main_mod

    got = []
    monkeypatch.setattr(main_mod.tasks_mod, "install",
                        lambda pkg, layout, repos_root: got.append(
                            (layout, repos_root)) or 0)

    main_mod.cmd_install_tasks(
        types.SimpleNamespace(layout="work", repos_root=r"D:\work"))

    assert got == [("work", r"D:\work")]


def test_bootstrap_command_needs_no_pythonpath_env_var():
    cmd = _bootstrap_command(r"C:\pkg", "default", ROOT, ["up", "--unattended"])
    assert "sys.path.insert(0," in cmd
    assert r"C:\\pkg" in cmd or "pkg" in cmd  # repr()'d path is present in some escaped form
    assert "from reloaded.__main__ import main" in cmd


def test_bootstrap_command_embeds_the_subcommand_and_layout():
    """Asserted on the decoded argv rather than a substring: the old version
    pinned `--layout` and the subcommand as adjacent text, which is a fact
    about spelling rather than about what the process is told, and it broke the
    moment another global option was added between them."""
    cmd = _bootstrap_command(r"C:\pkg", "work", ROOT, ["capture"])
    assert _scheduled_argv(cmd) == [
        "--layout", "work", "--repos-root", ROOT, "capture"]


def test_bootstrap_command_survives_a_package_dir_with_a_single_quote(tmp_path):
    # repr() switches to double-quoted Python literals when the string
    # contains a single quote -- must not break the surrounding construction.
    weird = str(tmp_path) + "\\o'brien"
    cmd = _bootstrap_command(weird, "default", ROOT, ["capture"])
    # The resulting text must still be a single valid Python expression -- if
    # this compiles, the quoting held together correctly end-to-end.
    compile(cmd, "<bootstrap>", "exec")


def test_launcher_argv_produces_one_correctly_quoted_argument():
    bootstrap = "import sys; print('hi')"
    argv = _launcher_argv(bootstrap)
    # Windows' own argv parser must reconstruct exactly ["-3", "-c", bootstrap].
    import shlex

    assert argv.startswith("-3 -c ")
    # Round-trip through the same quoting convention list2cmdline uses.
    assert subprocess.list2cmdline(["-3", "-c", bootstrap]) == argv


def test_launcher_argv_handles_a_bootstrap_containing_double_quotes():
    bootstrap = 'import sys; print("hi")'
    argv = _launcher_argv(bootstrap)
    assert argv == subprocess.list2cmdline(["-3", "-c", bootstrap])


def test_build_vbs_runs_hidden_and_non_blocking():
    vbs = _build_vbs(r"C:\pkg", "default", ROOT)
    assert "shell.Run" in vbs
    assert vbs.rstrip().endswith(", 0, False")


def test_build_vbs_needs_no_pythonpath_environment_line():
    # The old design set PYTHONPATH via shell.Environment; the bootstrap
    # approach folds sys.path setup into the command itself instead.
    vbs = _build_vbs(r"C:\pkg", "default", ROOT)
    assert "Environment" not in vbs


def test_build_vbs_targets_pyw_with_the_stable_launcher_flag():
    vbs = _build_vbs(r"C:\pkg", "default", ROOT)
    assert "pyw.exe -3 -c" in vbs


def test_build_vbs_embeds_the_unattended_up_command():
    vbs = _build_vbs(r"C:\pkg", "work", ROOT)
    assert _scheduled_argv(vbs) == [
        "--layout", "work", "--repos-root", ROOT, "up", "--unattended"]


@pytest.fixture
def fake_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def test_startup_vbs_path_is_under_the_startup_folder(fake_startup):
    assert startup_vbs_path() == fake_startup / STARTUP_VBS_NAME


def _mock_powershell(monkeypatch, returncode, stdout="", stderr=""):
    def fake_run(argv, **kwargs):
        # Accepts **kwargs rather than a fixed signature so the fake does not
        # pin the real call's exact keyword set. Asserts the one keyword that
        # is load-bearing: powershell is console-subsystem, so without this
        # flag it gets a visible window - and install/uninstall are reachable
        # from a windowless parent.
        assert kwargs.get("creationflags") == readiness_mod.NO_WINDOW, (
            "PowerShell spawned without CREATE_NO_WINDOW"
        )
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(tasks_mod.subprocess, "run", fake_run)


def test_install_registers_the_reconcile_task_with_battery_and_timeout_settings(fake_startup, monkeypatch, tmp_path):
    """The two MEDIUM-severity findings this guards: the task must run on
    battery (laptops), and must not be able to wedge reconciliation
    indefinitely (ExecutionTimeLimit below the trigger interval)."""
    captured = {}

    def fake_run(argv, **kwargs):
        captured["script"] = argv[-1]
        assert kwargs.get("creationflags") == readiness_mod.NO_WINDOW
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(tasks_mod.subprocess, "run", fake_run)
    rc = install(str(tmp_path / "pkg"), "default", ROOT)
    assert rc == 0
    script = captured["script"]
    assert "-AllowStartIfOnBatteries" in script
    assert "-DontStopIfGoingOnBatteries" in script
    assert f"-Minutes {RECONCILE_EXECUTION_TIME_LIMIT_MINUTES}" in script
    assert f"-Minutes {RECONCILE_INTERVAL_MINUTES}" in script
    assert "-MultipleInstances IgnoreNew" in script
    assert "pyw.exe" in script
    assert TASK_RECONCILE in script


def test_install_writes_a_vbs_and_migrates_away_the_legacy_cmd(fake_startup, monkeypatch, tmp_path):
    _mock_powershell(monkeypatch, 0)
    fake_startup.mkdir(parents=True)
    (fake_startup / _LEGACY_CMD_NAME).write_text("stub", encoding="utf-8")

    rc = install(str(tmp_path / "pkg"), "default", ROOT)
    assert rc == 0
    assert (fake_startup / STARTUP_VBS_NAME).exists()
    assert not (fake_startup / _LEGACY_CMD_NAME).exists()


def test_install_writes_a_vbs_windows_script_host_can_actually_read(
    fake_startup, monkeypatch, tmp_path
):
    """Assert on bytes, not text.

    read_text() applies universal newlines, so it returns the same string for
    a correct \\r\\n file and a broken \\r\\r\\n one - the obvious regression
    test cannot see this bug. Only the bytes distinguish them.

    Encoding is load-bearing, not cosmetic: wscript decodes a BOM-less file
    with the ANSI code page, so a non-ASCII install path arrives mojibake'd
    and the logon launcher silently starts nothing. utf-8-sig is not an
    alternative - WSH rejects it with "Invalid character".
    """
    _mock_powershell(monkeypatch, 0)
    fake_startup.mkdir(parents=True)

    assert install(str(tmp_path / "pkg"), "default", ROOT) == 0
    raw = (fake_startup / STARTUP_VBS_NAME).read_bytes()

    assert raw[:2] in (b"\xff\xfe", b"\xfe\xff"), "no UTF-16 BOM; WSH will assume ANSI"
    assert raw[:3] != b"\xef\xbb\xbf", "utf-8-sig BOM is rejected by WSH"

    text = raw.decode("utf-16")
    assert "\r\r\n" not in text, "newline translation doubled the carriage returns"
    assert text.count("\r\n") == text.count("\n"), "mixed terminators"


def test_install_leaves_a_working_launcher_when_the_write_fails(
    fake_startup, monkeypatch, tmp_path
):
    """The write must not truncate a good launcher before it can fail.

    open(path, "w") truncates on open, so a failure mid-write left a 0-byte
    VBS that logon_launcher_installed() still reports as installed - having
    destroyed a launcher that worked, and leaving `down` to warn about a
    logon relaunch that can no longer happen.

    Also pins the handler's exception class: encoding errors are ValueError,
    not OSError, so `except OSError` alone would let one escape as a traceback
    out of an unattended install.
    """
    _mock_powershell(monkeypatch, 0)
    fake_startup.mkdir(parents=True)
    target = fake_startup / STARTUP_VBS_NAME
    assert install(str(tmp_path / "pkg"), "default", ROOT) == 0
    good = target.read_bytes()

    # Fail the move that publishes the new file. On Windows this is a real
    # failure mode - the Startup folder file can be held open by the shell.
    import reloaded.tasks as tasks_mod

    def denied(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(tasks_mod.os, "replace", denied)
    rc = install(str(tmp_path / "pkg"), "default", ROOT)

    assert rc != 0
    assert target.read_bytes() == good, "a failed install damaged the launcher"
    assert not list(fake_startup.glob("*.tmp")), "left a temp file behind"


def test_install_fails_cleanly_when_task_registration_is_denied(fake_startup, monkeypatch, tmp_path):
    _mock_powershell(monkeypatch, 1, stderr="Access is denied.")
    rc = install(str(tmp_path / "pkg"), "default", ROOT)
    assert rc != 0


def test_uninstall_reports_success_when_the_task_never_existed(fake_startup, monkeypatch):
    """Locale-independent by construction: this checks Get-ScheduledTask's
    structured ABSENT/REMOVED marker, never schtasks.exe's translated error
    text -- the previous version misread a real failure as "not found" on
    any non-English Windows install."""
    _mock_powershell(monkeypatch, 0, stdout="ABSENT\n")
    assert uninstall() == 0


def test_uninstall_reports_success_when_the_task_is_actually_removed(fake_startup, monkeypatch):
    _mock_powershell(monkeypatch, 0, stdout="REMOVED\n")
    assert uninstall() == 0


def test_logon_launcher_installed_true_when_the_vbs_exists(fake_startup):
    fake_startup.mkdir(parents=True)
    (fake_startup / STARTUP_VBS_NAME).write_text("stub", encoding="utf-8")
    assert logon_launcher_installed() is True


def test_logon_launcher_installed_false_when_absent(fake_startup):
    assert logon_launcher_installed() is False


def test_uninstall_reports_failure_when_deletion_is_denied(fake_startup, monkeypatch):
    """The bug this guards: a real failure (access denied, task locked) must
    not be reported as success just because it also exits non-zero the way
    "not found" used to."""
    _mock_powershell(monkeypatch, 1, stderr="Access is denied.")
    assert uninstall() == 1


def test_uninstall_removes_the_vbs_launcher(fake_startup, monkeypatch):
    _mock_powershell(monkeypatch, 0, stdout="ABSENT\n")
    fake_startup.mkdir(parents=True)
    (fake_startup / STARTUP_VBS_NAME).write_text("stub", encoding="utf-8")
    assert uninstall() == 0
    assert not (fake_startup / STARTUP_VBS_NAME).exists()


def test_uninstall_also_removes_a_legacy_cmd_launcher_from_an_older_install(fake_startup, monkeypatch):
    _mock_powershell(monkeypatch, 0, stdout="ABSENT\n")
    fake_startup.mkdir(parents=True)
    (fake_startup / _LEGACY_CMD_NAME).write_text("stub", encoding="utf-8")
    assert uninstall() == 0
    assert not (fake_startup / _LEGACY_CMD_NAME).exists()
