"""守护 FSM 重设计（Step 2/3, 2026-09-24）落地后的行为与表结构。

历史：本文件最初用于验证"FSM 框住模型选择空间"的三个约束
（一步一个槽 / Thought 强制 / 批内不可短路），并因此挖出三个
转移表缺口。三项决策拍板后，那些约束已被主动移除、缺口随旧状态
一起消失。现在本文件守护新形态：

1. 决策1：无 Thought 的工具调用直接放行
2. 决策2：tools + text 共存（commentary 随行展示）
3. 决策3：默认无 max_steps；配置时软收尾给出诚实答案
4. 表结构 totality（手动版；Step 5 会以 AST 审计固化）：
   每个状态的出口集合 == 其 handler 的返回值封闭集
"""
from unittest.mock import MagicMock

import pytest

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.agents.react_transitions import TRANSFER_TABLE
from agentnexus.agents.react_types import (
    AgentStep,
    CallingStrategy,
    ExecutionContext,
    ReActEvent,
    ReActEventType as E,
    ReActState as S,
    RetryReason,
)
from agentnexus.core.capabilities import SessionCapabilityTracker
from agentnexus.tools.registry import ToolRegistry


def _make_llm(supports_tool_calling: bool = True):
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
    llm.capabilities.supports_tool_calling = supports_tool_calling
    llm.capabilities.supports_json_mode = True
    llm.capabilities.supports_json_schema = False
    llm.capabilities.supports_parallel_tool_calls = False
    llm.capabilities.thinking_effort = "none"
    llm.session_tracker = SessionCapabilityTracker()
    llm.think.return_value = ""
    return llm


def _rows_for(state: S):
    return [t for t in TRANSFER_TABLE if t.state == state]


# ══════════════════════════════════════════════════════════════
# 4. 表结构：出口集合封闭（totality 手动断言）
# ══════════════════════════════════════════════════════════════


class TestTableShape:
    def test_six_states_only(self):
        assert [s.name for s in S] == [
            "INIT", "AWAIT_MODEL", "EXECUTE_TOOL", "RECOVER", "ANSWER", "DONE",
        ]

    def test_await_model_exits_match_round_return_set(self):
        # _on_round 返回封闭于 {TOOLS_REQUESTED, ANSWER_READY, FAULT, ROUND_READY}
        events = {t.event for t in _rows_for(S.AWAIT_MODEL)}
        assert events == {
            None,                    # auto-advance 循环
            E.TOOLS_REQUESTED,
            E.ANSWER_READY,
            E.FAULT,
            E.ROUND_READY,
        }

    def test_recover_exits_match_recover_return_set(self):
        # _on_recover 返回封闭于 {ROUND_READY, ANSWER_READY, ABORT}
        events = {t.event for t in _rows_for(S.RECOVER)}
        assert events == {E.ROUND_READY, E.ANSWER_READY, E.ABORT}

    def test_answer_is_total(self):
        events = {t.event for t in _rows_for(S.ANSWER)}
        assert events == {E.ANSWER_VETOED, None}  # None = 无条件兜底

    def test_execute_tool_exits_match_tool_return_set(self):
        # _on_tools_requested 返回封闭于 {TOOLS_DONE, ANSWER_READY}
        events = {t.event for t in _rows_for(S.EXECUTE_TOOL)}
        assert events == {E.TOOLS_DONE, E.ANSWER_READY}


# ══════════════════════════════════════════════════════════════
# 决策1：无 Thought 直接调工具
# ══════════════════════════════════════════════════════════════


class TestNoThoughtGate:
    def test_tool_only_model_terminates_gracefully(self):
        """模型每轮只发 tool_calls、没有可见文本：工具照跑，配置的小
        max_steps 到点后软收尾，绝不 FSMError。"""
        printed: list[str] = []
        llm = _make_llm()
        te = ToolRegistry()
        te.register_tool("file_read", "读文件", lambda **kw: "content")
        agent = ReActAgent(llm, te, max_steps=4, output=printed.append)
        ran: list[str] = []

        def think(**_kw):
            llm.last_tool_calls = [{"name": "file_read", "arguments": {"path": "a.txt"}}]
            return ""

        llm.think.side_effect = think
        orig_execute = agent._execute_tool
        agent._execute_tool = lambda name, args: ran.append(name) or orig_execute(name, args)

        result = agent.run("读一下 a.txt")

        assert "file_read" in ran
        assert "tool_calling" not in llm.session_tracker.disabled_features
        assert result.answer is not None
        assert "最大步数" in result.answer


# ══════════════════════════════════════════════════════════════
# 决策3：默认无 max_steps
# ══════════════════════════════════════════════════════════════


class TestDefaultNoMaxSteps:
    def test_default_is_unlimited(self):
        llm = _make_llm()
        assert ReActAgent(llm, ToolRegistry()).max_steps is None
        assert ExecutionContext(question="q").max_steps is None
        assert ReActAgent(llm, ToolRegistry(), max_steps=7).max_steps == 7


