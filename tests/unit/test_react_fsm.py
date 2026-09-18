"""Isolated FSM transition tests for ReActAgent handler logic.

Uses StateMachine directly with minimal transfer tables that mirror
key ReActAgent transitions.  Handlers are defined inline to simulate
real ReActAgent behaviour without AgentLLM / ToolRegistry dependencies.

IMPORTANT: run_loop always resets state to INIT and processes the initial
event through the transition table.  Tests that need to start from a
non-INIT state use a _jump handler attached to (INIT, START) that
emits the real event for the target state.
"""
from unittest.mock import MagicMock

import pytest

from agentnexus.agents.exceptions import AgentCancelled
from agentnexus.agents.fsm import StateMachine
from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.agents.react_types import (
    AgentStep,
    CallingStrategy,
    ExecutionContext,
    ReActEvent,
    ReActEventType,
    ReActState,
    RetryReason,
    Transition,
)

S = ReActState
E = ReActEventType


class TestReActFsmOnInit:
    TABLE = [
        Transition(S.INIT, E.START, S.SELECT_STRATEGY, "_on_init"),
    ]

    def test_on_init_transitions_to_strategy_ready(self):
        ctx = ExecutionContext(question="test question")
        fsm = StateMachine(self.TABLE)
        on_init = MagicMock(return_value=[])

        answer, _ = fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_on_init": on_init},
        )

        assert fsm.current_state == ReActState.SELECT_STRATEGY
        on_init.assert_called_once()
        args, _ = on_init.call_args
        assert args[0] is ctx
        assert args[1].type == ReActEventType.START


class TestReActFsmStrategyReady:
    TABLE = [
        Transition(S.INIT, E.START, S.SELECT_STRATEGY, "_on_init"),
        Transition(S.SELECT_STRATEGY, E.STRATEGY_READY, S.PREPARE_LLM_CALL, "_on_strategy_ready"),
    ]

    def test_strategy_ready_sets_llm_params_ready(self):
        ctx = ExecutionContext(question="test")
        fsm = StateMachine(self.TABLE)
        on_init = MagicMock(return_value=[ReActEvent(ReActEventType.STRATEGY_READY)])
        on_strategy = MagicMock(return_value=[])

        answer, _ = fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_on_init": on_init, "_on_strategy_ready": on_strategy},
        )

        assert fsm.current_state == ReActState.PREPARE_LLM_CALL
        on_init.assert_called_once()
        on_strategy.assert_called_once()


class TestReActFsmLlmParamsReady:
    TABLE = [
        Transition(S.INIT, E.START, S.PREPARE_LLM_CALL, "_jump"),
        Transition(S.PREPARE_LLM_CALL, E.LLM_PARAMS_READY, S.CALL_LLM, "_on_llm_params_ready"),
        Transition(S.CALL_LLM, E.LLM_RESPONSE, S.CALL_LLM, "sink"),
    ]

    def test_llm_params_ready_increments_step_and_calls_llm(self):
        ctx = ExecutionContext(question="test")

        def llm_params_handler(ctx, event):
            ctx.current_step += 1
            return [ReActEvent(ReActEventType.LLM_RESPONSE, {"response_text": "test response"})]

        fsm = StateMachine(self.TABLE)
        answer, _ = fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_jump": MagicMock(return_value=[ReActEvent(ReActEventType.LLM_PARAMS_READY)]),
             "_on_llm_params_ready": llm_params_handler,
             "sink": MagicMock(return_value=[])},
        )

        assert ctx.current_step == 1
        assert fsm.current_state == ReActState.CALL_LLM


