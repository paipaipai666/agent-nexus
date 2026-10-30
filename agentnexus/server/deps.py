"""FastAPI dependency factories shared by API route modules.

Replaces per-handler lazy-import service-locator boilerplate with typed
``Depends(...)`` factories. Error semantics match the previous inline code:
services that may be absent surface as ``APIError`` and are rendered by the
registered error handlers (see ``server/error_handlers.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentnexus.server.app import _get_runtime
from agentnexus.server.error_handlers import APIError

if TYPE_CHECKING:
    from agentnexus.memory.long_term import LongTermMemory
    from agentnexus.tools.mcp.adapter import MCPManager
    from agentnexus.wiki.wiki_service import WikiService


def get_runtime() -> Any:
    """Return the runtime bound to the current server request."""
    return _get_runtime()


def get_ltm() -> "LongTermMemory":
    """Resolve the owning runtime's selected long-term memory provider."""
    from agentnexus.core.modules import get_module_host

    return get_module_host().require("long_term")


def get_mcp_manager() -> "MCPManager":
    """MCP manager, or 404 when MCP is not initialized."""
    manager = _get_runtime().mcp_manager
    if manager is None:
        raise APIError(404, "not_found", "MCP manager not initialized")
    return manager


def get_mcp_manager_optional() -> "MCPManager | None":
    """MCP manager that may be absent; callers render empty-state stubs."""
    return _get_runtime().mcp_manager


def get_wiki_service() -> "WikiService":
    """Resolve the owning runtime's selected Wiki provider."""
    from agentnexus.core.modules import get_module_host

    return get_module_host().require("wiki")
