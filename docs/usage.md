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
| `a 1 "C:\work repos\client"` | Add one by absolute path; quote a path with spaces. |
| `d 1.2` | Delete a saved tab. |
| `n` | Add an empty window, copying the last window's geometry. |
| `c` | Recapture the live arrangement, retaining pinned tabs. |
| `s` | Save and exit. |
| `q` | Quit without saving; confirm if there are unsaved edits. |

Indices start at 1. Pinned entries survive later captures when their repository
is not running. If the added repo is running, the editor reads its agent kind
and command; otherwise it defaults to Claude Code. The editor splits input on
whitespace; double quotes keep whitespace inside an argument and are removed,
joining with any text they touch (`C:\"work repos"\client` is one argument).
Backslashes are never escapes, so `"C:\work repos\client\"` keeps its trailing
separator; a Windows path cannot contain `"`, so there is nothing to escape. An
unterminated quote is reported and the line is ignored, whatever command it
holds. Arrange live
windows and recapture when changing geometry is easier on screen.

`up` skips already-running repositories and missing directories, and opens the
remaining tabs in new windows based on saved geometry. It adapts positions and
DPI to available monitors; it does not move existing sessions. `open <repo>`
opens one missing repository in Terminal's current/most-recent window and uses
its launch settings from the selected layout when available.

## Status as JSON

`reloaded status --json` prints one JSON document and nothing else (a layout
file that cannot be parsed is still an error, not a document). It is built
from the same collected answers as the text, so the two always agree. It only
reads; it captures, launches and writes nothing.

