"""Tests for MCP capability import and descriptor construction."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_capabilities import (
    build_prompt_tool_descriptors,
    build_resource_tool_descriptors,
    import_prompts,
    import_resources,
    import_server_capabilities,
)
from agentnexus.tools.mcp_schema import (
    MCPToolDescriptor,
)


def _make_config(**overrides):
    defaults = dict(
        name="test_server",
        transport="stdio",
        command="echo",
        import_tools=True,
        import_resources=True,
        import_prompts=True,
        tool_prefix="",
    )
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


def _make_runtime(config=None, **overrides):
    if config is None:
        config = _make_config()
    attrs = dict(
        config=config,
        session=AsyncMock(),
        exit_stack=SimpleNamespace(aclose=AsyncMock()),
        tool_names=[],
        resource_tool_names=[],
        prompt_tool_names=[],
        resource_descriptors=[],
        resource_templates=[],
        prompt_descriptors=[],
        call_lock=None,
        semaphore=None,
    )
    attrs.update(overrides)
    return SimpleNamespace(**attrs)


def _make_tool_item(name="my_tool", description="does stuff"):
    return SimpleNamespace(name=name, description=description, inputSchema={"type": "object", "properties": {}})


def _make_resource_item(name="res.txt", uri="file:///res.txt"):
    return SimpleNamespace(name=name, uri=uri, description="a resource", mimeType="text/plain")


def _make_prompt_item(name="greet", description="say hello"):
    return SimpleNamespace(name=name, description=description, arguments=[])


# ---------------------------------------------------------------------------
# import_server_capabilities
# ---------------------------------------------------------------------------


class TestImportServerCapabilities:
    """Tests for import_server_capabilities."""

    def test_tools_only_imports_tools(self):
        config = _make_config(import_tools=True, import_resources=False, import_prompts=False)
        runtime = _make_runtime(config=config)
        tool_item = _make_tool_item()
        tools_result = SimpleNamespace(tools=[tool_item])
        runtime.session.list_tools = AsyncMock(return_value=tools_result)

        build_descriptor = MagicMock(return_value=MCPToolDescriptor(
            local_name="mcp_test_server__my_tool",
            remote_name="my_tool",
            server_name="test_server",
            description="desc",
            param_schema={},
            allowed_agents=[],
            risk_level="medium",
            require_hitl=False,
            timeout_sec=30,
            rate_limit_per_min=10,
            capability="tool",
        ))
        clear_descriptors = MagicMock()
        tool_descriptors = {}
        failures = {}

        async def run():
            await import_server_capabilities(
                runtime,
                startup_timeout=10,
                tool_descriptors=tool_descriptors,
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
                failures=failures,
                clear_descriptors=clear_descriptors,
                build_descriptor=build_descriptor,
                ensure_unique_name=lambda n: n,
            )

        asyncio.run(run())
        clear_descriptors.assert_called_once_with("test_server")
        build_descriptor.assert_called_once()
        assert "mcp_test_server__my_tool" in tool_descriptors
        assert "mcp_test_server__my_tool" in runtime.tool_names

    def test_all_disabled_only_clears_descriptors(self):
        config = _make_config(import_tools=False, import_resources=False, import_prompts=False)
        runtime = _make_runtime(config=config)
        clear_descriptors = MagicMock()

        async def run():
            await import_server_capabilities(
                runtime,
                startup_timeout=10,
                tool_descriptors={},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
                failures={},
                clear_descriptors=clear_descriptors,
                build_descriptor=MagicMock(),
                ensure_unique_name=lambda n: n,
            )

        asyncio.run(run())
        clear_descriptors.assert_called_once_with("test_server")

    def test_build_descriptor_returns_none_skips_tool(self):
        config = _make_config(import_tools=True, import_resources=False, import_prompts=False)
        runtime = _make_runtime(config=config)
        tools_result = SimpleNamespace(tools=[_make_tool_item()])
        runtime.session.list_tools = AsyncMock(return_value=tools_result)

        build_descriptor = MagicMock(return_value=None)
        tool_descriptors = {}

        async def run():
            await import_server_capabilities(
                runtime,
                startup_timeout=10,
                tool_descriptors=tool_descriptors,
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
                failures={},
                clear_descriptors=MagicMock(),
                build_descriptor=build_descriptor,
                ensure_unique_name=lambda n: n,
            )

        asyncio.run(run())
        assert len(tool_descriptors) == 0
        assert len(runtime.tool_names) == 0

    def test_list_tools_timeout_propagates(self):
        config = _make_config(import_tools=True, import_resources=False, import_prompts=False)
        runtime = _make_runtime(config=config)
        runtime.session.list_tools = AsyncMock(side_effect=asyncio.TimeoutError)

        async def run():
            await import_server_capabilities(
                runtime,
                startup_timeout=1,
                tool_descriptors={},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
                failures={},
                clear_descriptors=MagicMock(),
                build_descriptor=MagicMock(),
                ensure_unique_name=lambda n: n,
            )

        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(run())


# ---------------------------------------------------------------------------
# import_resources
# ---------------------------------------------------------------------------


class TestImportResources:
    """Tests for import_resources."""

    def test_session_lacks_both_list_methods_early_return(self):
        config = _make_config()
        runtime = _make_runtime(config=config)
        # Remove both list methods
        if hasattr(runtime.session, "list_resources"):
            delattr(runtime.session, "list_resources")
        if hasattr(runtime.session, "list_resource_templates"):
            delattr(runtime.session, "list_resource_templates")
        resource_descriptors = {}
        resource_template_descriptors = {}
        tool_descriptors = {}
        failures = {}

        async def run():
            await import_resources(
                runtime,
                startup_timeout=10,
                tool_descriptors=tool_descriptors,
                resource_descriptors=resource_descriptors,
                resource_template_descriptors=resource_template_descriptors,
                failures=failures,
                ensure_unique_name=lambda n: n,
            )

        asyncio.run(run())
        assert resource_descriptors == {}
        assert resource_template_descriptors == {}

    def test_list_resources_succeeds_templates_fail(self):
        config = _make_config()
        runtime = _make_runtime(config=config)
        res_result = SimpleNamespace(resources=[_make_resource_item()])
        runtime.session.list_resources = AsyncMock(return_value=res_result)
        runtime.session.list_resource_templates = AsyncMock(side_effect=RuntimeError("templates broken"))
        # Ensure read_resource exists so tool descriptors would be built
        runtime.session.read_resource = AsyncMock()

        resource_descriptors = {}
        resource_template_descriptors = {}
        tool_descriptors = {}
        failures = {}

        async def run():
            await import_resources(
                runtime,
                startup_timeout=10,
                tool_descriptors=tool_descriptors,
                resource_descriptors=resource_descriptors,
                resource_template_descriptors=resource_template_descriptors,
                failures=failures,
                ensure_unique_name=lambda n: n,
            )

        asyncio.run(run())
        assert "test_server" in resource_descriptors
        assert len(resource_descriptors["test_server"]) == 1
        assert "test_server" in failures
        assert "list_resource_templates" in failures["test_server"]

    def test_both_fail_failures_has_list_resources_error(self):
        config = _make_config()
        runtime = _make_runtime(config=config)
        runtime.session.list_resources = AsyncMock(side_effect=RuntimeError("resources broken"))
        runtime.session.list_resource_templates = AsyncMock(side_effect=RuntimeError("templates broken"))

        resource_descriptors = {}
        resource_template_descriptors = {}
        tool_descriptors = {}
        failures = {}

        async def run():
            await import_resources(
                runtime,
                startup_timeout=10,
                tool_descriptors=tool_descriptors,
                resource_descriptors=resource_descriptors,
                resource_template_descriptors=resource_template_descriptors,
                failures=failures,
                ensure_unique_name=lambda n: n,
            )

        asyncio.run(run())
        assert "test_server" in failures
        assert "list_resources" in failures["test_server"]

    def test_session_lacks_read_resource_no_tool_descriptors(self):
        config = _make_config()
        runtime = _make_runtime(config=config)
        res_result = SimpleNamespace(resources=[_make_resource_item()])
        runtime.session.list_resources = AsyncMock(return_value=res_result)
        tpl_result = SimpleNamespace(resourceTemplates=[])
        runtime.session.list_resource_templates = AsyncMock(return_value=tpl_result)
        # Remove read_resource
        if hasattr(runtime.session, "read_resource"):
            delattr(runtime.session, "read_resource")

        resource_descriptors = {}
        resource_template_descriptors = {}
        tool_descriptors = {}
        failures = {}

        async def run():
            await import_resources(
                runtime,
                startup_timeout=10,
                tool_descriptors=tool_descriptors,
                resource_descriptors=resource_descriptors,
                resource_template_descriptors=resource_template_descriptors,
                failures=failures,
                ensure_unique_name=lambda n: n,
            )

        asyncio.run(run())
        assert len(tool_descriptors) == 0

    def test_empty_results_stores_empty_descriptors(self):
        config = _make_config()
        runtime = _make_runtime(config=config)
        empty_resources = SimpleNamespace(resources=[])
        empty_templates = SimpleNamespace(resourceTemplates=[])
        runtime.session.list_resources = AsyncMock(return_value=empty_resources)
        runtime.session.list_resource_templates = AsyncMock(return_value=empty_templates)
        runtime.session.read_resource = AsyncMock()

        resource_descriptors = {}
        resource_template_descriptors = {}
        tool_descriptors = {}
        failures = {}

        async def run():
            await import_resources(
                runtime,
                startup_timeout=10,
                tool_descriptors=tool_descriptors,
                resource_descriptors=resource_descriptors,
                resource_template_descriptors=resource_template_descriptors,
                failures=failures,
                ensure_unique_name=lambda n: n,
            )

        asyncio.run(run())
        assert resource_descriptors["test_server"] == []
        assert resource_template_descriptors["test_server"] == []
        # Tool descriptors ARE built because read_resource exists and listed_any is True
        assert len(tool_descriptors) == 3


# ---------------------------------------------------------------------------
# import_prompts
# ---------------------------------------------------------------------------


class TestImportPrompts:
    """Tests for import_prompts."""

    def test_session_lacks_list_prompts_early_return(self):
        config = _make_config()
        runtime = _make_runtime(config=config)
        if hasattr(runtime.session, "list_prompts"):
            delattr(runtime.session, "list_prompts")

        prompt_descriptors = {}
        tool_descriptors = {}
        failures = {}

        async def run():
            await import_prompts(
                runtime,
                startup_timeout=10,
                tool_descriptors=tool_descriptors,
                prompt_descriptors=prompt_descriptors,
                failures=failures,
                ensure_unique_name=lambda n: n,
            )

        asyncio.run(run())
        assert prompt_descriptors == {}
        assert failures == {}

    def test_list_prompts_fails_failure_recorded(self):
        config = _make_config()
        runtime = _make_runtime(config=config)
        runtime.session.list_prompts = AsyncMock(side_effect=RuntimeError("prompt list broken"))

        prompt_descriptors = {}
        tool_descriptors = {}
        failures = {}

        async def run():
            await import_prompts(
                runtime,
                startup_timeout=10,
                tool_descriptors=tool_descriptors,
                prompt_descriptors=prompt_descriptors,
                failures=failures,
                ensure_unique_name=lambda n: n,
            )

        asyncio.run(run())
        assert "test_server" in failures
        assert "list_prompts" in failures["test_server"]

    def test_session_lacks_get_prompt_no_tool_descriptors(self):
        config = _make_config()
        runtime = _make_runtime(config=config)
        prompts_result = SimpleNamespace(prompts=[_make_prompt_item()])
        runtime.session.list_prompts = AsyncMock(return_value=prompts_result)
        if hasattr(runtime.session, "get_prompt"):
            delattr(runtime.session, "get_prompt")

        prompt_descriptors = {}
        tool_descriptors = {}
        failures = {}

        async def run():
            await import_prompts(
                runtime,
                startup_timeout=10,
                tool_descriptors=tool_descriptors,
                prompt_descriptors=prompt_descriptors,
                failures=failures,
                ensure_unique_name=lambda n: n,
            )

        asyncio.run(run())
        assert "test_server" in prompt_descriptors
        assert len(tool_descriptors) == 0


# ---------------------------------------------------------------------------
# build_resource_tool_descriptors
# ---------------------------------------------------------------------------


class TestBuildResourceToolDescriptors:
    """Tests for build_resource_tool_descriptors."""

    def test_returns_exactly_three_descriptors(self):
        config = _make_config()
        result = build_resource_tool_descriptors(config)
        assert len(result) == 3

    def test_correct_names(self):
        config = _make_config()
        result = build_resource_tool_descriptors(config)
        names = {d.remote_name for d in result}
        assert names == {"list_resources", "read_resource", "list_resource_templates"}

    def test_correct_capabilities(self):
        config = _make_config()
        result = build_resource_tool_descriptors(config)
        for descriptor in result:
            assert descriptor.capability == "resource"

    def test_read_resource_has_required_uri(self):
        config = _make_config()
        result = build_resource_tool_descriptors(config)
        read_desc = next(d for d in result if d.remote_name == "read_resource")
        assert "uri" in read_desc.param_schema.get("required", [])


# ---------------------------------------------------------------------------
# build_prompt_tool_descriptors
# ---------------------------------------------------------------------------


class TestBuildPromptToolDescriptors:
    """Tests for build_prompt_tool_descriptors."""

    def test_returns_exactly_two_descriptors(self):
        config = _make_config()
        result = build_prompt_tool_descriptors(config)
        assert len(result) == 2

    def test_get_prompt_has_required_name(self):
        config = _make_config()
        result = build_prompt_tool_descriptors(config)
        get_desc = next(d for d in result if d.remote_name == "get_prompt")
        assert "name" in get_desc.param_schema.get("required", [])
