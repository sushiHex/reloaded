# reloaded

[![CI](https://github.com/sushiHex/reloaded/actions/workflows/ci.yml/badge.svg)](https://github.com/sushiHex/reloaded/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Bring your [Claude Code](https://claude.com/claude-code) and
[Codex CLI](https://github.com/openai/codex) sessions back to their Windows
Terminal layout: repositories, window positions, sizes, and tab order.
Capture an arrangement once, restore missing sessions later, or restart a
selected session in its existing tab.

Reloaded is a **Windows command-line tool**. It manages terminal sessions;
Codex desktop sessions and agents running inside another agent are excluded.

## Get started

Use **Windows**, **Python 3.9+**, **Git**, and **Windows Terminal**. The `claude`
and/or `codex` commands for your sessions must be on PATH. PowerShell 7 (`pwsh`)
is preferred; Windows PowerShell 5.1 is used when it is unavailable.
`reloaded status` checks readiness and warns about unverified older Terminal
versions; the project's placement checks used Terminal 1.24.11911.0.

Install [pipx](https://pipx.pypa.io/latest/how-to/install-pipx.html) if needed
(current pipx requires Python 3.10+):

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
```

Open a new PowerShell terminal, then clone and install:

```powershell
git clone https://github.com/sushiHex/reloaded.git
cd reloaded
pipx install -e .
```

Keep the checkout in place: this editable installation runs its source directly.
If you prefer a virtual environment, see [source installation](docs/usage.md#source-installation).

### Save your first layout

Open your agent sessions in Windows Terminal and arrange the windows and tabs
as you want them restored. Then run:

```powershell
reloaded capture
reloaded status
reloaded up --dry-run
```

`capture` saves the arrangement to `~/.reloaded/layouts/default.json`.
`status` shows what is running and what would launch. The preview prints launch
commands without opening tabs; if everything is already running, there is
nothing to launch.

Later, restore missing sessions with:

```powershell
reloaded up
```

`up` skips repositories already running and opens missing sessions in new
windows. It does not rearrange existing windows. Use `reloaded edit` to change
the saved layout, then preview the next deployment.

**Check how your sessions resume.** Capture preserves the running command's
arguments. A plain `claude` or `codex` invocation may reopen a fresh conversation.
When no command was recorded, the bundled defaults bypass agent approval
prompts and, for Codex, the sandbox. Review [launch commands and resumption](docs/usage.md#launch-commands-and-resumption)
before using those defaults.

## Commands

| Command | Purpose |
| --- | --- |
| `reloaded status` | Compare the saved layout with live sessions and check readiness. |
| `reloaded capture` | Save the current arrangement, retaining pinned tabs. |
| `reloaded edit` | Edit the saved layout; also the default when running `reloaded` alone. |
| `reloaded up` | Launch missing sessions from the saved layout. |
| `reloaded open <repo>` | Add one repository as a tab in Terminal's current/recent window. |
| `reloaded restart <repo>...` | Restart named live sessions, keeping their tabs where possible. |
| `reloaded restart --self` | Hand off a restart of the calling agent session to a detached helper. |
| `reloaded restart` | Capture the live arrangement, exit its sessions, and redeploy it. |
| `reloaded down` | Gracefully exit recognized live sessions and close eligible empty windows. |
| `reloaded install-tasks` | Enable logon restoration and capture every five minutes. |
| `reloaded uninstall-tasks` | Remove both automation entries. |

`up`, `open`, `down`, and `restart` accept `--dry-run`. Put global options before
the command: `reloaded --layout work --repos-root "D:\repos" capture`.
A repository argument can be an absolute path or a name under `--repos-root`
(default `~/repos`). See [layouts and the editor](docs/usage.md#layouts-and-the-editor).

## Restart sessions deliberately

From another terminal or agent session, preview a named restart before running it:

```powershell
reloaded restart "C:\work\my-project" --dry-run
reloaded restart "C:\work\my-project"
```

`down` and `restart` take keyboard focus and send the agent's quit keys:
`/exit` then Enter for Claude Code, two Ctrl+C presses for Codex. Finish active
work first. Unresponsive sessions are reported; Reloaded does not force-kill them.

A bare `restart` saves a fresh capture over the selected layout before exiting
sessions. `--layout` selects a file; it does **not** restrict `down` or a full
`restart` to that layout's members. Name repositories when targeting a subset.

Hand-started sessions can be upgraded to Reloaded's launcher during a named
restart. This changes the shell's behavior: after a successful agent run ends,
its tab closes automatically. See [restart behavior](docs/usage.md#restarting-sessions)
for timeouts, fallback tabs, and concurrent restart limits.

## For agents

Invoke Reloaded through your shell tool. Start with `reloaded status`, inspect
the relevant `--dry-run`, and check the command's output and status afterward.
An opened tab or successful helper dispatch does not prove a conversation resumed.

To restart the terminal session you are running inside:

```powershell
reloaded restart --self --dry-run
reloaded restart --self --after 10
```

Save useful handoff notes before dispatching and finish the turn within the
delay. The default delay is five seconds. `--self` requires a recognized agent
session using Reloaded's launcher and cannot take a repository name. Its helper
currently resolves the repo name under `~/repos`; for other locations, use a
named restart by absolute path from another session or manual arming. For manual
exit timing, cancellation limits, and hand-started sessions, read
[self restart](docs/usage.md#self-restart).

## Restore at sign-in

First verify the saved layout and launch commands. Automation uses the system
Python selected by `py -3`, which needs dependencies separately from pipx:

```powershell
py -3 -m pip install psutil uiautomation
reloaded install-tasks
```

The logon launcher runs `up --unattended`. A separate five-minute task runs
`capture`; it refreshes the saved arrangement without launching sessions.
Use `reloaded uninstall-tasks` to disable both. `down` alone leaves automation
installed. See [automation setup and troubleshooting](docs/automation.md).

## Guides

| Guide | Read it for |
| --- | --- |
| [Layouts and sessions](docs/usage.md) | Installation alternatives, editing, launch defaults, restart behavior, and agent workflows. |
| [Automation](docs/automation.md) | Logon setup, shared state, logs, and troubleshooting. |
| [Architecture](docs/architecture.md) | Discovery, window placement, restart markers, transcript handling, and design limits. |

## Development

From a checkout, create and activate a virtual environment, then install and test:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -v
```

CI runs on Windows with Python 3.12. Tests mock desktop operations and guard
against sending real keystrokes, changing windows, or launching live agents.
Preserve those guards when adding tests.

## License

[MIT](LICENSE)
