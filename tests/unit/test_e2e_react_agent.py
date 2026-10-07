"""End-to-end ReAct Agent benchmark test.

Runs the full ReAct agent through multi-step tasks with mocked LLM,
validating intermediate state assertions (tool calls, observations, reasoning steps).
"""
from unittest.mock import MagicMock, call

import pytest

from agentnexus.agents.exceptions import AgentCancelled
from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.memory.todo import SessionTodoList
from agentnexus.tools.registry import ToolRegistry, ToolMeta


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
    llm.think.return_value = ""
    return llm


class TestE2EReActAgent:
    """End-to-end ReAct agent with intermediate state assertions."""

    def _make_agent(self):
        llm = _make_llm()
        te = ToolRegistry()
        te.register(
            ToolMeta(
                name="web_search",
                description="搜索",
                param_schema={"type": "object", "properties": {}},
            ),
            lambda **kw: {"results": [{"title": "Python"}]},
        )
        te.register(
            ToolMeta(
                name="file_read",
                description="读文件",
                param_schema={"type": "object", "properties": {}},
            ),
            lambda **kw: "file content",
        )
        agent = ReActAgent(llm, te, max_steps=5)
        return agent, llm

    def test_e2e_single_step_answer(self):
        agent, llm = self._make_agent()

        def mock_think(**kw):
            llm.last_tool_calls = []
            return "The answer is 42"
        llm.think.side_effect = mock_think

        result = agent.run("What is 6*7?")
        assert result.answer is not None
        assert "42" in result.answer

    def test_cancel_checker_stops_run(self):
        agent, llm = self._make_agent()
        agent.set_cancel_checker(lambda: True)

        with pytest.raises(AgentCancelled):
            agent.run("stop")

        llm.think.assert_not_called()

    def test_e2e_tool_then_answer(self):
        agent, llm = self._make_agent()
        call_count = [0]

        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [{"name": "web_search", "arguments": {"query": "Python"}}]
                return "Searching..."
            llm.last_tool_calls = []
            return "Python is a high-level language."
        llm.think.side_effect = mock_think

        result = agent.run("What is Python?")
        assert call_count[0] >= 1
        assert result.answer is not None

    def test_persists_final_answer_thought_before_answer(self):
        agent, llm = self._make_agent()
        llm.capabilities.supports_tool_calling = False
        memory = MagicMock()
        memory.init_session.return_value = ""
        memory.short_term.get_all.return_value = []
        call_count = [0]

        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return '{"thought":"Need to search first.","tool":"web_search","params":{"query":"Python"}}'
            return '{"thought":"Now I can answer from the search result.","answer":"Python is a language."}'

        llm.think.side_effect = mock_think

        agent.run("What is Python?", memory_manager=memory)

        assert call("assistant", "Now I can answer from the search result.", metadata={"display_only": True}) in memory.append.call_args_list
        assert call("system", "[最终答案] Python is a language.") in memory.append.call_args_list
        final_thought_index = memory.append.call_args_list.index(
            call("assistant", "Now I can answer from the search result.", metadata={"display_only": True})
        )
        final_answer_index = memory.append.call_args_list.index(call("system", "[最终答案] Python is a language."))
        assert final_thought_index < final_answer_index
        assert memory.append.call_args_list.count(
            call("assistant", "Now I can answer from the search result.", metadata={"display_only": True})
        ) == 1
        assert memory.append.call_args_list.count(
            call("system", "[最终答案] Python is a language.")
        ) == 1

    def test_e2e_multiple_tools(self):
        agent, llm = self._make_agent()
        call_count = [0]

        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [
                    {"name": "web_search", "arguments": {"query": "test"}},
                    {"name": "file_read", "arguments": {"path": "f.py"}},
                ]
                return "Searching..."
            llm.last_tool_calls = []
            return "Combined result."
        llm.think.side_effect = mock_think

        agent.run("Search and read")
        assert call_count[0] >= 1

    def test_e2e_max_steps_respected(self):
        """Agent respects max_steps and terminates quickly."""
        agent, llm = self._make_agent()
        agent.max_steps = 1
        call_count = [0]

        def mock_think(**kw):
            call_count[0] += 1
            llm.last_tool_calls = []
            return "Answer"
        llm.think.side_effect = mock_think

        result = agent.run("Question")
        assert call_count[0] <= 1
        assert result.answer is not None

    def test_e2e_execution_context_accumulates(self):
        agent, llm = self._make_agent()
        llm.think.side_effect = lambda **kw: (setattr(llm, 'last_tool_calls', []) or "Answer")

        agent.run("Test question")
        assert llm.think.called

    def test_e2e_error_handling(self):
        agent, llm = self._make_agent()
        llm.last_error = "Connection failed"
        llm.think.return_value = ""
        llm.think.side_effect = lambda **kw: ""

        result = agent.run("Trigger error")
        assert result.answer is None or isinstance(result.answer, str)


