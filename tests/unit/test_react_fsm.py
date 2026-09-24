"""Isolated FSM transition tests for ReActAgent handler logic (redesigned FSM).

Uses StateMachine directly with minimal transfer tables that mirror
key ReActAgent transitions.  Handlers are defined inline to simulate
real ReActAgent behaviour without AgentLLM / ToolRegistry dependencies.

IMPORTANT: run_loop always resets state to INIT and processes the initial
event through the transition table.  Tests that need to start from a
non-INIT state use a _jump handler attached to (INIT, START) that
emits the real event for the target state.

历史：本文件曾覆盖旧 15 状态机的逐状态 handler（SELECT_STRATEGY /
PREPARE_LLM_CALL / CHECK_TOOL_CALLS / RETRY_GATE / DEGRADE ...），
Step 3 重设计后那些状态已消失，相关用例按新 6 状态形态重写或删除。
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
        Transition(S.INIT, E.START, S.AWAIT_MODEL, "_on_init"),
    ]

    def test_on_init_lands_in_await_model(self):
        ctx = ExecutionContext(question="test question")
        fsm = StateMachine(self.TABLE)
        on_init = MagicMock(return_value=[])

        answer, _ = fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_on_init": on_init},
        )

        assert fsm.current_state == ReActState.AWAIT_MODEL
        on_init.assert_called_once()
        args, _ = on_init.call_args
        assert args[0] is ctx
        assert args[1].type == ReActEventType.START


class TestReActFsmRoundLoop:
    """AWAIT_MODEL 的 auto-advance 循环契约。"""

    TABLE = [
        Transition(S.INIT, E.START, S.AWAIT_MODEL, "_jump"),
        Transition(S.AWAIT_MODEL, None, S.AWAIT_MODEL, "_on_round"),
        Transition(S.AWAIT_MODEL, E.ANSWER_READY, S.ANSWER, "_on_answer_ready"),
        Transition(S.ANSWER, None, S.DONE, "_on_emit_answer"),
    ]

    def test_round_auto_advances_until_answer_ready(self):
        """handler 返回空 → auto-advance 再次进入 _on_round，直到发出 ANSWER_READY。"""
        rounds = []

        def round_handler(ctx, event):
            rounds.append(1)
            if len(rounds) < 3:
                return []  # auto-advance 循环
            ctx.last_answer = "done"
            return [ReActEvent(ReActEventType.ANSWER_READY)]

        fsm = StateMachine(self.TABLE)
        ctx = ExecutionContext(question="test")

        answer, _ = fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_jump": MagicMock(return_value=[]),
             "_on_round": round_handler,
             "_on_answer_ready": MagicMock(return_value=[]),
             "_on_emit_answer": MagicMock(return_value=[])},
        )

        assert len(rounds) == 3
        assert answer == "done"
        assert fsm.current_state == ReActState.DONE

    def test_round_routes_tools_to_execute(self):
        TABLE = [
            Transition(S.INIT, E.START, S.AWAIT_MODEL, "_jump"),
            Transition(S.AWAIT_MODEL, None, S.AWAIT_MODEL, "_on_round"),
            Transition(S.AWAIT_MODEL, E.TOOLS_REQUESTED, S.EXECUTE_TOOL, "_on_tools_requested"),
            Transition(S.EXECUTE_TOOL, E.TOOLS_DONE, S.AWAIT_MODEL, "_on_round_advance"),
            Transition(S.AWAIT_MODEL, E.ANSWER_READY, S.ANSWER, "_on_answer_ready"),
            Transition(S.ANSWER, None, S.DONE, "_on_emit_answer"),
        ]
        calls = []

        def round_handler(ctx, event):
            calls.append("round")
            if len(calls) == 1:
                return [ReActEvent(ReActEventType.TOOLS_REQUESTED,
                                   {"tool_calls": [{"name": "read"}]})]
            ctx.last_answer = "final"
            return [ReActEvent(ReActEventType.ANSWER_READY)]

        tools_requested = MagicMock(return_value=[ReActEvent(ReActEventType.TOOLS_DONE)])

        fsm = StateMachine(TABLE)
        ctx = ExecutionContext(question="test")
        answer, _ = fsm.run_loop(
            ReActEvent(ReActEventType.START), ctx,
            {"_jump": MagicMock(return_value=[]),
             "_on_round": round_handler,
             "_on_tools_requested": tools_requested,
             "_on_round_advance": MagicMock(return_value=[]),
             "_on_answer_ready": MagicMock(return_value=[]),
             "_on_emit_answer": MagicMock(return_value=[])},
        )

        assert calls == ["round", "round"]
        tools_requested.assert_called_once()
        assert answer == "final"


class TestReActFsmEmitAnswer:
    TABLE = [
        Transition(S.INIT, E.START, S.ANSWER, "_jump"),
        Transition(S.ANSWER, None, S.DONE, "_on_emit_answer"),
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
    """fatal FAULT → RECOVER → ABORT → DONE。"""

    TABLE = [
        Transition(S.INIT, E.START, S.RECOVER, "_jump"),
        Transition(S.RECOVER, E.ABORT, S.DONE, "_on_error_abort"),
    ]

    def test_fatal_fault_aborts_through_fsm(self):
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

    def test_fail_truncated_tool_calls_marks_all_failed_without_executing(self):
        """Truncated native response: every pending tool call is failed with an
        error observation and NO tool executes (pi fail-all semantics)。"""
        agent = self._make_agent(last_truncated=True, last_reasoning_content="")
        agent._execute_tool = MagicMock()
        ctx = ExecutionContext(question="q", strategy=CallingStrategy.NATIVE_TOOLS)
        ctx.last_response_text = "Let me read the file"
        ctx.last_reasoning = ""
        ctx.current_step = 1
        ctx.steps.append(AgentStep(step_id=1))
        ctx.pending_tool_calls = [{"id": "c1", "name": "read", "arguments": {"path": "f.py"}}]

        events = agent._fail_truncated_tool_calls(ctx)

        assert [e.type for e in events] == [ReActEventType.ROUND_READY]
        agent._execute_tool.assert_not_called()
        assert ctx.pending_tool_calls == []
        tool_msgs = [m for m in ctx.messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "c1"
        assert "未执行" in tool_msgs[0]["content"]
        assistant_msgs = [m for m in ctx.messages if m.get("role") == "assistant"]
        assert assistant_msgs and assistant_msgs[-1].get("tool_calls"), \
            "assistant message must carry the failed tool_calls for a valid message sequence"

    def test_truncated_fault_routes_through_recover_on_real_table(self):
        """生产表：AWAIT_MODEL + FAULT(TRUNCATED) → RECOVER，预算内 → ROUND_READY。"""
        from agentnexus.agents.react_transitions import TRANSFER_TABLE

        agent = self._make_agent()
        ctx = ExecutionContext(question="q", strategy=CallingStrategy.JSON_MODE)
        ctx.steps.append(AgentStep(step_id=1))
        ctx.run_state.json_retries = 0
        # _on_init 被 stub，这里手动补它在真实流程中做的事
        ctx.memory_state.session_caps = agent.llm_client.session_tracker

        handlers = {t.handler: MagicMock(return_value=[]) for t in TRANSFER_TABLE}
        handlers["_on_init"] = MagicMock(return_value=[])
        rounds = {"n": 0}

        def round_once_fault_then_answer(ctx, event):
            rounds["n"] += 1
            if rounds["n"] == 1:
                return [ReActEvent(ReActEventType.FAULT,
                                   {"reason": RetryReason.TRUNCATED, "detail": "len"})]
            ctx.last_answer = "recovered"
            return [ReActEvent(ReActEventType.ANSWER_READY)]

        handlers["_on_round"] = round_once_fault_then_answer
        handlers["_on_recover"] = agent._on_recover

        fsm = StateMachine(TRANSFER_TABLE)
        answer, _ = fsm.run_loop(ReActEvent(ReActEventType.START), ctx, handlers)

        assert fsm.current_state == ReActState.DONE       # ANSWER_READY → ANSWER →(auto) DONE
        assert answer == "recovered"
        assert ctx.json_retries == 1
