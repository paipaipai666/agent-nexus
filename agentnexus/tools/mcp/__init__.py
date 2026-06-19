"""MCP subpackage — all MCP client/adapter modules consolidated here."""

from agentnexus.tools.mcp.adapter import (
    MCPToolManager,
    _apply_capability_mcp_enabled,
    _content_block_to_text,
    _dump_sdk_object,
    _json_text,
    _normalize_prompt_result,
    _normalize_resource_result,
    _normalize_tool_result,
    _prompt_descriptor_from_sdk,
    _resource_descriptor_from_sdk,
    _sanitize_name,
    create_mcp_manager_from_settings,
)
from agentnexus.tools.mcp.call import call_descriptor, run_with_limiter
from agentnexus.tools.mcp.capabilities import (
    build_prompt_tool_descriptors,
    build_resource_tool_descriptors,
    import_prompts,
    import_resources,
    import_server_capabilities,
)
from agentnexus.tools.mcp.connection import build_http_client_kwargs, ensure_sdk_available
from agentnexus.tools.mcp.descriptors import (
    build_local_tool_name,
    build_tool_descriptor,
    normalize_param_schema,
    prompt_descriptor_from_sdk,
    resource_descriptor_from_sdk,
    should_import_tool,
)
from agentnexus.tools.mcp.health import (
    mark_runtime_failure,
    mark_runtime_healthy,
    schedule_reconnect,
    should_attempt_reconnect,
)
from agentnexus.tools.mcp.lifecycle import (
    close_all,
    connect_all,
    connect_server,
    disconnect_server,
)
from agentnexus.tools.mcp.result import (
    content_block_to_text,
    dump_sdk_object,
    get_sdk_attr,
    json_text,
    normalize_prompt_result,
    normalize_resource_result,
    normalize_tool_result,
    sanitize_name,
)
from agentnexus.tools.mcp.schema import (
    MCPPromptDescriptor,
    MCPResourceDescriptor,
    MCPServerState,
    MCPToolDescriptor,
    ServerRuntime,
)

__all__ = [
    # adapter
    "MCPToolManager",
    "create_mcp_manager_from_settings",
    # call
    "call_descriptor",
    "run_with_limiter",
    # capabilities
    "build_prompt_tool_descriptors",
    "build_resource_tool_descriptors",
    "import_prompts",
    "import_resources",
    "import_server_capabilities",
    # connection
    "build_http_client_kwargs",
    "ensure_sdk_available",
    # descriptors
    "build_local_tool_name",
    "build_tool_descriptor",
    "normalize_param_schema",
    "prompt_descriptor_from_sdk",
    "resource_descriptor_from_sdk",
    "should_import_tool",
    # health
    "mark_runtime_failure",
    "mark_runtime_healthy",
    "schedule_reconnect",
    "should_attempt_reconnect",
    # lifecycle
    "close_all",
    "connect_all",
    "connect_server",
    "disconnect_server",
    # result
    "content_block_to_text",
    "dump_sdk_object",
    "get_sdk_attr",
    "json_text",
    "normalize_prompt_result",
    "normalize_resource_result",
    "normalize_tool_result",
    "sanitize_name",
    # schema
    "MCPPromptDescriptor",
    "MCPResourceDescriptor",
    "MCPServerState",
    "MCPToolDescriptor",
    "ServerRuntime",
    # backward compat helpers (underscore-prefixed)
    "_apply_capability_mcp_enabled",
    "_content_block_to_text",
    "_dump_sdk_object",
    "_json_text",
    "_normalize_prompt_result",
    "_normalize_resource_result",
    "_normalize_tool_result",
    "_prompt_descriptor_from_sdk",
    "_resource_descriptor_from_sdk",
    "_sanitize_name",
]
