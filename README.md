# reloaded

[![CI](https://github.com/sushiHex/reloaded/actions/workflows/ci.yml/badge.svg)](https://github.com/sushiHex/reloaded/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Windows Terminal launcher for [Claude Code](https://claude.com/claude-code). It
captures the live arrangement of Claude Code sessions across Windows Terminal
windows — which repo, which window, tab order, exact screen position — and
redeploys it on demand or unattended at logon.

It's a launcher first, not a recovery tool: the same `up` command you'd run by
hand is what the scheduled task and logon entry run when nobody's watching, so
that path is exercised every day, not just after a crash.

## Requirements

- **Windows** — this uses ctypes/Win32 APIs and Windows Terminal directly; no
  other platform is supported
- **Python 3.9+**
- **Windows Terminal**, ideally 1.24.11911.0 or newer — `--pos=x,y` and `-w -1`
  are verified at that version; `reloaded status` warns (does not block) if an
  older version is detected
- **[`claude`](https://claude.com/claude-code)** on PATH
- A PowerShell to run session commands in: **PowerShell 7 (`pwsh`)** is
  preferred and auto-detected; if it isn't installed, Windows PowerShell 5.1
  (present on every supported Windows version) is used automatically

CI runs the unit suite on Windows with OS-facing calls (UI Automation, real
window placement) mocked out — it verifies the logic, not a live Windows
Terminal.

## Install

```
pipx install .
```

(or, from a clone you intend to keep developing against: `pipx install -e .`)

[`pipx`](https://pipx.pypa.io) installs `reloaded` into its own isolated
environment and reliably puts the `reloaded` command on PATH — plain
`pip install -e .` also works, but on Windows it frequently installs the
console script into a `Scripts` directory that isn't on PATH by default,
silently leaving you with a package that's installed but not runnable as a
bare command. If you don't have pipx yet: `pip install --user pipx` then
`pipx ensurepath` (new terminal needed afterward).

Either way this installs `psutil` and `uiautomation`. `capture`/`edit`
report a clear message (not a traceback) if `uiautomation` didn't make it
in; session discovery degrades to "0 live sessions" if `psutil` is missing.

The scheduled task and logon launcher `install-tasks` registers always run
against the system-wide `py`/`pyw` launcher, not whatever Python `reloaded`
itself runs under — since both pipx and a virtualenv install into an
isolated environment, that system-wide Python also needs
`pip install psutil uiautomation` (a plain `pip install`, no `-e`, is enough)
or the unattended runs will fail with an import error.

## Commands

| Command | Does |
|---|---|
| `reloaded` (bare), or `reloaded edit` | Open the interactive layout editor |
| `reloaded capture` | Snapshot the current arrangement into the saved layout |
| `reloaded up` | Deploy the saved layout (`--dry-run` to preview, `--unattended` for scheduled/logon use) |
| `reloaded status` | Compare the saved layout against what's actually running; also reports readiness and the detected Windows Terminal version |
| `reloaded open <repo>` | Add one repo as a new tab in the *current* window (see also: `up`, which deploys the whole saved layout into new windows) |
| `reloaded down` | Gracefully `/exit` every live Claude Code tab, then close windows that were entirely made up of sessions that exited (`--dry-run` to preview) |
| `reloaded restart` | Capture the current arrangement, gracefully `/exit` everything, then relaunch it exactly as it was (`--dry-run` to preview) |
| `reloaded install-tasks` | Register the logon launcher and the 5-minute reconcile task |
| `reloaded uninstall-tasks` | Remove both |

State lives under `~/.reloaded/` — layouts in `layouts/<name>.json`, an
unattended-run log at `reloaded.log`.

## How it works

**No cooperation required.** Reloaded asks nothing of the Claude Code sessions
it manages — no heartbeat, no hook, no plugin. It reads what's already there:
`psutil` for which sessions are live and their working directory, and Claude
Code's own transcript files (`~/.claude/projects/*/*.jsonl`) for each
session's custom title. Window grouping and tab order come from UI Automation,
since Windows Terminal's tabs aren't separate Win32 windows.

**Exact geometry, not "close enough."** Window position and size are captured
and restored in real pixels via `ctypes` (`GetWindowPlacement`/
`SetWindowPlacement`) — `wt`'s own `--size` is in character cells, not pixels,
so it can't do this alone. If the monitor layout changes between capture and
restore, the saved rect is re-anchored and DPI-rescaled into whatever's
actually available rather than placed off-screen.

**Resume without the prompt.** Claude Code's "resume from summary or continue
as-is?" prompt and mid-session auto-compaction appear, from observed
black-box behavior, to be separate subsystems gated by different thresholds —
not something reloaded can verify against closed-source internals, so treat
it as current-version behavior rather than a guarantee. Each launched session
gets `CLAUDE_CODE_RESUME_TOKEN_THRESHOLD` set out of reach for that process
only — full context loads with no prompt, and auto-compaction is untouched.

**Unattended means actually unattended.** The scheduled reconcile task runs
`capture` (not `up`) every 5 minutes to keep the saved layout in sync with
whatever's actually running — it only re-snapshots, it never launches
anything. Relaunching only ever happens via the logon launcher, a `.vbs` run
through `wscript.exe → pyw.exe` at your next sign-in — deliberately never a
`.cmd`, because Windows 11 makes Windows Terminal the default console host,
and a stray console gets adopted as a tab inside whatever WT window is
already open. The reconcile task is registered via PowerShell's
`Register-ScheduledTask`, not `schtasks.exe`, specifically for two settings
the classic tool has no flag for: an `ExecutionTimeLimit` (so one hung run
can't block reconciliation for days) and clearing the default battery-power
restrictions (so it still runs on an unplugged laptop).

**Crash-safe by construction.** Layout writes are atomic. A transcript torn by
a hard power-off is detected and repaired — the removed bytes are kept
alongside, never discarded — before `--continue` ever sees it. A reboot-time
deploy waits for `wt.exe`, `claude`, and the saved monitors to actually be
available, bounded rather than a fixed guess at how long boot takes.

**`down` types, it doesn't kill.** There is no IPC between `reloaded` and the
sessions it manages, so a graceful exit means the real thing: for each tab
recognized as a live Claude Code session, `down` selects that tab, brings its
window to the actual OS foreground (verified — Windows can silently refuse a
foreground request, and this never sends a keystroke on an unverified
foreground), and types `/exit` followed by Enter, the same as doing it by
hand. It then polls (waiting up to 20 seconds per session) for that session's
process to actually end before moving on. A window is only closed once every
one of its tabs was a recognized Claude session and every one of them exited
— a manual tab sharing that window, or a holdout that never responded, keeps
the whole window open rather than being force-closed. If the logon launcher
is installed, `down` warns that it will relaunch everything again at your
next logon unless `uninstall-tasks` is also run — the periodic reconcile task
only re-captures the current state (see below), it never relaunches
anything, so it isn't the thing to worry about here.

**`restart` captures before it exits, on purpose.** The order matters: a
capture reads live process cwd and UI-Automation tab/window state, so it has
to happen *before* anything exits, or there would be nothing left to capture.
`restart` therefore always saves the layout it is about to tear down —
overwriting whatever was last saved — rather than deploying a possibly-stale
one, so "exactly as it was" means the arrangement at the moment `restart` ran,
not at the last `capture`. It then runs the same graceful `down` sequence,
and relaunches from that fresh capture; a session that didn't exit in time is
skipped rather than duplicated, the same as any other already-running session
`up` encounters.

## License

[MIT](LICENSE)
