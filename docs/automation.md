# Automation

[Back to the README](../README.md)

## Enable restoration at sign-in

First capture the arrangement you want and verify its launch commands with
`reloaded up --dry-run`. Test interactive restoration before enabling logon
runs, and resolve any agent sign-in or directory-trust prompts in its tab.

The automation runs through `pyw.exe -3`, independently of your pipx or virtual
environment. Install its dependencies into the Python selected by that launcher:

```powershell
py -3 -m pip install psutil uiautomation
py -3 -c "import psutil, uiautomation; print('Automation dependencies available')"
reloaded install-tasks
```

For another saved layout, use `reloaded --layout work install-tasks`. Installing
again replaces the entries with that layout; this is one automation setup per
user, not a separate task pair for every layout.

| Entry | Behavior |
| --- | --- |
| Startup folder's `reloaded-up.vbs` | Runs `up --unattended` at sign-in. |
| Scheduled task `Reloaded-Reconcile` | Runs `capture` every five minutes to refresh the saved arrangement. |

No elevation is normally required. Keep the installed package or editable
checkout where it was registered: both entries embed that location. Re-run
`install-tasks` after moving or reinstalling it.

The periodic task uses the default repository root, `~/repos`; registration
persists `--layout` but not `--repos-root`. Verify capture under that default
if your repositories live elsewhere. Absolute paths already saved in a layout
are used when restoring its tabs.

## What unattended mode does

At sign-in, `up --unattended` waits up to 120 seconds for Windows Terminal,
required agent executables, and saved monitors. A Codex-only layout does not
wait for Claude Code. Missing directories are reported and skipped rather than
holding up every launch.

When readiness times out, Reloaded logs the reason and still attempts deployment
using the available monitors. It checks the Terminal version and logs placement
warnings. An opened window is not proof that an agent started; inspect the log
and the tab for startup errors or prompts.

The logon entry uses `wscript.exe` and `pyw.exe` to avoid opening a helper console.
The reconcile task has a four-minute execution limit, ignores overlapping runs,
and is allowed on battery power. It only captures: it does not reopen sessions
that you close during the day.

`capture` keeps an existing layout when it finds no sessions. Consequently,
closing everything does not necessarily clear what the next sign-in will restore.
Pinned entries also survive captures while absent. Use the editor to change the
saved arrangement or disable automation when you want restoration to stop.

## Disable automation

```powershell
reloaded uninstall-tasks
```

This removes the Startup entry and reconcile task. It leaves saved layouts and
live sessions in place. `reloaded down` exits sessions but does not uninstall
automation, so a later sign-in can restore the saved layout again.

## State and logs

| Path | Contents |
| --- | --- |
| `~/.reloaded/layouts/<name>.json` | Saved windows, tabs, geometry, and launch commands. |
| `~/.reloaded/reloaded.log` | Unattended deployment output, readiness results, and crash reports. |
| `~/.reloaded/restart/` | Short-lived restart markers. |
| `~/.reloaded/relaunch/` | Launcher scripts used to upgrade hand-started tabs. |

In Windows, `~` denotes your user profile directory. To inspect the log in
PowerShell:

```powershell
Get-Content -LiteralPath "$env:USERPROFILE\.reloaded\reloaded.log" -Tail 80
```

Routine periodic-capture output is not a continuous audit log. Reproduce a
capture interactively to see its refusal or unmatched-session messages.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| `reloaded` is not found | Run `py -m pipx ensurepath`, then open a new terminal, or use your environment's executable by absolute path. |
| `capture`/`edit` reports missing UI Automation | Install `uiautomation` into the interpreter running Reloaded; the scheduled interpreter may differ from the interactive one. |
| Zero sessions or unmatched tabs | Confirm a terminal agent is running and `psutil` is installed. Check titles and repository-root matching. Desktop and nested agents are excluded intentionally. |
| Capture refuses to replace a layout | Previously saved sessions are still running but did not match captured tabs. Let windows settle and retry; use `--force` only to accept that omission. |
| Logon restores nothing | Inspect `reloaded.log`, confirm the saved layout exists, check the registered package path, and test imports with `py -3`. |
| A tab opens without an agent | Read the tab's startup error or trust prompt. Verify the captured executable is on PATH and the repository exists. |
| `down` or `restart` times out | Read the per-session result. Active prompts can consume quit keys; finish the work and retry the intended target. A waiting restart marker may still be active. |
| Geometry is wrong | Check `reloaded status` for changed monitors and the Terminal version. Placement can fall back when a window cannot be identified. |
