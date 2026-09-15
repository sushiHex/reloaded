"""No user-facing message claims this tool only knows Claude Code.

#8 corrected the package metadata and the CLI help. It left the runtime
messages, which are the half a user actually reads: `--help` is consulted once,
an empty-state message fires on every no-op invocation. A Codex-only user
following the README's onboarding was told, at the very first command, that no
*Claude Code* tabs were found.

The guard is deliberately narrow. `Claude Code tabs` is a phrase that is only
ever wrong - the tool has managed two kinds of tab since `agents.py` existed -
so asserting it appears nowhere needs no parsing and cannot rot. Plenty of
other mentions of Claude Code in that file are correct and must stay: transcript
paths, `CLAUDE_CODE_CHILD_SESSION`, the spinner-range hint, and the comments
explaining why `/exit` is the default. A blanket ban on the word would be wrong
and would have to be fought later.
"""
from __future__ import annotations

import pathlib

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "reloaded" / "__main__.py"


def test_no_message_says_claude_code_tabs():
    """Four empty-state prints said this, one per command. Nothing generated
    them and nothing connected them, so correcting the help text left them
    behind - which is the drift this test exists to fail on."""
    text = SOURCE.read_text(encoding="utf-8")

    assert "Claude Code tabs" not in text, (
        "a user-facing message still claims Claude-only support. Say 'agent "
        "session' rather than naming the agents: the parser description "
        "carries the roster once, and a second hand-maintained copy is "
        "exactly what drifted into #8."
    )
