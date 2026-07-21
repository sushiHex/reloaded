from __future__ import annotations

import subprocess

import pytest

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
    startup_vbs_path,
    uninstall,
)


def test_vbs_quote_wraps_in_double_quotes():
    assert _vbs_quote("hello") == '"hello"'


def test_vbs_quote_escapes_embedded_double_quotes():
    # A raw " would terminate the VBScript string literal early.
    assert _vbs_quote('a"b') == '"a""b"'


def test_ps_quote_wraps_in_single_quotes():
    assert _ps_quote("hello") == "'hello'"


def test_ps_quote_escapes_embedded_single_quotes():
    assert _ps_quote("O'Brien") == "'O''Brien'"


def test_bootstrap_command_needs_no_pythonpath_env_var():
    cmd = _bootstrap_command(r"C:\pkg", "default", ["up", "--unattended"])
    assert "sys.path.insert(0," in cmd
    assert r"C:\\pkg" in cmd or "pkg" in cmd  # repr()'d path is present in some escaped form
    assert "from reloaded.__main__ import main" in cmd


def test_bootstrap_command_embeds_the_subcommand_and_layout():
    cmd = _bootstrap_command(r"C:\pkg", "work", ["capture"])
    assert "'--layout', 'work', 'capture'" in cmd


def test_bootstrap_command_survives_a_package_dir_with_a_single_quote(tmp_path):
    # repr() switches to double-quoted Python literals when the string
    # contains a single quote -- must not break the surrounding construction.
    weird = str(tmp_path) + "\\o'brien"
    cmd = _bootstrap_command(weird, "default", ["capture"])
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
    vbs = _build_vbs(r"C:\pkg", "default")
    assert "shell.Run" in vbs
    assert vbs.rstrip().endswith(", 0, False")


def test_build_vbs_needs_no_pythonpath_environment_line():
    # The old design set PYTHONPATH via shell.Environment; the bootstrap
    # approach folds sys.path setup into the command itself instead.
    vbs = _build_vbs(r"C:\pkg", "default")
    assert "Environment" not in vbs


def test_build_vbs_targets_pyw_with_the_stable_launcher_flag():
    vbs = _build_vbs(r"C:\pkg", "default")
    assert "pyw.exe -3 -c" in vbs


def test_build_vbs_embeds_the_unattended_up_command():
    vbs = _build_vbs(r"C:\pkg", "work")
    assert "'--layout', 'work', 'up', '--unattended'" in vbs


@pytest.fixture
def fake_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def test_startup_vbs_path_is_under_the_startup_folder(fake_startup):
    assert startup_vbs_path() == fake_startup / STARTUP_VBS_NAME


def _mock_powershell(monkeypatch, returncode, stdout="", stderr=""):
    def fake_run(argv, capture_output=True, text=True):
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(tasks_mod.subprocess, "run", fake_run)


def test_install_registers_the_reconcile_task_with_battery_and_timeout_settings(fake_startup, monkeypatch, tmp_path):
    """The two MEDIUM-severity findings this guards: the task must run on
    battery (laptops), and must not be able to wedge reconciliation
    indefinitely (ExecutionTimeLimit below the trigger interval)."""
    captured = {}

    def fake_run(argv, capture_output=True, text=True):
        captured["script"] = argv[-1]
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(tasks_mod.subprocess, "run", fake_run)
    rc = install(str(tmp_path / "pkg"), "default")
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

    rc = install(str(tmp_path / "pkg"), "default")
    assert rc == 0
    assert (fake_startup / STARTUP_VBS_NAME).exists()
    assert not (fake_startup / _LEGACY_CMD_NAME).exists()


def test_install_fails_cleanly_when_task_registration_is_denied(fake_startup, monkeypatch, tmp_path):
    _mock_powershell(monkeypatch, 1, stderr="Access is denied.")
    rc = install(str(tmp_path / "pkg"), "default")
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
