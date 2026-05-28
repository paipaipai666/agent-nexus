"""Codegraph hook registration.

Registers AFTER_TOOL_CALL hooks to automatically sync the code graph
when Agent writes or reads Python files.
"""

from __future__ import annotations

import logging

from agentnexus.core.hooks import HookType, on

logger = logging.getLogger(__name__)


@on(HookType.AFTER_TOOL_CALL, name="codegraph_sync_write", priority=300)
def sync_codegraph_on_file_write(ctx) -> None:
    """Sync codegraph after file_write operations.

    Silently executes; failures don't affect the main flow.
    """
    name = ctx.payload.get("name")
    result = ctx.payload.get("result", {})

    # Only handle file_write
    if name != "file_write":
        return

    # Only handle successful results
    if isinstance(result, dict) and result.get("status") != "ok":
        return

    # Only handle Python files
    params = ctx.payload.get("params", {})
    path = params.get("path", "")
    if not path.endswith(".py"):
        return

    try:
        from agentnexus.codegraph.updater import sync_file
        sync_file(path)
    except Exception:
        pass  # Silent failure


@on(HookType.AFTER_TOOL_CALL, name="codegraph_sync_read", priority=300)
def sync_codegraph_on_file_read(ctx) -> None:
    """Check for external file modifications on file_read.

    When a file is read, check if its content has changed externally
    and update the graph if needed.
    """
    name = ctx.payload.get("name")
    result = ctx.payload.get("result", {})

    # Only handle file_read
    if name != "file_read":
        return

    # Skip error results
    if isinstance(result, str) and result.startswith("错误:"):
        return

    # Only handle Python files
    params = ctx.payload.get("params", {})
    path = params.get("path", "")
    if not path.endswith(".py"):
        return

    try:
        from agentnexus.codegraph.updater import check_and_sync_file
        check_and_sync_file(path)
    except Exception:
        pass  # Silent failure


__all__ = ["sync_codegraph_on_file_write", "sync_codegraph_on_file_read"]
