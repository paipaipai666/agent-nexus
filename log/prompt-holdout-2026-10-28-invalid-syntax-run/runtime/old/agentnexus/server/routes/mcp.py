"""MCP (Model Context Protocol) management API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentnexus.server.deps import get_mcp_manager, get_mcp_manager_optional
from agentnexus.server.error_handlers import APIError

router = APIRouter(tags=["mcp"])


@router.get("/status")
def mcp_status(manager=Depends(get_mcp_manager_optional)):
    if manager is None:
        return {"started": False, "servers": [], "total_tools": 0, "total_resources": 0, "total_prompts": 0}
    return manager.status_snapshot()


@router.get("/tools")
def list_mcp_tools(server: str | None = None, manager=Depends(get_mcp_manager_optional)):
    if manager is None:
        return {"tools": [], "count": 0}
    snapshot = manager.status_snapshot()
    servers = snapshot.get("servers", [])
    if server:
        servers = [s for s in servers if s["name"] == server]
    all_tools = []
    for s in servers:
        for tool_name in s.get("tool_names", []):
            all_tools.append({"server": s["name"], "tool": tool_name, "transport": s["transport"]})
    return {"tools": all_tools, "count": len(all_tools)}


@router.get("/resources")
def list_mcp_resources(server: str | None = None, manager=Depends(get_mcp_manager_optional)):
    if manager is None:
        return {"resources": []}
    snapshot = manager.status_snapshot()
    servers = snapshot.get("servers", [])
    if server:
        servers = [s for s in servers if s["name"] == server]
    all_resources = []
    for s in servers:
        all_resources.append({
            "server": s["name"],
            "resource_count": s.get("resource_count", 0),
            "template_count": s.get("resource_template_count", 0),
            "resource_tool_names": s.get("resource_tool_names", []),
        })
    return {"resources": all_resources}


@router.get("/prompts")
def list_mcp_prompts(server: str | None = None, manager=Depends(get_mcp_manager_optional)):
    if manager is None:
        return {"prompts": []}
    snapshot = manager.status_snapshot()
    servers = snapshot.get("servers", [])
    if server:
        servers = [s for s in servers if s["name"] == server]
    all_prompts = []
    for s in servers:
        all_prompts.append({
            "server": s["name"],
            "prompt_count": s.get("prompt_count", 0),
            "prompt_tool_names": s.get("prompt_tool_names", []),
        })
    return {"prompts": all_prompts}


@router.get("/failures")
def list_mcp_failures(manager=Depends(get_mcp_manager_optional)):
    if manager is None:
        return {"failures": [], "count": 0}
    snapshot = manager.status_snapshot()
    failures = [s for s in snapshot.get("servers", []) if s.get("state") != "healthy"]
    return {"failures": failures, "count": len(failures)}


@router.post("/retry")
def retry_mcp(server: str | None = None, manager=Depends(get_mcp_manager)):
    try:
        result = manager.retry_failed(server_name=server)
        return {"status": "retried", "result": result}
    except Exception as e:
        raise APIError(500, "internal_error", str(e))


@router.post("/{server_name}/enable")
def enable_mcp_server(server_name: str, manager=Depends(get_mcp_manager)):
    try:
        result = manager.enable_server(server_name)
        return {"status": "enabled", "server": server_name, "result": result}
    except Exception as e:
        raise APIError(500, "internal_error", str(e))


@router.post("/{server_name}/disable")
def disable_mcp_server(server_name: str, manager=Depends(get_mcp_manager)):
    try:
        result = manager.disable_server(server_name)
        return {"status": "disabled", "server": server_name, "result": result}
    except Exception as e:
        raise APIError(500, "internal_error", str(e))


@router.post("/reload")
def reload_mcp(server: str | None = None, manager=Depends(get_mcp_manager)):
    try:
        result = manager.reload_server(server)
        return {"status": "reloaded", "result": result}
    except Exception as e:
        raise APIError(500, "internal_error", str(e))
