import asyncio
import inspect
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_adapter import (
    MCPServerState,
    MCPToolDescriptor,
    MCPToolManager,
    _content_block_to_text,
    _normalize_tool_result,
    _sanitize_name,
    create_mcp_manager_from_settings,
)
from agentnexus.tools.tool_executor import ToolExecutor


class FakeExitStack:
    async def aclose(self):
        return None


def _make_descriptor(**overrides) -> MCPToolDescriptor:
    defaults = dict(
        local_name="mcp_docs__search",
        remote_name="search",
        server_name="docs",
        description="[MCP:docs] 搜索文档",
        param_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        allowed_agents=["react_agent"],
        risk_level="medium",
        require_hitl=False,
        timeout_sec=30,
        rate_limit_per_min=5,
    )
    defaults.update(overrides)
    return MCPToolDescriptor(**defaults)


class TestSanitizeName:
    def test_sanitize_lowercases(self):
        assert _sanitize_name("HelloWorld") == "helloworld"

    def test_sanitize_replaces_non_alnum(self):
        assert _sanitize_name("foo bar!baz@123") == "foo_bar_baz_123"

    def test_sanitize_strips_leading_trailing_underscores(self):
        assert _sanitize_name("__hello__") == "hello"

    def test_sanitize_empty_falls_back(self):
        assert _sanitize_name("") == "tool"

    def test_sanitize_all_special_chars(self):
        assert _sanitize_name("!!!___!!!") == "tool"


class TestContentBlockToText:
    def test_text_block_returns_text(self):
        block = SimpleNamespace(text="hello")
        assert _content_block_to_text(block) == "hello"

    def test_resource_text_block(self):
        block = SimpleNamespace(text=None, resource=SimpleNamespace(text="resource text"))
        assert _content_block_to_text(block) == "resource text"

    def test_resource_blob_block(self):
        block = SimpleNamespace(
            text=None, resource=SimpleNamespace(text=None, blob=b"binary", mimeType="image/png")
        )
        assert _content_block_to_text(block) == "[embedded resource: image/png]"

    def test_resource_blob_no_mime(self):
        block = SimpleNamespace(
            text=None, resource=SimpleNamespace(text=None, blob=b"data", mimeType=None, mime_type="application/pdf")
        )
        assert _content_block_to_text(block) == "[embedded resource: application/pdf]"

    def test_resource_uri_only(self):
        res = SimpleNamespace(text=None, blob=None, uri="https://example.com/resource", mimeType=None, mime_type=None)
        block = SimpleNamespace(text=None, resource=res)
        assert _content_block_to_text(block) == "[embedded resource] https://example.com/resource"

    def test_binary_content_with_mime(self):
        block = SimpleNamespace(text=None, resource=None, mimeType="audio/wav", data=b"...")
        assert _content_block_to_text(block) == "[binary content: audio/wav]"

    def test_binary_content_mime_alt(self):
        block = SimpleNamespace(text=None, resource=None, mimeType=None, mime_type="video/mp4", data=b"...")
        assert _content_block_to_text(block) == "[binary content: video/mp4]"

    def test_model_dump_fallback(self):
        block = MagicMock()
        block.text = None
        block.resource = None
        block.mimeType = None
        block.mime_type = None
        block.data = None
        block.model_dump.return_value = {"key": "value"}
        result = _content_block_to_text(block)
        assert '"key"' in result

    def test_str_fallback(self):
        block = object()
        result = _content_block_to_text(block)
        assert result == str(block)


class TestNormalizeToolResult:
    def test_simple_text_content(self):
        result = SimpleNamespace(content=[SimpleNamespace(text="line1"), SimpleNamespace(text="line2")],
                                  isError=False, structuredContent=None)
        assert _normalize_tool_result(result) == "line1\nline2"

    def test_structured_content_takes_priority(self):
        result = SimpleNamespace(
            structuredContent={"summary": "structured"},
            structured_content=None,
            content=[SimpleNamespace(text="text content")],
            isError=False,
        )
        text = _normalize_tool_result(result)
        assert "summary" in text
        assert "structured" in text

    def test_structured_content_fallback(self):
        result = SimpleNamespace(
            structuredContent=None,
            structured_content={"key": "val"},
            content=[],
            isError=False,
        )
        text = _normalize_tool_result(result)
        assert "key" in text

    def test_no_content_fallback(self):
        result = SimpleNamespace(content=[], structuredContent=None, structured_content=None, isError=False)
        assert _normalize_tool_result(result) == "[mcp] 工具未返回文本内容"

    def test_resource_content(self):
        block = SimpleNamespace(text=None, resource=SimpleNamespace(text="resource text"))
        result = SimpleNamespace(content=[block], structuredContent=None, structured_content=None, isError=False)
        assert _normalize_tool_result(result) == "resource text"


