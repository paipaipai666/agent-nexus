"""复现：工具/LLM 失败后下一轮「继续」丢失上下文。

期望：上一问 + 工具观测必须出现在下一轮构建的对话上下文里。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.agents.react_types import CallingStrategy
from agentnexus.tools.registry import ToolRegistry


def _make_llm():
    llm = MagicMock()
    llm.model = "test/model"
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


def _make_agent(llm):
    te = ToolRegistry()
    te.register_tool("file_list", "列文件", lambda **kw: "[ToolErrorCode.VALIDATION_FAILED] 路径越界")
    agent = ReActAgent(llm, te, max_steps=5, conversation_mode=True)
    agent._output = lambda _m: None
    return agent


class TestContextSurvivesFailedTurn:
    def test_after_tool_then_llm_error_continue_sees_prior_context(self, monkeypatch):
        """工具成功 → 下一轮 LLM 400 → 新 run('继续') 必须看到上一问与 Observation。"""
        from agentnexus.memory.manager import MemoryManager

        llm = _make_llm()
        agent = _make_agent(llm)
        mm = MemoryManager(session_id="s-continue", enable_long_term=False)

        captured_convs = []
        real_build = agent._build_conversation_context

        def spy_build(mgr=None):
            ctx = real_build(mgr)
            captured_convs.append(ctx)
            return ctx

        agent._build_conversation_context = spy_build  # type: ignore

        # ── 第一轮：调用工具，然后 LLM 在下一次 think 时 400 ──
        rounds = [0]

        def think_round1(**kw):
            rounds[0] += 1
            if rounds[0] == 1:
                llm.last_tool_calls = [{"name": "file_list", "arguments": {"path": "D:\\code\\AgentNexus"}}]
                return "先列目录。"
            llm.last_error = "Error code: 400 - missing field tool_call_id"
            llm.last_tool_calls = []
            return ""

        llm.think.side_effect = think_round1
        try:
            agent.run("详细说说 hook 层", memory_manager=mm)
        except Exception:
            pass  # 400 可能以空答案/异常结束

        # 工具观测应已进 STM
        stm_all = mm.short_term.get_all()
        stm_dump = [(m.get("role"), (m.get("content") or "")) for m in stm_all]
        assert any(r == "user" and "hook" in c for r, c in stm_dump), [(r, c[:60]) for r, c in stm_dump]
        assert any(r == "tool" and "路径越界" in c for r, c in stm_dump), [(r, c[:80]) for r, c in stm_dump]

        # ── 第二轮：用户只说「继续」──
        llm2 = _make_llm()
        agent2 = ReActAgent(llm2, ToolRegistry(), max_steps=5, conversation_mode=True)
        agent2._output = lambda _m: None

        second_messages = []

        def think_round2(**kw):
            second_messages.append(list(kw.get("messages") or []))
            llm2.last_tool_calls = []
            return "继续 hook 层说明……"

        llm2.think.side_effect = think_round2
        agent2.run("继续", memory_manager=mm)

        assert second_messages, "第二轮必须发起 LLM 调用"
        flat = "\n".join(
            str(m.get("content") or "") for m in second_messages[0]
        )
        assert "hook" in flat or "路径越界" in flat, (
            "第二轮上下文丢失：未包含上一问或工具观测。\n"
            f"conv_ctx 尾部: {captured_convs[-1][:300]!r}\n"
            f"messages 片段: {flat[:500]!r}"
        )

    def test_conversation_context_includes_failed_tool_observation(self):
        from agentnexus.memory.manager import MemoryManager

        llm = _make_llm()
        agent = _make_agent(llm)
        mm = MemoryManager(session_id="s-conv", enable_long_term=False)
        mm.append("user", "详细说说 hook 层")
        mm.append("tool", "Action: file_list[{\"path\": \"x\"}]\nObservation: [ToolErrorCode.VALIDATION_FAILED] 路径越界")
        # 无最终答案（失败轮）

        conv = agent._build_conversation_context(mm)
        assert "hook" in conv
        assert "路径越界" in conv or "file_list" in conv