class TestReActFsmReceiveNative:
    TABLE = [
        Transition(S.INIT, E.START, S.RECEIVE_RESPONSE, "_jump"),
        Transition(S.RECEIVE_RESPONSE, E.ROUTE_NATIVE, S.CHECK_TOOL_CALLS, "_on_receive_native"),
        Transition(S.CHECK_TOOL_CALLS, E.TOOLS_FOUND, S.EXECUTE_TOOL, "_on_tools_found"),
        Transition(S.CHECK_TOOL_CALLS, E.NO_TOOLS, S.EMIT_ANSWER, "_on_no_tools_answer"),
    ]

    def test_receive_native_routes_to_tools_or_answer(self):
        fsm = StateMachine(self.TABLE)
        ctx = ExecutionContext(question="test")
        ctx.pending_tool_calls = [{"name": "web_search", "arguments": {"query": "test"}}]
        ctx.last_response_text = "Let me search for that"

        tools_found = MagicMock(return_value=[])
        no_tools = MagicMock(return_value=[])

        def receive_native_handler(ctx, event):
            if ctx.pending_tool_calls:
                return [ReActEvent(ReActEventType.TOOLS_FOUND, {
                    "tool_calls": list(ctx.pending_tool_calls)})]
            ctx.last_answer = ctx.last_response_text
            return [ReActEvent(ReActEventType.NO_TOOLS, {"text": ctx.last_response_text})]

        fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_jump": MagicMock(return_value=[ReActEvent(ReActEventType.ROUTE_NATIVE)]),
             "_on_receive_native": receive_native_handler,
             "_on_tools_found": tools_found,
             "_on_no_tools_answer": no_tools},
        )

        tools_found.assert_called_once()
        no_tools.assert_not_called()

    def test_receive_native_without_tools_goes_to_answer(self):
        fsm = StateMachine(self.TABLE)
        ctx = ExecutionContext(question="test")
        ctx.pending_tool_calls = []
        ctx.last_response_text = "The answer is 42"

        tools_found = MagicMock(return_value=[])
        no_tools = MagicMock(return_value=[])

        def receive_native_handler(ctx, event):
            if ctx.pending_tool_calls:
                return [ReActEvent(ReActEventType.TOOLS_FOUND, {
                    "tool_calls": list(ctx.pending_tool_calls)})]
            ctx.last_answer = ctx.last_response_text
            return [ReActEvent(ReActEventType.NO_TOOLS, {"text": ctx.last_response_text})]

        fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_jump": MagicMock(return_value=[ReActEvent(ReActEventType.ROUTE_NATIVE)]),
             "_on_receive_native": receive_native_handler,
             "_on_tools_found": tools_found,
             "_on_no_tools_answer": no_tools},
        )

        no_tools.assert_called_once()
        tools_found.assert_not_called()


class TestReActFsmToolDone:
    TABLE = [
        Transition(S.INIT, E.START, S.EXECUTE_TOOL, "_jump"),
        Transition(S.EXECUTE_TOOL, E.TOOL_DONE, S.EXECUTE_TOOL, "_on_tool_done"),
        Transition(S.EXECUTE_TOOL, E.ALL_TOOLS_DONE, S.PREPARE_LLM_CALL, "_on_all_tools_done"),
        Transition(S.PREPARE_LLM_CALL, E.LLM_PARAMS_READY, S.PREPARE_LLM_CALL, "sink"),
    ]

    def test_tool_done_records_result_and_continues(self):
        fsm = StateMachine(self.TABLE)
        ctx = ExecutionContext(question="test")
        ctx.current_step = 1
        ctx.steps.append(AgentStep(step_id=1))

        def tool_done_handler(ctx, event):
            tc = event.payload
            step = ctx.steps[-1]
            step.tool_outputs.append({"tool": tc["name"], "output": tc["result"]})
            return [ReActEvent(ReActEventType.ALL_TOOLS_DONE)]

        all_tools_done = MagicMock(return_value=[ReActEvent(ReActEventType.LLM_PARAMS_READY)])

        answer, _ = fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_jump": MagicMock(return_value=[
                ReActEvent(ReActEventType.TOOL_DONE, {
                    "name": "read", "arguments": {"path": "f.py"},
                    "result": "file content"})]),
             "_on_tool_done": tool_done_handler,
             "_on_all_tools_done": all_tools_done,
             "sink": MagicMock(return_value=[])},
        )

        assert len(ctx.steps[-1].tool_outputs) == 1
        assert ctx.steps[-1].tool_outputs[0]["tool"] == "read"


class TestReActFsmExecNextTool:
    TABLE = [
        Transition(S.INIT, E.START, S.EXECUTE_TOOL, "_jump"),
        Transition(S.EXECUTE_TOOL, E.TOOL_DONE, S.EXECUTE_TOOL, "_on_tool_done"),
        Transition(S.EXECUTE_TOOL, E.ALL_TOOLS_DONE, S.PREPARE_LLM_CALL, "_on_all_tools_done"),
        Transition(S.PREPARE_LLM_CALL, E.LLM_PARAMS_READY, S.PREPARE_LLM_CALL, "sink"),
    ]

    def test_exec_next_tool_queued_sequential(self):
        fsm = StateMachine(self.TABLE)
        ctx = ExecutionContext(question="test")
        ctx.current_step = 1
        ctx.steps.append(AgentStep(step_id=1))
        ctx.pending_tool_calls = [
            {"name": "web_search", "arguments": {"query": "q1"}, "id": "call_1"},
            {"name": "read", "arguments": {"path": "f.py"}, "id": "call_2"},
        ]
        executed = []

        def exec_next(ctx) -> ReActEvent:
            if not ctx.pending_tool_calls:
                return ReActEvent(ReActEventType.ALL_TOOLS_DONE)
            tc = ctx.pending_tool_calls.pop(0)
            executed.append(tc["name"])
            return ReActEvent(ReActEventType.TOOL_DONE, {"name": tc["name"], "result": "ok"})

        def tool_done_handler(ctx, event):
            return [exec_next(ctx)]

        def all_tools_done_handler(ctx, event):
            return [ReActEvent(ReActEventType.LLM_PARAMS_READY)]

        fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_jump": MagicMock(return_value=[
                ReActEvent(ReActEventType.TOOL_DONE, {"name": "start", "result": ""})]),
             "_on_tool_done": tool_done_handler,
             "_on_all_tools_done": all_tools_done_handler,
             "sink": MagicMock(return_value=[])},
        )

        assert executed == ["web_search", "read"]
        assert fsm.current_state == ReActState.PREPARE_LLM_CALL


