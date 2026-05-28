from unittest.mock import MagicMock

from agentnexus.agents.react_runtime import record_llm_response, record_tool_done, retry_gate
from agentnexus.agents.react_types import CallingStrategy, ExecutionContext, ReActEventType


class TestRecordLlmResponse:
    def _make_ctx(self, *, strategy=CallingStrategy.NATIVE_TOOLS, current_step=0):
        ctx = ExecutionContext(
            question="test",
            strategy=strategy,
            current_step=current_step,
        )
        return ctx

    def _make_llm_client(self, *, usage=None, tool_calls=None, reasoning="reasoning_text"):
        client = MagicMock()
        client.last_usage = usage or {"input_tokens": 10, "output_tokens": 20}
        client.last_tool_calls = tool_calls
        client.last_reasoning_content = reasoning
        return client

    def test_creates_agent_step(self):
        ctx = self._make_ctx()
        llm = self._make_llm_client(tool_calls=[{"name": "t", "arguments": {}}])

        record_llm_response(ctx, response_text="response", llm_client=llm)

        assert len(ctx.steps) == 1
        assert ctx.steps[0].content == "response"
        assert ctx.steps[0].reasoning_content == "reasoning_text"

    def test_accumulates_tokens(self):
        ctx = self._make_ctx()
        llm = self._make_llm_client(
            usage={"input_tokens": 100, "output_tokens": 200},
            tool_calls=[{"name": "t", "arguments": {}}],
        )

        record_llm_response(ctx, response_text="r", llm_client=llm)

        assert ctx._total_usage["input_tokens"] == 100
        assert ctx._total_usage["output_tokens"] == 200

    def test_routes_native(self):
        ctx = self._make_ctx(strategy=CallingStrategy.NATIVE_TOOLS)
        llm = self._make_llm_client(tool_calls=[{"name": "t", "arguments": {}}])

        events = record_llm_response(ctx, response_text="r", llm_client=llm)

        assert len(events) == 1
        assert events[0].type == ReActEventType.ROUTE_NATIVE

    def test_routes_json(self):
        ctx = self._make_ctx(strategy=CallingStrategy.JSON_MODE)
        llm = self._make_llm_client(tool_calls=None)

        events = record_llm_response(ctx, response_text="r", llm_client=llm)

        assert len(events) == 1
        assert events[0].type == ReActEventType.ROUTE_JSON

    def test_sets_pending_tool_calls_native(self):
        ctx = self._make_ctx(strategy=CallingStrategy.NATIVE_TOOLS)
        calls = [{"name": "web_search", "arguments": {"q": "test"}}]
        llm = self._make_llm_client(tool_calls=calls)

        record_llm_response(ctx, response_text="r", llm_client=llm)

        assert ctx.pending_tool_calls == calls

    def test_sets_last_response_text(self):
        ctx = self._make_ctx()
        llm = self._make_llm_client(tool_calls=[])

        record_llm_response(ctx, response_text="final answer", llm_client=llm)

        assert ctx.last_response_text == "final answer"


class TestRecordToolDone:
    def _make_ctx(self):
        ctx = ExecutionContext(question="test", current_step=1)
        from agentnexus.agents.react_types import AgentStep
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


class TestRetryGate:
    def _make_ctx(self, *, json_retries=0, max_json_retries=2, strategy=CallingStrategy.JSON_MODE):
        ctx = ExecutionContext(
            question="test",
            strategy=strategy,
            json_retries=json_retries,
            max_json_retries=max_json_retries,
        )
        return ctx

    def test_retries_left(self):
        ctx = self._make_ctx(json_retries=0, max_json_retries=3)

        events = retry_gate(ctx, "parse error")

        assert len(events) == 1
        assert events[0].type == ReActEventType.RETRIES_LEFT

    def test_no_retries_json_mode(self):
        ctx = self._make_ctx(json_retries=2, max_json_retries=2, strategy=CallingStrategy.JSON_MODE)

        events = retry_gate(ctx, "parse error")

        assert len(events) == 1
        assert events[0].type == ReActEventType.NO_RETRIES

    def test_fallback_text_when_not_json_mode(self):
        ctx = self._make_ctx(json_retries=2, max_json_retries=2, strategy=CallingStrategy.PROMPT_JSON)

        events = retry_gate(ctx, "parse error")

        assert len(events) == 1
        assert events[0].type == ReActEventType.FALLBACK_TEXT

    def test_payload_contains_reason(self):
        ctx = self._make_ctx(json_retries=0, max_json_retries=2)

        events = retry_gate(ctx, "bad format")

        assert events[0].payload["reason"] == "bad format"
