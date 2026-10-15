# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` carries the same project conventions in a tool-neutral form; keep the two consistent when either changes.

## Commands

```powershell
python -m pip install -e ".[dev]"      # editable install + pytest
python -m pytest -v                    # full suite, as CI runs it
python -m pytest tests/test_layout.py -q                                   # one module
python -m pytest tests/test_layout.py::test_every_field_survives_a_roundtrip -q  # one test
python -m pip wheel . --no-deps -w dist                                    # build
python -m reloaded status              # inspect saved vs. live sessions
python -m reloaded up --dry-run        # preview a deployment without opening tabs
python tools/smoke.py setup --fake alpha:claude  # live smoke test: takes the desktop's focus; see docs/smoke-testing.md
```

No formatter or linter is configured. CI (`.github/workflows/ci.yml`) is `windows-latest` with Python 3.9 (the floor) and 3.14.

## Hard constraints

- **Windows-only, not by preference.** `win32.py` calls `ctypes.WinDLL("user32")` at import time; `ctypes.WinDLL` does not exist on other platforms. The test suite runs without a live desktop or real Windows Terminal, but only on Windows.
- **Python 3.9 floor.** Every module opens with `from __future__ import annotations`, which is what makes `list[int]` / `str | None` legal on 3.9. Keep that import when adding a module.
- **The checkout is live** once `install-tasks` has run from it: the five-minute capture task and the logon launcher put the directory of the `reloaded` package they were installed from on `sys.path`, so they run whatever is checked out there, uncommitted edits included, until reinstalled from elsewhere. Develop in a `git worktree` and keep that checkout on `master`.
- Runtime dependencies are `psutil` (process identity) and `uiautomation` (Windows Terminal tab strip). A missing `psutil` degrades to 0 live sessions; a missing `uiautomation` raises `tabs.UIAUnavailable`.

## The blast door (`tests/conftest.py`)

An autouse fixture blocks everything that can touch the real desktop. This is not defensive ceremony: a rename of `send_exit_keystrokes` left several tests stubbing a dead name, the real sender ran, and six live Claude Code sessions died in 31 seconds.

The guard sits on what cannot be renamed away — the `uiautomation` module itself (replaced with a poisoned stand-in), `win32.set_foreground`, the four `user32` calls that *change* a window (`PostMessageW`, `SetWindowPlacement`, `ShowWindow`, `SetForegroundWindow`), and `subprocess.Popen` for `wt`/`claude`/`codex`/`wscript`/`pyw`. `is_foreground` is pinned to `True` so the suite does not depend on what the developer is looking at.

A test that needs one of these substitutes its own fake (monkeypatch inside a test wins over the fixture). Never weaken the fixture to make a test pass, and never stub by name where the guard is at the module or syscall boundary. `tests/test_the_blast_door.py` falls into the guard on purpose to prove it still holds.

## Architecture

Layered bottom-up; each layer only reads what Windows and the agent CLIs already record, so no session cooperation is required:

- `win32.py` — ctypes geometry, monitor enumeration, DPI. Declares per-monitor-DPI-v2 awareness at import so coordinates are deterministic regardless of whether `uiautomation` was imported first.
- `tabs.py` — Windows Terminal tabs via UI Automation. WT is one XAML surface, so tabs are unreachable through Win32; the window title only reflects the *active* tab.
- `discover.py` — live sessions from `psutil` ancestry, plus titles from Claude transcripts (bounded head/tail reads; transcripts reach hundreds of MB).
- `layout.py` — the persisted model (`Monitor`/`Window`/`Tab`/`Layout`), atomic per-pid writes, `clamp_rect` re-anchoring.
- `capture.py` / `deploy.py` / `teardown.py` — snapshot, launch, graceful quit. One deployment path serves interactive `up`, full `restart`, and unattended logon restore.
- `relaunch.py` — `restart --self`: ends its own session by exact pid and has its tab's loop, or a detached helper typing into a hand-started shell, bring it back.
- `__main__.py` — argparse dispatch; all `cmd_*` functions live here.

State lives under `~/.reloaded/` (`layouts/`, `restart/`, `relaunch/`, `attempts/`, `refusals/`, `reloaded.log`). Never commit it.

### Invariants that bite

