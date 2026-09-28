"""Per-session workspace override for tool cwd/path resolution.

Tools resolve relative paths against the process cwd by default. ChatService
sets the override around each agent run so shell/file tools operate in the
session's chosen folder. ContextVar propagates through ``asyncio.to_thread``
and FastAPI's threadpool, covering both the WS and REST run paths.
"""

from __future__ import annotations

import contextvars
import os
import tempfile
from pathlib import Path

current_workspace: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agentnexus_session_workspace",
    default=None,
)

# Per-run user attachment paths. ChatService registers them around each run
# so file_read can reach attachments outside the session workspace.
# Plain module state, NOT a ContextVar: tool calls execute on dispatcher
# lane threads that never inherit context — a ContextVar set in send_message
# is invisible there (verified by REST smoke: agent's file_read got 路径越界).
# Counted so concurrent runs sharing a file stay correct; paths are revoked
# when the last registering run ends.
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


def get_effective_workspace() -> Path:
    """Return the session's workspace override, falling back to process cwd."""
    override = current_workspace.get()
    if override:
        return Path(override).resolve(strict=False)
    try:
        return Path(os.getcwd()).resolve(strict=False)
    except (FileNotFoundError, OSError):
        return Path(tempfile.gettempdir()).resolve(strict=False)
