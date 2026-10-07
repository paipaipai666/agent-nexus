"""显式终止信号守卫（2026-10-07 决策3，取代旧的 fast-path 启发式）。

旧契约（已废除）：批内全是记账工具 + 长文本 → 猜文本是最终答案。该启发式
把模型更新 todo 时的计划旁白误判为答案，导致 run 提前结束（2026-10-07 事故，
session 7774923c1358：子代理从未启动）。

新契约：终止只认显式信号 —— 本批含 todo_update 且清单非空全部 done 且同响应
携带非空可见文本（模型显式关闭全部 todo = 显式收尾声明）。批内是否混有内容
工具不影响该信号；无 todo_list 时信号不存在，一律正常循环。
"""
from unittest.mock import MagicMock

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
    return llm


def _make_agent():
    llm = _make_llm()
    te = ToolRegistry()
    todo_list = SessionTodoList()
    todo_list.add("任务")
    te.register(
        ToolMeta(
            name="todo_update",
            description="更新待办",
            param_schema={"type": "object", "properties": {}},
        ),
        lambda item_id, status, **kw: todo_list.update(item_id, status) and "ok",
    )
    te.register(
        ToolMeta(
            name="web_search",
            description="搜索",
            param_schema={"type": "object", "properties": {}},
        ),
        lambda **kw: {"results": ["data"]},
    )
    agent = ReActAgent(llm, te, max_steps=5)
    agent._todo_list = todo_list
    return agent, llm


class TestExplicitTerminateSignal:
    def test_all_todos_done_with_final_text_needs_no_second_round(self):
        """todo_update 关闭全部 todo + 同响应最终答案文本 → 直接收尾（一轮）。"""
        agent, llm = _make_agent()
        rounds = []

        def mock_think(**kw):
            rounds.append(1)
            if len(rounds) == 1:
                llm.last_tool_calls = [
                    {"name": "todo_update", "arguments": {"item_id": 1, "status": "done"}, "id": "c1"}
                ]
                return "最终答案：任务已全部完成。报告包含三个章节的分析结果，已保存到输出目录。"
            llm.last_tool_calls = []
            return "最终答案：任务已全部完成。（被迫重复的第二遍）"

        llm.think.side_effect = mock_think

        result = agent.run("完成任务")

        assert len(rounds) == 1
        assert result.answer == "最终答案：任务已全部完成。报告包含三个章节的分析结果，已保存到输出目录。"

    def test_content_tool_still_loops_for_observation(self):
        """守卫：内容工具（web_search）的 observation 可能改变答案，
        无 todo 信号时必须继续循环。"""
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

    def test_mixed_batch_with_all_todos_done_terminates(self):
        """新契约：信号来自 todo 状态而非批次纯度 —— 混合批（web_search +
        todo_update 全 done）+ 答案文本同样收尾。"""
        agent, llm = _make_agent()
        rounds = []

        def mock_think(**kw):
            rounds.append(1)
            if len(rounds) == 1:
                llm.last_tool_calls = [
                    {"name": "todo_update", "arguments": {"item_id": 1, "status": "done"}, "id": "c1"},
                    {"name": "web_search", "arguments": {"query": "q"}, "id": "c2"},
                ]
                return "搜索核验完毕，任务全部完成，这是完整最终答案。"
            llm.last_tool_calls = []
            return "被迫重复的第二遍。"

        llm.think.side_effect = mock_think

        result = agent.run("混合调用")

        assert len(rounds) == 1
        assert result.answer == "搜索核验完毕，任务全部完成，这是完整最终答案。"
