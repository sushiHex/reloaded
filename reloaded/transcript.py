"""Pre-flight transcript guards for `claude --continue`."""
from __future__ import annotations

import json
import os
import pathlib
import time

SIZE_WARN_BYTES = 100 * 1024 * 1024  # 100 MB

_SCAN_BYTES = 1024 * 1024  # enough to contain any single record

TORN_BACKUP_MAX_AGE_DAYS = 30


# Which agent kinds have a verified torn-tail guard. Claude Code's transcript
# format and its failure mode are known here. Codex keeps its own rollout
# files, and nothing in this module has been checked against them - repair
# ends in a truncation, so running it on an unverified format is a guess with
# a destructive edit on the end.
_GUARDED_KINDS = {"claude"}


def guards_for(kind) -> bool:
    """Whether `kind` has a torn-transcript guard. Absent reads as Claude,
    matching the layout's own back-compat rule."""
    return str(kind or "claude").strip().lower() in _GUARDED_KINDS


def human_size(n: int) -> str:
    mb = n / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{int(round(mb))} MB"


def last_line_offset(path) -> int | None:
    """Byte offset where the final non-empty line begins, or None if empty."""
    p = pathlib.Path(path)
    size = p.stat().st_size
    if size == 0:
        return None

    with open(p, "rb") as fh:
        window = min(_SCAN_BYTES, size)
        fh.seek(size - window)
        blob = fh.read(window)

    stripped = blob.rstrip(b"\r\n")
    if not stripped:
        return None
    idx = stripped.rfind(b"\n")
    if idx == -1:
        # The whole scanned window is one line; only correct if we read from 0.
        return size - window if window == size else None
    return (size - window) + idx + 1


def _read_from(path, offset: int) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(offset)
        return fh.read()


def _torn_offset(path) -> int | None:
    """Offset where a torn tail starts, or None if the file is intact/empty.

    A single offset computation shared by is_tail_torn and repair_torn_tail:
    computing it independently in each (as an earlier version did) let the two
    calls see different file states if the transcript changed size in between,
    so the bytes checked for torn-ness and the bytes truncated could disagree.
    """
    offset = last_line_offset(path)
    if offset is None:
        return None
    tail = _read_from(path, offset).strip()
    if not tail:
        return None
    try:
        json.loads(tail.decode("utf-8", errors="strict"))
        return None
    except Exception:
        return offset


def is_tail_torn(path) -> bool:
    """True when the final line is not parseable JSON — a hard-crash signature."""
    return _torn_offset(path) is not None


def repair_torn_tail(path) -> bool:
    """Truncate a partial final line, preserving the removed bytes alongside.

    Only the discarded tail is backed up, never the whole transcript — these
    files reach hundreds of megabytes and copying one at logon would stall
    the deploy.
    """
    offset = _torn_offset(path)
    if offset is None:
        return False

    removed = _read_from(path, offset)
    pathlib.Path(str(path) + ".torn").write_bytes(removed)
    os.truncate(path, offset)
    return True


def prune_torn_backups(root, max_age_days: float = TORN_BACKUP_MAX_AGE_DAYS) -> list[str]:
    """Delete `.torn` backups older than `max_age_days` under `root`.

    A `.torn` file exists purely as a safety net for the repair that produced
    it — nothing reads it again once a crash has been investigated (or simply
    not investigated at all). Transcript filenames are per-session UUIDs, so
    each crashed session leaves a permanent file with nothing to ever clean
    it up; on a machine that crashes periodically these accumulate forever.
    Returns the paths actually removed. A single file that can't be removed
    (permissions, in-use) is skipped rather than aborting the rest.
    """
    root = pathlib.Path(root)
    removed: list[str] = []
    if not root.is_dir():
        return removed

    cutoff = time.time() - max_age_days * 86400
    for p in root.rglob("*.torn"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed.append(str(p))
        except OSError:
            continue
    return removed


def repair_all(paths) -> list[tuple[str, bool | OSError]]:
    """Repair each path, returning (path, repaired-or-error) per attempt.

    Returns results rather than printing them: deciding what to say about a
    repair belongs to the caller that owns the user's console.
    """
    results: list[tuple[str, bool | OSError]] = []
    for path in paths:
        try:
            results.append((str(path), repair_torn_tail(path)))
        except OSError as exc:
            results.append((str(path), exc))
    return results
