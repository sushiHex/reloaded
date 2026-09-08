# Codex session identity, and why the obvious index is wrong

Claude Code tabs resolve through `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`:
the directory name carries the cwd, and each transcript carries a `customTitle`.
`discover.transcript_index` and `discover.title_to_cwd` are built on that.

Codex has no equivalent, and `agents.CODEX.sessions_dir` is read by nothing.
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
`--all` widens it, `codex resume <id>` is exact.

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
