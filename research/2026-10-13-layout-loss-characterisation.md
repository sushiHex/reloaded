# Layout losses, as #19's recording saw them (2026-09-18 to 2026-10-13)

Precondition for #20 (layout retention). Source: `~/.reloaded/reloaded.log`,
every `[reloaded] layout ...` and `refused to overwrite` line, read-only.
Cross-reviewed by Codex (thread 01a1348f-7194-7ef2-8556-068115ff0640); its
corrections are folded in and marked.

## Excluded

- `c:\repos\app`, `c:\repos\app-a`, 2026-09-18 to 09-20: 116 "gained" lines
  written by the test suite into the real state dir, before
  `tests/conftest.py::_no_real_state_dir` isolated it.
- A `smoketest` layout from running `spike/layout-retention` (09-18).

## The data (default layout, 25 days)

Repository names are replaced with letters.

| When | Event | Reading |
| --- | --- | --- |
| 09-20 01:41 | logon restore of 13, then lost 4 (A, B, C, D) | the restore race fixed in #28 |
| 09-20 01:46 | gained the same 4 | the next reconcile, with no user action |
| 09-20 21:37 | refused: E, F running but missing | shrinkage guard |
| 09-22 11:57 | refused: G running but missing | shrinkage guard |
| 09-23 16:52 | lost H (and gained another) | not reopened in 20 days; intent unknown |
| 09-24 04:40 | logon restore of 15; no loss followed | last boot on this machine |
| 10-01 | I gained 20:12, lost 20:37 | a spike; lived 25 min |
| 10-02 | J lost 15:12, regained 15:17, lost 21:12 | a test repo |
| 10-06/07 | J gained 20:07, refused 04:17, lost 04:22 | the same test repo |
| 10-08 | K gained 03:37, lost 03:47 | lived 10 min |

Six loss events and 11 repos. One event, of 4 repos, is attributed to a
known bug, since fixed. It was back within five minutes. The other five are
single repos, and their intent is **unresolved**. Most were short-lived or test
sessions, which fits a deliberate close but does not establish one.

## What this measurement cannot see (Codex, verified)

- **Per-tab losses in a shared directory.** `layout.repo_set`
  (`layout.py:163`) is a set of cwds, so the log records nothing when one of
  two tabs in a directory goes away. That has really happened. The
  `tests/test_two_agents_one_directory.py` docstring records three shared
  directories each coming back with one tab of two,
  fixed in 2655276 (2026-09-24). Those sessions were never saved, so no loss
  journal would have held them either.
- **A changed kind, command or pin.** None of these alters the repo set, so
  none is recorded.
- **The editor writes without the guard.** `editor.py:123` calls
  `layout.save` directly: no shrinkage check, no change record.
- **Exposure is not elapsed time.** In 25 days there were two logon
  restores, and only one after #28. That is too few to say whether #28's
  window is enough. It is a bounded startup guard: stagger plus 30s.
- **"Self-healed" depended on luck.** The four survived until the next
  capture. A reboot in those five minutes would have made the loss permanent.

## What recovery looks like today (Codex, verified against `cmd_open`)

You can rebuild a lost repo with `reloaded open <path>`, and the reconcile
then captures it back. That is a reconstruction, not a restore:

- It does not bring back the placement. The tab opens with `wt -w 0`.
- It takes the agent kind from the current layout, which no longer holds the
  lost repo, so the kind falls back to Claude Code. A lost Codex repo comes
  back as the wrong CLI.
- It is blocked in a shared directory. If the Claude tab is still running,
  `open` returns "Already running" and the missing Codex tab cannot be
  asked for.
- It is not loss-free either. `save` replaces the whole file
  (`layout.py:173`), so an editor holding a stale layout, or two captures
  that overlap, can still overwrite newer state.

## Conclusion

Do not build snapshot retention now. The one observed unintended loss came
from a bug that has since been fixed, and the case the spike's rotation
targeted has not recurred. The cases that did happen were invisible to the
recording, and a lost-tab journal keyed by cwd would repeat the identity
mistake that hid them.

Fix the measurement first:

- Diff tab identities counted by `(cwd, kind)`, not by cwd.
- Name the layout and the writer (capture or editor).
- Record a kind or command degrading in place.

Then re-read. If recovery is built later, it should keep the old `Tab`
payloads, bounded, and let the user choose an entry. It should never fall
back silently to the latest record for a cwd.