| Field | Meaning |
| --- | --- |
| `schema` | `1`. Bumped only when an existing field changes meaning or is removed. |
| `layout` | `path`, `exists`, and `saved` (the layout's timestamp, or `null`). |
| `live` | Every session discovered: `path`, `pid`, `agent`. A shared directory appears once per session (the text's `live sessions` line counts directories). Without `psutil`, or for a process whose directory cannot be read, a session is not discovered, so absence here is not proof. |
| `shared` | Directories holding more than one session: `path`, `agents`. |
| `monitors` | `saved` and `current` monitor devices. |
| `readiness`, `terminal` | `ok` and `detail`. `ok` is `false` exactly when the text prints a warning; an undeterminable Terminal version is `true`, with the detail saying so. |
| `tabs` | Every saved tab in layout order: `window` (`w1`...), `title`, `path`, `agent`, and `state`: `running`, `launch`, or `missing` (no live session, and its directory is not there, so `up` skips it). |
| `not_in_layout` | Live sessions no saved tab accounts for, one entry per session: `path`, `agent`. |
| `would_launch` | How many tabs `up` would launch: those in state `launch`. |

Live paths are normalized (lowercase); saved paths are as the layout records
them. Without a layout, only `schema`, `layout`, `live` and `shared` are present
and `layout.exists` is `false`.

The exit code is the text command's: `1` when no layout is saved, otherwise `0`,
including when readiness, the Terminal version, or drift would print a warning.

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
a particular conversation. Capture saves a Codex tab's thread id
(`session_id`) when its process holds exactly one root thread open, which a
Codex on its own server does, and the launch then resumes that thread by id
(`codex resume <id>`) instead of the directory's newest; a thread that has
gone fails on screen rather than being swapped for another. Claude Code tabs
carry no id.

A Claude Code directory with no conversation yet gives `--continue` nothing to
resume, and Claude exits: at once, or, in a directory not yet trusted, once
its folder-trust question is answered. A Reloaded tab then starts a new
conversation, but only when Claude's folder for that directory holds no saved
conversation. Where one exists, the error stays on screen instead: a new
conversation would become the latest, the one later restarts resume.

Every Codex tab Reloaded launches runs on a server of its own: `--no-daemon`
is added after the program, or after `resume`, unless the command already has
it or names a `--remote` server. This is the one change Reloaded makes to a
captured Codex command, and it applies to tabs first opened by hand on Codex's
shared background server too, the next time Reloaded launches them. The
conversation is the same; only where it runs changes. On the shared server
(Codex 0.162.1, measured), a tab is not where its session runs: its commands
and MCP servers belong to the server, `restart --self` cannot find its tab,
and ending the tab leaves its turn running, so a teardown that sees it exit
has stopped nothing. Such tabs are not listed live in `codex agents` or the
desktop app; their conversations are in history as usual.

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
starts, a later failure can still leave a partially restarted batch. A tab is
found by its title, which is its directory's name, so a repository is refused
while another running session is in a directory with the same name.

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
skips sessions that stayed alive. If that capture loses a repository that is
still running — a window that read as empty rather than a session that went
away — the restart refuses before saving anything and before exiting anything.
The relaunch deploys from that capture, so a session it missed is dropped from
the layout either way; if the window recovers before teardown the session is
also exited and then not brought back.

Prefer `reloaded restart <repo>` in that state: a named restart takes no
capture and cannot rewrite the layout. `restart --force` accepts the capture
anyway, as `capture --force` does. `restart --dry-run` reports the refusal
instead of previewing a run that would stop, and exits non-zero to say so —
the only dry run in the CLI that does, because it is the only one that can
preview a refusal.

A named restart verifies a different process ID that stays for five seconds;
seeing the original process still running does not count as a restart, and
neither does a relaunch that dies at once. Read timeout and placement warnings
even if a tab remains visible.

Avoid overlapping restart commands for the same repository, or mixing a full
restart with named restarts. They share markers without transaction isolation.
Periodic capture can also observe a session's restart gap and temporarily drop
it; the next capture can add it back.

A timed-out named restart may still relaunch if the agent exits before its
marker expires. Do not delete restart markers manually while another session
may be consuming them.

## Self restart

Run from inside an agent session — through its shell tool, or as `/relaunch`
below — this restarts that session in the same tab:

```powershell
reloaded restart --self --dry-run
reloaded restart --self
```

The session is found through the parent process chain, so it is exact even
when another agent shares its directory. A detached helper writes its quit keys
— `/exit` for Claude Code — straight into the session's console input, which
needs no keyboard focus, and the command waits while the session quits the
ordinary way. A session that has not quit within a minute is ended by pid,
with its MCP servers. How it comes back depends on the tab:

- **A Reloaded tab** relaunches it from its restart marker, with its captured
  command.
- **A tab started by hand in PowerShell** gets the resume command written into
  its shell's console input, which needs no focus. For Claude Code, which
  hands its session id to the commands it runs, that command ends in
  `--resume <session id>`, so the same conversation returns even when the
  session was started with a bare `--resume` or with none. Codex hands its
  commands its session id too (`CODEX_SESSION_ID`), and its command comes
  back as `codex resume <id>` when the session's own process holds that
  thread's file open, which only a Codex on its own server does; otherwise it
  gets its original command line back. A hand tab under any other shell is
  refused before anything is ended.

A Codex tab on the shared background server is refused: its commands run in
the server, so nothing ties them to a tab.

A Reloaded tab replays its captured command, so what comes back is whatever
that command resumes — `--continue`, by default, the directory's latest
conversation.

The helper records what it did in `~/.reloaded/reloaded.log`. If a restart's
helper could not do its part, or stopped without saying, the next `--self` in
that directory says so before it starts. Its part ends at handing the session
to the tab's loop or writing the command into the shell; whether that command
then starts is not checked. `--self` cannot
take repository names. Codex's quit keys are two Ctrl+C, written the same way
(measured, 0.162.1, on its own server): an idle Codex quits on the first, and
the second is sent only if it is still running; a busy one has its turn interrupted by the first and
quits on the second. A session that ignores them falls back to being ended by
pid.

As a Claude Code user command, `~/.claude/commands/relaunch.md`:

```markdown
---
allowed-tools: Bash(reloaded restart --self)
description: "Restart this session in place, now."
effort: low
---
!`reloaded restart --self`

The command above has already run. Repeat its output in one line and nothing
else. Do not run anything: if it says the session is restarting, it will quit
on its own as soon as this reply ends.
```

Claude Code runs the `!` line while expanding the command, before the model is
asked anything, and takes `/exit` while that line is still running, so a
successful `/relaunch` quits without a model request — which, over a large
conversation, is the expensive part. The prompt below it is read only if the
restart was refused, or the session did not quit while the command waited.

Codex has no command that runs anything before the model is asked, so its
`$relaunch` is a skill, and costs one model turn. As
`~/.codex/skills/relaunch/SKILL.md`:

```markdown
---
name: relaunch
description: Restart this Codex session in place with reloaded, keeping its conversation. Only when the user invokes $relaunch.
---

Run `reloaded restart --self` once with your shell tool, then reply with its
output in one line and nothing else. Do not run anything else and do not
retry: if it says the session is restarting, this session quits on its own.
```

and `~/.codex/skills/relaunch/agents/openai.yaml`, so that only the user can
start it, never the model on its own:

```yaml
policy:
  allow_implicit_invocation: false
```
