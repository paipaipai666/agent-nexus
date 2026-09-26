"""长回答截断：默认上限抬高 + 答案阶段续写（禁止「请缩短」）。"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentnexus.agents import decisions
from agentnexus.agents.react_types import CallingStrategy, RetryReason
from agentnexus.core.capabilities import ModelCapabilities, detect_capabilities


class TestMaxOutputTokensDefault:
    def test_default_allows_long_reports(self, temp_agentnexus_home):
        caps = detect_capabilities("any", "https://x.test")
        assert caps.max_output_tokens >= 32_768, (
            f"默认 max_output_tokens={caps.max_output_tokens} 过小，长汇报会被截断"
        )

    def test_dataclass_default(self):
        assert ModelCapabilities().max_output_tokens >= 32_768


class TestTruncatedAnswerContinues:
    def test_truncated_with_tools_still_fails_batch(self):
        d = decisions.interpret_native(
            response_text="partial",
            reasoning_text="",
            tool_calls=[{"name": "x", "arguments": {}}],
            truncated=True,
            streamed=False,
            tool_exists=lambda n: True,
        )
        assert d.kind == "fault"
        assert d.fail_pending_calls is True

    def test_truncated_without_tools_is_recoverable_text(self):
        d = decisions.interpret_native(
            response_text="这是很长的报告开头……",
            reasoning_text="",
            tool_calls=None,
            truncated=True,
            streamed=False,
            tool_exists=lambda n: True,
        )
        assert d.kind == "fault"
        assert d.reason is RetryReason.TRUNCATED
        assert d.fail_pending_calls is False


class TestPartialAnswerJoin:
    def test_join_and_clear(self):
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.agents.react_types import ExecutionContext, RunState

        ctx = ExecutionContext(question="q")
        ctx.run_state.partial_answer = "第一段"
        assert ReActAgent._join_partial_answer(ctx, "第二段") == "第一段第二段"
        assert ctx.run_state.partial_answer == ""

    def test_retry_nudge_stashes_partial_and_asks_to_continue(self):
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.agents.react_types import ExecutionContext
        from agentnexus.tools.registry import ToolRegistry

        llm = MagicMock()
        llm.capabilities.supports_thinking = False
        llm.last_error = ""
        agent = ReActAgent(llm, ToolRegistry(), conversation_mode=False)
        agent._output = lambda _m: None

        ctx = ExecutionContext(question="写一份长报告", strategy=CallingStrategy.NATIVE_TOOLS)
        ctx.last_response_text = "报告第一章写到一半"
        ctx.json_retries = 0

        agent._emit_retry_nudge(ctx, RetryReason.TRUNCATED, "length")

        assert ctx.run_state.partial_answer == "报告第一章写到一半"
        last = ctx.messages[-1]["content"]
        assert "请缩短本次输出" not in last, "不得要求缩短长回答"
        assert "继续" in last
        # 多次截断要累加
        ctx.last_response_text = "（续写片段）"
        agent._emit_retry_nudge(ctx, RetryReason.TRUNCATED, "length")
        assert ctx.run_state.partial_answer == "报告第一章写到一半（续写片段）"
