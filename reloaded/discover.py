"""Read-only session discovery: live processes and transcript metadata.

Reloaded requires no cooperation from the sessions it manages. Everything here
reads what Claude Code and Windows already record.
"""
from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass

from .paths import norm

# Claude Code prefixes the terminal title with a spinner glyph while working.
_GLYPHS = "✳⠐⠂⠁⠉⠙⠒⠄ \t"

# Bounded reads: a transcript can be hundreds of megabytes, and this runs on a
# timer. cwd appears on nearly every record so the head is enough; customTitle
# must come from the tail because a rename appends a newer one.
_HEAD_BYTES = 64 * 1024
_TAIL_BYTES = 256 * 1024


@dataclass
class TranscriptInfo:
    cwd: str
    # Empty when the session has no custom title *or* when it was read with
    # need_title=False — the two are indistinguishable, so do not use an index
    # built that way for title lookups.
    title: str
    path: str
    mtime: float
    size: int


def strip_glyph(title: str) -> str:
    return (title or "").lstrip(_GLYPHS).strip()


def live_sessions() -> dict[str, int]:
    """Map normalized cwd -> pid for every running Claude Code session.

    This is ground truth for liveness and stays correct for an idle session,
    which is why no heartbeat is needed.
    """
    try:
        import psutil
    except ImportError:
        return {}

    out: dict[str, int] = {}
    for proc in psutil.process_iter(["pid", "name"]):
        name = (proc.info.get("name") or "").lower()
        if name != "claude.exe":
            continue
        try:
            cwd = proc.cwd()
        except Exception:
            continue
        if cwd:
            out[norm(cwd)] = int(proc.info["pid"])
    return out


def _iter_json_lines(blob: str):
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue  # torn or partial line — skip it
        if isinstance(obj, dict):
            yield obj


def read_transcript_info(jsonl_path, stat_result=None, need_title: bool = True) -> TranscriptInfo | None:
    """Read cwd (and optionally the session title) from a transcript.

    `stat_result` lets a caller that already stat'ed the file avoid a second
    syscall. `need_title=False` skips the tail read entirely — most transcripts
    here exceed the tail threshold, so callers that only want the size or path
    would otherwise pay a 256 KB read and parse for a field they discard.

    Note that `need_title=False` does *not* help the periodic reconcile: that
    path resolves tab titles, so it needs the tail. The flag benefits the
    logon deploy and single-repo launch, which only read cwd and size.
    """
    p = pathlib.Path(jsonl_path)
    st = stat_result
    if st is None:
        try:
            st = p.stat()
        except OSError:
            return None

    cwd = ""
    title = ""
    try:
        with open(p, "rb") as fh:
            head = fh.read(_HEAD_BYTES).decode("utf-8", errors="replace")
            for obj in _iter_json_lines(head):
                if obj.get("cwd"):
                    cwd = obj["cwd"]
                    break

            # The tail is also where cwd hides when the head scan missed it, so
            # a size-only caller still reads it in that (rare) case.
            must_read_tail = need_title or not cwd
            if must_read_tail:
                if st.st_size > _TAIL_BYTES:
                    fh.seek(-_TAIL_BYTES, os.SEEK_END)
                    tail = fh.read().decode("utf-8", errors="replace")
                    tail = tail.split("\n", 1)[1] if "\n" in tail else ""
                else:
                    fh.seek(0)
                    tail = fh.read().decode("utf-8", errors="replace")
                for obj in _iter_json_lines(tail):
                    if obj.get("customTitle"):
                        title = obj["customTitle"]  # last one wins
                    if not cwd and obj.get("cwd"):
                        cwd = obj["cwd"]
    except OSError:
        return None

    if not cwd:
        return None
    return TranscriptInfo(
        cwd=cwd, title=title, path=str(p), mtime=st.st_mtime, size=st.st_size
    )


def default_projects_dir() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~")) / ".claude" / "projects"


def transcript_index(projects_dir=None, need_title: bool = True) -> dict[str, TranscriptInfo]:
    """Newest transcript per project, keyed by normalized cwd.

    Uses scandir rather than glob+stat over a corpus of >1,000 files: on Windows
    a DirEntry's stat is served from the directory enumeration itself, so
    selecting the newest costs no syscalls, and the winner's stat is reused
    instead of being taken again.
    """
    root = pathlib.Path(projects_dir) if projects_dir else default_projects_dir()
    index: dict[str, TranscriptInfo] = {}
    if not root.is_dir():
        return index

    try:
        with os.scandir(root) as it:
            children = sorted(it, key=lambda e: e.name)
    except OSError:
        return index

    for child in children:
        if not child.is_dir():
            continue
        # Claude Code actively creates and rotates transcripts in these
        # directories, so a file can disappear between being listed and being
        # stat'ed — cover the whole selection, not just the listing.
        try:
            with os.scandir(child.path) as entries:
                transcripts = [
                    e for e in entries if e.name.endswith(".jsonl") and e.is_file()
                ]
            newest = max(transcripts, key=lambda e: e.stat().st_mtime, default=None)
            if newest is None:
                continue
            newest_stat = newest.stat()
        except OSError:
            continue

        info = read_transcript_info(
            newest.path, stat_result=newest_stat, need_title=need_title
        )
        if info is None:
            continue
        key = norm(info.cwd)
        prior = index.get(key)
        if prior is None or info.mtime > prior.mtime:
            index[key] = info
    return index


def title_to_cwd(index: dict[str, TranscriptInfo]) -> dict[str, str]:
    """Reverse map for resolving a tab title to a repo. Newest wins on collision."""
    best: dict[str, TranscriptInfo] = {}
    for info in index.values():
        if not info.title:
            continue
        prior = best.get(info.title)
        if prior is None or info.mtime > prior.mtime:
            best[info.title] = info
    return {title: info.cwd for title, info in best.items()}
