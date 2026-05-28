"""Unit tests for agent lifecycle hooks (AGENT_START, AGENT_END)."""

from unittest.mock import MagicMock, patch

import pytest

from agentnexus.core.hooks import HookType, _reset_hook_manager, get_hook_manager


@pytest.fixture(autouse=True)
def clean_hooks():
    _reset_hook_manager()
    yield
    _reset_hook_manager()


class TestAgentLifecycleHooks:
    def test_agent_start_fires_on_run(self):
        mgr = get_hook_manager()
        fired = []
        mgr.register(
            HookType.AGENT_START, lambda ctx: fired.append(ctx.payload)
        )
        with patch("agentnexus.agents.re_act_agent.StateMachine") as mock_fsm:
            mock_fsm.return_value.run_loop.return_value = ("answer", [])
            from agentnexus.agents.re_act_agent import ReActAgent

            agent = ReActAgent(llm_client=MagicMock(), tool_executor=MagicMock())
            agent.run("test question")
        assert len(fired) == 1
        assert fired[0]["question"] == "test question"

    def test_agent_end_fires_on_run(self):
        mgr = get_hook_manager()
        fired = []
        mgr.register(
            HookType.AGENT_END, lambda ctx: fired.append(ctx.payload)
        )
        with patch("agentnexus.agents.re_act_agent.StateMachine") as mock_fsm:
            mock_fsm.return_value.run_loop.return_value = ("the answer", [])
            from agentnexus.agents.re_act_agent import ReActAgent

            agent = ReActAgent(llm_client=MagicMock(), tool_executor=MagicMock())
            agent.run("q")
        assert len(fired) == 1
        assert fired[0]["answer"] == "the answer"

    def test_agent_end_receives_step_count(self):
        mgr = get_hook_manager()
        fired = []
        mgr.register(
            HookType.AGENT_END, lambda ctx: fired.append(ctx.payload)
        )
        step1 = MagicMock()
        step2 = MagicMock()
        with patch("agentnexus.agents.re_act_agent.StateMachine") as mock_fsm:
            mock_fsm.return_value.run_loop.return_value = ("ans", [step1, step2])
            from agentnexus.agents.re_act_agent import ReActAgent

            agent = ReActAgent(llm_client=MagicMock(), tool_executor=MagicMock())
            agent.run("q")
        assert fired[0]["steps"] == 2

    def test_no_hooks_works_normally(self):
        with patch("agentnexus.agents.re_act_agent.StateMachine") as mock_fsm:
            mock_fsm.return_value.run_loop.return_value = ("ok", [])
            from agentnexus.agents.re_act_agent import ReActAgent

            agent = ReActAgent(llm_client=MagicMock(), tool_executor=MagicMock())
            result = agent.run("q")
        assert result.answer == "ok"
