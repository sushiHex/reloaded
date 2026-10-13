# Two sessions in one directory: Codex review of the (cwd, kind) design

Codex job `job_1599c226981f`, 2026-09-22, effort xhigh, read-only against the
working tree at master `bfa5e6f`. Verdict: **block as written.** The feature is
paused; this is what an implementation has to answer first. Line numbers are
from that tree and will drift.

The proposal: identify a slot by `(cwd, agent kind)` instead of cwd, keep
directory-named restart markers, and tell tabs apart by a `[codex]` title
suffix.

## P1 — must be designed before any code

1. **Teardown collapses identity back to cwd.** `agent_kinds()` reads each pid
   correctly and then stores `kinds[norm(cwd)]`, so Claude followed by Codex
   gives both targets Codex's quit keys (teardown.py ~308/313/404). Exits are
   recorded as `(title, cwd)` but `close_dead_tabs()` matches on cwd alone, so
   one exited session qualifies its sibling's tab for closing even when the
   sibling timed out (~573/270/583). Reproduced with fake targets. Carry the
   kind, or the exited pid, on `Target` and through the exit results.
2. **Capture can silently drop the second session and its pin.**
   `merge_pinned()` keys presence and lookup by cwd (capture.py ~102-119), and
   `_capture_shrinkage()` / `layout.repo_set()` compare cwd sets, so capturing
   one kind masks the loss of the other.
3. **The title suffix is not an identity.** Existing Codex tabs are titled with
   the bare basename, so "unsuffixed means Claude" misbinds them; two old tabs
   both titled `app` cannot be told apart; a directory literally named
   `app [codex]` collides; transcript-title lookup takes precedence over the
   basename and could redirect a stripped Codex title elsewhere.
4. **`open <repo>` cannot say which kind.** It keeps the first saved tab per
   cwd. Needs an explicit kind selector, chosen before the liveness check; the
   editor's add command has the same ambiguity.
5. **Bare `restart` and `down` never go through the crowding refusal**, so
   finding 1 hits them directly. Directory markers stay shared: the refusal
   counts agent processes, not shells watching the marker, so a staggered or
   later shell can still take it.

## P2

- `deploy.launch_window()` scores windows by bare basenames; suffixed titles
  would lose the score and geometry can land on the wrong window.
- Kind normalization differs: `Tab.agent` only defaults falsy values while
  `agents.for_kind()` lowercases and falls back to Claude.

## Silent key-change sites

Changing live maps from `cwd` keys to `(cwd, kind)` keys keeps many
expressions running with wrong answers rather than raising: `discover.sweep()`
/ `live_agents()`, `resolve_tab()`, `build_layout()`, `plan_deploy()`
(launches duplicates), editor `.get()`s, `_capture_shrinkage()`,
`_never_started()`, `_wait_for_session()`, `_nothing_running_there()`,
`status`, `cmd_open()`. `crowded_dirs()` must keep its pre-dedup count.

## Scope, if pursued

One managed slot per `(cwd, kind)` — not arbitrary many sessions, and not
conversation identity. Counterexample tests required first: mixed-agent
partial exit, pin survival, missing-sibling capture refusal, old duplicate
titles, suffix collision, explicit `open` selection, Codex-only window
identification.

`restart --self` no longer needs any of this: since the relaunch rewrite it
ends its own session by pid, so it works in a shared directory already.
