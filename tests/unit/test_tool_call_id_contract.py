"""复现：工具执行后的下一轮 LLM 调用因 tool 消息缺 tool_call_id 被 400。

线上现象（agnes-hub 等严格 OpenAI 兼容网关）：
  Error code: 400 - messages[N]: missing field `tool_call_id`
  稳定出现在「模型调用工具 → 工具返回 → 再次思考」之后。

根因假设：pending_tool_calls 的 id 为空/缺失时，
ctx.messages 里的 role:tool 要么没有 tool_call_id 键，要么是空字符串，
严格后端按 missing field 拒绝。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

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
    llm.capabilities.supports_tool_calling = True  # NATIVE
    llm.capabilities.supports_json_mode = True
    llm.capabilities.supports_json_schema = False
    llm.capabilities.supports_parallel_tool_calls = False
    llm.capabilities.thinking_effort = "none"
    return llm


def _make_agent():
    llm = _make_llm()
    te = ToolRegistry()
    te.register_tool("grep_search", "搜索", lambda **kw: "[grep_search] 未找到匹配 'hook' 的结果")
    te.register_tool("file_list", "列文件", lambda **kw: ["a.py", "b.py"])
    agent = ReActAgent(llm, te, max_steps=5)
    agent._output = lambda _msg: None
    return agent, llm


def _assert_tool_messages_valid(messages: list[dict], *, where: str) -> None:
    """OpenAI tool 协议：每条 role:tool 必须有非空 tool_call_id。"""
    for i, m in enumerate(messages):
        if m.get("role") != "tool":
            continue
        assert "tool_call_id" in m, (
            f"{where}: messages[{i}] role=tool 缺少 tool_call_id 键 → "
            f"严格后端报 missing field tool_call_id。msg={m!r}"
        )
        assert str(m.get("tool_call_id") or "").strip(), (
            f"{where}: messages[{i}] tool_call_id 为空 → "
            f"部分后端按 missing field 拒绝。msg={m!r}"
        )


class TestToolCallIdContract:
    """工具后下一轮 LLM 调用的 messages 必须满足 tool_call_id 契约。"""

    def test_native_tool_without_id_then_next_llm_call(self):
        """真实流式形状：tool_calls 无 id（或 id 空）→ 下一轮 think 不得带坏 tool 消息。"""
        agent, llm = _make_agent()
        captured: list[list[dict]] = []
        round_no = [0]

        def mock_think(**kw):
            round_no[0] += 1
            captured.append(list(kw.get("messages") or []))
            if round_no[0] == 1:
                # 与 openai_provider / JSON 协议一致：id 可能缺失
                llm.last_tool_calls = [{"name": "grep_search", "arguments": {"query": "hook"}}]
                return "用户问的是 hook 层，先搜索。"
            llm.last_tool_calls = []
            return "hook 层用于…（最终答案）"

        llm.think.side_effect = mock_think
        agent.run("详细说说 hook 层")

        assert len(captured) >= 2, "工具后必须有下一轮 LLM 调用（否则测不到 bug）"
        second = captured[1]
        tool_msgs = [m for m in second if m.get("role") == "tool"]
        assert tool_msgs, f"第二轮 messages 里应有 tool 观测，实际: {second!r}"
        _assert_tool_messages_valid(second, where="native/no-id")

    def test_native_tool_empty_id_then_next_llm_call(self):
        """流式拼装失败时 id 为 ''——严格后端同样会拒。"""
        agent, llm = _make_agent()
        captured: list[list[dict]] = []
        round_no = [0]

        def mock_think(**kw):
            round_no[0] += 1
            captured.append(list(kw.get("messages") or []))
            if round_no[0] == 1:
                llm.last_tool_calls = [{"id": "", "name": "file_list", "arguments": {"path": "."}}]
                return "列一下文件。"
            llm.last_tool_calls = []
            return "目录下有 a.py、b.py。"

        llm.think.side_effect = mock_think
        agent.run("列出文件")

        assert len(captured) >= 2
        _assert_tool_messages_valid(captured[1], where="native/empty-id")

    def test_json_protocol_tool_without_id_then_next_llm_call(self):
        """JSON 协议 tool_call 天生无 id——同样必须补全。"""
        agent, llm = _make_agent()
        # 强制 JSON 档，走 interpret_json 路径
        from agentnexus.agents.react_types import CallingStrategy

        captured: list[list[dict]] = []
        round_no = [0]

        def mock_think(**kw):
            round_no[0] += 1
            captured.append(list(kw.get("messages") or []))
            if round_no[0] == 1:
                llm.last_tool_calls = []
                return (
                    '{"thought": "先搜 hook", "tool": "grep_search", '
                    '"params": {"query": "hook"}}'
                )
            llm.last_tool_calls = []
            return "hook 层说明…"

        llm.think.side_effect = mock_think

        # interpret_json 依赖策略非 NATIVE；probe 认为支持 tool_calling 时会走 NATIVE，
        # 这里强制首轮降为 JSON 以覆盖协议 JSON 形状。
        original_select = agent._select_strategy
        agent._select_strategy = lambda caps: CallingStrategy.JSON_MODE  # type: ignore[method-assign]

        try:
            agent.run("详细说说 hook 层")
        finally:
            agent._select_strategy = original_select  # type: ignore[method-assign]

        assert len(captured) >= 2, f"JSON 协议也应有工具后二轮调用，rounds={round_no[0]}"
        _assert_tool_messages_valid(captured[1], where="json-protocol")

    def test_serialized_payload_keeps_tool_call_id(self):
        """序列化到 JSON 后字段仍必须在——对应网关 serde 的 missing field。"""
        agent, llm = _make_agent()
        captured: list[list[dict]] = []
        round_no = [0]

        def mock_think(**kw):
            round_no[0] += 1
            captured.append(list(kw.get("messages") or []))
            if round_no[0] == 1:
                llm.last_tool_calls = [{"name": "grep_search", "arguments": {"query": "hook"}}]
                return "search"
            llm.last_tool_calls = []
            return "done"

        llm.think.side_effect = mock_think
        agent.run("hook")

        assert len(captured) >= 2
        raw = json.dumps(captured[1], ensure_ascii=False)
        # 与线上报错同构：body 里每个 tool 消息都必须带 tool_call_id 键
        for i, m in enumerate(captured[1]):
            if m.get("role") != "tool":
                continue
            assert f'"tool_call_id"' in raw or "tool_call_id" in m
            assert "tool_call_id" in m and str(m["tool_call_id"]).strip(), (
                f"serialize 后 messages[{i}] 仍缺有效 tool_call_id: {m!r}"
            )
