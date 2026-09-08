# Supporting Codex CLI tabs

`reloaded` assumes every tab it manages is a Claude Code session. This adds a
second kind — the Codex CLI — with full parity: discovery, capture, `up`,
`status`, `down` and `restart`.

The work starts with a bug that has nothing to do with Codex, because nothing
above it can work until that bug is gone.

## The bug underneath everything

`tabs.select_tab` calls `Select()` on the tab item inside a `try/except` that
swallows failure, then returns whether the **window** reached the foreground.
Its own docstring names the gap:

> this verifies which *window* has focus, not which *tab* is active within it.
> Select() is best-effort (swallowed on failure below); if it silently fails
> while a different tab in the same window is already active, keystrokes go to
> that tab instead.

That is exactly what happens. Measured on a disposable Codex session:

```
before          : selected=False      <- select_tab had already returned True
  selection took on attempt 0
after selecting : selected=True
{Enter}         -> landed
```

The same `{Enter}` had done nothing three times in a row. It worked the moment
the selection was *verified* rather than assumed.

This accounts for every previously unexplained `/exit` failure. `agents` was
`selected=False`, so its `/exit` went to whichever tab was active in that
window — `computers`, which was busy running the command that sent it. `fonts`
worked because its tab already happened to be selected. Two days of
explanations died against this one line.

**Fix:** `select_tab` retries `Select()` until `IsSelected` reads true, and
returns `False` if it never does. A caller that cannot confirm the tab must not
type — sending `/exit` or `Ctrl+C` to an unknown tab is worse than reporting
failure.

This ships as its own change, before any Codex work, and fixes `down` and
`restart` for Claude Code on its own.

## Closing a tab needs no keystroke at all

Every tab exposes its own button:

```
TabItemControl 'CODEX-QUIT-SPIKE'
  ButtonControl 'Close Tab'      -> GetInvokePattern().Invoke()
```

Invoking it needs no focus, no foreground and no keystroke, so it cannot land
on the wrong tab. Verified: it closed a wedged tab that `{Ctrl}d` would not,
with three live sessions in the same window untouched.

`teardown` currently closes whole windows with `WM_CLOSE` and relies on each
tab's shell running `exit` to close itself. Per-tab `Invoke` is strictly more
precise. **Scope decision:** this spec adopts Invoke only where a tab must be
*gone* and its session has already ended. Ending a live session still means
typing at it, because a session must be given the chance to shut down cleanly
rather than have its tab pulled out from under it.

## What a Codex tab looks like

All measured on this machine, not assumed:

| | |
|---|---|
| process | `codex.exe` |
| cwd | the repo, reliably |
| tab title | bare basename — `constructicon`. Claude Code prefixes a spinner glyph |
| quit | `Ctrl+C` twice. Verified: the target died, other sessions untouched |
| sessions | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, plus `session_index.jsonl` |
| launch | `codex resume --last --dangerously-bypass-approvals-and-sandbox` |

Two traps:

**A second `codex.exe` runs under `ChatGPT.exe`** — the desktop app, not a
terminal tab. Discovery must exclude it or `up` will try to relaunch the
desktop app into a terminal. Distinguished by its parent process, and by a cwd
under `WindowsApps` rather than a repo.

**A directory Codex has not seen prompts before starting.** It asks whether to
trust the contents and waits. An unattended `up` into a fresh repo will sit at
that prompt rather than starting a session.

## The agent abstraction

One module, `reloaded/agents.py`, holding what differs per kind and nothing
else:

```python
@dataclass(frozen=True)
class Agent:
    kind: str            # "claude" | "codex"
    process: str         # "claude.exe"
    binary: str          # what readiness waits for on PATH
    launch: str          # default command, when none was captured
    quit_keys: tuple     # see below
    sessions_dir: str    # for transcript size warnings
```

`quit_keys` is a sequence of sends, not one string. Each element is typed on
its own with a pause before the next, because Claude Code's slash-command menu
does not accept an Enter that arrives ten milliseconds after the command:

```python
claude.quit_keys = ("/exit", "{Enter}")
codex.quit_keys  = ("{Ctrl}c", "{Ctrl}c")
```

Two more modules turned out NOT to be agent-agnostic, found by checking rather
than by assuming:

**`readiness.py:70`** blocks the logon deploy until `shutil.which("claude")`
succeeds. A layout holding only Codex tabs would wait forever for a binary it
does not need. It must wait for the binaries the layout's tabs actually
require — which is why `Agent` carries `binary` separately from `process`.

**`transcript.py`** repairs torn transcripts before `claude --continue`,
against Claude Code's store. Codex keeps its own rollout files and this guard
does not apply to them. The pre-flight runs per kind, and does nothing for a
kind with no such guard, rather than being skipped wholesale.

Everything else — window placement, geometry, the restart-marker loop, the
staggered launch — is genuinely agent-agnostic and stays untouched.

`discover.live_sessions()` currently returns `cwd -> pid`. It becomes
`cwd -> Session(pid, kind)`. Every caller that only wants liveness keeps
working through a thin accessor rather than being rewritten.

## Layout schema

`Tab` gains two optional fields:

```json
{"cwd": "...\\constructicon", "title": "constructicon",
 "agent": "codex",
 "command": "codex resume --last --dangerously-bypass-approvals-and-sandbox"}
```

`agent` absent reads as `"claude"`, so every existing layout keeps working
untouched and unmigrated.

`command` is **captured from the live process** at capture time, per the
decision taken during design: reloaded's premise is "put it back exactly as it
was", and a Codex session's flags are the user's choice rather than this
tool's. Absent, the kind's default launch is used.

This does mean a command line is persisted and later replayed. It is only ever
read from a process already running as the user, and only ever run in the
directory it was captured from.

## Teardown

`teardown` sends the target kind's `quit_keys` rather than a hardcoded
`/exit`. Both kinds need the pause between typing and submitting that the
Claude Code work already established — that fix generalises unchanged.

A Codex session that will not quit is reported the same way a Claude one is:
named, counted, and left running rather than force-killed.

## Testing

Follows the repo's existing convention: UIA and the live-session scan are
monkeypatched at the module boundary, and behaviour is asserted rather than
mock arguments.

The `select_tab` fix gets a test that fails when `Select()` never takes —
the exact condition observed. The agent registry gets a test per kind. The
layout gets round-trip tests including a schema-less layout reading as Claude.
Discovery gets a test that a `codex.exe` under `ChatGPT.exe` is excluded.
`readiness` gets a test that a Codex-only layout does not wait for `claude`.

Files touched: `agents.py` (new), `tabs.py`, `discover.py`, `deploy.py`,
`layout.py`, `capture.py`, `teardown.py`, `readiness.py`, `transcript.py`,
`__main__.py`, `README.md`.

Anything that can only be proven against a live session is stated as unproven
until it has been, rather than inferred. This project has already produced six
confident wrong explanations from inference.

## Out of scope

- Auto-answering Codex's trust prompt. It is a security question and the tool
  should report it, not click through it.
- Replacing `WM_CLOSE` window teardown wholesale with per-tab `Invoke`.
  Worth doing, separate change.
- Any third agent kind. The registry makes one cheap; nothing here builds for
  one that does not exist.