class TestReActFsmAllToolsDone:
    TABLE = [
        Transition(S.INIT, E.START, S.EXECUTE_TOOL, "_jump"),
        Transition(S.EXECUTE_TOOL, E.ALL_TOOLS_DONE, S.PREPARE_LLM_CALL, "_on_all_tools_done"),
        Transition(S.PREPARE_LLM_CALL, E.LLM_PARAMS_READY, S.PREPARE_LLM_CALL, "sink"),
    ]

    def test_all_tools_done_formats_results_for_llm_reentry(self):
        fsm = StateMachine(self.TABLE)
        ctx = ExecutionContext(question="test", strategy=CallingStrategy.NATIVE_TOOLS)
        ctx.current_step = 1
        ctx.steps.append(AgentStep(step_id=1))

        all_tools_done_hit = MagicMock(return_value=[ReActEvent(ReActEventType.LLM_PARAMS_READY)])

        fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_jump": MagicMock(return_value=[ReActEvent(ReActEventType.ALL_TOOLS_DONE)]),
             "_on_all_tools_done": all_tools_done_hit,
             "sink": MagicMock(return_value=[])},
        )

        all_tools_done_hit.assert_called_once()
        assert fsm.current_state == ReActState.PREPARE_LLM_CALL


class TestReActFsmEmitAnswer:
    TABLE = [
        Transition(S.INIT, E.START, S.EMIT_ANSWER, "_jump"),
        Transition(S.EMIT_ANSWER, None, S.DONE, "_on_emit_answer"),
    ]

    def test_on_emit_answer_finalizes_and_returns_result(self):
        fsm = StateMachine(self.TABLE)
        ctx = ExecutionContext(question="test", last_answer="The final answer is 42")

        emit_handler = MagicMock(return_value=[])

        answer, steps = fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_jump": MagicMock(return_value=[]),
             "_on_emit_answer": emit_handler},
        )

        emit_handler.assert_called_once()
        assert answer == "The final answer is 42"
        assert fsm.current_state == ReActState.DONE


class TestReActFsmErrorPropagation:
    TABLE = [
        Transition(S.INIT, E.START, S.ERROR_ABORT, "_jump"),
        Transition(S.ERROR_ABORT, E.ABORT, S.DONE, "_on_error_abort"),
    ]

    def test_llm_error_aborts_through_fsm(self):
        fsm = StateMachine(self.TABLE)
        ctx = ExecutionContext(question="test")

        error_abort_handler = MagicMock(return_value=[])

        answer, _ = fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_jump": MagicMock(return_value=[ReActEvent(ReActEventType.ABORT)]),
             "_on_error_abort": error_abort_handler},
        )

        error_abort_handler.assert_called_once()
        assert fsm.current_state == ReActState.DONE


class TestReActFsmToolErrorRecovery:
    TABLE = [
        Transition(S.INIT, E.START, S.EXECUTE_TOOL, "_jump"),
        Transition(S.EXECUTE_TOOL, E.TOOL_DONE, S.EXECUTE_TOOL, "_on_tool_done"),
        Transition(S.EXECUTE_TOOL, E.ALL_TOOLS_DONE, S.PREPARE_LLM_CALL, "_on_all_tools_done"),
        Transition(S.PREPARE_LLM_CALL, E.LLM_PARAMS_READY, S.PREPARE_LLM_CALL, "sink"),
    ]

    def test_tool_execution_error_triggers_recovery_path(self):
        fsm = StateMachine(self.TABLE)
        ctx = ExecutionContext(question="test")
        ctx.current_step = 1
        ctx.steps.append(AgentStep(step_id=1))
        ctx.pending_tool_calls = [{"name": "bash", "arguments": {"code": "invalid"}}]
        executed = []

        def exec_next(ctx) -> ReActEvent:
            if not ctx.pending_tool_calls:
                return ReActEvent(ReActEventType.ALL_TOOLS_DONE)
            tc = ctx.pending_tool_calls.pop(0)
            executed.append(tc["name"])
            return ReActEvent(
                ReActEventType.TOOL_DONE,
                {"name": tc["name"], "result": "error: tool execution failed",
                 "arguments": tc.get("arguments", {})})

        def tool_done_handler(ctx, event):
            tc = event.payload
            step = ctx.steps[-1]
            step.tool_outputs.append({"tool": tc["name"], "output": tc["result"]})
            return [exec_next(ctx)]

        def all_tools_done_handler(ctx, event):
            return [ReActEvent(ReActEventType.LLM_PARAMS_READY)]

        fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_jump": MagicMock(return_value=[
                ReActEvent(ReActEventType.TOOL_DONE, {
                    "name": "bash", "result": "start", "arguments": {}})]),
             "_on_tool_done": tool_done_handler,
             "_on_all_tools_done": all_tools_done_handler,
             "sink": MagicMock(return_value=[])},
        )

        assert len(executed) == 1
        assert executed[0] == "bash"
        assert "error" in ctx.steps[-1].tool_outputs[-1]["output"]