# ══════════════════════════════════════════════════════════════
# 工具批次：整批完成后才交还控制权
# ══════════════════════════════════════════════════════════════


class TestToolBatchMustComplete:
    def test_all_five_tools_run_before_the_model_sees_results(self):
        printed: list[str] = []
        order: list[tuple[str, str]] = []
        llm = _make_llm()

        te = ToolRegistry()
        for i in range(5):
            te.register_tool(
                f"probe_{i}", "探测",
                lambda i=i, **_kw: order.append(("tool", f"probe_{i}")) or f"r{i}",
            )

        agent = ReActAgent(llm, te, max_steps=4, output=printed.append)
        rounds = {"n": 0}

        def think(**_kw):
            rounds["n"] += 1
            order.append(("think", str(rounds["n"])))
            if rounds["n"] == 1:
                llm.last_tool_calls = [
                    {"name": f"probe_{i}", "arguments": {}} for i in range(5)
                ]
                return "先并行读五个探测点再判断。"
            llm.last_tool_calls = []
            return "够了，答案是这样。"

        llm.think.side_effect = think

        result = agent.run("给我结果")

        assert rounds["n"] == 2
        tools_done = [tag for kind, tag in order if kind == "tool"]
        assert len(tools_done) == 5
        second_round_index = order.index(("think", "2"))
        assert second_round_index == len(order) - 1
        assert second_round_index > order.index(("tool", "probe_4"))
        assert result.answer is not None


# ══════════════════════════════════════════════════════════════
# RECOVER 策略：返回封闭于 {ROUND_READY, ANSWER_READY, ABORT}
# ══════════════════════════════════════════════════════════════


class TestRecoverPolicy:
    def _ctx(self, llm, strategy=None):
        ctx = ExecutionContext(question="q", max_steps=None)
        ctx.steps.append(AgentStep(step_id=1))
        ctx.memory_state.session_caps = llm.session_tracker
        if strategy is not None:
            ctx.run_state.strategy = strategy
        return ctx

    def test_fatal_fault_aborts(self):
        llm = _make_llm()
        agent = ReActAgent(llm, ToolRegistry(), output=lambda s: None)
        ctx = self._ctx(llm)
        events = agent._on_recover(ctx, ReActEvent(E.FAULT, {"fatal": True, "detail": "boom"}))
        assert [e.type for e in events] == [E.ABORT]

    def test_no_tools_no_text_degrades_and_continues(self):
        llm = _make_llm()
        agent = ReActAgent(llm, ToolRegistry(), output=lambda s: None)
        ctx = self._ctx(llm, strategy=CallingStrategy.NATIVE_TOOLS)
        events = agent._on_recover(ctx, ReActEvent(E.FAULT, {"no_tools_no_text": True}))
        assert [e.type for e in events] == [E.ROUND_READY]
        assert "tool_calling" in llm.session_tracker.disabled_features

    def test_empty_response_with_budget_retries(self):
        llm = _make_llm()
        agent = ReActAgent(llm, ToolRegistry(), output=lambda s: None)
        ctx = self._ctx(llm, strategy=CallingStrategy.JSON_MODE)
        ctx.run_state.json_retries = 0
        events = agent._on_recover(ctx, ReActEvent(E.FAULT, {
            "reason": RetryReason.EMPTY_RESPONSE, "detail": ""}))
        assert [e.type for e in events] == [E.ROUND_READY]
        assert ctx.json_retries == 1

    def test_parse_error_exhausted_in_json_mode_degrades(self):
        llm = _make_llm()
        agent = ReActAgent(llm, ToolRegistry(), output=lambda s: None)
        ctx = self._ctx(llm, strategy=CallingStrategy.JSON_MODE)
        ctx.run_state.json_retries = 2  # == max_json_retries
        ctx.last_response_text = "not json"
        events = agent._on_recover(ctx, ReActEvent(E.FAULT, {
            "reason": RetryReason.PARSE_ERROR, "detail": "broken"}))
        assert [e.type for e in events] == [E.ROUND_READY]
        assert "json_mode" in llm.session_tracker.disabled_features

    def test_parse_error_exhausted_in_prompt_json_salvages(self):
        llm = _make_llm()
        agent = ReActAgent(llm, ToolRegistry(), output=lambda s: None)
        ctx = self._ctx(llm, strategy=CallingStrategy.PROMPT_JSON)
        ctx.run_state.json_retries = 2
        ctx.last_response_text = "最终答案是 42，虽然格式不对"
        events = agent._on_recover(ctx, ReActEvent(E.FAULT, {
            "reason": RetryReason.PARSE_ERROR, "detail": "broken"}))
        assert [e.type for e in events] == [E.ANSWER_READY]
