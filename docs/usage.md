# Layouts and sessions

[Back to the README](../README.md)

## Source installation

The README uses an editable pipx installation so `reloaded` is available on PATH.
For a virtual environment instead, run these commands in a stable checkout:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m reloaded --help
```

Inside that environment, `python -m reloaded` and `reloaded` invoke the same CLI.
Outside it, use the absolute path to `.venv\Scripts\reloaded.exe` if the command
is not on PATH. `pipx install .` is also supported for a non-editable installation.
Both methods install `psutil` and `uiautomation`.

## Layouts and the editor

Global options go **before** the subcommand:

```powershell
reloaded --layout work --repos-root "D:\repos" capture
reloaded --layout work status
reloaded --layout work up --dry-run
```

Layouts live at `~/.reloaded/layouts/<name>.json`; the default name is `default`.
`--repos-root` resolves short repository arguments and helps match tab titles.
Use absolute paths for repositories elsewhere or when a short name is ambiguous.

`capture` saves recognized live tabs, grouping them by window and recording
geometry and launch commands. It keeps the old layout when no tabs are found,
and refuses a capture that loses previously saved repositories still detected
as running. Recheck after windows finish starting. `capture --force` accepts a
partial capture deliberately; sessions you actually closed may disappear from
a normal capture without forcing.

Run `reloaded edit` for the interactive editor. Its changes affect the saved
layout, not the live desktop. With no saved layout, it captures a starting
arrangement in memory; save to persist it.

| Editor input | Effect |
| --- | --- |
| `m 1.2 2` | Move window 1's second tab into window 2. |
| `o 1.2 1` | Move that tab to the first position in its window. |
| `a 1 my-project` | Add a repository to window 1 and pin it. |
| `d 1.2` | Delete a saved tab. |
| `n` | Add an empty window, copying the last window's geometry. |
| `c` | Recapture the live arrangement, retaining pinned tabs. |
| `s` | Save and exit. |
| `q` | Quit without saving; confirm if there are unsaved edits. |

Indices start at 1. Pinned entries survive later captures when their repository
is not running. If the added repo is running, the editor reads its agent kind
and command; otherwise it defaults to Claude Code. The editor splits input on
whitespace, so use the JSON layout for paths containing spaces. Arrange live
windows and recapture when changing geometry is easier on screen.

`up` skips already-running repositories and missing directories, and opens the
remaining tabs in new windows based on saved geometry. It adapts positions and
DPI to available monitors; it does not move existing sessions. `open <repo>`
opens one missing repository in Terminal's current/most-recent window and uses
its launch settings from the selected layout when available.

## Launch commands and resumption

Capture stores each tab's `agent` and `command` from its live process. Relaunch
reuses the arguments but resolves the executable by its bare name on PATH.
If no command was captured, Reloaded uses these bundled defaults:

| Agent | Default command |
| --- | --- |
| Claude Code | `claude --dangerously-skip-permissions --continue` |
| Codex CLI | `codex resume --last --dangerously-bypass-approvals-and-sandbox` |

These defaults bypass permission prompts; the Codex default also bypasses its
sandbox. To retain your chosen controls, start the agent with the flags you
want and capture it, or set an explicit `command` on the saved tab. A tab's
`agent` is `claude` or `codex`; a missing agent field defaults to Claude Code.
There is no `open --agent` option: capture the desired kind or edit its saved
entry before using `open`.

A plain `claude` or `codex` command can start a new conversation after restart.
Named restart warns about commands it does not recognize as resuming, but the
warning does not pause execution or rewrite the command. Review launch settings
before exiting. Restoring a directory and tab is not a guarantee of restoring
a particular conversation; Reloaded does not store a separate conversation ID.

Reloaded fixes the titles of tabs it launches to repository directory names so
it can find them again. A renamed conversation may therefore keep showing the
repository name in the tab strip. Codex directory-trust prompts require a user
response in the tab; Reloaded reports them without answering them.

## Restarting sessions

From outside the target session:

```powershell
reloaded restart "C:\work\my-project" --dry-run
reloaded restart "C:\work\my-project"
```

Before a named restart, every requested repository must be running and resolve
to a terminal tab. If any cannot be found, none are exited. Once execution
starts, a later failure can still leave a partially restarted batch.

A Reloaded-launched tab restarts through its shell loop, retaining its slot.
A hand-started session is exited and its waiting shell receives a launcher
script; subsequent runs use Reloaded's restart loop and automatic tab closing.
If the original tab disappears, a replacement may open in Terminal's most-recent
window, so fallback placement is best-effort.

`down` and full `restart` operate on recognized live sessions, independently
of which layout file was selected. They take foreground focus and send quit
keystrokes serially. A window closes only when all its original tabs were
recognized targets and exited; ordinary tabs or unresponsive sessions keep it
open. Quit attempts wait up to 20 seconds per session and include a retry.
Prompts or active work may consume the keystrokes instead of exiting.

Full `restart` saves a fresh capture **before** teardown, then deploys it and
skips sessions that stayed alive. A named restart verifies a different process
ID; seeing the original process still running does not count as a restart.
Read timeout and placement warnings even if a tab remains visible.

Avoid overlapping restart commands for the same repository, or mixing a full
restart with named restarts. They share markers without transaction isolation.
Periodic capture can also observe a session's restart gap and temporarily drop
it; the next capture can add it back.

## Self restart

An agent uses these shell commands **inside its own terminal session**:

```powershell
reloaded restart --self --dry-run
reloaded restart --self --after 10
```

Reloaded identifies the caller through its parent process chain, not the shell's
current directory. The normal path starts a detached helper with a five-second
default delay; `--after` changes that delay. A successful return confirms
handoff, not completion. Save a concise handoff note, finish the turn before
the delay expires, and check the session after it returns.

The detached helper currently receives the repository's basename and uses the
default `~/repos` root; `--repos-root` is not forwarded. For a session outside
that root, use a named restart with its absolute path from another terminal,
or the manual arming path below, which uses the caller's actual directory.

`--self` cannot take repository names. It refuses an ordinary terminal and a
hand-started session whose shell has no Reloaded loop. Restart a hand-started
session once from another terminal using its repository path to upgrade it.

For manual exit timing, arm the marker without dispatching a helper:

```powershell
reloaded restart --self --arm-only
```

Quit through the agent's UI within two minutes. After that, the marker expires
and an ordinary quit closes the tab instead of relaunching it. To disarm a
waiting marker while still in that session:

```powershell
reloaded restart --self --cancel
```

Cancellation clears only the marker. It cannot recall a helper already
dispatched by the normal `--self` command. Likewise, a timed-out named restart
may still relaunch if the agent exits before its marker expires. Do not delete
restart markers manually while another session may be consuming them.
