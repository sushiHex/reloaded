"""Parked tabs: a session quit and handed off, whose tab waits without it.

Codex's remote control drives a conversation through Codex's shared
background server, and a tab Reloaded launches runs its own and holds the
conversation open - so to continue it from a phone, the tab's session has to
quit. The tab stays (deploy.ended_prompt), and typing `park` there writes a
record of it here. Capture then keeps the tab where it is although no session
runs in it, and a restore reopens it waiting rather than starting the session
and taking the conversation back. Enter in the tab removes the record and
starts the session.

The record is written by the tab's own shell, at the moment it is parked, and
removed by it at the moment it is taken back - not inferred later from what
runs where, which cannot tell a handed-off session from one that was quit,
nor see a session started and quit between two captures. It is a file of its
own rather than a field of the layout, which every capture rewrites.

Written by PowerShell (deploy.park_write), read here:
`{"cwd", "agent", "command", "shell_pid", "shell_created"}`, the last in epoch
milliseconds.
"""
from __future__ import annotations

import json
from typing import NamedTuple

from .paths import norm, parked_dir, parked_record

# How far the shell's start time may differ between PowerShell's clock and
# the kernel's process list and still be the same process. They agree to the
# millisecond in practice; a reused pid is a different process started later.
SAME_SHELL_SECONDS = 2.0


class Record(NamedTuple):
    cwd: str
    agent: str
    # What the tab runs when it is taken back: the exact resume command.
    command: str
    shell_pid: int
    shell_created: float  # epoch seconds

    @property
    def key(self) -> tuple[str, str]:
        return norm(self.cwd), self.agent


def load_all() -> list[Record]:
    """Every record that can be read. One that cannot is skipped: a parked
    tab that comes back as an ordinary one costs a restart, and a corrupt
    file must not stop every capture and restore."""
    out = []
    for path in sorted(parked_dir(create=False).glob("*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8-sig"))
            out.append(Record(str(d["cwd"]), str(d["agent"]), str(d.get("command") or ""),
                              int(d.get("shell_pid") or 0),
                              float(d.get("shell_created") or 0) / 1000.0))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def by_key(records: list[Record]) -> dict[tuple[str, str], Record]:
    return {r.key: r for r in records}


def is_open(record: Record, table) -> bool:
    """Whether the shell that parked the tab is still running - that is, the
    parked tab is open now. `table` is win32.process_table()."""
    proc = table.get(record.shell_pid)
    return proc is not None and abs(proc.created - record.shell_created) <= SAME_SHELL_SECONDS


def remove(cwd: str, kinds) -> list[str]:
    """Remove the records for `cwd` of each kind in `kinds`; the kinds whose
    record existed."""
    gone = []
    for kind in kinds:
        path = parked_record(cwd, kind, create=False)
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        gone.append(kind)
    return gone