class TestReActAgentHardening:
    """Behavior proofs for the FSM hardening changes (cancel contract, truncation)."""

    def _make_agent(self, **llm_attrs):
        llm = MagicMock()
        for key, value in llm_attrs.items():
            setattr(llm, key, value)
        agent = ReActAgent(llm, MagicMock())
        agent._output = lambda _msg: None
        return agent

    def test_run_cancel_raises_typed(self):
        """Cancellation surfaces as AgentCancelled, not a bare RuntimeError."""
        agent = self._make_agent()
        agent.set_cancel_checker(lambda: True)

        with pytest.raises(AgentCancelled) as exc_info:
            agent.run("question")

        assert not isinstance(exc_info.value, RuntimeError)

    def test_receive_native_truncated_fails_tool_calls_without_executing(self):
        """Truncated native response: every pending tool call is failed with an
        error observation and NO tool executes (pi fail-all semantics)."""
        agent = self._make_agent(last_truncated=True, last_reasoning_content="")
        agent._execute_tool = MagicMock()
        ctx = ExecutionContext(question="q", strategy=CallingStrategy.NATIVE_TOOLS)
        ctx.last_response_text = "Let me read the file"
        ctx.last_reasoning = ""
        ctx.current_step = 1
        ctx.steps.append(AgentStep(step_id=1))
        ctx.pending_tool_calls = [{"id": "c1", "name": "read", "arguments": {"path": "f.py"}}]

        events = agent._on_receive_native(ctx, MagicMock())

        assert [e.type for e in events] == [ReActEventType.ALL_TOOLS_DONE]
        agent._execute_tool.assert_not_called()
        assert ctx.pending_tool_calls == []
        tool_msgs = [m for m in ctx.messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "c1"
        assert "未执行" in tool_msgs[0]["content"]
        assistant_msgs = [m for m in ctx.messages if m.get("role") == "assistant"]
        assert assistant_msgs and assistant_msgs[-1].get("tool_calls"), \
            "assistant message must carry the failed tool_calls for a valid message sequence"

    def test_truncated_response_routes_through_retry_gate(self):
        """Native truncation without tool_calls hits RETRY_GATE with TRUNCATED reason."""
        agent = self._make_agent()
        ctx = ExecutionContext(question="q", strategy=CallingStrategy.NATIVE_TOOLS)

        events = agent._on_truncated_response(ctx, MagicMock())

        assert events[0].type == ReActEventType.RETRIES_LEFT
        assert events[0].payload["reason"] == RetryReason.TRUNCATED

    def test_truncated_response_reaches_retry_gate_via_real_transition(self):
        """The production table wires CHECK_TOOL_CALLS + TRUNCATED_RESPONSE -> RETRY_GATE."""
        table = [
            Transition(S.INIT, E.START, S.CHECK_TOOL_CALLS, "_jump"),
            Transition(S.CHECK_TOOL_CALLS, E.TRUNCATED_RESPONSE, S.RETRY_GATE, "_on_truncated_response"),
            Transition(S.RETRY_GATE, E.RETRIES_LEFT, S.RETRY_GATE, "gate_sink"),
        ]
        agent = self._make_agent()
        ctx = ExecutionContext(question="q", strategy=CallingStrategy.NATIVE_TOOLS)
        fsm = StateMachine(table)
        fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_jump": MagicMock(return_value=[ReActEvent(ReActEventType.TRUNCATED_RESPONSE)]),
             "_on_truncated_response": agent._on_truncated_response,
             "gate_sink": MagicMock(return_value=[])},
        )
        assert fsm.current_state == ReActState.RETRY_GATE

    def test_has_content_truncated_returns_parse_error_for_retry(self):
        """JSON-path truncation is a retry signal, not a success path."""
        agent = self._make_agent(last_truncated=True)
        ctx = ExecutionContext(question="q")
        ctx.last_response_text = '{"answer": "partial'

        events = agent._on_has_content(ctx, MagicMock())

        assert events[0].type == ReActEventType.PARSE_ERROR
        assert events[0].payload["reason"] == RetryReason.TRUNCATED
