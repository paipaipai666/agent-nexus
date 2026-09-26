"""Tests for the runtime helpers that are still live after the FSM redesign.

历史：本文件原先测试 `record_llm_response`（轮次记录）与 `retry_gate`（重试门）。
Step 5 清理死代码时这两个函数被删除——轮次记录已并入 `_on_round`，重试门由
`decisions.retry_gate_decision` 承担。用例改为覆盖这两处**仍然活着**的等价逻辑。
"""
from unittest.mock import MagicMock

from agentnexus.agents import decisions
from agentnexus.agents.react_runtime import record_tool_done
from agentnexus.agents.react_types import (
    AgentStep,
    CallingStrategy,
    ExecutionContext,
    ReActEvent,
    ReActEventType,
)


def _make_llm(**attrs):
    llm = MagicMock()
    llm.last_usage = attrs.get("usage", {"input_tokens": 10, "output_tokens": 20})
    llm.last_tool_calls = attrs.get("tool_calls", [])
    llm.last_reasoning_content = attrs.get("reasoning", "reasoning_text")
    llm.last_error = ""
    llm.last_truncated = False
    return llm


class TestRoundRecording:
    """_on_round 的轮次记录（替代旧 record_llm_response 的覆盖）。"""

    def _agent(self, llm):
        from agentnexus.agents.re_act_agent import ReActAgent

        agent = ReActAgent(llm, MagicMock())
        agent._output = lambda _msg: None
        return agent

    def _drive(self, agent, ctx, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.agents.re_act_agent.call_llm",
            lambda llm, ctx, json_format_section="", on_token=None: ctx.last_response_text,
        )
        return agent._on_round(ctx, ReActEvent(ReActEventType.ROUND_READY))

    def test_creates_agent_step(self, monkeypatch):
        llm = _make_llm(tool_calls=[])
        agent = self._agent(llm)
        ctx = ExecutionContext(question="test", strategy=CallingStrategy.NATIVE_TOOLS)
        ctx.last_response_text = "the answer"

        self._drive(agent, ctx, monkeypatch)

        assert len(ctx.steps) == 1
        assert ctx.steps[0].content == "the answer"
        assert ctx.steps[0].reasoning_content == "reasoning_text"

    def test_accumulates_tokens(self, monkeypatch):
        llm = _make_llm(usage={"input_tokens": 100, "output_tokens": 200}, tool_calls=[])
        agent = self._agent(llm)
        ctx = ExecutionContext(question="test", strategy=CallingStrategy.NATIVE_TOOLS)
        ctx.last_response_text = "the answer"

        self._drive(agent, ctx, monkeypatch)

        assert ctx._total_usage["input_tokens"] == 100
        assert ctx._total_usage["output_tokens"] == 200

    def test_native_path_sets_pending_and_emits_tools_requested(self, monkeypatch):
        calls = [{"name": "web_search", "arguments": {"q": "test"}}]
        llm = _make_llm(tool_calls=calls)
        agent = self._agent(llm)
        ctx = ExecutionContext(question="test", strategy=CallingStrategy.NATIVE_TOOLS)
        ctx.last_response_text = "先查一下"

        events = self._drive(agent, ctx, monkeypatch)

        assert [e.type for e in events] == [ReActEventType.TOOLS_REQUESTED]
        assert ctx.pending_tool_calls == calls
        assert ctx.last_response_text == "先查一下"

    def test_json_path_answer_emits_answer_ready(self, monkeypatch):
        llm = _make_llm(tool_calls=[], reasoning="")
        agent = self._agent(llm)
        ctx = ExecutionContext(question="test", strategy=CallingStrategy.PROMPT_JSON)
        ctx.last_response_text = '{"answer": "final answer"}'

        events = self._drive(agent, ctx, monkeypatch)

        assert [e.type for e in events] == [ReActEventType.ANSWER_READY]
        assert ctx.last_answer == "final answer"


class TestRecordToolDone:
    def _make_ctx(self):
        ctx = ExecutionContext(question="test", current_step=1)
        ctx.steps.append(AgentStep(step_id=1))
        return ctx

    def test_appends_tool_output(self):
        ctx = self._make_ctx()

        record_tool_done(ctx, {"name": "read", "arguments": {"path": "f.py"}, "result": "content"})

        assert len(ctx.steps[-1].tool_outputs) == 1
        assert ctx.steps[-1].tool_outputs[0]["tool"] == "read"

    def test_subagent_run_parses_json_payload(self):
        ctx = self._make_ctx()
        import json
        payload_str = json.dumps({"role": "coder", "status": "ok", "answer": "done"})

        record_tool_done(ctx, {"name": "subagent_run", "arguments": {}, "result": payload_str})

        assert ctx.tool_state.last_subagent_payload is not None
        assert ctx.tool_state.last_subagent_payload["role"] == "coder"

    def test_subagent_run_invalid_json_sets_none(self):
        ctx = self._make_ctx()

        record_tool_done(ctx, {"name": "subagent_run", "arguments": {}, "result": "not json"})

        assert ctx.tool_state.last_subagent_payload is None


class TestRecoverGate:
    """重试/降档/兜底策略（替代旧 react_runtime.retry_gate 的覆盖）。"""

    def test_retries_left(self):
        assert decisions.retry_gate_decision(
            json_retries=0, max_json_retries=3,
            strategy=CallingStrategy.JSON_MODE) == "round"

    def test_no_retries_json_mode(self):
        assert decisions.retry_gate_decision(
            json_retries=2, max_json_retries=2,
            strategy=CallingStrategy.JSON_MODE) == "degrade"

    def test_fallback_text_when_not_json_mode(self):
        assert decisions.retry_gate_decision(
            json_retries=2, max_json_retries=2,
            strategy=CallingStrategy.PROMPT_JSON) == "salvage"
