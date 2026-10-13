# Repository Guidelines

## Project Structure & Module Organization

`reloaded/` contains the Windows Terminal launcher for Claude Code and Codex CLI. `__main__.py` handles CLI commands; `agents.py` defines agent differences; `capture.py`, `deploy.py`, and `teardown.py` manage session lifecycles. `layout.py` owns persisted models and geometry, while `tabs.py` and `win32.py` wrap desktop integration. Supporting modules cover discovery, transcripts, readiness, editing, and scheduled tasks.

`tests/` contains pytest suites and shared fixtures in `conftest.py`. `docs/` holds session-identity notes and design plans. `.github/workflows/ci.yml` defines Windows CI. There is no separate assets directory.

## Build, Test, and Development Commands

Develop on Windows with Python 3.9+; CI uses Python 3.12. Live operation requires Windows Terminal, the relevant agent CLI on PATH, and PowerShell (7 preferred; 5.1 supported).

- `python -m pip install -e ".[dev]"`: install editable source and pytest.
- `python -m pip wheel . --no-deps -w dist`: build a wheel using the configured setuptools backend.
- `python -m pytest -v`: run the suite, matching CI.
- `python -m pytest tests/test_layout.py -q`: run a focused module.
- `python -m reloaded status`: inspect saved and live sessions.
- `python -m reloaded up --dry-run`: preview deployment.
- `python -m reloaded edit`: open the interactive layout editor.

## Coding Style & Naming Conventions

Use four-space indentation, snake_case functions/modules, PascalCase classes, and UPPER_CASE constants. Follow surrounding type hints, dataclasses, and module docstrings; keep Python 3.9 compatibility. No formatter or linter is configured. Centralize agent differences in `agents.py` and normalize Windows path comparisons through `paths.norm`.

## Testing Guidelines

Name files `test_*.py` and functions `test_<behavior>`. Use `tmp_path` for filesystem state and `monkeypatch` for OS boundaries. Preserve the autouse desktop guards in `tests/conftest.py`; substitute deliberate fakes for keystrokes, window mutations, and terminal/agent spawning. Add regression tests for behavior changes and serialization round trips when changing layout fields. No numerical coverage threshold is configured.

## Commit & Pull Request Guidelines

Follow history's Conventional Commit style: `fix(tabs): confirm tab focus`, `feat(reloaded): ...`, or `test: ...`. Keep commits focused. PRs should describe the problem, resulting behavior, related issues, and validation commands/results. Include screenshots or terminal output for visible changes and distinguish mocked tests from live verification.

## State & Compatibility

Runtime layouts, logs, and restart markers live under `~/.reloaded/`. Keep local state out of commits. Preserve atomic layout writes and backward-compatible defaults; update `Layout.from_dict` when adding persisted fields.
