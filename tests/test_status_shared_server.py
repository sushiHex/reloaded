"""`status` names the sessions on a shared background server.

Codex 0.162.1 attaches a TUI to its shared daemon unless it is started with
`--no-daemon` (measured): `restart --self` cannot find such a tab, and ending
it leaves its work running. After a restore, none should be listed.
"""
from __future__ import annotations

import reloaded.__main__ as main_mod
import reloaded.discover as discover_mod
from reloaded.layout import Tab, tab_flags

COMMANDS = {
    1: "codex resume --last --dangerously-bypass-approvals-and-sandbox",
    2: "codex resume --no-daemon 01a1414f-f035-76b1-a2e3-5ddb2b3db568",
    3: "claude --continue",
    4: "",  # unreadable: says nothing either way
}


def test_only_sessions_off_their_own_server_are_named(monkeypatch):
    monkeypatch.setattr(discover_mod, "session_command", lambda pid: COMMANDS[pid])
    census = discover_mod.Census({
        r"c:\repos\a": [(1, "codex"), (3, "claude")],
        r"c:\repos\b": [(2, "codex")],
        r"c:\repos\c": [(4, "codex")],
    })

    assert main_mod._on_shared_server(census) == [(r"c:\repos\a", "codex")]


def test_a_saved_thread_shows_in_the_tab_flags():
    """How to see, after a restore, that capture recorded it."""
    tab = Tab(cwd=r"C:\x", title="x", agent="codex",
              session_id="01a1414f-f035-76b1-a2e3-5ddb2b3db568")

    assert tab_flags(tab) == "  [codex, thread 01a1414f]"
