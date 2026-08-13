"""Read-only session discovery: live processes and transcript metadata.

Reloaded requires no cooperation from the sessions it manages. Everything here
reads what Claude Code and Windows already record.
"""
from __future__ import annotations

import json
import os
import pathlib
import unicodedata
from dataclasses import dataclass

from .paths import norm

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


# The spinner families Claude Code has shipped, as ranges rather than single
# codepoints so a new frame *within* a family still matches without a code
# change. That is the whole point: an exact frame list went stale once already
# (Braille dots -> circled halves) and failed silently.
#
# Deliberately NOT "any symbol". Matching every category-So character also ate
# user decoration: a hand-renamed shell tab "▶ build" (U+25B6) cleaned to
# "build", resolved against a live repo of that name, and `down` typed /exit
# into the user's shell and could close its window. A spinner and a decoration
# are textually identical - symbol, space, name - so breadth cannot be traded
# for safety here. Prefer the recoverable failure: an unrecognised frame leaves
# the title alone and the tab is skipped, which a re-capture fixes.
#
# To add a family: append its range, add a case to
# test_strip_glyph_removes_every_shipped_spinner_family.
_SPINNER_RANGES = (
    (0x2733, 0x2733),  # ✳   ready marker
    (0x2800, 0x28FF),  # ⠐⠂⠁ Braille patterns
    (0x25D0, 0x25D3),  # ◐◑◒◓ circled halves
    (0x25F0, 0x25F7),  # ◰◱◲◳ quadrant squares/circles, same visual family
)

# Characters that only ever modify the glyph before them: variation selectors
# (Mn), skin-tone modifiers (Sk), zero-width joiner (Cf). They are not So, so a
# category match left them behind as invisible debris - "⚙️ x" kept an orphan
# U+FE0F, which then never matched any path.
_GLYPH_MODIFIERS = frozenset({"Mn", "Sk", "Cf"})


def _is_spinner_char(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _SPINNER_RANGES)


def strip_glyph(title: str) -> str:
    """Drop Claude Code's leading status glyph, whichever frame it is showing.

    Strips a leading run of whitespace and known spinner frames, plus any
    modifier that trails one. Anything else - including an emoji the user chose
    as their title - is left alone, so "🚀" and the busy "✳ 🚀" both clean to
    "🚀" and a tab keeps matching its own key while it works.
    """
    s = title if isinstance(title, str) else ""
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isspace() or _is_spinner_char(ch):
            i += 1
        elif i > 0 and unicodedata.category(ch) in _GLYPH_MODIFIERS:
            i += 1
        else:
            break
    return s[i:].strip()


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


def _str_field(obj: dict, key: str) -> bool:
    """Whether `obj[key]` is a non-empty string.

    A transcript record is untrusted input: these fields are whatever the JSON
    held, and nothing downstream re-checks them. `cwd` reaches os.path via
    paths.norm and `customTitle` reaches strip_glyph, both of which raise on a
    non-string. A single record with a numeric title - a format change, or a
    corrupt line that still parses - would otherwise take down capture and
    every reconcile run with a traceback until that transcript rotates away.

    Screening at the read boundary keeps every consumer safe without each one
    repeating the check.
    """
    value = obj.get(key)
    return isinstance(value, str) and bool(value)


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
                if _str_field(obj, "cwd"):
                    cwd = obj["cwd"]
                    break

            # The tail is also where cwd hides when the head scan missed it, so
            # a size-only caller still reads it in that (rare) case.
            must_read_tail = need_title or not cwd
            if must_read_tail:
                # Size from the open handle, never the cached scandir stat. A
                # transcript can rotate between enumeration and open, and a
                # stale larger size makes seek(-_TAIL_BYTES, SEEK_END) land
                # before the start of the file: OSError [Errno 22], swallowed
                # below as "unreadable", so the project silently disappeared
                # from the index and every session in it from the layout.
                size = os.fstat(fh.fileno()).st_size
                if size > _TAIL_BYTES:
                    fh.seek(-_TAIL_BYTES, os.SEEK_END)
                    tail = fh.read().decode("utf-8", errors="replace")
                    tail = tail.split("\n", 1)[1] if "\n" in tail else ""
                else:
                    fh.seek(0)
                    tail = fh.read().decode("utf-8", errors="replace")
                for obj in _iter_json_lines(tail):
                    if _str_field(obj, "customTitle"):
                        title = obj["customTitle"]  # last one wins
                    if not cwd and _str_field(obj, "cwd"):
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
    """Reverse map for resolving a tab title to a repo. Newest wins on collision.

    Keyed on the *raw* customTitle, deliberately, and that is sound because
    strip_glyph only removes spinner frames. A customTitle is what the user set
    via /title; the spinner decorates the terminal title, not the transcript.
    So the key and the stripped tab title agree, including for an emoji title -
    "🚀 svc" keys and resolves, idle and busy alike.

    Keying on the stripped title instead looks like a repair and is worse:
    stripping is not injective, so two repos titled "✳ api" and "🔧 api"
    collapse to one key and the loser's cwd is *discarded*, erasing a live repo
    from the layout, binding a tab to another session's pid, and letting
    teardown close a window whose session is still working. Measured, not
    theorised.

    Residual miss: a customTitle that itself begins with a spinner character
    ("✳ odd") keeps it here but loses it on the lookup side, so it falls
    through to the repos-root basename guess.
    """
    best: dict[str, TranscriptInfo] = {}
    for info in index.values():
        if not info.title:
            continue
        prior = best.get(info.title)
        if prior is None or info.mtime > prior.mtime:
            best[info.title] = info
    return {title: info.cwd for title, info in best.items()}