class TestTodoTerminateSignal:
    """显式终止信号（pi 风格）：只有模型用 todo_update 关闭全部 todo，
    且同响应携带非空可见文本，批处理后才直接收尾。

    回归 2026-10-07 事故：旧启发式（长文本 + 全是记账工具）把模型
    更新 todo 时的计划旁白当成最终答案，run 提前结束、子代理从未启动。
    """

    def _make_agent(self):
        llm = _make_llm()
        te = ToolRegistry()
        todo_list = SessionTodoList()

        def _todo_add(description: str) -> str:
            item = todo_list.add(description)
            return f"Added todo #{item.id}: {item.description}"

        def _todo_update(item_id: int, status: str) -> str:
            item = todo_list.update(item_id, status)
            return f"Updated todo #{item.id}: {item.status}"

        te.register(
            ToolMeta(name="todo_add", description="todo", param_schema={"type": "object", "properties": {}}),
            _todo_add,
        )
        te.register(
            ToolMeta(name="todo_update", description="todo", param_schema={"type": "object", "properties": {}}),
            _todo_update,
        )
        te.register(
            ToolMeta(name="file_list", description="list", param_schema={"type": "object", "properties": {}}),
            lambda **kw: "[目录] . (24 项)",
        )
        te.register(
            ToolMeta(name="subagent_run", description="delegate", param_schema={"type": "object", "properties": {}}),
            lambda **kw: "subagent result",
        )
        agent = ReActAgent(llm, te)
        agent._todo_list = todo_list
        return agent, llm

    def _scripted(self, llm, script):
        def mock_think(**kw):
            idx = min(len(llm.think.call_args_list) - 1, len(script) - 1)
            item = script[idx]
            llm.last_tool_calls = item["tool_calls"]
            llm.last_error = ""
            return item["text"]
        llm.think.side_effect = mock_think

    def test_todo_add_batch_with_plan_text_does_not_finish_early(self):
        # 事故复现路径：todo_add 批 + 计划旁白（旧启发式会误判为最终答案）
        agent, llm = self._make_agent()
        plan_text = ("The project is fairly large and has multiple modules. "
                     "I'll check the packaging configuration myself first, then "
                     "dispatch sub-agents to explore in parallel.")
        self._scripted(llm, [
            {"tool_calls": [{"name": "file_list", "arguments": {"path": "."}}],
             "text": "Let me look at the root."},
            {"tool_calls": [{"name": "todo_add", "arguments": {"description": f"调研模块 {i}"}} for i in range(3)],
             "text": plan_text},
            {"tool_calls": [{"name": "subagent_run", "arguments": {"task": "explore"}}],
             "text": "Dispatching subagents now."},
            {"tool_calls": [], "text": "Real final answer."},
        ])

        result = agent.run("详细介绍这个项目")
        assert llm.think.call_count == 4, "计划旁白不得触发提前收尾"
        assert result.answer == "Real final answer."

    def test_all_todos_done_with_answer_text_terminates(self):
        # 显式信号：本批 todo_update 关闭全部 todo → 同响应文本即最终答案，
        # 不必再开一轮（d99d823f 想保留的收益，改由信号驱动）
        agent, llm = self._make_agent()
        self._scripted(llm, [
            {"tool_calls": [{"name": "todo_add", "arguments": {"description": "任务A"}},
                            {"name": "todo_add", "arguments": {"description": "任务B"}}],
             "text": "我先分解一下任务。"},
            {"tool_calls": [{"name": "file_list", "arguments": {"path": "."}}],
             "text": "先做任务A。"},
            {"tool_calls": [{"name": "todo_update", "arguments": {"item_id": 1, "status": "done"}},
                            {"name": "todo_update", "arguments": {"item_id": 2, "status": "done"}}],
             "text": "两个任务都完成了，这是完整最终答案。"},
            {"tool_calls": [], "text": "多余的一轮。"},
        ])

        result = agent.run("做任务")
        assert llm.think.call_count == 3, "全部 todo done + 答案文本应直接收尾"
        assert result.answer == "两个任务都完成了，这是完整最终答案。"

    def test_all_todos_done_with_empty_text_continues(self):
        # 信号存在但没有答案文本 → 不收尾，下一轮正常给答案
        agent, llm = self._make_agent()
        self._scripted(llm, [
            {"tool_calls": [{"name": "todo_add", "arguments": {"description": "任务A"}}],
             "text": "分解任务。"},
            {"tool_calls": [{"name": "todo_update", "arguments": {"item_id": 1, "status": "done"}}],
             "text": ""},
            {"tool_calls": [], "text": "真正的最终答案。"},
        ])

        result = agent.run("做任务")
        assert llm.think.call_count == 3
        assert result.answer == "真正的最终答案。"

    def test_todo_update_without_closing_all_continues(self):
        # 只关闭部分 todo → 不收尾
        agent, llm = self._make_agent()
        self._scripted(llm, [
            {"tool_calls": [{"name": "todo_add", "arguments": {"description": "任务A"}},
                            {"name": "todo_add", "arguments": {"description": "任务B"}}],
             "text": "分解任务。"},
            {"tool_calls": [{"name": "todo_update", "arguments": {"item_id": 1, "status": "done"}}],
             "text": "任务A完成，继续任务B，这是很长的进度说明文本。"},
            {"tool_calls": [], "text": "最终答案。"},
        ])

        result = agent.run("做任务")
        assert llm.think.call_count == 3
        assert result.answer == "最终答案。"
