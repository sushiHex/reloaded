# reloaded

A Windows Terminal launcher for Claude Code. It captures the live arrangement of
Claude Code sessions across Windows Terminal windows — which repo, which window,
tab order, exact screen position — and can redeploy that arrangement on demand or
automatically at logon.

It is a launcher first: the same `up` command you'd run by hand is what a
scheduled task and a Startup-folder entry run unattended, so the unattended path
is exercised every day rather than only after a crash.

## Requirements

- **Windows** (this package uses ctypes/Win32 APIs and Windows Terminal directly —
  no other platform is supported)
- **Python 3.9+**
- `pip install psutil uiautomation` — both are required; `capture`/`edit` fail
  with a clear message (not a traceback) if `uiautomation` is missing, and
  session discovery degrades to "0 live sessions" if `psutil` is missing
- **Windows Terminal**, ideally 1.24.11911.0 or newer — `--pos=x,y` and `-w -1`
  are verified at that version; `reloaded status` warns (does not block) if an
  older version is detected
- **`claude`** (the Claude Code CLI) on PATH
- A PowerShell to run session commands in: **PowerShell 7 (`pwsh`)** is
  preferred and auto-detected; if it isn't installed, Windows PowerShell 5.1
  (`powershell.exe`, present on every supported Windows version) is used
  automatically — no configuration needed either way

## Running it

This is not yet installed via `pip install`. Run it from the repo with the
package directory on `sys.path`:

```
$env:PYTHONPATH = "C:\path\to\computers\playbooks"
python -m reloaded <command>
```

(`reloaded install-tasks` sets this up automatically for the logon/reconcile
launchers it registers — see below. The `PYTHONPATH` line above is only needed
for running commands by hand from a plain shell.)

## Commands

| Command | Does |
|---|---|
| `reloaded` (bare), or `reloaded edit` | Open the interactive layout editor |
| `reloaded capture` | Snapshot the current arrangement into the saved layout |
| `reloaded up` | Deploy the saved layout (`--dry-run` to preview, `--unattended` for scheduled/logon use) |
| `reloaded status` | Compare the saved layout against what's actually running; also reports readiness and the detected Windows Terminal version |
| `reloaded open <repo>` | Add one repo as a new tab in the *current* window (see also: `up`, which deploys the whole saved layout into new windows) |
| `reloaded install-tasks` | Register the logon launcher and the 5-minute reconcile task |
| `reloaded uninstall-tasks` | Remove both |

## Notes

- State lives under `~/.reloaded/` — layouts in `layouts/<name>.json`, an
  unattended-run log at `reloaded.log`.
- `install-tasks` requires no elevation: the logon launcher is a `.vbs` in the
  Startup folder (run via `wscript.exe` → `pyw.exe`, so it never opens a
  console — Windows 11 makes Windows Terminal the default console host, and a
  console tab would otherwise get adopted into an existing WT window), and the
  reconcile task is registered via PowerShell's `Register-ScheduledTask`
  rather than `schtasks.exe` (needed for `ExecutionTimeLimit` and clearing the
  default battery-power restrictions — plain `schtasks` cannot set either).
