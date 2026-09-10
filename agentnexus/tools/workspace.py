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


def get_effective_workspace() -> Path:
    """Return the session's workspace override, falling back to process cwd."""
    override = current_workspace.get()
    if override:
        return Path(override).resolve(strict=False)
    try:
        return Path(os.getcwd()).resolve(strict=False)
    except (FileNotFoundError, OSError):
        return Path(tempfile.gettempdir()).resolve(strict=False)
