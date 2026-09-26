"""每轮 ReAct 后原子 commit 到 SQLite；崩溃只丢未 commit 的当前轮。"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentnexus.memory.manager import MemoryManager
from agentnexus.services.turn import TurnRuntime


def _keys(msgs):
    return [(m.get("role"), m.get("content")) for m in msgs]


class TestPerRoundCommit:
    def test_commit_round_flushes_tool_observation_before_next_round(self):
        mm = MemoryManager(session_id="round-1", enable_long_term=False)
        version = MagicMock()
        turn = TurnRuntime(
            run_id="r1", session_id="round-1", question="介绍项目",
            memory_manager=mm, version_manager=version,
        )
        mm.append("user", "介绍项目")  # early-committed at send_message
        mm.append("system", "[思考过程] 先搜代码")
        mm.append("tool", "Action: read[{}]\nObservation: 文件列表")

        assert turn.commit_round() is True
        assert version.commit_with_messages.call_count == 1
        sent = version.commit_with_messages.call_args.kwargs.get("messages") or \
            version.commit_with_messages.call_args.args[0]
        roles = [m.get("role") for m in sent]
        assert "tool" in roles
        # user 问题已在 send_message 提前提交，本轮不得重复
        assert roles.count("user") == 0
        assert any("文件列表" in str(m.get("content", "")) for m in sent)

    def test_second_round_only_commits_new_rows(self):
        mm = MemoryManager(session_id="round-2", enable_long_term=False)
        version = MagicMock()
        turn = TurnRuntime(
            run_id="r1", session_id="round-2", question="q",
            memory_manager=mm, version_manager=version,
        )
        mm.append("tool", "obs-1")
        assert turn.commit_round() is True
        mm.append("tool", "obs-2")
        assert turn.commit_round() is True

        assert version.commit_with_messages.call_count == 2
        second = version.commit_with_messages.call_args_list[1].kwargs.get("messages") or \
            version.commit_with_messages.call_args_list[1].args[0]
        assert _keys(second) == [("tool", "obs-2")]

    def test_empty_round_treats_as_success_so_next_round_proceeds(self):
        mm = MemoryManager(session_id="round-3", enable_long_term=False)
        version = MagicMock()
        turn = TurnRuntime(
            run_id="r1", session_id="round-3", question="q",
            memory_manager=mm, version_manager=version,
        )
        assert turn.commit_round() is True
        assert version.commit_with_messages.call_count == 0

    def test_persist_snapshot_after_rounds_does_not_duplicate(self):
        mm = MemoryManager(session_id="round-4", enable_long_term=False)
        version = MagicMock()
        turn = TurnRuntime(
            run_id="r1", session_id="round-4", question="q",
            memory_manager=mm, version_manager=version,
        )
        mm.append("user", "q")
        mm.append("system", "[最终答案] 完整答案")
        assert turn.commit_round() is True
        turn.finish("完整答案")

        # finish → persist_snapshot 仍会 commit 一次（带 answer checkpoint）
        assert version.commit_with_messages.call_count == 2
        final = version.commit_with_messages.call_args_list[-1]
        msgs = final.kwargs.get("messages")
        if msgs is None and final.args:
            msgs = final.args[0]
        # 不得把已 commit 的行再塞回去
        assert _keys(msgs or []) == []
        answer = final.kwargs.get("answer")
        if answer is None and len(final.args) >= 3:
            answer = final.args[2]
        assert answer == "完整答案"

    def test_crash_before_commit_round_leaves_nothing_for_that_round(self):
        """中途崩溃（不调 commit_round/finish）→ 本轮不落盘。"""
        mm = MemoryManager(session_id="round-5", enable_long_term=False)
        version = MagicMock()
        TurnRuntime(
            run_id="r1", session_id="round-5", question="q",
            memory_manager=mm, version_manager=version,
        )
        mm.append("tool", "half-written")
        # 崩溃：无 commit_round、无 finish
        assert version.commit_with_messages.call_count == 0

    def test_commit_retries_then_fails_after_bound(self):
        """写失败有限次重试，耗尽后返回 False（不无限重试）。"""
        import agentnexus.services.turn as turn_mod
        mm = MemoryManager(session_id="round-retry", enable_long_term=False)
        version = MagicMock()
        version.commit_with_messages.side_effect = RuntimeError("disk full")
        turn = TurnRuntime(
            run_id="r1", session_id="round-retry", question="q",
            memory_manager=mm, version_manager=version,
        )
        mm.append("tool", "obs")

        old_sleep = turn_mod.time.sleep
        turn_mod.time.sleep = lambda _s: None
        try:
            assert turn.commit_round() is False
        finally:
            turn_mod.time.sleep = old_sleep

        assert version.commit_with_messages.call_count == turn_mod.COMMIT_MAX_ATTEMPTS
        assert "disk full" in turn.last_commit_error

    def test_commit_succeeds_after_transient_failure(self):
        mm = MemoryManager(session_id="round-retry2", enable_long_term=False)
        version = MagicMock()
        version.commit_with_messages.side_effect = [RuntimeError("busy"), "cp1"]
        turn = TurnRuntime(
            run_id="r1", session_id="round-retry2", question="q",
            memory_manager=mm, version_manager=version,
        )
        mm.append("tool", "obs")

        import agentnexus.services.turn as turn_mod
        old_sleep = turn_mod.time.sleep
        turn_mod.time.sleep = lambda _s: None
        try:
            assert turn.commit_round() is True
        finally:
            turn_mod.time.sleep = old_sleep
        assert version.commit_with_messages.call_count == 2


class TestWriteFailureBlocksNextRound:
    def test_tools_commit_failure_aborts_without_next_llm_call(self):
        from unittest.mock import MagicMock

        from agentnexus.agents.exceptions import MemoryCommitError
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.agents.react_types import (
            AgentStep,
            CallingStrategy,
            ExecutionContext,
            ReActEvent,
            ReActEventType,
        )
        from agentnexus.memory.manager import MemoryManager
        from agentnexus.tools.registry import ToolRegistry

        llm = MagicMock()
        llm.model = "m"
        llm.total_usage = {"input_tokens": 0, "output_tokens": 0}
        llm.last_error = ""
        llm.last_truncated = False
        llm.last_tool_calls = []
        llm.last_reasoning_content = ""
        llm.last_usage = {"input_tokens": 0, "output_tokens": 0}
        llm.capabilities = MagicMock()
        llm.capabilities.supports_thinking = False
        llm.capabilities.supports_tool_calling = False
        llm.capabilities.supports_json_mode = False
        llm.capabilities.thinking_effort = "none"

        te = ToolRegistry()
        te.register_tool("grep_search", "s", lambda **kw: "ok")
        agent = ReActAgent(llm, te, max_steps=5)
        agent._output = lambda _m: None

        mm = MemoryManager(session_id="block-round", enable_long_term=False)

        def bad_persist():
            raise MemoryCommitError("记忆写入失败: disk full")

        agent.set_round_persist(bad_persist)

        ctx = ExecutionContext(question="q", strategy=CallingStrategy.PROMPT_JSON)
        ctx.memory_state.memory_manager = mm
        ctx.tool_state.pending_tool_calls = [
            {"id": "c1", "name": "grep_search", "arguments": {}},
        ]
        ctx.steps.append(AgentStep(step_id=1))

        events = agent._on_tools_requested(ctx, ReActEvent(
            ReActEventType.TOOLS_REQUESTED,
            {
                "tool_calls": list(ctx.tool_state.pending_tool_calls),
                "thought": "",
                "strategy": "PROMPT_JSON",
            },
        ))
        assert [e.type for e in events] == [ReActEventType.FAULT]
        assert events[0].payload.get("fatal") is True
        assert "记忆写入失败" in events[0].payload.get("detail", "")
