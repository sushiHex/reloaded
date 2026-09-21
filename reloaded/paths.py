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
    psutil reports — only match after normalization. Two files are keyed this
    way and both must land on the same name for the same directory, which is a
    reason to compute it once rather than twice identically.
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


def restart_marker(cwd: str) -> pathlib.Path:
    """The file `restart` drops to tell one tab's own shell to relaunch.

    Keyed by `_digest(norm(cwd))` rather than the path itself: a cwd is not a
    legal filename, and the two spellings that have to agree on this file — the
    user's argument and the path baked into the running tab's command — only
    match after normalization.
    """
    return restart_marker_dir() / f"{_digest(cwd)}.marker"
