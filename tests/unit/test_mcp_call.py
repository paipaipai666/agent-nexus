"""Tests for MCP descriptor call dispatch and concurrency limiter."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentnexus.tools.mcp_call import call_descriptor, run_with_limiter
from agentnexus.tools.mcp_schema import MCPToolDescriptor


def _make_descriptor(**overrides):
    defaults = dict(
        local_name="test_tool",
        remote_name="test_tool",
        server_name="srv",
        description="desc",
        param_schema={},
        allowed_agents=["agent"],
        risk_level="medium",
        require_hitl=False,
        timeout_sec=30,
        rate_limit_per_min=10,
        capability="tool",
    )
    defaults.update(overrides)
    return MCPToolDescriptor(**defaults)


def _make_runtime(**overrides):
    attrs = dict(
        config=SimpleNamespace(name="srv"),
        session=AsyncMock(),
        exit_stack=SimpleNamespace(aclose=AsyncMock()),
        semaphore=None,
        call_lock=None,
        tool_names=[],
        resource_tool_names=[],
        prompt_tool_names=[],
        resource_descriptors=[],
        resource_templates=[],
        prompt_descriptors=[],
    )
    attrs.update(overrides)
    return SimpleNamespace(**attrs)


# ---------------------------------------------------------------------------
# call_descriptor tests
# ---------------------------------------------------------------------------


class TestCallDescriptorTool:
    """call_descriptor with capability='tool'."""

    def test_tool_success_returns_normalized_text(self):
        descriptor = _make_descriptor(capability="tool", remote_name="my_tool")
        runtime = _make_runtime()
        mock_result = SimpleNamespace(isError=False, content=[SimpleNamespace(text="hello")])
        runtime.session.call_tool = AsyncMock(return_value=mock_result)

        async def run():
            return await call_descriptor(
                runtime, descriptor, {"x": 1},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
            )

        result = asyncio.run(run())
        assert result == "hello"
        runtime.session.call_tool.assert_awaited_once_with("my_tool", arguments={"x": 1})

    def test_tool_error_raises_runtime_error(self):
        descriptor = _make_descriptor(capability="tool", remote_name="bad_tool")
        runtime = _make_runtime()
        mock_result = SimpleNamespace(isError=True, content=[SimpleNamespace(text="boom")])
        runtime.session.call_tool = AsyncMock(return_value=mock_result)

        async def run():
            return await call_descriptor(
                runtime, descriptor, {},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
            )

        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(run())

    def test_tool_timeout_raises_timeout_error(self):
        descriptor = _make_descriptor(capability="tool", remote_name="slow_tool", timeout_sec=1)
        runtime = _make_runtime()
        runtime.session.call_tool = AsyncMock(side_effect=asyncio.TimeoutError)

        async def run():
            return await call_descriptor(
                runtime, descriptor, {},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
            )

        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(run())


class TestCallDescriptorListResources:
    """call_descriptor for list_resources."""

    def test_list_resources_returns_json(self):
        item = SimpleNamespace(name="file.txt", uri="file:///tmp/file.txt")
        descriptor = _make_descriptor(remote_name="list_resources", capability="resource")
        runtime = _make_runtime()

        async def run():
            return await call_descriptor(
                runtime, descriptor, {},
                resource_descriptors={"srv": [item]},
                resource_template_descriptors={},
                prompt_descriptors={},
            )

        result = asyncio.run(run())
        assert "file.txt" in result
        assert "file:///tmp/file.txt" in result

    def test_list_resource_templates_returns_json(self):
        descriptor = _make_descriptor(remote_name="list_resource_templates", capability="resource")
        runtime = _make_runtime()
        templates = [{"uriTemplate": "file:///{path}"}]

        async def run():
            return await call_descriptor(
                runtime, descriptor, {},
                resource_descriptors={},
                resource_template_descriptors={"srv": templates},
                prompt_descriptors={},
            )

        result = asyncio.run(run())
        assert "file:///{path}" in result


class TestCallDescriptorReadResource:
    """call_descriptor for read_resource."""

    def test_read_resource_success(self):
        descriptor = _make_descriptor(remote_name="read_resource", capability="resource")
        runtime = _make_runtime()
        read_result = SimpleNamespace(contents=[SimpleNamespace(text="content here")])
        runtime.session.read_resource = AsyncMock(return_value=read_result)

        async def run():
            return await call_descriptor(
                runtime, descriptor, {"uri": "file:///a"},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
            )

        result = asyncio.run(run())
        assert "content here" in result
        runtime.session.read_resource.assert_awaited_once_with("file:///a")

    def test_read_resource_missing_uri_raises_key_error(self):
        descriptor = _make_descriptor(remote_name="read_resource", capability="resource")
        runtime = _make_runtime()

        async def run():
            return await call_descriptor(
                runtime, descriptor, {},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
            )

        with pytest.raises(KeyError):
            asyncio.run(run())


class TestCallDescriptorPrompts:
    """call_descriptor for prompt capabilities."""

    def test_list_prompts_returns_json(self):
        item = SimpleNamespace(name="greet", server_name="srv")
        descriptor = _make_descriptor(remote_name="list_prompts", capability="prompt")
        runtime = _make_runtime()

        async def run():
            return await call_descriptor(
                runtime, descriptor, {},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={"srv": [item]},
            )

        result = asyncio.run(run())
        assert "greet" in result

    def test_get_prompt_success(self):
        descriptor = _make_descriptor(remote_name="get_prompt", capability="prompt")
        runtime = _make_runtime()
        prompt_result = SimpleNamespace(messages=[SimpleNamespace(role="user", content="hi")])
        runtime.session.get_prompt = AsyncMock(return_value=prompt_result)

        async def run():
            return await call_descriptor(
                runtime, descriptor, {"name": "greet"},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
            )

        result = asyncio.run(run())
        assert "hi" in result
        runtime.session.get_prompt.assert_awaited_once_with("greet", arguments=None)

    def test_get_prompt_missing_name_raises_key_error(self):
        descriptor = _make_descriptor(remote_name="get_prompt", capability="prompt")
        runtime = _make_runtime()

        async def run():
            return await call_descriptor(
                runtime, descriptor, {},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
            )

        with pytest.raises(KeyError):
            asyncio.run(run())

    def test_get_prompt_empty_arguments_treated_as_none(self):
        descriptor = _make_descriptor(remote_name="get_prompt", capability="prompt")
        runtime = _make_runtime()
        prompt_result = SimpleNamespace(messages=[])
        runtime.session.get_prompt = AsyncMock(return_value=prompt_result)

        async def run():
            return await call_descriptor(
                runtime, descriptor, {"name": "greet", "arguments": {}},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
            )

        asyncio.run(run())
        runtime.session.get_prompt.assert_awaited_once_with("greet", arguments=None)


class TestCallDescriptorUnsupported:
    """call_descriptor with unsupported capability."""

    def test_unsupported_remote_name_raises_runtime_error(self):
        descriptor = _make_descriptor(remote_name="unknown_op", capability="magic")
        runtime = _make_runtime()

        async def run():
            return await call_descriptor(
                runtime, descriptor, {},
                resource_descriptors={},
                resource_template_descriptors={},
                prompt_descriptors={},
            )

        with pytest.raises(RuntimeError, match="Unsupported MCP capability"):
            asyncio.run(run())


# ---------------------------------------------------------------------------
# run_with_limiter tests
# ---------------------------------------------------------------------------


class TestRunWithLimiter:
    """Tests for run_with_limiter concurrency helper."""

    def test_uses_semaphore_when_available(self):
        mock_semaphore = MagicMock()
        mock_semaphore.__aenter__ = AsyncMock()
        mock_semaphore.__aexit__ = AsyncMock()
        runtime = _make_runtime(semaphore=mock_semaphore)
        call = AsyncMock(return_value="ok")

        async def run():
            return await run_with_limiter(runtime, call)

        result = asyncio.run(run())
        assert result == "ok"
        mock_semaphore.__aenter__.assert_awaited_once()
        call.assert_awaited_once()

    def test_uses_call_lock_when_semaphore_is_none(self):
        mock_lock = MagicMock()
        mock_lock.__aenter__ = AsyncMock()
        mock_lock.__aexit__ = AsyncMock()
        runtime = _make_runtime(semaphore=None, call_lock=mock_lock)
        call = AsyncMock(return_value="locked")

        async def run():
            return await run_with_limiter(runtime, call)

        result = asyncio.run(run())
        assert result == "locked"
        mock_lock.__aenter__.assert_awaited_once()

    def test_both_none_creates_per_call_semaphore(self):
        runtime = _make_runtime(semaphore=None, call_lock=None)
        call = AsyncMock(return_value="fallback")

        async def run():
            return await run_with_limiter(runtime, call)

        result = asyncio.run(run())
        assert result == "fallback"
        call.assert_awaited_once()

    def test_call_raises_semaphore_still_released(self):
        mock_semaphore = AsyncMock()
        runtime = _make_runtime(semaphore=mock_semaphore)
        call = AsyncMock(side_effect=RuntimeError("tool exploded"))

        async def run():
            return await run_with_limiter(runtime, call)

        with pytest.raises(RuntimeError, match="tool exploded"):
            asyncio.run(run())
        mock_semaphore.__aenter__.assert_awaited_once()
        mock_semaphore.__aexit__.assert_awaited_once()
