"""Tests for MCPToolManager core methods."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_adapter import (
    MCPToolManager,
)
from agentnexus.tools.registry import ToolRegistry

from .conftest import FakeExitStack, _make_descriptor


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
        executor = ToolRegistry()
        registered = manager.register_tools(executor)
        assert registered == ["mcp_api__search"]
        assert executor.get_tool("mcp_api__search") is not None

    def test_register_tools_respects_include_filter(self):
        manager = MCPToolManager([])
        manager._tool_descriptors["tool_a"] = _make_descriptor(local_name="tool_a")
        manager._tool_descriptors["tool_b"] = _make_descriptor(local_name="tool_b")
        executor = ToolRegistry()
        registered = manager.register_tools(executor, include_tools={"tool_a"})
        assert registered == ["tool_a"]
        assert executor.get_tool("tool_a") is not None
        assert executor.get_tool("tool_b") is None

    def test_register_tools_empty_when_no_descriptors(self):
        manager = MCPToolManager([])
        executor = ToolRegistry()
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
            semaphore=asyncio.Semaphore(4),
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
            semaphore=asyncio.Semaphore(4),
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
            semaphore=asyncio.Semaphore(4),
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

        import inspect
        sig = inspect.signature(streamable_http_client)
        params = list(sig.parameters.keys())
        # At least one of url/server_url must be present
        assert "url" in params or "server_url" in params
        assert "http_client" in params
