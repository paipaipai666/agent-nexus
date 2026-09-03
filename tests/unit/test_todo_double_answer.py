"""Bug reproduction: bookkeeping tool calls (todo_*) force a duplicate final answer.

Native-tools mode lets the model return tool_calls AND final-answer text in one
response. When the tool calls are pure bookkeeping (todo_update — results cannot
change the answer), the FSM should end the run with the accompanying text.
Current behavior: the text is downgraded to "thought", the tool executes, and
the FSM loops back to CALL_LLM, forcing the model to write the final answer a
second time.
"""
from unittest.mock import MagicMock

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.tools.registry import ToolRegistry


def _make_llm():
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
    return llm


def _make_agent():
    llm = _make_llm()
    te = ToolRegistry()
    te.register_tool("todo_update", "更新待办", lambda **kw: "ok")
    te.register_tool("web_search", "搜索", lambda **kw: {"results": ["data"]})
    return ReActAgent(llm, te, max_steps=5), llm


class TestBookkeepingToolDoubleAnswer:
    def test_bookkeeping_tool_with_final_text_needs_no_second_round(self):
        """RED: todo_update + final text in one response should terminate the run.

        Currently fails: the FSM discards the text and loops for round 2.
        """
        agent, llm = _make_agent()
        rounds = []

        def mock_think(**kw):
            rounds.append(1)
            if len(rounds) == 1:
                # Work is done; model marks the todo AND states the final answer
                llm.last_tool_calls = [
                    {"name": "todo_update", "arguments": {"id": "1", "status": "done"}, "id": "c1"}
                ]
                return "最终答案：任务已全部完成。报告包含三个章节的分析结果，已保存到输出目录。"
            # Second round only happens because the FSM forced it
            llm.last_tool_calls = []
            return "最终答案：任务已全部完成。报告包含三个章节的分析结果，已保存到输出目录。（被迫重复的第二遍）"

        llm.think.side_effect = mock_think

        result = agent.run("完成任务")

        assert len(rounds) == 1, (
            f"BUG: agent needed {len(rounds)} LLM rounds — the final answer text "
            "accompanying the bookkeeping tool call was discarded"
        )
        assert result.answer == "最终答案：任务已全部完成。报告包含三个章节的分析结果，已保存到输出目录。"

    def test_content_tool_still_loops_for_observation(self):
        """Guard: text + a content-producing tool (web_search) MUST loop —
        the observation can change the answer, so the fast path must not fire."""
        agent, llm = _make_agent()
        rounds = []

        def mock_think(**kw):
            rounds.append(1)
            if len(rounds) == 1:
                llm.last_tool_calls = [
                    {"name": "web_search", "arguments": {"query": "q"}, "id": "c1"}
                ]
                return "我先搜一下。"
            llm.last_tool_calls = []
            return "基于搜索结果的答案。"

        llm.think.side_effect = mock_think

        result = agent.run("搜索并回答")

        assert len(rounds) == 2
        assert result.answer == "基于搜索结果的答案。"

    def test_mixed_bookkeeping_and_content_tool_still_loops(self):
        """Guard: any non-bookkeeping tool in the batch disables the fast path."""
        agent, llm = _make_agent()
        rounds = []

        def mock_think(**kw):
            rounds.append(1)
            if len(rounds) == 1:
                llm.last_tool_calls = [
                    {"name": "todo_update", "arguments": {"id": "1", "status": "done"}, "id": "c1"},
                    {"name": "web_search", "arguments": {"query": "q"}, "id": "c2"},
                ]
                return "差不多完成了。"
            llm.last_tool_calls = []
            return "真正的最终答案。"

        llm.think.side_effect = mock_think

        result = agent.run("混合调用")

        assert len(rounds) == 2
        assert result.answer == "真正的最终答案。"