class TestMcpToolManager:
    def test_disabled_servers_are_filtered(self):
        manager = MCPToolManager([
            MCPServerConfig(name="enabled", transport="stdio", command="python"),
            MCPServerConfig(name="disabled", transport="stdio", command="python", enabled=False),
        ])
        assert len(manager._servers) == 1
        assert manager._servers[0].name == "enabled"

    def test_failures_property_returns_copy(self):
        manager = MCPToolManager([])
        manager._failures["s1"] = "err"
        copied = manager.failures
        copied["s2"] = "another"
        assert "s2" not in manager._failures

    def test_tool_descriptors_returns_list(self):
        manager = MCPToolManager([])
        manager._tool_descriptors["a"] = _make_descriptor(local_name="a")
        manager._tool_descriptors["b"] = _make_descriptor(local_name="b")
        assert len(manager.tool_descriptors()) == 2

    def test_list_tool_names(self):
        manager = MCPToolManager([])
        manager._tool_descriptors["a"] = _make_descriptor(local_name="a")
        manager._tool_descriptors["b"] = _make_descriptor(local_name="b")
        names = manager.list_tool_names()
        assert "a" in names
        assert "b" in names

    def test_list_subagent_tool_names_filters_by_allowed_agents_wildcard(self):
        manager = MCPToolManager([])
        manager._tool_descriptors["shared"] = _make_descriptor(
            local_name="shared", allowed_agents=["*"]
        )
        manager._tool_descriptors["restricted"] = _make_descriptor(
            local_name="restricted", allowed_agents=["react_agent"]
        )
        names = manager.list_subagent_tool_names()
        assert "shared" in names
        assert "restricted" not in names

    def test_list_subagent_tool_names_includes_subagent_prefix(self):
        manager = MCPToolManager([])
        manager._tool_descriptors["mcp_foo__echo"] = _make_descriptor(
            local_name="mcp_foo__echo",
            allowed_agents=["react_agent", "subagent_explorer"],
        )
        names = manager.list_subagent_tool_names()
        assert "mcp_foo__echo" in names

    # ── _build_local_tool_name ───────────────────────────────────

    def test_build_local_tool_name_uses_tool_prefix(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(name="my-server", transport="stdio", command="python", tool_prefix="my_prefix")
        name = manager._build_local_tool_name(config, "search-tool")
        assert name == "mcp_my_prefix__search_tool"

    def test_build_local_tool_name_falls_back_to_server_name(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(name="my-server", transport="stdio", command="python")
        name = manager._build_local_tool_name(config, "do-something")
        assert name == "mcp_my_server__do_something"

    # ── _ensure_unique_name ──────────────────────────────────────

    def test_ensure_unique_name_returns_as_is_when_no_collision(self):
        manager = MCPToolManager([])
        assert manager._ensure_unique_name("mcp_foo__bar") == "mcp_foo__bar"

    def test_ensure_unique_name_appends_suffix_on_collision(self):
        manager = MCPToolManager([])
        manager._tool_descriptors["mcp_foo__bar"] = _make_descriptor(local_name="mcp_foo__bar")
        assert manager._ensure_unique_name("mcp_foo__bar") == "mcp_foo__bar_2"

    def test_ensure_unique_name_increments_suffix(self):
        manager = MCPToolManager([])
        manager._tool_descriptors["mcp_foo__bar"] = _make_descriptor(local_name="mcp_foo__bar")
        manager._tool_descriptors["mcp_foo__bar_2"] = _make_descriptor(local_name="mcp_foo__bar_2")
        assert manager._ensure_unique_name("mcp_foo__bar") == "mcp_foo__bar_3"

    # ── _normalize_param_schema ──────────────────────────────────

    def test_normalize_param_schema_defaults(self):
        assert MCPToolManager._normalize_param_schema(None) == {"type": "object", "properties": {}}

    def test_normalize_param_schema_preserves_existing(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
        result = MCPToolManager._normalize_param_schema(schema)
        assert result == schema

    def test_normalize_param_schema_fills_missing_type(self):
        schema = {"properties": {"x": {"type": "string"}}}
        result = MCPToolManager._normalize_param_schema(schema)
        assert result["type"] == "object"

    def test_normalize_param_schema_fills_missing_properties(self):
        schema = {"type": "object"}
        result = MCPToolManager._normalize_param_schema(schema)
        assert result["properties"] == {}

    def test_normalize_param_schema_non_dict(self):
        assert MCPToolManager._normalize_param_schema("bad") == {"type": "object", "properties": {}}

    # ── _should_import_tool ──────────────────────────────────────

    def test_should_import_no_lists(self):
        config = MCPServerConfig(name="x", transport="stdio", command="python")
        assert MCPToolManager._should_import_tool(config, "search", "mcp_x__search") is True

    def test_should_import_excludes_by_remote_name(self):
        config = MCPServerConfig(name="x", transport="stdio", command="python", exclude_tools=["search"])
        assert MCPToolManager._should_import_tool(config, "search", "mcp_x__search") is False

    def test_should_import_excludes_by_local_name(self):
        config = MCPServerConfig(name="x", transport="stdio", command="python", exclude_tools=["mcp_x__delete"])
        assert MCPToolManager._should_import_tool(config, "delete", "mcp_x__delete") is False

    def test_should_import_requires_include(self):
        config = MCPServerConfig(name="x", transport="stdio", command="python", include_tools=["search"])
        assert MCPToolManager._should_import_tool(config, "write", "mcp_x__write") is False

    def test_should_import_include_matches_remote(self):
        config = MCPServerConfig(name="x", transport="stdio", command="python", include_tools=["search"])
        assert MCPToolManager._should_import_tool(config, "search", "mcp_x__search") is True

    def test_should_import_include_matches_local(self):
        config = MCPServerConfig(name="x", transport="stdio", command="python", include_tools=["mcp_x__search"])
        assert MCPToolManager._should_import_tool(config, "search", "mcp_x__search") is True

    def test_should_import_exclude_overrides_include(self):
        config = MCPServerConfig(
            name="x", transport="stdio", command="python",
            include_tools=["search", "delete"],
            exclude_tools=["delete"],
        )
        assert MCPToolManager._should_import_tool(config, "search", "mcp_x__search") is True
        assert MCPToolManager._should_import_tool(config, "delete", "mcp_x__delete") is False

    # ── _build_descriptor ────────────────────────────────────────

    def test_build_descriptor_returns_descriptor(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(name="myapi", transport="stdio", command="python")
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        tool = SimpleNamespace(name="search", description="Search API", inputSchema=schema)
        descriptor = manager._build_descriptor(config, tool)
        assert descriptor is not None
        assert descriptor.local_name == "mcp_myapi__search"
        assert descriptor.remote_name == "search"
        assert descriptor.server_name == "myapi"
        assert "[MCP:myapi]" in descriptor.description
        assert descriptor.param_schema["properties"]["q"]["type"] == "string"

    def test_build_descriptor_no_name_returns_none(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(name="x", transport="stdio", command="python")
        tool = SimpleNamespace(name=None)
        assert manager._build_descriptor(config, tool) is None

    def test_build_descriptor_empty_description_fallback(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(name="api", transport="stdio", command="python")
        tool = SimpleNamespace(name="echo", description="")
        descriptor = manager._build_descriptor(config, tool)
        assert descriptor is not None
        assert "远端工具" in descriptor.description

    def test_build_descriptor_uses_input_schema_alternate_name(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(name="api", transport="stdio", command="python")
        schema = {"type": "object", "properties": {"msg": {"type": "string"}}}
        tool = SimpleNamespace(name="echo", description="Echo", input_schema=schema)
        descriptor = manager._build_descriptor(config, tool)
        assert descriptor.param_schema["properties"]["msg"]["type"] == "string"

    def test_build_descriptor_propagates_security_settings(self):
        manager = MCPToolManager([])
        config = MCPServerConfig(
            name="risky", transport="stdio", command="python",
            allowed_agents=["admin"],
            risk_level="high",
            require_hitl=True,
            timeout_sec=10,
            rate_limit_per_min=1,
        )
        tool = SimpleNamespace(
            name="delete", description="Delete everything",
            inputSchema={"type": "object", "properties": {}},
        )
        descriptor = manager._build_descriptor(config, tool)

        assert descriptor.allowed_agents == ["admin"]
        assert descriptor.risk_level == "high"
        assert descriptor.require_hitl is True
        assert descriptor.timeout_sec == 10
        assert descriptor.rate_limit_per_min == 1

    # ── _make_tool_callable ──────────────────────────────────────

    def test_make_tool_callable_returns_wrapper(self, monkeypatch):
        manager = MCPToolManager([])
        manager._tool_descriptors["echo"] = _make_descriptor(local_name="echo")
        called = []

        def fake_call(name, params):
            called.append((name, params))
            return "ok"

        monkeypatch.setattr(manager, "call_tool", fake_call)
        wrapper = manager._make_tool_callable("echo")
        assert wrapper(message="hello") == "ok"
        assert called == [("echo", {"message": "hello"})]
        assert wrapper.__name__ == "mcp_echo"

    # ── register_tools ───────────────────────────────────────────

    def test_register_tools_registers_on_executor(self):
        manager = MCPToolManager([])
        manager._tool_descriptors["mcp_api__search"] = _make_descriptor(
            local_name="mcp_api__search",
            remote_name="search",
            server_name="api",
        )
        executor = ToolExecutor()
        registered = manager.register_tools(executor)
        assert registered == ["mcp_api__search"]
        assert executor.getTool("mcp_api__search") is not None

    def test_register_tools_respects_include_filter(self):
        manager = MCPToolManager([])
        manager._tool_descriptors["tool_a"] = _make_descriptor(local_name="tool_a")
        manager._tool_descriptors["tool_b"] = _make_descriptor(local_name="tool_b")
        executor = ToolExecutor()
        registered = manager.register_tools(executor, include_tools={"tool_a"})
        assert registered == ["tool_a"]
        assert executor.getTool("tool_a") is not None
        assert executor.getTool("tool_b") is None

    def test_register_tools_empty_when_no_descriptors(self):
        manager = MCPToolManager([])
        executor = ToolExecutor()
        assert manager.register_tools(executor) == []

    # ── call_tool ────────────────────────────────────────────────

    def test_call_tool_raises_on_unknown_tool(self):
        manager = MCPToolManager([])
        manager._started = True
        try:
            manager.call_tool("nonexistent")
            assert False, "Expected KeyError"
        except KeyError as e:
            assert "nonexistent" in str(e)

    def test_call_tool_submits_async_call(self, monkeypatch):
        manager = MCPToolManager([])
        manager._tool_descriptors["echo"] = _make_descriptor(
            local_name="echo", timeout_sec=15
        )
        manager._started = True
        manager._loop = SimpleNamespace()  # just for _submit check
        submitted = []

        def fake_submit(coro, timeout):
            submitted.append((coro, timeout))
            return "mocked"

        monkeypatch.setattr(manager, "_submit", fake_submit)
        result = manager.call_tool("echo", {"msg": "hi"})
        assert result == "mocked"
        assert submitted[0][1] == 20  # timeout_sec + 5

    # ── _call_tool_async ─────────────────────────────────────────

    def test_call_tool_async_happy_path(self):
        manager = MCPToolManager([])

        async def fake_call_tool(name, arguments=None):
            return SimpleNamespace(
                content=[SimpleNamespace(text="result text")],
                isError=False,
                is_error=False,
            )

        mock_session = SimpleNamespace(call_tool=fake_call_tool)
        manager._server_runtimes["docs"] = SimpleNamespace(
            session=mock_session,
            call_lock=asyncio.Lock(),
        )
        descriptor = _make_descriptor(server_name="docs", remote_name="search", timeout_sec=30)
        result = asyncio.run(manager._call_tool_async(descriptor, {"q": "test"}))
        assert result == "result text"

    def test_call_tool_async_raises_on_disconnected_server(self):
        manager = MCPToolManager([])
        descriptor = _make_descriptor(server_name="missing")
        try:
            asyncio.run(manager._call_tool_async(descriptor, {}))
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "not connected" in str(e)

    def test_call_tool_async_raises_on_tool_error(self):
        manager = MCPToolManager([])

        async def fake_call_tool(name, arguments=None):
            return SimpleNamespace(
                content=[SimpleNamespace(text="error msg")],
                isError=True,
                is_error=True,
            )

        mock_session = SimpleNamespace(call_tool=fake_call_tool)
        manager._server_runtimes["docs"] = SimpleNamespace(
            session=mock_session,
            call_lock=asyncio.Lock(),
        )
        descriptor = _make_descriptor(server_name="docs", remote_name="fail")
        try:
            asyncio.run(manager._call_tool_async(descriptor, {}))
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "error msg" in str(e)

    def test_call_tool_async_raises_on_tool_error_no_content(self):
        manager = MCPToolManager([])

        async def fake_call_tool(name, arguments=None):
            return SimpleNamespace(
                content=[],
                isError=True,
                is_error=True,
            )

        mock_session = SimpleNamespace(call_tool=fake_call_tool)
        manager._server_runtimes["docs"] = SimpleNamespace(
            session=mock_session,
            call_lock=asyncio.Lock(),
        )
        descriptor = _make_descriptor(server_name="docs", remote_name="fail")
        try:
            asyncio.run(manager._call_tool_async(descriptor, {}))
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "未返回文本内容" in str(e)

    # ── _disconnect_server ───────────────────────────────────────

    def test_disconnect_server_removes_runtime_and_descriptors(self):
        manager = MCPToolManager([])
        manager._server_runtimes["docs"] = SimpleNamespace(
            tool_names=["mcp_docs__search"],
            exit_stack=FakeExitStack(),
        )
        manager._tool_descriptors["mcp_docs__search"] = _make_descriptor()
        asyncio.run(manager._disconnect_server("docs"))
        assert "docs" not in manager._server_runtimes
        assert "mcp_docs__search" not in manager._tool_descriptors

    def test_disconnect_server_noop_for_unknown(self):
        manager = MCPToolManager([])
        asyncio.run(manager._disconnect_server("ghost"))
        assert True  # no exception

    # ── _close_all ───────────────────────────────────────────────

    def test_close_all_clears_everything(self):
        manager = MCPToolManager([])
        manager._server_runtimes["a"] = SimpleNamespace(exit_stack=FakeExitStack())
        manager._server_runtimes["b"] = SimpleNamespace(exit_stack=FakeExitStack())
        manager._tool_descriptors["t1"] = _make_descriptor()
        asyncio.run(manager._close_all())
        assert manager._server_runtimes == {}
        assert manager._tool_descriptors == {}

    # ── start / close lifecycle ──────────────────────────────────

    def test_start_with_no_servers(self):
        manager = MCPToolManager([])
        manager.start()
        assert manager._started is True
        assert manager._loop is None  # no loop needed
        manager.close()
        assert manager._started is False

    def test_start_raises_when_connection_fails(self, monkeypatch):
        manager = MCPToolManager(
            [MCPServerConfig(name="x", transport="stdio", command="python")],
            startup_timeout=1,
        )

        async def fail_connect():
            raise ConnectionError("failed")

        monkeypatch.setattr(manager, "_connect_all", fail_connect)
        try:
            manager.start()
            assert False, "Expected ConnectionError"
        except ConnectionError:
            pass
        assert manager._started is False

    def test_close_without_start_is_noop(self):
        manager = MCPToolManager([])
        manager.close()
        assert manager._started is False

    # ── _ensure_sdk_available ────────────────────────────────────

    def test_ensure_sdk_available_raises_when_mcp_missing(self, monkeypatch):
        def mock_import(name, *a, **kw):
            if name == "mcp":
                raise ImportError()
            return __import__(name, *a, **kw)

        monkeypatch.setattr("builtins.__import__", mock_import)
        try:
            MCPToolManager._ensure_sdk_available()
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "MCP SDK" in str(e)

    # ── _connect_all ─────────────────────────────────────────────

    def test_connect_all_records_failures(self, monkeypatch):
        manager = MCPToolManager([
            MCPServerConfig(name="a", transport="stdio", command="python"),
            MCPServerConfig(name="b", transport="stdio", command="python"),
        ])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        async def fake_connect(server):
            if server.name == "b":
                raise ValueError("bad server")

        monkeypatch.setattr(manager, "_connect_server", fake_connect)
        asyncio.run(manager._connect_all())
        assert "b" in manager._failures
        assert "a" not in manager._failures

    # ── _submit ──────────────────────────────────────────────────

    def test_submit_raises_when_no_loop(self):
        manager = MCPToolManager([])

        async def dummy():
            return 42

        try:
            manager._submit(dummy())
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "event loop" in str(e)


# ── _build_http_client_kwargs ───────────────────────────────────


class TestBuildHttpClientKwargs:
    def test_factory_expects_url_param(self):
        config = MCPServerConfig(name="x", transport="streamable_http", url="https://mcp.example.com")

        async def factory(url, http_client):
            pass

        http_client = httpx.AsyncClient()
        kwargs = MCPToolManager._build_http_client_kwargs(factory, config, http_client)
        assert kwargs["url"] == "https://mcp.example.com"
        assert kwargs["http_client"] is http_client

    def test_factory_expects_server_url_param(self):
        config = MCPServerConfig(name="x", transport="streamable_http", url="https://mcp.example.com")

        async def factory(server_url, http_client):
            pass

        http_client = httpx.AsyncClient()
        kwargs = MCPToolManager._build_http_client_kwargs(factory, config, http_client)
        assert kwargs["server_url"] == "https://mcp.example.com"
        assert kwargs["http_client"] is http_client

    def test_factory_url_priority_over_server_url(self):
        """If factory has both url and server_url, url is used."""
        config = MCPServerConfig(name="x", transport="streamable_http", url="https://mcp.example.com")

        async def factory(url, server_url, http_client):
            pass

        http_client = httpx.AsyncClient()
        kwargs = MCPToolManager._build_http_client_kwargs(factory, config, http_client)
        assert "url" in kwargs
        assert kwargs["url"] == "https://mcp.example.com"

    def test_factory_no_http_client_param(self):
        """If factory doesn't accept http_client, don't pass it."""
        config = MCPServerConfig(name="x", transport="streamable_http", url="https://mcp.example.com")

        def factory(url):
            pass

        http_client = httpx.AsyncClient()
        kwargs = MCPToolManager._build_http_client_kwargs(factory, config, http_client)
        assert kwargs == {"url": "https://mcp.example.com"}

    def test_real_streamable_http_client_signature(self):
        """Verify the real SDK's streamable_http_client has expected params."""
        try:
            from mcp.client.streamable_http import streamable_http_client
        except ImportError:
            pytest.skip("MCP SDK not installed")

        sig = inspect.signature(streamable_http_client)
        params = list(sig.parameters.keys())
        # At least one of url/server_url must be present
        assert "url" in params or "server_url" in params
        assert "http_client" in params


# ── HTTP 传输连接测试 ──────────────────────────────────────────


class TestHttpConnectServer:
    def test_http_connect_success(self, monkeypatch):
        manager = MCPToolManager(
            [MCPServerConfig(name="api", transport="streamable_http", url="https://api.example.com/mcp")]
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)
        monkeypatch.setattr(manager, "_build_descriptor", lambda c, t: None)

        read_stream = SimpleNamespace(read=MagicMock())
        write_stream = SimpleNamespace(write=MagicMock())
        streamable_result = (read_stream, write_stream)

        fake_session = MagicMock()
        fake_session.initialize = AsyncMock()
        fake_session.list_tools = AsyncMock(return_value=SimpleNamespace(tools=[]))
        fake_session.__aenter__.return_value = fake_session

        with (
            patch("mcp.ClientSession", return_value=fake_session),
            patch("mcp.client.streamable_http.streamable_http_client") as mock_http,
        ):
            mock_http.return_value.__aenter__.return_value = streamable_result
            asyncio.run(manager._connect_server(manager._servers[0]))

        assert "api" in manager._server_runtimes
        runtime = manager._server_runtimes["api"]
        assert runtime.session is fake_session
        assert runtime.config.name == "api"
        assert runtime.call_lock is not None

    def test_http_connect_handles_single_element_result(self, monkeypatch):
        manager = MCPToolManager(
            [MCPServerConfig(name="api", transport="streamable_http", url="https://api.example.com/mcp")]
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)
        monkeypatch.setattr(manager, "_build_descriptor", lambda c, t: None)

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.streamable_http.streamable_http_client") as mock_http,
        ):
            fake_session = MagicMock()
            fake_session.initialize = AsyncMock()
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session
            mock_http.return_value.__aenter__.return_value = (SimpleNamespace(),)

            with pytest.raises(RuntimeError, match="Unexpected MCP HTTP transport result"):
                asyncio.run(manager._connect_server(manager._servers[0]))

    def test_http_connect_propagates_timeout(self, monkeypatch):
        manager = MCPToolManager(
            [MCPServerConfig(name="api", transport="streamable_http", url="https://api.example.com/mcp")],
            startup_timeout=0.01,
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        async def slow_initialize():
            await asyncio.sleep(100)

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.streamable_http.streamable_http_client") as mock_http,
        ):
            fake_session = MagicMock()
            fake_session.initialize = slow_initialize
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session
            mock_http.return_value.__aenter__.return_value = (
                SimpleNamespace(), SimpleNamespace(),
            )

            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                asyncio.run(manager._connect_server(manager._servers[0]))


# ── retry_failed 完整流程 ────────────────────────────────────


class TestRetryFailedFullFlow:
    def test_retry_failed_sync_submit(self, monkeypatch):
        """retry_failed() calls _submit with _retry_failed_async."""
        manager = MCPToolManager([MCPServerConfig(name="x", transport="stdio", command="python")])
        manager._started = True
        manager._loop = SimpleNamespace()
        submitted = []

        def fake_submit(coro, timeout):
            submitted.append(coro)
            return {"retried": ["x"], "reconnected": ["x"], "skipped": [], "failed": {}}

        monkeypatch.setattr(manager, "_submit", fake_submit)
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        result = manager.retry_failed()
        assert result["retried"] == ["x"]

    def test_retry_failed_re_registers_descriptors(self, monkeypatch):
        """After reconnect, new tool descriptors should be populated."""
        manager = MCPToolManager([MCPServerConfig(name="api", transport="stdio", command="python")])
        manager._started = True
        manager._loop = SimpleNamespace()
        manager._loop.call_soon_threadsafe = MagicMock()
        manager._loop.stop = MagicMock()

        async def fake_retry(server_name=None):
            manager._server_runtimes["api"] = SimpleNamespace(tool_names=["mcp_api__echo"])
            manager._tool_descriptors["mcp_api__echo"] = _make_descriptor(
                local_name="mcp_api__echo",
                remote_name="echo",
                server_name="api",
            )
            return {
                "retried": ["api"],
                "reconnected": ["api"],
                "skipped": [],
                "failed": {},
                "snapshot": manager.status_snapshot(),
            }

        monkeypatch.setattr(manager, "_retry_failed_async", fake_retry)
        # _submit must be patched to avoid real event loop
        monkeypatch.setattr(manager, "_submit", lambda coro, timeout: asyncio.run(coro))
        result = manager.retry_failed(server_name="api")
        assert "mcp_api__echo" in manager._tool_descriptors
        assert result["reconnected"] == ["api"]

    def test_retry_skips_connected_healthy(self, monkeypatch):
        """A healthy connected server should be skipped, not retried."""
        manager = MCPToolManager([MCPServerConfig(name="ok", transport="stdio", command="python")])
        manager._started = True
        manager._server_runtimes["ok"] = SimpleNamespace(
            tool_names=["mcp_ok__tool"],
            exit_stack=FakeExitStack(),
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        result = asyncio.run(manager._retry_failed_async())
        assert result["retried"] == []
        assert result["skipped"] == ["ok"]

    def test_retry_partial_failure(self, monkeypatch):
        """When one server reconnects and another still fails, both outcomes reported."""
        manager = MCPToolManager([
            MCPServerConfig(name="good", transport="stdio", command="python"),
            MCPServerConfig(name="bad", transport="stdio", command="python"),
        ])
        manager._started = True
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        connected = []

        async def fake_connect(server):
            if server.name == "bad":
                raise ConnectionError("still broken")
            connected.append(server.name)
            local_name = f"mcp_{server.name}__tool"
            manager._server_runtimes[server.name] = SimpleNamespace(
                tool_names=[local_name],
                exit_stack=FakeExitStack(),
            )
            manager._tool_descriptors[local_name] = _make_descriptor(
                local_name=local_name, server_name=server.name,
            )

        monkeypatch.setattr(manager, "_connect_server", fake_connect)

        # Mark both as failed initially
        manager._failures["good"] = "was down"
        manager._failures["bad"] = "was down"

        result = asyncio.run(manager._retry_failed_async())
        assert result["reconnected"] == ["good"]
        assert "bad" in result["failed"]
        assert manager._failures.get("bad") == "still broken"
        assert "good" not in manager._failures
        assert "mcp_good__tool" in manager._tool_descriptors

    def test_retry_with_server_name_filter(self, monkeypatch):
        """Only the named server should be retried."""
        manager = MCPToolManager([
            MCPServerConfig(name="a", transport="stdio", command="python"),
            MCPServerConfig(name="b", transport="stdio", command="python"),
        ])
        manager._started = True
        manager._failures["a"] = "err"
        manager._failures["b"] = "err"
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        async def fake_connect(server):
            manager._server_runtimes[server.name] = SimpleNamespace(
                tool_names=[f"mcp_{server.name}__tool"],
                exit_stack=FakeExitStack(),
            )

        monkeypatch.setattr(manager, "_connect_server", fake_connect)

        result = asyncio.run(manager._retry_failed_async(server_name="a"))
        assert result["reconnected"] == ["a"]
        assert "b" not in result["reconnected"]  # 'b' was not iterated at all


# ── 完整资源清理测试 ──────────────────────────────────────────


class TestResourceCleanup:
    @staticmethod
    def _make_loop_for_close():
        loop = SimpleNamespace()
        loop.call_soon_threadsafe = MagicMock()
        loop.stop = MagicMock()
        return loop

    def test_close_multiple_calls_idempotent(self, monkeypatch):
        """Calling close() multiple times must not crash."""
        manager = MCPToolManager([])
        manager._started = True
        manager._loop = self._make_loop_for_close()

        thread = threading.Thread(target=lambda: None)
        thread.start()
        manager._thread = thread

        def fake_submit(coro, timeout):
            """Run the coroutine synchronously since no real loop exists."""
            return asyncio.run(coro)

        monkeypatch.setattr(manager, "_submit", fake_submit)
        monkeypatch.setattr(threading.Thread, "join", lambda self, timeout: None)

        manager.close()
        assert manager._loop is None
        assert manager._thread is None
        assert manager._started is False

        manager.close()
        assert manager._started is False

    def test_close_cleans_up_runtimes(self, monkeypatch):
        """close() must disconnect all runtimes and clear descriptors."""
        manager = MCPToolManager([])
        manager._started = True
        manager._loop = self._make_loop_for_close()
        thread = threading.Thread(target=lambda: None)
        thread.start()
        manager._thread = thread
        manager._server_runtimes["s1"] = SimpleNamespace(exit_stack=FakeExitStack())
        manager._server_runtimes["s2"] = SimpleNamespace(exit_stack=FakeExitStack())
        manager._tool_descriptors["t1"] = _make_descriptor()

        calls = []

        async def fake_close_all():
            calls.append("close_all")
            manager._server_runtimes.clear()
            manager._tool_descriptors.clear()

        def fake_submit(coro, timeout):
            return asyncio.run(coro)

        monkeypatch.setattr(manager, "_close_all", fake_close_all)
        monkeypatch.setattr(manager, "_submit", fake_submit)
        monkeypatch.setattr(threading.Thread, "join", lambda self, timeout: None)

        manager.close()
        assert calls == ["close_all"]
        assert manager._server_runtimes == {}
        assert manager._tool_descriptors == {}

    def test_close_swallows_errors(self, monkeypatch):
        """Errors during close must not propagate."""
        manager = MCPToolManager([])
        manager._started = True
        manager._loop = self._make_loop_for_close()
        thread = threading.Thread(target=lambda: None)
        thread.start()
        manager._thread = thread

        async def failing_close():
            raise RuntimeError("cleanup error")

        def fake_submit(coro, timeout):
            return asyncio.run(coro)

        monkeypatch.setattr(manager, "_close_all", failing_close)
        monkeypatch.setattr(manager, "_submit", fake_submit)
        monkeypatch.setattr(threading.Thread, "join", lambda self, timeout: None)

        manager.close()  # must not raise
        assert manager._started is False

    def test_asyncexitstack_aclose_called_on_disconnect(self):
        """Disconnecting must call aclose on the exit stack."""
        manager = MCPToolManager([])
        closed = False

        async def fake_aclose():
            nonlocal closed
            closed = True

        manager._server_runtimes["api"] = SimpleNamespace(
            tool_names=["mcp_api__tool"],
            exit_stack=SimpleNamespace(aclose=fake_aclose),
        )
        manager._tool_descriptors["mcp_api__tool"] = _make_descriptor()

        asyncio.run(manager._disconnect_server("api"))
        assert closed is True


# ── 超时测试 ──────────────────────────────────────────────────


class TestTimeoutBehavior:
    def test_call_tool_async_timeout_propagates(self):
        """_call_tool_async must raise TimeoutError when session.call_tool exceeds timeout."""
        manager = MCPToolManager([])

        async def slow_call_tool(name, arguments=None):
            await asyncio.sleep(100)

        mock_session = SimpleNamespace(call_tool=slow_call_tool)
        manager._server_runtimes["slow"] = SimpleNamespace(
            session=mock_session,
            call_lock=asyncio.Lock(),
        )
        descriptor = _make_descriptor(server_name="slow", remote_name="slow_tool", timeout_sec=0.01)

        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            asyncio.run(manager._call_tool_async(descriptor, {}))

    def test_connect_server_timeout_propagates(self, monkeypatch):
        """_connect_server must raise TimeoutError when initialize exceeds startup_timeout."""
        from contextlib import asynccontextmanager

        manager = MCPToolManager(
            [MCPServerConfig(name="slow", transport="stdio", command="python")],
            startup_timeout=0.01,
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        async def slow_init():
            await asyncio.sleep(100)

        @asynccontextmanager
        async def fake_stdio_client(params):
            read_stream = SimpleNamespace(read=MagicMock())
            write_stream = SimpleNamespace(write=MagicMock())
            yield read_stream, write_stream

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            fake_session = MagicMock()
            fake_session.initialize = slow_init
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session

            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                asyncio.run(manager._connect_server(manager._servers[0]))

    def test_call_tool_submit_inherits_timeout(self, monkeypatch):
        """call_tool must pass descriptor.timeout_sec + 5 to _submit."""
        manager = MCPToolManager([])
        manager._tool_descriptors["tool"] = _make_descriptor(timeout_sec=10)
        manager._started = True
        manager._loop = SimpleNamespace()
        captured = []

        def fake_submit(coro, timeout):
            captured.append(timeout)
            return "ok"

        monkeypatch.setattr(manager, "_submit", fake_submit)
        result = manager.call_tool("tool", {})
        assert result == "ok"
        assert captured[0] == 15  # timeout_sec + 5


# ── _content_block_to_text 边缘情况 ──────────────────────────


class TestContentBlockToTextEdgeCases:
    def test_mixed_content_types(self):
        """Multiple content blocks with different types."""
        blocks = [
            SimpleNamespace(text="text block"),
            SimpleNamespace(
                text=None, resource=SimpleNamespace(text="resource text")
            ),
            SimpleNamespace(
                text=None, resource=SimpleNamespace(text=None, blob=b"img", mimeType="image/png")
            ),
        ]
        from agentnexus.tools.mcp_adapter import _normalize_tool_result

        result = SimpleNamespace(
            content=blocks,
            structuredContent=None,
            isError=False,
        )
        text = _normalize_tool_result(result)
        assert "text block" in text
        assert "resource text" in text
        assert "[embedded resource: image/png]" in text

    def test_structured_content_with_text_mixed(self):
        """Both structuredContent and content blocks should appear."""
        from agentnexus.tools.mcp_adapter import _normalize_tool_result

        result = SimpleNamespace(
            structuredContent={"summary": "structured data"},
            content=[SimpleNamespace(text="text block")],
            isError=False,
        )
        text = _normalize_tool_result(result)
        assert "summary" in text
        assert "text block" in text

    def test_resource_blob_unknown_mime(self):
        """Blob with no mime should use 'unknown'."""
        block = SimpleNamespace(
            text=None,
            resource=SimpleNamespace(text=None, blob=b"\x00\x01", mimeType=None, mime_type=None),
        )
        result = _content_block_to_text(block)
        assert result == "[embedded resource: unknown]"

    def test_resource_no_text_no_blob_no_uri(self):
        """Resource with none of text/blob/uri should fall through."""
        block = SimpleNamespace(
            text=None,
            resource=SimpleNamespace(text=None, blob=None, uri=None, mimeType=None, mime_type=None),
        )
        result = _content_block_to_text(block)
        assert result == str(block)

    def test_binary_data_without_mime_falls_through(self):
        """data without mimeType should not be classified as binary."""
        block = SimpleNamespace(text=None, resource=None, mimeType=None, mime_type=None, data=b"raw")
        result = _content_block_to_text(block)
        assert isinstance(result, str)
        # Falls through to str() fallback; binary markers not present
        assert "[binary content:" not in result
        assert "[embedded resource:" not in result

    def test_model_dump_exception_falls_to_str(self):
        """If model_dump raises, fall back to str()."""
        block = MagicMock()
        block.text = None
        block.resource = None
        block.mimeType = None
        block.mime_type = None
        block.data = None
        block.model_dump.side_effect = ValueError("oops")
        result = _content_block_to_text(block)
        assert isinstance(result, str)

    def test_complex_mcp_result_with_binary_content(self):
        """MCP result containing binary content blocks."""
        from agentnexus.tools.mcp_adapter import _normalize_tool_result

        result = SimpleNamespace(
            content=[
                SimpleNamespace(text="stdout line 1"),
                SimpleNamespace(
                    text=None, resource=None,
                    mimeType="application/octet-stream", data=b"\xff\xfe",
                ),
            ],
            structuredContent=None,
            isError=False,
        )
        text = _normalize_tool_result(result)
        assert "stdout line 1" in text
        assert "[binary content: application/octet-stream]" in text


class TestCreateMcpManagerFromSettings:
    def test_returns_none_when_mcp_disabled(self):
        settings = SimpleNamespace(mcp_enabled=False)
        assert create_mcp_manager_from_settings(settings) is None

    def test_returns_none_when_no_enabled_servers(self):
        settings = SimpleNamespace(mcp_enabled=True, mcp_startup_timeout=15, mcp_servers=[])
        assert create_mcp_manager_from_settings(settings) is None

    def test_returns_manager_when_all_servers_disabled(self, monkeypatch):
        started = False

        def fake_start(self):
            nonlocal started
            started = True

        monkeypatch.setattr(MCPToolManager, "start", fake_start)
        settings = SimpleNamespace(
            mcp_enabled=True,
            mcp_startup_timeout=15,
            mcp_servers=[MCPServerConfig(name="x", transport="stdio", command="python", enabled=False)],
        )
        manager = create_mcp_manager_from_settings(settings)
        assert manager is not None
        assert started is True
        assert manager.server_names() == ["x"]

    def test_returns_manager_for_configured_servers(self, monkeypatch):
        started = False

        def fake_start(self):
            nonlocal started
            started = True

        monkeypatch.setattr(MCPToolManager, "start", fake_start)
        settings = SimpleNamespace(
            mcp_enabled=True,
            mcp_startup_timeout=15,
            mcp_servers=[MCPServerConfig(name="x", transport="stdio", command="python")],
        )
        manager = create_mcp_manager_from_settings(settings)
        assert manager is not None
        assert started is True
        assert manager.server_names() == ["x"]

    def test_capability_config_controls_initial_enabled_servers(self, monkeypatch, temp_agentnexus_home):
        captured = {}

        def fake_start(self):
            captured["servers"] = [server.name for server in self._servers]

        monkeypatch.setattr(MCPToolManager, "start", fake_start)
        (temp_agentnexus_home / "config.yaml").write_text(
            "capabilities:\n  mcp_servers:\n    x: true\n",
            encoding="utf-8",
        )
        settings = SimpleNamespace(
            mcp_enabled=True,
            mcp_startup_timeout=15,
            mcp_servers=[MCPServerConfig(name="x", transport="stdio", command="python")],
        )

        manager = create_mcp_manager_from_settings(settings)

        assert manager is not None
        assert captured["servers"] == ["x"]


# ── _run_loop 生命周期测试 ──────────────────────────────────


class TestEventLoopLifecycle:
    def teardown_method(self):
        # 如果测试失败了导致 loop 还在运行，确保清理
        if hasattr(self, "_loop") and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

    def test_run_loop_executes_submitted_task(self):
        """_run_loop must execute a coroutine submitted via run_coroutine_threadsafe."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        async def simple_task():
            return 42

        future = asyncio.run_coroutine_threadsafe(simple_task(), manager._loop)
        result = future.result(timeout=5)
        assert result == 42

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)
        assert manager._loop.is_closed()

    def test_run_loop_cancels_pending_tasks_on_stop(self):
        """When loop stops, pending tasks must be cancelled."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        cancelled_flag = []
        started = []

        async def slow_task():
            started.append(True)
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled_flag.append(True)
                raise

        future = asyncio.run_coroutine_threadsafe(slow_task(), manager._loop)
        # 等待任务开始执行后再停止 loop
        import time
        for _ in range(50):
            if started:
                break
            time.sleep(0.01)

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)

        assert len(cancelled_flag) == 1
        assert future.cancelled()

    def test_run_loop_clears_event_loop_setting(self):
        """After _run_loop exits, the event loop should be closed."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)
        assert manager._loop.is_closed()

    def test_run_loop_multiple_tasks(self):
        """Multiple tasks submitted to the loop should all complete."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        results = []

        async def task_a():
            results.append("a")

        async def task_b():
            results.append("b")

        f1 = asyncio.run_coroutine_threadsafe(task_a(), manager._loop)
        f2 = asyncio.run_coroutine_threadsafe(task_b(), manager._loop)
        f1.result(timeout=5)
        f2.result(timeout=5)

        assert "a" in results
        assert "b" in results

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)


# ── _submit 真实 Event Loop 测试 ────────────────────────────


class TestSubmitRealLoop:
    def teardown_method(self):
        if hasattr(self, "_loop") and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

    def test_submit_with_real_loop_returns_result(self):
        """_submit must return the coroutine result via the real event loop."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        async def add(a, b):
            return a + b

        result = manager._submit(add(1, 2), timeout=5)
        assert result == 3

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)

    def test_submit_timeout_raises(self):
        """_submit must raise TimeoutError when coroutine exceeds timeout."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        async def slow():
            await asyncio.sleep(100)

        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            manager._submit(slow(), timeout=0.01)

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)

    def test_submit_cancelled_error(self):
        """_submit must raise CancelledError when the future is cancelled."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        async def wait_forever():
            await asyncio.sleep(100)

        future = asyncio.run_coroutine_threadsafe(wait_forever(), manager._loop)
        future.cancel()

        with pytest.raises((asyncio.CancelledError, Exception)):
            future.result(timeout=5)

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)


# ── start() 成功路径测试 ─────────────────────────────────────


class TestStartSuccess:
    def teardown_method(self):
        if hasattr(self, "_manager") and self._manager is not None:
            try:
                self._manager.close()
            except Exception:
                pass

    def test_start_with_servers_sets_up_loop_and_thread(self, monkeypatch):
        """start() must create an event loop and thread, then mark started."""
        manager = MCPToolManager(
            [MCPServerConfig(name="x", transport="stdio", command="python")],
        )
        self._manager = manager

        async def fake_connect_all():
            pass

        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)
        monkeypatch.setattr(manager, "_connect_all", fake_connect_all)

        manager.start()
        assert manager._started is True
        assert manager._loop is not None
        assert manager._thread is not None
        assert manager._thread.is_alive()

    def test_start_calls_connect_all(self, monkeypatch):
        """start() must invoke _connect_all via _submit."""
        manager = MCPToolManager(
            [MCPServerConfig(name="x", transport="stdio", command="python")],
        )
        self._manager = manager
        connect_called = []

        async def track_connect_all():
            connect_called.append(True)

        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)
        monkeypatch.setattr(manager, "_connect_all", track_connect_all)

        manager.start()
        assert len(connect_called) == 1

    def test_start_nested_cleanup_on_failure(self, monkeypatch):
        """start() must reset loop and thread when _connect_all raises."""
        manager = MCPToolManager(
            [MCPServerConfig(name="x", transport="stdio", command="python")],
        )
        self._manager = manager

        async def raise_error():
            raise RuntimeError("connect failed")

        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)
        monkeypatch.setattr(manager, "_connect_all", raise_error)

        with pytest.raises(RuntimeError, match="connect failed"):
            manager.start()
        assert manager._started is False
        # loop and thread must be cleaned up
        assert manager._loop is None
        assert manager._thread is None


# ── start() 幂等性测试 ────────────────────────────────────────


class TestStartIdempotency:
    def test_start_when_already_started_returns_immediately(self, monkeypatch):
        """start() must return early when _started is already True."""
        manager = MCPToolManager(
            [MCPServerConfig(name="x", transport="stdio", command="python")],
        )
        manager._started = True
        called = []

        def fake_connect_all():
            called.append(True)

        monkeypatch.setattr(manager, "_connect_all", fake_connect_all)

        manager.start()
        assert manager._loop is None  # no loop created
        assert called == []  # _connect_all not called

    def test_start_no_servers_sets_started_without_loop(self):
        """start() with no servers must set started and not create loop."""
        manager = MCPToolManager([])
        manager.start()
        assert manager._started is True
        assert manager._loop is None
        assert manager._thread is None


# ── _connect_all 完整测试 ─────────────────────────────────────


class TestConnectAllSuite:
    def test_connect_all_all_succeed(self, monkeypatch):
        """_connect_all must record no failures when all servers connect."""
        manager = MCPToolManager([
            MCPServerConfig(name="a", transport="stdio", command="python"),
            MCPServerConfig(name="b", transport="stdio", command="python"),
        ])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)
        connected = []

        async def fake_connect(server):
            connected.append(server.name)

        monkeypatch.setattr(manager, "_connect_server", fake_connect)

        asyncio.run(manager._connect_all())
        assert manager._failures == {}
        assert connected == ["a", "b"]

    def test_connect_all_sdk_unavailable_raises(self, monkeypatch):
        """_ensure_sdk_available is called before the loop, so failure must propagate."""
        manager = MCPToolManager([
            MCPServerConfig(name="a", transport="stdio", command="python"),
        ])

        def fail_sdk():
            raise RuntimeError("MCP SDK not available")

        monkeypatch.setattr(manager, "_ensure_sdk_available", fail_sdk)

        with pytest.raises(RuntimeError, match="MCP SDK not available"):
            asyncio.run(manager._connect_all())

    def test_connect_all_mixed(self, monkeypatch):
        """Partial failures: some servers connect, some fail."""
        manager = MCPToolManager([
            MCPServerConfig(name="good", transport="stdio", command="python"),
            MCPServerConfig(name="bad", transport="stdio", command="python"),
        ])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        async def fake_connect(server):
            if server.name == "bad":
                raise ConnectionError("refused")

        monkeypatch.setattr(manager, "_connect_server", fake_connect)

        asyncio.run(manager._connect_all())
        assert "good" not in manager._failures
        assert "bad" in manager._failures
        assert "refused" in manager._failures["bad"]


# ── stdio 传输 _connect_server 测试 ──────────────────────────


class TestStdioConnectServer:
    def test_stdio_connect_success(self, monkeypatch):
        """_connect_server must successfully connect via stdio and register tools."""
        manager = MCPToolManager([MCPServerConfig(name="demo", transport="stdio", command="python")])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        mock_tool = SimpleNamespace(
            name="echo",
            description="Echo tool",
            inputSchema={"type": "object", "properties": {"msg": {"type": "string"}}},
        )
        tools_result = SimpleNamespace(tools=[mock_tool])
        fake_session = MagicMock()
        fake_session.initialize = AsyncMock()
        fake_session.list_tools = AsyncMock(return_value=tools_result)
        fake_session.__aenter__.return_value = fake_session

        @asynccontextmanager
        async def fake_stdio_client(params):
            yield SimpleNamespace(read=MagicMock()), SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession", return_value=fake_session),
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            asyncio.run(manager._connect_server(manager._servers[0]))

        assert "demo" in manager._server_runtimes
        runtime = manager._server_runtimes["demo"]
        assert runtime.session is fake_session
        assert runtime.call_lock is not None
        assert "mcp_demo__echo" in manager._tool_descriptors
        assert runtime.tool_names == ["mcp_demo__echo"]

    def test_stdio_connect_skips_tool_without_name(self, monkeypatch):
        """Tools without a name should be skipped."""
        manager = MCPToolManager([MCPServerConfig(name="demo", transport="stdio", command="python")])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        mock_tool = SimpleNamespace(name=None, description="no name", inputSchema=None)
        tools_result = SimpleNamespace(tools=[mock_tool])
        fake_session = MagicMock()
        fake_session.initialize = AsyncMock()
        fake_session.list_tools = AsyncMock(return_value=tools_result)
        fake_session.__aenter__.return_value = fake_session

        @asynccontextmanager
        async def fake_stdio_client(params):
            yield SimpleNamespace(read=MagicMock()), SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession", return_value=fake_session),
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            asyncio.run(manager._connect_server(manager._servers[0]))

        assert "demo" in manager._server_runtimes
        assert manager._tool_descriptors == {}

    def test_stdio_connect_error_cleans_up_stack(self, monkeypatch):
        """When connect fails, the exit stack must be cleaned up."""
        manager = MCPToolManager([MCPServerConfig(name="demo", transport="stdio", command="python")])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        @asynccontextmanager
        async def fake_stdio_client(params):
            yield SimpleNamespace(read=MagicMock()), SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            fake_session = MagicMock()
            fake_session.__aenter__.return_value = fake_session
            fake_session.initialize.side_effect = RuntimeError("init failed")
            mock_session_cls.return_value = fake_session

            with pytest.raises(RuntimeError, match="init failed"):
                asyncio.run(manager._connect_server(manager._servers[0]))

        assert "demo" not in manager._server_runtimes
        assert manager._tool_descriptors == {}

    def test_stdio_connect_includes_only_config_respected(self, monkeypatch):
        """include_tools config must filter which tools are registered."""
        config = MCPServerConfig(name="demo", transport="stdio", command="python", include_tools=["search"])
        manager = MCPToolManager([config])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        tools = [
            SimpleNamespace(name="search", description="Search", inputSchema=None),
            SimpleNamespace(name="delete", description="Delete", inputSchema=None),
        ]
        tools_result = SimpleNamespace(tools=tools)
        fake_session = MagicMock()
        fake_session.initialize = AsyncMock()
        fake_session.list_tools = AsyncMock(return_value=tools_result)
        fake_session.__aenter__.return_value = fake_session

        @asynccontextmanager
        async def fake_stdio_client(params):
            yield SimpleNamespace(read=MagicMock()), SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession", return_value=fake_session),
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            asyncio.run(manager._connect_server(manager._servers[0]))

        assert "mcp_demo__search" in manager._tool_descriptors
        assert "mcp_demo__delete" not in manager._tool_descriptors


# ── create_mcp_manager_from_settings start 异常传播 ──────────


class TestCreateMcpManagerFromSettingsExtended:
    def test_start_failure_propagates(self, monkeypatch):
        """If manager.start() raises, create_mcp_manager_from_settings must propagate."""
        original_start = MCPToolManager.start

        def failing_start(self):
            raise RuntimeError("start failed")

        monkeypatch.setattr(MCPToolManager, "start", failing_start)
        settings = SimpleNamespace(
            mcp_enabled=True,
            mcp_startup_timeout=15,
            mcp_servers=[MCPServerConfig(name="x", transport="stdio", command="python")],
        )
        with pytest.raises(RuntimeError, match="start failed"):
            create_mcp_manager_from_settings(settings)

        monkeypatch.setattr(MCPToolManager, "start", original_start)


# ── _connect_server: list_tools() 失败路径 ───────────────────────


class TestConnectServerFailurePaths:
    def test_list_tools_failure_cleans_up_http(self, monkeypatch):
        """HTTP: if session.list_tools() raises, exit stack must be cleaned up and error propagated."""
        manager = MCPToolManager(
            [MCPServerConfig(name="api", transport="streamable_http", url="https://api.example.com/mcp")]
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        read_stream = SimpleNamespace(read=MagicMock())
        write_stream = SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.streamable_http.streamable_http_client") as mock_http,
        ):
            fake_session = MagicMock()
            fake_session.initialize = AsyncMock()
            fake_session.list_tools = AsyncMock(side_effect=RuntimeError("list_tools failed"))
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session
            mock_http.return_value.__aenter__.return_value = (read_stream, write_stream)

            with pytest.raises(RuntimeError, match="list_tools failed"):
                asyncio.run(manager._connect_server(manager._servers[0]))

        assert "api" not in manager._server_runtimes
        assert manager._tool_descriptors == {}

    def test_list_tools_failure_cleans_up_stdio(self, monkeypatch):
        """Stdio: if session.list_tools() raises, exit stack must be cleaned up and error propagated."""
        manager = MCPToolManager([MCPServerConfig(name="demo", transport="stdio", command="python")])
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        @asynccontextmanager
        async def fake_stdio_client(params):
            yield SimpleNamespace(read=MagicMock()), SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            fake_session = MagicMock()
            fake_session.initialize = AsyncMock()
            fake_session.list_tools = AsyncMock(side_effect=ValueError("tools broken"))
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session

            with pytest.raises(ValueError, match="tools broken"):
                asyncio.run(manager._connect_server(manager._servers[0]))

        assert "demo" not in manager._server_runtimes
        assert manager._tool_descriptors == {}

    def test_list_tools_timeout_cleans_up(self, monkeypatch):
        """If session.list_tools() times out, stack must be cleaned up."""
        manager = MCPToolManager(
            [MCPServerConfig(name="slow", transport="stdio", command="python")],
            startup_timeout=0.01,
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        async def endless_list():
            await asyncio.sleep(100)

        @asynccontextmanager
        async def fake_stdio_client(params):
            yield SimpleNamespace(read=MagicMock()), SimpleNamespace(write=MagicMock())

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            fake_session = MagicMock()
            fake_session.initialize = AsyncMock()
            fake_session.list_tools = endless_list
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session

            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                asyncio.run(manager._connect_server(manager._servers[0]))

        assert "slow" not in manager._server_runtimes
        assert manager._tool_descriptors == {}


# ── HTTP 传输 initialize() 失败 ─────────────────────────────────


class TestHttpInitializeFailure:
    def test_http_initialize_failure_cleans_up_stack(self, monkeypatch):
        """HTTP: session.initialize() must clean up exit stack on failure."""
        manager = MCPToolManager(
            [MCPServerConfig(name="api", transport="streamable_http", url="https://api.example.com/mcp")]
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.streamable_http.streamable_http_client") as mock_http,
        ):
            fake_session = MagicMock()
            fake_session.initialize = AsyncMock(side_effect=RuntimeError("init failed"))
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session
            mock_http.return_value.__aenter__.return_value = (
                SimpleNamespace(), SimpleNamespace(),
            )

            with pytest.raises(RuntimeError, match="init failed"):
                asyncio.run(manager._connect_server(manager._servers[0]))

        assert "api" not in manager._server_runtimes
        assert manager._tool_descriptors == {}


# ── call_lock 并发测试 ──────────────────────────────────────────


class TestCallLockConcurrency:
    def test_call_lock_serializes_concurrent_calls(self):
        """Two concurrent tool calls on the same server must be serialized by call_lock."""
        manager = MCPToolManager([])
        call_order = []
        event = asyncio.Event()

        async def slow_call_tool(name, arguments=None):
            call_order.append("enter")
            await event.wait()
            await asyncio.sleep(0.01)
            call_order.append("exit")
            return SimpleNamespace(content=[SimpleNamespace(text="done")], isError=False, is_error=False)

        mock_session = SimpleNamespace(call_tool=slow_call_tool)
        manager._server_runtimes["docs"] = SimpleNamespace(
            session=mock_session,
            call_lock=asyncio.Lock(),
        )
        descriptor = _make_descriptor(server_name="docs", remote_name="search", timeout_sec=30)

        async def run():
            t1 = asyncio.create_task(manager._call_tool_async(descriptor, {}))
            t2 = asyncio.create_task(manager._call_tool_async(descriptor, {}))
            await asyncio.sleep(0.05)  # let t1 acquire lock
            event.set()
            await asyncio.gather(t1, t2)

        asyncio.run(run())
        # enter/exit pairs must be non-overlapping
        assert call_order == ["enter", "exit", "enter", "exit"]

    def test_call_lock_released_on_error(self):
        """If tool call raises, the lock must still be released so other calls can proceed."""
        manager = MCPToolManager([])
        call_count = []

        async def failing_call_tool(name, arguments=None):
            call_count.append("called")
            raise RuntimeError("tool error")

        mock_session = SimpleNamespace(call_tool=failing_call_tool)
        manager._server_runtimes["docs"] = SimpleNamespace(
            session=mock_session,
            call_lock=asyncio.Lock(),
        )
        descriptor = _make_descriptor(server_name="docs", remote_name="fail", timeout_sec=30)

        async def run():
            with pytest.raises(RuntimeError):
                await manager._call_tool_async(descriptor, {})
            # lock must be free for a second call
            with pytest.raises(RuntimeError):
                await manager._call_tool_async(descriptor, {})
            assert call_count == ["called", "called"]

        asyncio.run(run())


# ── _run_loop 异常处理 ──────────────────────────────────────────


class TestRunLoopExceptionHandling:
    def teardown_method(self):
        if hasattr(self, "_loop") and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

    def test_run_loop_recovers_from_task_exception(self):
        """Event loop must continue running after a task raises unhandled exception."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        self._loop.set_exception_handler(lambda loop, context: None)
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        async def failing_task():
            raise ValueError("oops")

        async def succeeding_task():
            return 42

        with pytest.raises(ValueError, match="oops"):
            manager._submit(failing_task(), timeout=5)

        second_result = manager._submit(succeeding_task(), timeout=5)
        assert second_result == 42

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)


# ── status_snapshot 混合状态验证 ────────────────────────────────


class TestStatusSnapshotValidation:
    def test_status_snapshot_mixed_servers(self):
        """status_snapshot must report connected, failed, and empty states correctly."""
        manager = MCPToolManager([
            MCPServerConfig(name="connected", transport="stdio", command="python"),
            MCPServerConfig(name="failed", transport="stdio", command="python"),
            MCPServerConfig(name="empty", transport="stdio", command="python"),
        ])
        manager._started = True
        manager._server_runtimes["connected"] = SimpleNamespace(
            tool_names=["mcp_connected__tool1", "mcp_connected__tool2"],
        )
        manager._tool_descriptors["mcp_connected__tool1"] = _make_descriptor(
            local_name="mcp_connected__tool1", server_name="connected",
        )
        manager._tool_descriptors["mcp_connected__tool2"] = _make_descriptor(
            local_name="mcp_connected__tool2", server_name="connected",
        )
        manager._failures["failed"] = "connection refused"

        snapshot = manager.status_snapshot()

        assert snapshot["started"] is True
        assert snapshot["server_count"] == 3
        assert snapshot["connected_count"] == 1
        assert snapshot["failure_count"] == 1
        assert snapshot["tool_count"] == 2

        servers_by_name = {s["name"]: s for s in snapshot["servers"]}
        assert servers_by_name["connected"]["connected"] is True
        assert servers_by_name["connected"]["tool_names"] == ["mcp_connected__tool1", "mcp_connected__tool2"]
        assert servers_by_name["connected"]["failure"] is None

        assert servers_by_name["failed"]["connected"] is False
        assert servers_by_name["failed"]["failure"] == "connection refused"

        assert servers_by_name["empty"]["connected"] is False
        assert servers_by_name["empty"]["tool_names"] == []
        assert servers_by_name["empty"]["failure"] is None

    def test_status_snapshot_no_servers(self):
        """status_snapshot must handle empty server list."""
        manager = MCPToolManager([])
        snapshot = manager.status_snapshot()
        assert snapshot["server_count"] == 0
        assert snapshot["connected_count"] == 0
        assert snapshot["failure_count"] == 0
        assert snapshot["tool_count"] == 0


class TestFullMcpCapabilities:
    def test_imports_resource_and_prompt_bridge_tools(self):
        config = MCPServerConfig(name="demo", transport="stdio", command="python")
        manager = MCPToolManager([config])

        session = SimpleNamespace(
            list_tools=AsyncMock(return_value=SimpleNamespace(tools=[])),
            list_resources=AsyncMock(return_value=SimpleNamespace(resources=[
                SimpleNamespace(name="Doc", uri="file:///doc.md", description="Docs", mimeType="text/markdown")
            ])),
            list_resource_templates=AsyncMock(return_value=SimpleNamespace(resourceTemplates=[
                SimpleNamespace(name="ById", uriTemplate="file:///{id}", description="By id")
            ])),
            list_prompts=AsyncMock(return_value=SimpleNamespace(prompts=[
                SimpleNamespace(name="review", description="Review prompt", arguments=[])
            ])),
            read_resource=AsyncMock(return_value=SimpleNamespace(contents=[SimpleNamespace(text="doc body")])),
            get_prompt=AsyncMock(return_value=SimpleNamespace(
                messages=[SimpleNamespace(role="user", content="review")]
            )),
        )
        runtime = SimpleNamespace(
            config=config,
            session=session,
            tool_names=[],
            resource_tool_names=[],
            prompt_tool_names=[],
            resource_descriptors=[],
            resource_templates=[],
            prompt_descriptors=[],
        )

        asyncio.run(manager._import_server_capabilities(runtime))

        assert "mcp_demo__list_resources" in manager._tool_descriptors
        assert "mcp_demo__read_resource" in manager._tool_descriptors
        assert "mcp_demo__list_resource_templates" in manager._tool_descriptors
        assert "mcp_demo__list_prompts" in manager._tool_descriptors
        assert "mcp_demo__get_prompt" in manager._tool_descriptors
        assert "file:///doc.md" in manager.auto_context()
        assert "review" in manager.auto_context()

    def test_read_resource_and_get_prompt_wrappers(self):
        config = MCPServerConfig(name="demo", transport="stdio", command="python")
        manager = MCPToolManager([config])
        session = SimpleNamespace(
            read_resource=AsyncMock(return_value=SimpleNamespace(contents=[SimpleNamespace(text="resource text")])),
            get_prompt=AsyncMock(return_value=SimpleNamespace(
                messages=[SimpleNamespace(role="user", content="prompt text")]
            )),
        )
        manager._server_runtimes["demo"] = SimpleNamespace(
            config=config,
            session=session,
            state=MCPServerState.HEALTHY,
            semaphore=asyncio.Semaphore(4),
        )
        read_desc = manager._internal_descriptor(
            config, "mcp_demo__read_resource", "read_resource", "read", {"uri": {"type": "string"}}, "resource", ["uri"]
        )
        prompt_desc = manager._internal_descriptor(
            config, "mcp_demo__get_prompt", "get_prompt", "prompt", {"name": {"type": "string"}}, "prompt", ["name"]
        )

        assert asyncio.run(manager._call_tool_async(read_desc, {"uri": "file:///doc.md"})) == "resource text"
        prompt_text = asyncio.run(manager._call_tool_async(prompt_desc, {"name": "review"}))
        assert "prompt text" in prompt_text

    def test_health_check_marks_degraded_and_schedules_reconnect(self):
        config = MCPServerConfig(
            name="demo",
            transport="stdio",
            command="python",
            health_check_interval_sec=1,
            reconnect_initial_delay_sec=1,
        )
        manager = MCPToolManager([config])
        session = SimpleNamespace(send_ping=AsyncMock(side_effect=ConnectionError("lost")))
        runtime = SimpleNamespace(
            config=config,
            session=session,
            state=MCPServerState.HEALTHY,
            last_ping_at=0,
            consecutive_failures=0,
            reconnect_attempts=0,
            next_reconnect_at=None,
            last_failure=None,
        )
        manager._server_runtimes["demo"] = runtime
        manager._server_states["demo"] = MCPServerState.HEALTHY

        asyncio.run(manager._health_check_once())

        assert manager._server_states["demo"] == MCPServerState.DEGRADED
        assert manager._failures["demo"] == "lost"
        assert runtime.reconnect_attempts == 1
        assert runtime.next_reconnect_at is not None
