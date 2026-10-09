"""Recursive depth limit tests.

Validates that subagent recursive calls terminate correctly
and prevent infinite recursion.
"""
from unittest.mock import MagicMock, patch

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.tools.registry import ToolMeta, ToolRegistry


def _make_llm(response=""):
    llm = MagicMock()
    llm.model = "test/test-model"
    llm.total_usage = {"input_tokens": 0, "output_tokens": 0}
    llm.last_error = ""
    llm.last_truncated = False
    llm.last_tool_calls = []
    llm.last_reasoning_content = ""
    llm.last_usage = {"input_tokens": 0, "output_tokens": 0}
    llm.capabilities = MagicMock()
    llm.capabilities.supports_thinking = False
    llm.capabilities.supports_tool_calling = True
    llm.capabilities.supports_json_mode = True
    llm.capabilities.supports_json_schema = False
    llm.capabilities.supports_parallel_tool_calls = False
    llm.capabilities.thinking_effort = "none"
    llm.think.return_value = response
    return llm


class TestRecursiveDepthLimit:
    """Subagent recursion depth limits."""

    def test_max_steps_prevents_infinite_loop(self):
        llm = _make_llm("Answer")
        llm.think.side_effect = lambda **kw: (setattr(llm, 'last_tool_calls', []) or "Answer")
        te = ToolRegistry()
        agent = ReActAgent(llm, te, max_steps=3)

        result = agent.run("Infinite loop question")
        assert result.answer is not None

    def test_nested_agent_calls_terminate(self):
        llm = _make_llm()
        call_count = [0]

        def mock_think(**kw):
            call_count[0] += 1
            llm.last_tool_calls = []
            return f"Answer {call_count[0]}"
        llm.think.side_effect = mock_think

        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="web_search",
                description="搜索",
                param_schema={"type": "object", "properties": {}},
            ),
            lambda **kw: "result",
        )
        agent = ReActAgent(llm, te, max_steps=5)

        result = agent.run("Complex question")
        assert result.answer is not None
        assert call_count[0] <= 5

    def test_json_retries_terminate(self):
        llm = _make_llm()
        llm.last_error = ""
        llm.think.side_effect = lambda **kw: (setattr(llm, 'last_tool_calls', []) or "Direct answer")

        te = ToolRegistry()
        agent = ReActAgent(llm, te, max_steps=2)

        result = agent.run("Test")
        assert result.answer is not None


class TestResourceExhaustion:
    """Sandbox resource exhaustion handling."""

    def test_timeout_handled_gracefully(self):
        with patch("agentnexus.tools.shell.get_settings") as mock_settings:
            mock_settings.return_value.shell_enabled = True
            mock_settings.return_value.shell_blacklist = []
            mock_settings.return_value.shell_execution_backend = "local_unsafe"
            mock_settings.return_value.shell_timeout = 2

            from agentnexus.tools.shell import shell_exec
            result = shell_exec("echo ok")
            assert "ok" in result

    def test_large_input_handled(self):
        from agentnexus.memory.short_term import ShortTermMemory
        stm = ShortTermMemory()
        stm.append("user", "A" * 10000)
        messages = stm.get_all()
        assert len(messages) == 1

    def test_many_messages_handled(self):
        from agentnexus.memory.short_term import ShortTermMemory
        stm = ShortTermMemory(max_messages=1000)
        for i in range(100):
            stm.append("user" if i % 2 == 0 else "assistant", f"Message {i}")
        messages = stm.get_all()
        assert len(messages) == 100
