"""截断/异常结束时，已生成的回复必须进 STM，下一轮才能看到。"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.agents.react_types import CallingStrategy
from agentnexus.memory.manager import MemoryManager
from agentnexus.tools.registry import ToolRegistry


def _llm():
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
    llm.capabilities.supports_json_schema = False
    llm.capabilities.supports_parallel_tool_calls = False
    llm.capabilities.thinking_effort = "none"
    return llm


class TestTruncatedAnswerPersistsToStm:
    def test_salvaged_truncated_answer_lands_in_stm(self):
        """截断后 salvage 出的答案必须写入 STM（[最终答案] 或 assistant）。"""
        llm = _llm()
        agent = ReActAgent(llm, ToolRegistry(), max_steps=3, conversation_mode=True)
        agent._output = lambda _m: None
        mm = MemoryManager(session_id="trunc-stm", enable_long_term=False)

        # 每次都「截断」且只吐半截正文 → 走续写/兜底
        llm.think.side_effect = lambda **kw: (
            setattr(llm, "last_truncated", True) or "AgentNexus 是一个框架，配备完整"
        )
        result = agent.run("介绍 AgentNexus 项目", memory_manager=mm)

        contents = " | ".join(m.get("content") or "" for m in mm.short_term.get_all())
        assert "介绍 AgentNexus 项目" in contents
        assert "AgentNexus" in contents, f"截断正文未入 STM: {contents!r}"
        # 最终答案应可被下一轮对话上下文捞到
        conv = agent._build_conversation_context(mm)
        assert "AgentNexus" in conv
        assert "介绍" in conv or "AgentNexus" in conv

    def test_next_run_sees_truncated_reply_in_conversation_context(self):
        llm = _llm()
        agent = ReActAgent(llm, ToolRegistry(), max_steps=3, conversation_mode=True)
        agent._output = lambda _m: None
        mm = MemoryManager(session_id="trunc-ctx", enable_long_term=False)

        llm.think.side_effect = lambda **kw: (
            setattr(llm, "last_truncated", True) or "AgentNexus 是一个 Python 实现的框架，配备完整"
        )
        agent.run("介绍 AgentNexus 项目", memory_manager=mm)

        llm2 = _llm()
        agent2 = ReActAgent(llm2, ToolRegistry(), max_steps=3, conversation_mode=True)
        agent2._output = lambda _m: None
        captured = []

        def think2(**kw):
            captured.append(list(kw.get("messages") or []))
            llm2.last_truncated = False
            return "上文已经介绍过框架。"

        llm2.think.side_effect = think2
        agent2.run("怎么话没说完？", memory_manager=mm)

        assert captured
        flat = "\n".join(str(m.get("content") or "") for m in captured[0])
        assert "介绍 AgentNexus 项目" in flat or "配备完整" in flat or "AgentNexus" in flat, (
            f"第二轮看不到被截断的上文: {flat[:800]!r}"
        )
