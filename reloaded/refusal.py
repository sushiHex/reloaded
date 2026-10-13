"""What the log has already said about a capture that keeps refusing.

The reconcile runs `capture` every five minutes, forever, and each run is a
fresh process. A condition that refuses every run wrote the same line every
run - 288 a day - which is a log nobody can skim. So the first refusal is
recorded, the same one again is recorded only as a periodic "still", and the
next save of that layout records how long it went on.

The only memory a run has is the state directory, so that is where this lives:
one small file per layout, holding what was last said and when. It records
what the log was told, not anything about the user's sessions, so losing it
costs at most one repeated line. That is why it needs none of a layout's care
(#20): no recovery, no lock. Two runs racing on it can repeat a line or drop
the closing one, never hide a new refusal - a different message is always
said.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from typing import Callable

from . import paths

# How often an unchanged refusal says it is still happening. Rare enough that
# a week-long condition is a couple of dozen lines, often enough that the tail
# of the log shows the reconcile is still alive and still refusing.
HEARTBEAT_SECONDS = 6 * 3600


def _path(layout: str):
    """Named by a digest of the layout name, not the name itself: `--layout`
    accepts anything, and `..\\work` or an absolute path as a filename here
    would land on the layout file itself and overwrite it.

    Through the module, not a name imported from it: the test suite's guard
    redirects `paths.state_dir`, and a copy bound at import time would go on
    pointing at the user's real ~/.reloaded.
    """
    d = paths.state_dir() / "refusals"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{hashlib.sha256(layout.encode('utf-8')).hexdigest()[:16]}.json"


def _load(layout: str) -> dict | None:
    try:
        raw = json.loads(_path(layout).read_text(encoding="utf-8"))
        state = {"message": str(raw["message"]), "since": float(raw["since"]),
                 "said": float(raw["said"]), "count": int(raw["count"])}
        # Both are rendered later; one that cannot be (NaN, 1e300) is garbage.
        _when(state["since"]), _when(state["said"])
        return state
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        # Unreadable reads as nothing said: the cost is one repeated line,
        # where trusting garbage could suppress a real one.
        return None


def _store(layout: str, state: dict) -> None:
    tmp = None
    try:
        p = _path(layout)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass
    finally:
        try:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _when(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def note(layout: str, message: str, record: Callable[[str], bool], *,
         now: float | None = None) -> None:
    """Record a refusal of `layout`, unless the log already says it.

    `record` writes a line and says whether it landed. State is only advanced
    for a line that did: a failed write followed by "already said" would hide
    the first warning for a whole heartbeat.
    """
    now = datetime.datetime.now().timestamp() if now is None else now
    state = _load(layout)
    # A `said` in the future - a clock set back, or a damaged file - would
    # keep `now - said` negative and the refusal silent for as long as it
    # lasted. Read as nothing said.
    if state is None or state["message"] != message or state["said"] > now:
        if record(message):
            _store(layout, {"message": message, "since": now, "said": now,
                            "count": 1})
        return
    state["count"] += 1
    if now - state["said"] >= HEARTBEAT_SECONDS:
        if record(f"{message} — still refusing, since "
                  f"{_when(state['since'])} ({state['count']} times)"):
            state["said"] = now
    _store(layout, state)


def saved(layout: str, record: Callable[[str], bool], *, forced: bool = False) -> None:
    """`layout` was just saved: close out any refusal the log is tracking.

    Says what happened - the layout was saved - rather than that the cause
    went away. A save proves the capture got through, or that `--force`
    overrode it, not that whatever made it refuse is fixed.
    """
    state = _load(layout)
    if state is None:
        return
    how = "saved with --force" if forced else "saved"
    if record(f"[reloaded] layout {how} after {state['count']} "
              f"refusal(s) since {_when(state['since'])}: "
              f"{state['message']}"):
        try:
            _path(layout).unlink(missing_ok=True)
        except OSError:
            pass
