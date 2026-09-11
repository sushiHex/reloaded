# Architecture

[Back to the README](../README.md)

Reloaded discovers local terminal sessions, captures a layout, and replays its
launch commands. The same deployment path serves interactive `up`, full restart,
and unattended logon restoration.

## Discovery and identity

`psutil` supplies process identity and working directories. Process ancestry
excludes the Codex desktop app and agents nested inside another agent.
Claude transcript files under `~/.claude/projects/` provide custom titles;
UI Automation supplies Windows Terminal windows, tab titles, and ordering.

Resolution combines recorded titles and repository directory names. Reloaded
sets `--title <repo> --suppressApplicationTitle` for tabs it creates, keeping
those titles stable even when an agent would normally replace them. That makes
newly opened sessions discoverable before they write transcript titles, at the
cost of hiding later conversation-name changes in the tab strip.

Live discovery and restart markers are keyed by normalized working directory.
Multiple simultaneous sessions in the same repository are therefore not tracked
as independent identities. A restored layout records an agent kind and command,
not a separate conversation ID. See [Codex session identity](codex-session-identity.md)
for the investigation and limits of reconstructing identities from rollout files.

## Capture and placement

`layout.py` stores windows, ordered tabs, monitor identifiers, DPI, and pixel
geometry as JSON. Writes use atomic replacement. Optional fields have defaults
so older layouts continue to load; missing agent kinds mean Claude Code.

Win32 placement APIs restore real pixel positions and dimensions. Saved frame
insets account for Terminal's invisible resize borders, and changed monitor
layouts trigger re-anchoring and DPI scaling. A launch must be associated with
its new window before geometry can be applied; failures are reported.

Pinned editor entries survive capture even when their sessions are absent.
Normal capture refuses to drop previously saved repositories that are still
running but were missed by the tab scan. A full restart performs its own fresh
capture and saves it before teardown; it does not use `cmd_capture`'s shrinkage
check. Preview a full restart's target set before proceeding.

## Launching and closing

Each tab runs its captured command, or the agent kind's fallback, inside a
PowerShell loop. PowerShell 7 is selected when available, otherwise Windows
PowerShell 5.1. Startup is staggered by four seconds between sessions.
A run ending within the ten-second startup grace leaves its shell visible so
an error can be read; a longer completed run closes its tab.

Teardown selects the intended tab and verifies foreground focus before sending
input. Claude receives `/exit` and Enter separately; Codex receives separated
Ctrl+C presses. A prompt, review, or side conversation may consume a key instead
of quitting. Teardown retries once and checks process exit rather than treating
sent keystrokes as success.

Only windows whose original tabs were all recognized targets and whose sessions
exited qualify for closure. Closing a window is verified afterward. Ordinary
tabs and holdouts remain open; agent processes are not forcibly killed.

## Restart markers

A named restart arms a marker as each target's turn begins. On agent exit, its
shell consumes the marker and starts the command again in the same tab. Markers
expire after 120 seconds and are consumed once. If deletion fails, the loop
stops instead of repeatedly launching against an unconsumed marker.

The restart coordinator leaves timed-out markers in place because a slow
session may still exit within the valid interval. The launcher handles expiry;
subsequent named restarts sweep stale markers. Explicit self-cancellation can
remove a waiting marker while its session is still alive.

For hand-started sessions, Reloaded types a short command invoking a generated
PowerShell script into the waiting shell. Keeping the full launcher in a file
avoids SendKeys metacharacters and dropped long input. The script ends the host
shell after a successful run so the adopted tab follows normal launcher behavior.

Self restart delegates to a detached process using breakaway-from-job flags when
Windows permits them, with a plain detached fallback. The helper drives the
ordinary named-restart path. Its survival depends on host process supervision;
its initial dispatch cannot certify the final result.

There is no generation or nonce on a restart marker. Overlapping restarts of
one repository collapse into one request; full and named restarts do not
coordinate. Reconcile can temporarily capture the gap between old and new
processes. If a fallback must open a new tab, Terminal's most-recent window may
receive it instead of the original one.

## Transcript handling

Claude transcript sizes produce startup warnings at 100 MB and above.
Before deployment, Reloaded repairs a torn Claude JSONL tail and saves removed
bytes beside the transcript as `.torn`. `capture` prunes these backups after
30 days. The repair index remains Claude-only so a Codex rollout is never
truncated on a Claude session's behalf.

The launcher clears `CLAUDE_CODE_CHILD_SESSION` for its child and sets
`CLAUDE_CODE_RESUME_TOKEN_THRESHOLD=999999999`. The project observed that the
latter suppresses Claude's resume-summary choice without changing automatic
compaction; this is version-dependent behavior, not a guaranteed API contract.

## Automation

A per-user Startup VBS entry launches `up --unattended` through `pyw.exe -3`.
A separate task registered through PowerShell runs periodic capture with an
execution limit and battery restrictions disabled. Both embed the package
location and selected layout, and use system Python dependencies rather than
assuming the interactive environment is available. See [automation](automation.md)
for setup and diagnostics.
