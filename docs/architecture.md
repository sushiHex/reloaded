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

Capture saves one tab per live session, not per directory, so a repository
running Claude Code in one tab and Codex in another keeps both. Their tabs carry
the same title, so no title can tell them apart; each session is placed by its
own process instead. Every Windows Terminal tab's console has a hidden
`PseudoConsoleWindow` belonging to the tab's shell and owned by the Terminal
window hosting it (`win32.tab_shells`), and an agent runs under its tab's shell.
Titles now only order a window's tabs. A restore then skips a tab only when that
directory and agent kind are both already running.

The rest of the tool is still keyed by normalized working directory: live
discovery for restarts, restart markers, and teardown's aim. `status` notes any
directory hosting more than one agent, since `restart` refuses one and `down`
may quit only one of its sessions. A restored layout records an agent kind and
command, not a separate conversation ID. See
[Codex session identity](codex-session-identity.md) for the investigation and
limits of reconstructing identities from rollout files.

`restart <repo>` refuses such a directory outright, and refuses the whole batch
rather than the one repository. Two independent choices go into a target —
discovery keeps the last process enumerated at a cwd, `plan_down` keeps the
first tab that resolves to it — so the result can carry one session's pid
beside another session's tab: the quit keys reach one, the wait watches the
other, and the outcome is reported against the wrong one. `restart --self`
does not refuse: it takes its session's pid from its own process ancestry and
ends exactly that process.

It still arms the directory's marker, because that is how a loop is told to
relaunch, and the marker does not name a session. A second looping session in
the same directory that exits while the marker is armed — from just before the
caller is ended until its own loop, or for a hand tab the helper, takes it —
would take it instead, and the caller would not come back. Closing
that needs a marker that names a session rather than a directory, which the
launcher's generated PowerShell cannot be given retroactively — it is already
running in every open tab.

## Capture and placement

`layout.py` stores windows, ordered tabs, monitor identifiers, DPI, and pixel
geometry as JSON. Writes use atomic replacement. Optional fields have defaults
so older layouts continue to load; missing agent kinds mean Claude Code.

Win32 placement APIs restore real pixel positions and dimensions. Saved frame
insets account for Terminal's invisible resize borders, and changed monitor
layouts trigger re-anchoring and DPI scaling. A launch must be associated with
its new window before geometry can be applied; failures are reported.

A capture records what it changed about the layout — repositories gained or
lost, and refusals — in `~/.reloaded/reloaded.log`, and says nothing when the
repository set is unchanged. Recorded regardless of whether the run was
attended, unlike deploy output, because it describes the user's saved state
rather than the run; a loss is printed as well, since the capture summary shows
what remains and never what went. The reconcile task rewrites the layout every
five minutes under `pyw.exe` with no console, so before this it changed the one
durable artifact thousands of times without leaving a trace.

Pinned editor entries survive capture even when their sessions are absent.
A capture refuses to drop previously saved repositories that are still running
but were missed by the tab scan — one check, shared by `capture` and a full
restart, differing in what it says proceeding would cost and what it offers
instead. A restart is the costlier of the two. Its relaunch deploys from the
capture, so a repository the capture missed is dropped from the layout for
certain; whether that session is also exited is a race, because `plan_down`
builds its targets from the same `tabs.list_tab_items` call that timed out, so
a window still wedged at teardown yields no targets and survives, while one
that recovers in between is exited and then not brought back. The refusal
therefore lands before the save and before any keystroke. `--force` accepts the
capture deliberately in both commands; for a restart the message names
`restart <repo>`, which takes no capture, before it names `--force`.

## Launching and closing

Each tab runs its captured command, or the agent kind's fallback, inside a
PowerShell loop. PowerShell 7 is selected when available, otherwise Windows
PowerShell 5.1. Startup is staggered by four seconds between sessions.

A window is created, under a name unique to the launch, with its first tab
only. Its remaining tabs are added to it by that name once every window has
been placed. Windows Terminal passes a window's new size only to the tab in
front; a background tab keeps the size it was created at until it is first
shown, and its agent has drawn for that size by then. When a restore created
every tab at once, 11 of 15 tabs were still at the default 120x30 in windows
holding 133x71, and each was drawn jumbled - overlaid lines, a footer stuck
mid-window - when switched to, until the window was resized by hand. A tab
added to a window that already has its size starts at that size.

That stagger is a sleep inside each launched shell, not something the deploy
waits out, so a thirteen-session restore is still starting its last session
nearly a minute after the deploy returns. A deploy therefore leaves a marker
covering that window: a capture taken inside it would see tabs whose agents
have not started, read them as closed, and write a layout with them missing.
The marker expires on its own, because the process that wrote it returns long
before the sessions it started have finished starting. For the same reason a
deploy only reports a tab as failed once its own staggered turn has passed.
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
subsequent named restarts sweep stale markers.

For hand-started sessions, Reloaded types a short command invoking a generated
PowerShell script into the waiting shell. Keeping the full launcher in a file
avoids SendKeys metacharacters and dropped long input. The script ends the host
shell after a successful run so the adopted tab follows normal launcher behavior.

Self restart (`relaunch.py`) does not use the teardown path at all. Aimed at
itself, that path's UI Automation keystrokes need keyboard focus, which Windows
refuses a background process once the user looks elsewhere. Instead a detached
helper writes the agent's quit keys into the session's console input buffer
with `WriteConsoleInputW`, which needs no focus. Claude Code takes `/exit` even
while it is running the `!` line that started the helper, and that command
waits for it, so the session quits before any model request is made.

It is asked to quit rather than ended by pid, which is what this first did.
The change was made after relaunched sessions came back drawn wrongly, but
every tab that happened in had been created at 120x30 in the background by a
logon restore (see Launching and closing), which is the likelier cause. A quit
is still the better way down: the session shuts its MCP servers and its
transcript itself. Ending by pid - its children with it, except the helper's
own line - is the fallback for a session that has not quit within a minute, on
a marker written fresh first.

The helper then waits for the pid to die and gives a loop two seconds to take
the marker. Its own attempt to delete the
marker settles it: if the file is already gone, something took it — the
caller's loop, or in a shared directory another one — and the helper writes
nothing; if the delete succeeds, it writes the resume command into the
shell's console input buffer with `WriteConsoleInputW`. The buffer belongs to
the shell's console, so it needs no focus and cannot land in another window.
It does queue behind anything already typed there, and it assumes the shell
is an interactive prompt; a `pwsh -Command "claude; exit"` tab closes instead,
and the helper can only log that.

The helper is started through an intermediate process that exits at once.
Claude Code ends the process tree of the command it is running when it quits,
and the command is still running then - waiting for that quit - so a helper
started from it directly died with the session. It is spawned windowless and
with breakaway-from-job, and nothing changes if Windows refuses that. The
helper removes the marker if the session outlives its wait. Its only report is
the log.

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
location, the selected layout and the repository root, and use system Python
dependencies rather than assuming the interactive environment is available.
A scheduled run has its own working directory and its own default root, so
configuration not baked in at registration is not recoverable later; an
install prints what it registered, because a later run that omits an option
replaces it with a default. The root governs the reconcile, which resolves
tab titles against it; `up` deploys from the absolute paths in the layout and
does not read it. See [automation](automation.md)
for setup and diagnostics.
