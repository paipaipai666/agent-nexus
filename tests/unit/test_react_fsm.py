"""Isolated FSM transition tests for ReActAgent handler logic.

Uses StateMachine directly with minimal transfer tables that mirror
key ReActAgent transitions.  Handlers are defined inline to simulate
real ReActAgent behaviour without AgentLLM / ToolExecutor dependencies.

IMPORTANT: run_loop always resets state to INIT and processes the initial
event through the transition table.  Tests that need to start from a
non-INIT state use a _jump handler attached to (INIT, START) that
emits the real event for the target state.
"""
from unittest.mock import MagicMock

from agentnexus.agents.fsm import StateMachine
from agentnexus.agents.react_types import (
    AgentStep,
    CallingStrategy,
    ExecutionContext,
    ReActEvent,
    ReActEventType,
    ReActState,
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
             "_on_llm_params_ready": llm_params_handler},
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
             "_on_all_tools_done": all_tools_done},
        )

        assert len(ctx.steps[-1].tool_outputs) == 1
        assert ctx.steps[-1].tool_outputs[0]["tool"] == "read"


class TestReActFsmExecNextTool:
    TABLE = [
        Transition(S.INIT, E.START, S.EXECUTE_TOOL, "_jump"),
        Transition(S.EXECUTE_TOOL, E.TOOL_DONE, S.EXECUTE_TOOL, "_on_tool_done"),
        Transition(S.EXECUTE_TOOL, E.ALL_TOOLS_DONE, S.PREPARE_LLM_CALL, "_on_all_tools_done"),
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
             "_on_all_tools_done": all_tools_done_handler},
        )

        assert executed == ["web_search", "read"]
        assert fsm.current_state == ReActState.PREPARE_LLM_CALL


class TestReActFsmAllToolsDone:
    TABLE = [
        Transition(S.INIT, E.START, S.EXECUTE_TOOL, "_jump"),
        Transition(S.EXECUTE_TOOL, E.ALL_TOOLS_DONE, S.PREPARE_LLM_CALL, "_on_all_tools_done"),
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
             "_on_all_tools_done": all_tools_done_hit},
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
             "_on_all_tools_done": all_tools_done_handler},
        )

        assert len(executed) == 1
        assert executed[0] == "bash"
        assert "error" in ctx.steps[-1].tool_outputs[-1]["output"]
