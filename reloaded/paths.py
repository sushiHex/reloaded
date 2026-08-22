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


def restart_marker_dir() -> pathlib.Path:
    """Where restart markers live. Swept by directory, so it has a name of its
    own rather than being reached through some arbitrary marker's parent."""
    d = state_dir() / "restart"
    d.mkdir(parents=True, exist_ok=True)
    return d


def restart_marker(cwd: str) -> pathlib.Path:
    """The file `restart` drops to tell one tab's own shell to relaunch.

    Keyed by the hash of `norm(cwd)` rather than the path itself: a cwd is not
    a legal filename, and the two spellings that have to agree on this file —
    the user's argument and the path baked into the running tab's command —
    only match after normalization.
    """
    digest = hashlib.sha256(norm(cwd).encode("utf-8")).hexdigest()[:16]
    return restart_marker_dir() / f"{digest}.marker"
