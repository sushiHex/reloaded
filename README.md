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
| `reloaded restart <repo>...` | Restart only the named sessions, in place — same window, same tab, same position (`--dry-run` to preview) |
| `reloaded restart --self` | Restart the session you are calling *from*, in place — hands it to a detached process that outlives the exit (`--arm-only` to just leave a marker) |
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

**`restart <repo>` keeps the tab instead of restoring it.** A tab's position
is lost the moment it closes, and Windows Terminal ships `moveTab` as an
action with no default keybinding — so there is no key to send to put a tab
back at an index. Rather than restore the slot, a named restart never gives it
up: the launcher command runs `claude` inside a loop, and `restart <repo>`
drops a marker file that tells the tab's own shell to go round again instead
of exiting. Same window, same slot, and no window is closed along the way.

Each marker is armed as its own tab's turn comes, not for the whole batch up
front. Targets are exited serially with a wait each, so a marker written up
front for the last of six repos would spend every earlier wait ageing toward
its two-minute TTL and be discarded before its tab was ever asked to exit.
Armed per tab, a marker only has to survive its own exit.

The marker is consumed on read, so one request can only produce one restart —
and if the delete fails, the loop stops rather than relaunching against a
marker it could not consume. A marker at a path `Remove-Item` cannot remove
otherwise relaunches `claude` forever, silently, because the failure is
suppressed.

**No marker is ever deleted by the command that wrote it.** Deleting one means
guessing that its tab will not read it, and that guess is unrecoverable when
wrong: the shell breaks out of the restart loop and the tab closes, destroying
the session the restart was meant to bring back. A session that timed out is
reported as still armed, since "did not exit within 20 seconds" is not "will
never exit" — it will restart if it exits inside the TTL. Staleness is handled
where it is safe to handle it: the TTL makes an old marker inert, and the next
run sweeps the directory.

**A named restart is all-or-nothing.** Every repo named must be both running
and reachable through a Windows Terminal tab. If any one of them resolves to no
tab, nothing is armed and nothing is exited — restarting the reachable subset
and returning success while silently skipping the rest is worse than refusing.

**A session that ignores `/exit` is not reported as restarted.** The relaunched
session is identified by a *change* of pid, not by the presence of one: a
session that never exited is still running under its original pid at the same
cwd, which any "is something live here" check would happily accept.

A session started before this existed has no loop in its shell, so its tab
closes on `/exit` as it always did. That is detected rather than predicted —
and the window is asked whether its tab actually went away, rather than
inferring it from the missing pid. A relaunch that fails inside the startup
grace deliberately leaves its tab open showing the error; concluding "the tab
closed" there would open a second tab beside the message you need to read.

### Sessions started by hand

Not every session comes from `reloaded`. One opened by hand — a plain tab, then
`claude` typed at the prompt — has none of the launcher in its shell, so
neither restart path fits it: no loop will relaunch it, and its tab does not
close, so reopening one would leave two tabs for one repo.

`restart <repo>` tells them apart by reading the parent shell's command line,
which contains the launcher verbatim for a `reloaded`-launched tab. Anything
unreadable counts as hand-launched, the recoverable guess of the two.

For a hand-launched session the marker is never armed — nothing would read it —
and after `/exit` the launcher is put into the shell that is left waiting at a
prompt. Same tab, same slot, and **the tab is upgraded**: from then on it
carries the restart loop and closes itself when the session ends, exactly like
any other. `--dry-run` says so before you commit to it, because that change is
permanent.

The launcher goes into a file and one short line runs it, rather than being
typed out. `SendKeys` reads `{` and `(` as syntax; escaped, the launcher is 838
keystrokes, and they did not all arrive when tried. Moving it into a file costs
one thing — the launcher's closing `exit` runs in script scope, where it need
not reach the host shell — so the script stops the shell by pid instead, which
works from any scope and was verified against a real shell.

**Reloaded names the tabs it launches.** `new-tab --title <repo>
--suppressApplicationTitle`, so a tab it opened is one it can find again.

Without it, a tab shows whatever the running program calls itself — `claude` —
which matches no repo and no recorded title. Measured: a freshly opened session
stayed unresolvable for the full two minutes it was watched, because reloaded
resolves a tab by its title and Claude Code only writes one into its transcript
once the session has been used. So `open` handed back a tab that `down` and
`restart` could not touch until you had talked to it.

`--suppressApplicationTitle` is required rather than cautious: with `--title`
alone the program overwrote it inside five seconds and it never came back —
watched at 5, 15, 30, 60 and 90 seconds, `claude` every time.

The cost, stated plainly: a session you rename will not show that name in the
tab strip, because reloaded is holding the title. Every recorded title on the
machine this was measured on was already its repo's directory name, so nothing
changed visually there — but if you do rename sessions and want that in the
strip, drop the one flag in `deploy.new_tab_args`.

**`restart --self` hands the job outside.** A session cannot drive its own
restart the way `restart <repo>` drives someone else's. That path selects the
tab, types the quit keys, and waits for the session to come back — and pointed
at yourself, the waiting happens in a process that dies with the session it
just ended.

