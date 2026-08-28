"""The torn-transcript repair is Claude Code's, and now says so.

repair_torn_tail truncates a file. Running it over a format whose failure mode
has never been checked is a guess with a destructive edit on the end.
"""
from __future__ import annotations

import reloaded.transcript as transcript_mod


def test_claude_has_a_torn_tail_guard():
    assert transcript_mod.guards_for("claude") is True


def test_codex_has_no_torn_tail_guard():
    """Codex keeps its own rollout files. Nothing here has been verified
    against them, and this repair ends in a truncation."""
    assert transcript_mod.guards_for("codex") is False


def test_an_unknown_kind_is_not_guarded():
    assert transcript_mod.guards_for("gemini") is False


def test_an_empty_kind_is_treated_as_claude():
    """Matching the layout's back-compat rule: absent means Claude Code."""
    assert transcript_mod.guards_for("") is True
    assert transcript_mod.guards_for(None) is True


def test_the_lookup_ignores_case_and_padding():
    assert transcript_mod.guards_for("  CLAUDE ") is True


# ── what it actually gates ───────────────────────────────────────────────

from reloaded.deploy import transcripts_to_repair  # noqa: E402
from reloaded.discover import TranscriptInfo  # noqa: E402
from reloaded.layout import Tab  # noqa: E402
from reloaded.paths import norm  # noqa: E402

CWD = r"C:\repos\constructicon"


class _Entry:
    """Only the attribute transcripts_to_repair reads."""

    def __init__(self, agent):
        self.tabs = [Tab(cwd=CWD, title="x", agent=agent)]


def _index():
    return {norm(CWD): TranscriptInfo(cwd=CWD, title="x", path=r"C:\t\a.jsonl",
                                      mtime=0.0, size=1)}


def test_a_claude_tab_has_its_transcript_repaired():
    assert transcripts_to_repair([_Entry("claude")], _index()) == [r"C:\t\a.jsonl"]


def test_a_codex_tab_does_not():
    """The repo has a Claude transcript from before it ran Codex. Repairing it
    on this tab's behalf truncates a file the session will never read."""
    assert transcripts_to_repair([_Entry("codex")], _index()) == []
