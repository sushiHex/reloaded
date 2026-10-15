# Codex session identity, and why the obvious index is wrong

Claude Code tabs resolve through `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`:
the directory name carries the cwd, and each transcript carries a `customTitle`.
`discover.transcript_index` and `discover.title_to_cwd` are built on that.

Codex has no equivalent, and `agents.CODEX.sessions_dir` is not indexed: the
only rollouts read are the ones a live Codex holds open (see "The shared
server" below).
The obvious fix — walk `~/.codex/sessions`, mirror what Claude does — was
investigated and rejected. This is why, so it is not re-derived.

Measured on one machine (2026-09-05, CLI 0.145–0.153.4), cross-checked against
a Codex review with access to the same files and to upstream documentation.

## What is actually in the rollout store

```
~/.codex/sessions/YYYY/MM/DD/rollout-<ISO8601>-<uuid>.jsonl
```

The first line is a `session_meta` record whose payload carries an absolute
`cwd`. That much held for all 218 files here. Also observed:

- `payload.source` is a tagged union, not a string. Local threads had
  `"exec"`, `"cli"`, `"vscode"`; subagent threads had objects like
  `{"subagent": "review"}`.
- `payload.id` and `payload.session_id` are equal for top-level threads and
  differ for subagents. Prefer `id`, which matches the filename UUID.
- `git`, `thread_source`, `multi_agent_version`, `dynamic_tools` are all
  conditional or version-gated. There is no compatibility contract for this
  format — a parser must require `type == "session_meta"` and skip, never
  crash on, anything else.

## The reason it cannot work

**`session_meta` records how a thread was created, not what hosts it now.**

A terminal can resume a thread that `codex exec` or the ChatGPT desktop app
created, and the first line is never rewritten. So a live terminal tab can be
displaying a rollout whose metadata says `exec` or `vscode`.

There is therefore no field in this directory that means "this is in a terminal
tab." Filtering on `originator == "codex-tui"` drops resumed tabs; including
`vscode` indexes desktop threads that never had one.

On this machine the point is stark: of 211 rollouts, **none** was created by a
terminal session. 178 were `codex exec` runs, the rest desktop and subagent.
An index built here would have been almost entirely noise.

The live-process check is closer to the truth, because it asks what is running.

## Two further hazards in the obvious design

**Newest-per-cwd picks the wrong thread.** A directory can hold an old thread
still open in a tab and a newer one that is closed. Newest wins binds the
closed thread's name and id.

**A merged index can damage the other agent's files.** `deploy` feeds selected
paths from the transcript index into Claude's torn-tail repair, which
truncates. If one index held both kinds and a Codex rollout won a cwd, a Claude
tab could hand a Codex `.jsonl` to that repair. Claude's transcript/size/repair
index must stay separate from any Codex catalog.

## What does exist

**Thread names.** `~/.codex/session_index.jsonl` is an append-only log of
`{id, thread_name, updated_at}`; the last record for an id wins. `/rename`
writes here without touching the transcript. There is also a `threads.name`
column in `state_5.sqlite`, whose schema is internal and migrates — do not read
it directly. The supported interface is the app-server `thread/list` API.

**Tab titles.** Codex sets the terminal title from `tui.terminal_title`,
defaulting to `["spinner", "project"]`. Reloaded strips known spinner families
already, which is why it appears to receive a bare basename. It is not a
contract: `thread`, `model`, `git-branch` are all available components, and
config can come from a profile, `-c`, or project-local file.

**Resume scope.** `codex resume --last` is scoped to the current working
directory, so staggered launches into six different repos do not collide.
`--all` widens it, `codex resume <id>` is exact. From the source at
`rust-v0.162.1` (`tui/src/lib.rs` `latest_session_lookup_params`): one
`thread/list` call, newest `updated_at`, sources `cli` and `vscode` only (no
`exec` unless `--include-non-interactive`, never a subagent), cwd compared
after canonicalizing; no match starts a new session. With the `worktrees`
feature on, the cwd filter is every linked worktree of the repository, so two
worktrees can resume the same thread. Measured: in a directory with no
thread, it started fresh.

## The shared server (0.162.1)

Codex now runs one background app-server for every TUI that does not pass
`--no-daemon` (feature `daemon_auto_start`, stable, on). Measured on this
machine, 2026-10-15:

- **A tab is not where its session runs.** The tab's `codex.exe` has no
  children. The shells its tool calls run in, and every MCP server, are the
  daemon's (`codex.exe app-server --listen unix://`, no parent, cwd
  `~/.codex/app-server-daemon/workdir`).
- **The daemon holds the files.** It has every attached thread's rollout
  open, subagents' included. An attached tab holds none.
- **Ending a tab ends nothing.** A tab ended by pid mid-turn left its turn
  running: it reached `task_complete` 85 s later. Upstream, exit unsubscribes
  and never cancels.
- **Ctrl+C on a busy attached tab opens a menu.** "Task is still running:
  Cancel task / Run in background / Exit", with Cancel highlighted. The turn
  carried on, the menu outlived it, and a second Ctrl+C closed it. The source
  at the tag says the first Ctrl+C interrupts; the installed binary does not,
  so the source is no authority on TUI keys.
- **A tab can show another directory's thread.** A tab started fresh in an
  empty directory ended up displaying a live thread from another repository,
  mid-turn, after a prompt was written into its console. The text sat unsent
  in that thread's composer. No index row tied the two. Mechanism unknown;
  not reproduced, because trying risks another live session.
- **Recency is not mtime.** The rollout of a thread the daemon holds open
  kept a stale NTFS mtime while records were being appended.

With `--no-daemon`, each of those is the tab's own again: tool calls are its
children, it holds its own thread's rollout (and its subagents'), one Ctrl+C
interrupts a busy turn, one quits an idle tab, and subagents work. The tool
calls measured got `CODEX_THREAD_ID` (their own thread) and `CODEX_SESSION_ID`
(the root session) either way; upstream sets both where it builds a shell's
environment. So reloaded launches every Codex tab with `--no-daemon`
(`agents.CODEX.own_server_flag`), finds which thread a tab is writing from
the root rollout it holds open (`discover.held_threads`), and `restart --self`
resumes that thread by id when the tab's variable and its open file agree.

## If this is ever built

Not "generalize `transcript_index` over a kind". Instead:

1. Leave Claude's index owning Claude titles, sizes and repair paths.
2. A separate Codex thread catalog returning *many* records per cwd —
   `id, cwd, name, source, recency, archived`.
3. Source it from app-server `thread/list`; fall back to first-line parsing
   plus `session_index.jsonl`.
4. Change title resolution from `dict[title, cwd]` to candidate sets tagged
   with kind and session id, and **fail closed on ambiguity** rather than
   taking the newest.
5. Add `session_id` to `Tab` and relaunch known threads with
   `codex resume <uuid>`.
6. For tabs reloaded launches, set a deterministic terminal title token and
   suppress the application title. That *creates* the tab-to-layout identity
   instead of trying to reconstruct it — and it is also the only route to
   fixing two sessions in one directory, which no amount of index work can
   repair.

`CODEX_HOME` moves all of this. `agents._codex_home` honours it.

## The one thing fixed instead

Capture records the running command and the relaunch replays it. A tab started
as a bare `codex`, or a bare `claude` with no `--continue`, comes back as a new
conversation at the same directory — silently, reported as success.
`agents.resumes` detects it and `restart` warns before anything is exited. It
warns rather than rewriting: the flags are the user's, and appending `resume`
to what someone typed is a guess about intent.