- **`agents.py` is the only place agent kinds differ.** Process name, PATH binary, default launch command, quit keystrokes, resume tokens. Everything else — placement, geometry, restart markers, staggered launch — is identical for Claude Code and Codex. Adding a per-kind branch elsewhere is almost always wrong. Every fact in that file was measured on a live machine; the module docstring says why inference has repeatedly produced confident wrong answers here.
- **All Windows path comparison goes through `paths.norm`.** A cwd from `psutil`, one from a transcript, and one from the layout file only agree after `normcase(normpath(...))`.
- **Adding a persisted field means editing `Layout.from_dict` too.** `asdict()` writes new fields automatically, so a field missing from `from_dict` silently reverts to its default on every load. `tests/test_layout.py::test_every_field_survives_a_roundtrip` guards this. Every field carries a default so older layouts keep loading — an absent `agent` reads as Claude Code, which is what made the second agent kind a migration-free addition.
- **The README's Commands table is tested.** `tests/test_readme.py` asserts every argparse subcommand appears in `README.md`; adding or renaming one without touching the README fails CI.
- **Restart markers** live under `~/.reloaded/restart/`, consumed exactly once by the tab's own PowerShell loop, TTL 120s. A loop reads `shell-<its pid>.marker` (only it reads that) before its directory's `sha256(norm(cwd))[:16].marker` (every loop there reads that); requests go to the shell's wherever the loop has `$rlOwn=` (`discover.marker_shell`), since a running loop's PowerShell cannot be changed. A failed delete stops the loop rather than relaunching forever. There is no generation or nonce, so overlapping restarts of one session collapse into one.
- **Commands are read as Claude Code's parser reads them.** `agents._roles` is the one walk behind `resuming`, `fresh` and `resume_exactly`: a required option value takes the next argument even when it starts with `-` (`--model --version` sets the model), an optional `[value]` does not. An option missing from `bare_flags` / `variadic_flags` / `optional_flags` reads as taking the next argument, so refresh them from `claude --help` when Claude's options change.
- **A directory's Claude conversations are in `agents.conversations_dir(cwd)`.** The path with every non-alphanumeric character replaced by `-`, under `projects` in `CLAUDE_CONFIG_DIR` or `~/.claude`. The tab's new-conversation fallback (`deploy.nothing_to_resume`) runs only where that folder holds no `*.jsonl`, so a wrong folder reads as "no conversation" — the unsafe direction, where a new conversation becomes the latest one every restart resumes.
- **A tab reloaded opens is titled `agents.tab_label(kind, cwd)`**: the directory's name and the kind's invisible mark, which is the only thing telling a directory's Claude tab from its Codex one. Read a title from the tab strip through `agents.title_kind`, and compare it with `tab_label`, never with `paths.tab_title`, which is the bare name used as a repo argument.
- **Teardown verifies process exit, not keystrokes sent.** A prompt or side conversation can eat `/exit` or a Ctrl+C. Nothing is force-killed; a holdout is reported.
- Transcript repair (`transcript.py`) is Claude-only by design — repair ends in a truncation, and Codex's rollout format has not been verified against it.

## Testing conventions

Files `test_*.py`, functions `test_<behavior>`. Use `tmp_path` for filesystem state and `monkeypatch` for OS boundaries. The suite deliberately runs real PowerShell to exercise the launcher loop and relaunch script — what is forbidden is opening a terminal window or starting an agent. Add a regression test for behavior changes, and a serialization round-trip test when layout fields change.

## Mainframe

- Search before answering from training data: `mcp__mainframe__search("specific technical terms")`
- Use specific nouns and function names, not natural language questions
- One topic per search. Multi-topic queries scatter results.
- Trust `rerank_score` (higher = better). Above 0.85 is strong. Below 0.3 is noise.
- `limit=2-3` for targeted lookups, `limit=5-10` for exploratory coverage
- After adding `research/` or `docs/` files, run `mcp__mainframe__sync_index` to index them
- Cross-project results are expected — knowledge from any repo is available here

## Commits and docs

Conventional Commits, scoped: `fix(tabs): confirm tab focus`, `feat(reloaded): ...`, `test: ...`. Distinguish mocked tests from live verification in PR descriptions. Knowledge goes in `docs/` (architecture, usage, automation, design notes) — no loose `.md` files in the repository root.
