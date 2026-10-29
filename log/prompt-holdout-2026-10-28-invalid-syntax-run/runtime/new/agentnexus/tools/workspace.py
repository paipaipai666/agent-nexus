"""Per-session workspace override for tool cwd/path resolution.

Tools resolve relative paths against the process cwd by default. ChatService
sets the override around each agent run so shell/file tools operate in the
session's chosen folder. Async framework workers inherit the ContextVar;
tool, subagent, and hook pools explicitly copy the submitting run's context.
"""

from __future__ import annotations

import contextvars
import os
import tempfile
import threading
from pathlib import Path

current_workspace: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agentnexus_session_workspace",
    default=None,
)

_ATTACHMENT_PATH_COUNTS: dict[str, int] = {}


def register_attachment_paths(paths: tuple[str, ...] | list[str]) -> None:
    """Allow file_read on exactly these absolute paths until unregister."""
    for p in paths:
        key = str(Path(p).resolve(strict=False))
        _ATTACHMENT_PATH_COUNTS[key] = _ATTACHMENT_PATH_COUNTS.get(key, 0) + 1


def unregister_attachment_paths(paths: tuple[str, ...] | list[str]) -> None:
    for p in paths:
        key = str(Path(p).resolve(strict=False))
        n = _ATTACHMENT_PATH_COUNTS.get(key, 0) - 1
        if n > 0:
            _ATTACHMENT_PATH_COUNTS[key] = n
        else:
            _ATTACHMENT_PATH_COUNTS.pop(key, None)


def get_registered_attachment_paths() -> tuple[str, ...]:
    return tuple(_ATTACHMENT_PATH_COUNTS)


_APPROVED_PATHS: set[tuple[str, str]] = set()
_APPROVED_PATHS_LOCK = threading.Lock()


def approve_extra_path(path: str | Path) -> None:
    """Allow the resolved path for the current workspace until process exit."""
    grant = (str(get_effective_workspace()), str(Path(path).resolve(strict=False)))
    with _APPROVED_PATHS_LOCK:
        _APPROVED_PATHS.add(grant)


def get_approved_extra_paths() -> tuple[str, ...]:
    workspace = str(get_effective_workspace())
    with _APPROVED_PATHS_LOCK:
        return tuple(p for ws, p in _APPROVED_PATHS if ws == workspace)


def clear_approved_paths() -> None:
    with _APPROVED_PATHS_LOCK:
        _APPROVED_PATHS.clear()


def get_effective_workspace() -> Path:
    """Return the session's workspace override, falling back to process cwd."""
    override = current_workspace.get()
    if override:
        return Path(override).resolve(strict=False)
    try:
        return Path(os.getcwd()).resolve(strict=False)
    except (FileNotFoundError, OSError):
        return Path(tempfile.gettempdir()).resolve(strict=False)