So `--self` does not try. It spawns a detached process running the ordinary
`restart <repo>`, and returns. That process is not inside the session, so it
survives the exit and reports nothing to a console nobody is reading; it uses
the same tab resolution and the same guards on where its keystrokes land as
any other named restart. `--after` (5s by default) gives the calling session
time to finish the turn it is in the middle of before anything types into it.

`DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB`, because Claude Code runs its
tool calls inside a job object and a plain child is killed when the call ends.
Verified by spawning one that wrote a file eight seconds after its parent
exited. Breakaway is refused on some systems; the fallback is a plain detached
spawn, and if that does not survive either, the marker the delegate writes
still gets the restart done.

`--arm-only` is the older, quieter shape: write the marker and tell you to quit
the session yourself. Nothing is spawned, the two-minute TTL applies, and
`--cancel` calls it off. `--cancel` only clears a waiting marker — a restart
already dispatched runs outside the session and cannot be recalled from within
it.

It refuses in the two cases where arming would be a lie: called from an
ordinary terminal, where there is no session above it to arm, and called from
a hand-launched session, whose shell has no loop to read the marker. It finds
the session by walking the parent process chain rather than by matching the
current directory — a tool call can run anywhere, and a directory match would
answer with the wrong session as readily as the right one.

It also refuses a repo name alongside `--self`. `--self` is checked first, so
naming both would arm the calling session and ignore what was named — and a
slash command that fails to interpolate its arguments hands over the literal
`$ARGUMENTS`, which would have been read as a repo.

As a Claude Code slash command, `~/.claude/commands/relaunch.md`:

```markdown
---
allowed-tools: Bash(reloaded restart --self:*)
description: "Arm this session to relaunch in its own tab; then /exit."
---
!`reloaded restart --self $ARGUMENTS`
```

The `!` runs at load time, so `/relaunch` arms on the keystroke rather than
waiting for a tool call. **Do not name it `/restart`** — that is a built-in
alias for `/update` ("Switch to the latest version"), shipped disabled in
2.1.x. A user command with that name works today only because the built-in's
`isEnabled` returns false, and an update can flip it.

### Known limits of the named restart

*Placement on the fallback path is best-effort.* `wt -w 0` means Windows
Terminal's most recently used window, which is normally the one just
foregrounded to type `/exit` — but it is not a captured window handle. If the
original window closed with its last tab, the session lands in whichever WT
window is MRU, or a new one.

*Two overlapping restarts of the same repo are not distinguished.* A marker
carries no nonce or generation, so concurrent requests collapse into one
restart. Not fixed: adding a handshake means the shell has to report back, and
nothing in the intended use — one person, one repo at a time — produces the
race.

*The 5-minute reconcile can drop a repo from the saved layout during the
restart gap.* A session is briefly absent from `live`, which is exactly how a
session the user deliberately closed looks. Not fixed: the next reconcile
re-adds it once it is back, so this self-heals, and the alternative is a lock
file that has to be correct when `reloaded` is killed.

*`restart <repo>` and a full `restart` do not coordinate.* Running both at once
lets the full restart's `/exit` consume the targeted marker.

## Agent kinds

A managed tab may hold a **Claude Code** session or a **Codex CLI** one. They
are told apart by the running process — `claude.exe` or `codex.exe` — and the
kind is recorded on the tab when it is captured.

A second `codex.exe` runs as a child of the Codex desktop app. That is not a
terminal tab, so it is excluded by its parent process; excluding by image name
would lose the real tab along with it. Both run at once on a machine with the
desktop app installed.

**Each tab remembers how it was launched.** Capture reads the live process's
own command line, so a session comes back with the flags it was actually
running rather than with whatever this tool would have picked. The executable
is reduced to its bare name — an absolute path would pin the layout to one
install location. A tab with no captured command falls back to its kind's
default.

**Quitting differs by kind.** Claude Code takes `/exit` then Enter; Codex takes
Ctrl+C twice. Both are sent one keystroke at a time with a pause between, never
joined — Claude Code's slash-command menu will not accept an Enter arriving ten
milliseconds behind the command, and two interrupts sent together read as one.

**Readiness waits only for what a layout needs.** A layout of Codex tabs does
not wait for `claude` on PATH, and the reason it reports names whichever
binary is actually missing.

**The torn-transcript guard is Claude Code's.** It ends in a truncation, so it
only runs for kinds whose failure mode has been verified. A repo that used to
run Claude and now runs Codex still has an entry in that corpus; it is skipped
rather than repaired on the Codex tab's behalf.

**Codex asks about a directory it has not seen.** It prompts to trust the
contents before starting, so an unattended `up` into a fresh repo opens the tab
and waits there. `up` names tabs that opened without starting a session — it
does not answer the prompt, because that is a security question.

A layout written before any of this carries no `agent` field and reads as
Claude Code, so it keeps working with no migration.

## License

[MIT](LICENSE)
