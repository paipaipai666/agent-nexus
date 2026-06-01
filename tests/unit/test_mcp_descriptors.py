"""Tests for MCP descriptor construction helpers."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from agentnexus.tools.mcp_descriptors import (
    build_local_tool_name,
    build_tool_descriptor,
    normalize_param_schema,
    prompt_descriptor_from_sdk,
    resource_descriptor_from_sdk,
    should_import_tool,
)


def _make_config(**overrides):
    """Create a mock MCPServerConfig with sensible defaults."""
    defaults = dict(
        name="test_server",
        tool_prefix=None,
        include_tools=[],
        exclude_tools=[],
        allowed_agents=["react_agent"],
        risk_level="medium",
        require_hitl=False,
        timeout_sec=30,
        rate_limit_per_min=10,
    )
    defaults.update(overrides)
    config = MagicMock()
    for k, v in defaults.items():
        setattr(config, k, v)
    return config


# ---------------------------------------------------------------------------
# build_local_tool_name
# ---------------------------------------------------------------------------

class TestBuildLocalToolName:
    """Tests for the build_local_tool_name helper."""

    def test_with_tool_prefix(self):
        config = _make_config(tool_prefix="web", name="server1")
        assert build_local_tool_name(config, "search") == "mcp_web__search"

    def test_without_tool_prefix_uses_server_name(self):
        config = _make_config(tool_prefix=None, name="my_server")
        assert build_local_tool_name(config, "lookup") == "mcp_my_server__lookup"

    def test_special_characters_sanitized(self):
        config = _make_config(tool_prefix="my-server", name="s")
        result = build_local_tool_name(config, "tool@v2!")
        # trailing special chars are stripped by sanitize_name
        assert result == "mcp_my_server__tool_v2"

    def test_tool_prefix_empty_string_uses_server_name(self):
        config = _make_config(tool_prefix="", name="fallback")
        assert build_local_tool_name(config, "tool") == "mcp_fallback__tool"


# ---------------------------------------------------------------------------
# normalize_param_schema
# ---------------------------------------------------------------------------

class TestNormalizeParamSchema:
    """Tests for the normalize_param_schema helper."""

    def test_none_returns_default_schema(self):
        result = normalize_param_schema(None)
        assert result == {"type": "object", "properties": {}}

    def test_dict_with_existing_type_properties_preserved(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
        result = normalize_param_schema(schema)
        assert result["type"] == "object"
        assert result["properties"] == {"x": {"type": "string"}}
        assert result["required"] == ["x"]

    def test_dict_with_missing_fields_defaults_filled(self):
        result = normalize_param_schema({})
        assert result["type"] == "object"
        assert result["properties"] == {}

    def test_non_dict_input_returns_default(self):
        assert normalize_param_schema("not a dict") == {"type": "object", "properties": {}}
        assert normalize_param_schema(42) == {"type": "object", "properties": {}}
        assert normalize_param_schema([1, 2]) == {"type": "object", "properties": {}}

    def test_does_not_mutate_original(self):
        schema = {"type": "string"}
        normalize_param_schema(schema)
        assert schema == {"type": "string"}


# ---------------------------------------------------------------------------
# should_import_tool
# ---------------------------------------------------------------------------

class TestShouldImportTool:
    """Tests for the should_import_tool helper."""

    def test_no_include_no_exclude_returns_true(self):
        config = _make_config(include_tools=[], exclude_tools=[])
        assert should_import_tool(config, "remote_tool", "local_tool") is True

    def test_include_matches_remote_name(self):
        config = _make_config(include_tools=["remote_tool"])
        assert should_import_tool(config, "remote_tool", "local_tool") is True

    def test_include_matches_local_name(self):
        config = _make_config(include_tools=["local_tool"])
        assert should_import_tool(config, "remote_tool", "local_tool") is True

    def test_include_neither_matches_returns_false(self):
        config = _make_config(include_tools=["other_tool"])
        assert should_import_tool(config, "remote_tool", "local_tool") is False

    def test_exclude_matches_remote_name_returns_false(self):
        config = _make_config(exclude_tools=["remote_tool"])
        assert should_import_tool(config, "remote_tool", "local_tool") is False

    def test_exclude_matches_local_name_returns_false(self):
        config = _make_config(exclude_tools=["local_tool"])
        assert should_import_tool(config, "remote_tool", "local_tool") is False

    def test_both_include_and_exclude_exclude_wins(self):
        config = _make_config(
            include_tools=["remote_tool"],
            exclude_tools=["remote_tool"],
        )
        assert should_import_tool(config, "remote_tool", "local_tool") is False


# ---------------------------------------------------------------------------
# build_tool_descriptor
# ---------------------------------------------------------------------------

class TestBuildToolDescriptor:
    """Tests for the build_tool_descriptor helper."""

    def test_tool_with_name_and_description(self):
        config = _make_config(name="srv")
        tool = SimpleNamespace(name="find", description="Find items", inputSchema={"type": "object"})
        desc = build_tool_descriptor(config, tool, local_name="mcp_srv__find")
        assert desc is not None
        assert desc.local_name == "mcp_srv__find"
        assert desc.remote_name == "find"
        assert desc.description == "[MCP:srv] Find items"

    def test_tool_without_name_returns_none(self):
        config = _make_config()
        tool = SimpleNamespace(description="no name here")
        assert build_tool_descriptor(config, tool, local_name="x") is None

    def test_tool_with_empty_description_chinese_fallback(self):
        config = _make_config(name="srv")
        tool = SimpleNamespace(name="my_tool", description="", inputSchema=None)
        desc = build_tool_descriptor(config, tool, local_name="mcp_srv__my_tool")
        assert desc is not None
        assert "远端工具 my_tool" in desc.description

    def test_tool_with_none_description_chinese_fallback(self):
        config = _make_config(name="srv")
        tool = SimpleNamespace(name="my_tool", description=None, inputSchema=None)
        desc = build_tool_descriptor(config, tool, local_name="mcp_srv__my_tool")
        assert desc is not None
        assert "远端工具 my_tool" in desc.description

    def test_tool_with_input_schema_snake_case_fallback(self):
        config = _make_config(name="srv")
        tool = SimpleNamespace(name="t", description="d", inputSchema=None, input_schema={"type": "string"})
        desc = build_tool_descriptor(config, tool, local_name="x")
        assert desc is not None
        assert desc.param_schema["type"] == "string"

    def test_tool_with_input_schema_camel_case(self):
        config = _make_config(name="srv")
        tool = SimpleNamespace(name="t", description="d", inputSchema={"type": "number"})
        desc = build_tool_descriptor(config, tool, local_name="x")
        assert desc.param_schema["type"] == "number"

    def test_descriptor_inherits_config_fields(self):
        config = _make_config(
            name="srv",
            allowed_agents=["agent_a"],
            risk_level="high",
            require_hitl=True,
            timeout_sec=60,
            rate_limit_per_min=5,
        )
        tool = SimpleNamespace(name="t", description="d", inputSchema=None)
        desc = build_tool_descriptor(config, tool, local_name="x")
        assert desc.allowed_agents == ["agent_a"]
        assert desc.risk_level == "high"
        assert desc.require_hitl is True
        assert desc.timeout_sec == 60
        assert desc.rate_limit_per_min == 5


# ---------------------------------------------------------------------------
# resource_descriptor_from_sdk
# ---------------------------------------------------------------------------

class TestResourceDescriptorFromSdk:
    """Tests for the resource_descriptor_from_sdk helper."""

    def test_full_item_all_fields_populated(self):
        item = SimpleNamespace(
            name="docs",
            uri="file:///docs",
            description="Documentation files",
            mimeType="text/plain",
        )
        desc = resource_descriptor_from_sdk("srv", item)
        assert desc.name == "docs"
        assert desc.uri == "file:///docs"
        assert desc.server_name == "srv"
        assert desc.description == "Documentation files"
        assert desc.mime_type == "text/plain"

    def test_minimal_item_name_falls_back_to_uri(self):
        item = SimpleNamespace(uri="file:///only_uri")
        desc = resource_descriptor_from_sdk("srv", item)
        assert desc.name == "file:///only_uri"
        assert desc.uri == "file:///only_uri"
        assert desc.server_name == "srv"
        assert desc.description == ""
        assert desc.mime_type == ""

    def test_no_name_no_uri_empty_name(self):
        item = SimpleNamespace()
        desc = resource_descriptor_from_sdk("srv", item)
        assert desc.name == ""
        assert desc.uri == ""


# ---------------------------------------------------------------------------
# prompt_descriptor_from_sdk
# ---------------------------------------------------------------------------

class TestPromptDescriptorFromSdk:
    """Tests for the prompt_descriptor_from_sdk helper."""

    def test_arguments_is_list_each_arg_dumped(self):
        arg1 = SimpleNamespace(name="user", description="username")
        arg2 = SimpleNamespace(name="msg", description="message")
        item = SimpleNamespace(
            name="greet",
            description="Greet user",
            arguments=[arg1, arg2],
        )
        desc = prompt_descriptor_from_sdk("srv", item)
        assert desc.name == "greet"
        assert desc.server_name == "srv"
        assert desc.description == "Greet user"
        assert len(desc.arguments) == 2
        assert desc.arguments[0]["name"] == "user"
        assert desc.arguments[1]["name"] == "msg"

    def test_arguments_not_a_list_defaults_empty(self):
        item = SimpleNamespace(
            name="prompt",
            description="d",
            arguments="not_a_list",
        )
        desc = prompt_descriptor_from_sdk("srv", item)
        assert desc.arguments == []

    def test_arguments_none_defaults_empty(self):
        item = SimpleNamespace(name="prompt", description="d", arguments=None)
        desc = prompt_descriptor_from_sdk("srv", item)
        assert desc.arguments == []

    def test_minimal_item(self):
        item = SimpleNamespace()
        desc = prompt_descriptor_from_sdk("srv", item)
        assert desc.name == ""
        assert desc.server_name == "srv"
        assert desc.description == ""
        assert desc.arguments == []
