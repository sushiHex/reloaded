# reloaded

[![CI](https://github.com/sushiHex/reloaded/actions/workflows/ci.yml/badge.svg)](https://github.com/sushiHex/reloaded/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Windows Terminal launcher for [Claude Code](https://claude.com/claude-code). It
captures the live arrangement of Claude Code sessions across Windows Terminal
windows — which repo, which window, tab order, exact screen position — and
redeploys that arrangement on demand or automatically at logon, unattended.

It is a launcher first, not a recovery tool: the same `up` command you'd run by
hand is what a scheduled task and a logon entry run when nobody's watching, so
the unattended path is exercised every day rather than only after a crash.

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

## Install

```
pip install -e .
```

This installs `psutil` and `uiautomation` and puts a `reloaded` command on
PATH. `capture`/`edit` report a clear message (not a traceback) if
`uiautomation` didn't make it in; session discovery degrades to "0 live
sessions" if `psutil` is missing.

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
and restored via `ctypes` (`GetWindowPlacement`/`SetWindowPlacement`) rather
than trusted to `wt`'s own `--size`, which is in character cells, not pixels.
If the monitor layout changes between capture and restore — a laptop undocked,
a display renumbered — `clamp_rect` re-anchors and DPI-rescales the saved rect
into whatever's actually available, instead of placing a window off-screen.

**The `--continue` resume prompt is suppressed without disabling
auto-compaction.** Claude Code's "resume from summary or continue as-is?"
prompt and mid-session auto-compaction are separate subsystems gated by
different thresholds. Each launched session gets
`CLAUDE_CODE_RESUME_TOKEN_THRESHOLD` set out of reach for that process only —
full context loads with no prompt, and normal auto-compaction is untouched.

**Unattended means actually unattended.** The scheduled reconcile task runs
via PowerShell's `Register-ScheduledTask`, not `schtasks.exe`, specifically
for two settings the classic tool has no flag for: an `ExecutionTimeLimit`
(so one hung run can't block reconciliation for days) and clearing the
default battery-power restrictions (so it still runs on an unplugged laptop).
The logon launcher is a `.vbs` run through `wscript.exe → pyw.exe` — deliberately
never a `.cmd` — because Windows 11 makes Windows Terminal the default console
host, and a stray console gets adopted as a tab inside whatever WT window is
already open.

**Crash-safe by construction.** Layout writes are atomic (temp file + rename).
A transcript whose last line was torn by a hard power-off is detected and
repaired (the removed bytes are kept alongside, never discarded) before
`--continue` ever sees it. A reboot-time deploy waits for `wt.exe`, `claude`,
and the saved monitors to actually be available — bounded, not a fixed guess
at how long boot takes.

## License

[MIT](LICENSE)
