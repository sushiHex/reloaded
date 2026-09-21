"""Path normalization and state-directory resolution."""
from __future__ import annotations

import hashlib
import os
import pathlib


def norm(p: str) -> str:
    """Canonical form for comparing Windows paths.

    Windows paths are case-insensitive and mix separators, so every comparison
    between a cwd from psutil, a cwd from a transcript, and a cwd from the
    layout file must go through this.
    """
    if not p:
        return ""
    return os.path.normcase(os.path.normpath(p))


def resolve_repo(repo: str, repos_root: str) -> str:
    """Resolve a user-typed repo argument: absolute path, or name under the root."""
    return repo if os.path.isabs(repo) else os.path.join(repos_root, repo)


def state_dir() -> pathlib.Path:
    """Directory holding layouts and logs. Created on demand."""
    d = pathlib.Path(os.path.expanduser("~")) / ".reloaded"
    d.mkdir(parents=True, exist_ok=True)
    return d


def layout_path(name: str = "default") -> pathlib.Path:
    d = state_dir() / "layouts"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.json"


def log_path() -> pathlib.Path:
    return state_dir() / "reloaded.log"


def restore_marker() -> pathlib.Path:
    """The file a deploy leaves while its sessions are still starting.

    One per machine rather than one per layout: what it says is "agent sessions
    are coming up right now", which is true of the desktop, not of a file. The
    reconcile reads it whichever layout it was installed with.
    """
    return state_dir() / "restoring.marker"


def restart_marker_dir() -> pathlib.Path:
    """Where restart markers live. Swept by directory, so it has a name of its
    own rather than being reached through some arbitrary marker's parent."""
    d = state_dir() / "restart"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _digest(cwd: str) -> str:
    """How a cwd becomes a filename.

    A cwd is not a legal filename, and the spellings that have to agree on one
    — the user's argument, the path baked into a running tab's command, the one
    psutil reports — only match after normalization. Three files are keyed this
    way and all three must land on the same name for the same directory, which
    is a reason to compute it once rather than three times identically.
    """
    return hashlib.sha256(norm(cwd).encode("utf-8")).hexdigest()[:16]


def relaunch_script_path(cwd: str) -> pathlib.Path:
    """Where the launcher is written for typing into a hand-launched tab.

    Named by the same digest as the marker, so the filename holds no character
    SendKeys would read as syntax - the whole point of using a file is that the
    line typed into the shell is short and literal.
    """
    d = state_dir() / "relaunch"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_digest(cwd)}.ps1"


def restart_attempt(cwd: str, token: str) -> pathlib.Path:
    """What a restart dispatched by `--self` leaves behind until it succeeds.

    `--self` cannot watch its own restart: it dies with the session it ends, so
    it hands the job to a detached helper and prints its result before anything
    has been attempted. The helper is then the only thing that knows the
    outcome, and the session that asked is gone by the time it does.

    Written before the helper starts and removed once the session is confirmed
    back, so a file still here is a restart that did not finish. Presence is the
    whole signal - nothing parses the contents - which is what makes a helper
    that was killed outright report correctly rather than not at all.

    `token` names one dispatch and is in the FILENAME, not the contents. Two
    overlapping `--self` calls for one repository would otherwise share a path:
    the older helper, finishing late, would settle the newer one's record, and
    if that newer helper then died there would be nothing left to report it.
    Carrying the token inside the file made that a read-then-delete race
    instead of a fix. A path of its own has no such window - each helper
    deletes exactly the file it was given and looks at nobody else's.

    Beside the marker, and deliberately not named `.marker`: `_sweep_stale_markers`
    globs that suffix, and an attempt outlives the marker's TTL on purpose.
    """
    return restart_marker_dir() / f"{_digest(cwd)}.{token}.attempt"


def restart_attempts(cwd: str) -> list:
    """Every dispatched restart of `cwd` still outstanding, newest last."""
    try:
        found = restart_marker_dir().glob(f"{_digest(cwd)}.*.attempt")
        return sorted(found, key=lambda p: p.stat().st_mtime)
    except OSError:
        return []


def restart_marker(cwd: str) -> pathlib.Path:
    """The file `restart` drops to tell one tab's own shell to relaunch.

    Keyed by `_digest(norm(cwd))` rather than the path itself: a cwd is not a
    legal filename, and the two spellings that have to agree on this file — the
    user's argument and the path baked into the running tab's command — only
    match after normalization.
    """
    return restart_marker_dir() / f"{_digest(cwd)}.marker"
