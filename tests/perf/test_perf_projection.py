"""Performance tests for read-time projection and microcompaction helpers."""

from __future__ import annotations

import pytest

from agentnexus.memory.compaction import is_recoverable_tool, parse_tool_message
from agentnexus.memory.projection import (
    build_projection,
    microcompact_messages,
    project_aggressive,
    project_mild,
)


def _make_messages(count: int, role: str = "user", content_len: int = 100) -> list[dict]:
    text = "content " * max(content_len // 8, 1)
    return [{"role": role, "content": f"{text} #{i}"} for i in range(count)]


def _make_mixed_messages(count: int) -> list[dict]:
    msgs = []
    tools = ["read", "bash", "grep", "glob", "web_search"]
    for i in range(count):
        if i % 3 == 0:
            tool = tools[i % len(tools)]
            msgs.append({"role": "tool", "content": f"Action: {tool}[key=value]\nObservation: result {i}"})
        elif i % 3 == 1:
            msgs.append({"role": "assistant", "content": f"response {i} " * 200})
        else:
            msgs.append({"role": "user", "content": f"question {i}"})
    return msgs


# ── project_mild ──────────────────────────────────────────────


class TestProjectMild:
    @pytest.mark.parametrize("count", [10, 50, 200])
    def test_project_mild_scaling(self, benchmark, count):
        messages = _make_mixed_messages(count)

        result = benchmark(project_mild, messages)
        assert isinstance(result, list)
        assert len(result) == count

    def test_project_mild_long_content(self, benchmark):
        messages = _make_messages(50, "assistant", content_len=5000)

        result = benchmark(project_mild, messages)
        assert isinstance(result, list)


# ── project_aggressive ────────────────────────────────────────


class TestProjectAggressive:
    @pytest.mark.parametrize("count", [10, 50, 200])
    def test_project_aggressive_scaling(self, benchmark, count):
        messages = _make_mixed_messages(count)

        result = benchmark(
            project_aggressive,
            messages,
            parse_tool_message=parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
        )
        assert isinstance(result, list)

    def test_project_aggressive_all_recoverable(self, benchmark):
        messages = []
        for i in range(100):
            messages.append({"role": "tool", "content": f"Action: read[path=/f{i}]\nObservation: data"})

        result = benchmark(
            project_aggressive,
            messages,
            parse_tool_message=parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
        )
        assert isinstance(result, list)


# ── microcompact_messages ─────────────────────────────────────


class TestMicrocompactMessages:
    def test_microcompact_100_messages(self, benchmark):
        messages = _make_mixed_messages(100)

        result = benchmark(
            microcompact_messages,
            messages,
            parse_tool_message=parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
        )
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_microcompact_no_recoverable(self, benchmark):
        messages = _make_messages(100, "user")

        result = benchmark(
            microcompact_messages,
            messages,
            parse_tool_message=parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
        )
        _, cleaned = result
        assert cleaned is False


# ── build_projection threshold routing ────────────────────────


class TestBuildProjection:
    def test_build_projection_below_threshold(self, benchmark):
        messages = _make_messages(20, "user")
        result = benchmark(
            build_projection,
            messages,
            token_count=100,
            ctx_max=128000,
            parse_tool_message=parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
        )
        assert result == messages

    def test_build_projection_mild(self, benchmark):
        messages = _make_mixed_messages(100)
        result = benchmark(
            build_projection,
            messages,
            token_count=116000,
            ctx_max=128000,
            parse_tool_message=parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
        )
        assert isinstance(result, list)

    def test_build_projection_aggressive(self, benchmark):
        messages = _make_mixed_messages(100)
        result = benchmark(
            build_projection,
            messages,
            token_count=122000,
            ctx_max=128000,
            parse_tool_message=parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
        )
        assert isinstance(result, list)
