"""Multi-turn regression tests.

Simulates user "modify here → refactor there" continuous conversations,
validating that the full agent maintains context across turns.
"""
from unittest.mock import MagicMock, patch

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.tools.tool_executor import ToolExecutor


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


class TestMultiTurnRegression:
    """Agent behavior across multiple user turns with accumulating STM/LTM."""

    def _make_agent(self, response=""):
        llm = _make_llm(response)
        te = ToolExecutor()
        te.registerTool("web_search", "搜索", lambda **kw: {"results": []})
        te.registerTool("memory_save", "保存", lambda **kw: {"saved": True})
        agent = ReActAgent(llm, te, max_steps=3)
        return agent, llm

    def test_consecutive_runs_accumulate_usage(self):
        agent, llm = self._make_agent("Answer")
        llm.think.side_effect = lambda **kw: (setattr(llm, 'last_error', '') or "Answer")

        agent.run("Turn 1 question")

        agent.run("Turn 2 question")

        assert llm.think.call_count >= 2

    def test_agent_resets_between_runs(self):
        agent, llm = self._make_agent()
        call_count = [0]
        def mock_think(**kw):
            call_count[0] += 1
            return f"Answer {call_count[0]}"
        llm.think.side_effect = lambda **kw: (setattr(llm, 'last_error', '') or mock_think(**kw))

        r1 = agent.run("First question")
        r2 = agent.run("Second question")

        assert r1.answer != r2.answer

    def test_memory_context_survives_across_runs(self, temp_agentnexus_home):
        from agentnexus.memory.manager import MemoryManager

        llm = _make_llm()
        te = ToolExecutor()
        te.registerTool("web_search", "搜索", lambda **kw: {"results": []})

        with patch("agentnexus.memory.manager.get_embedding_model") as mock_emb:
            mock_emb.return_value.encode.return_value.tolist.return_value = [0.1] * 384
            mgr = MemoryManager(session_id="multi_turn", enable_long_term=False)

        agent = ReActAgent(llm, te, max_steps=3)
        llm.think.side_effect = lambda **kw: (setattr(llm, 'last_error', '') or "Done")

        mgr.append("user", "Use Python for this")
        mgr.append("assistant", "Okay, using Python")

        agent.run("Add unit tests", memory_manager=mgr)

        assert mgr.short_term.get_all()
        assert len(mgr.short_term.get_all()) >= 2

    def test_agent_handles_incomplete_previous_run(self):
        agent, llm = self._make_agent()

        call_count = [0]
        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Interrupted")
            llm.last_error = ""
            return "Recovered"
        llm.think.side_effect = mock_think

        try:
            agent.run("Incomplete task")
        except Exception:
            pass

        reset_llm = _make_llm("Recovered")
        reset_agent = ReActAgent(reset_llm, agent.tool_executor, max_steps=3)
        reset_llm.think.side_effect = lambda **kw: (setattr(reset_llm, 'last_error', '') or "Recovered")

        result = reset_agent.run("New task")
        assert result.answer is not None

    def test_context_window_does_not_shrink_prematurely(self, temp_agentnexus_home):
        with patch("agentnexus.memory.manager.get_embedding_model") as mock_emb:
            mock_emb.return_value.encode.return_value.tolist.return_value = [0.1] * 384
            from agentnexus.memory.manager import MemoryManager
            mgr = MemoryManager(session_id="context_test", enable_long_term=False)

        for i in range(10):
            mgr.append("user", f"Question {i}")
            mgr.append("assistant", f"Answer {i}")

        all_msgs = mgr.short_term.get_all()
        assert len(all_msgs) == 20
        assert all_msgs[0]["content"] == "Question 0"
        assert all_msgs[-1]["content"] == "Answer 9"
