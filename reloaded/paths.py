"""Path normalization and state-directory resolution."""
from __future__ import annotations

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
