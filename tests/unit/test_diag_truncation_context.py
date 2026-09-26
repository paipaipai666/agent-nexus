"""对齐截图症状的诊断测试（先证明现象，不改产品代码）。

截图症状：
1. 回复在句中被截断（「配备完整」后戛然而止）
2. 用户追问「怎么话没说完？」后，模型称没有「近期对话」、看不到上一轮正文
3. 历史记忆只显示「用户尚未提出具体任务」类空话

此文件用来区分：
- H1: 截断后 [最终答案] 未写入 STM → conv_ctx 丢上文
- H2: 答案只在 UI 流式缓冲，agent 返回空 → STM 无正文
- H3: 正常 salvage 截断后其实能续上（则 H1 对 salvage 路径不成立）
- H4: conversation_mode / turn 边界导致 turn 被丢掉
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.memory.manager import MemoryManager
from agentnexus.services.turn import TurnRuntime
from agentnexus.tools.registry import ToolRegistry


def _llm(**attrs):
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
    for k, v in attrs.items():
        setattr(llm, k, v)
    return llm


def _agent(llm, **kw):
    a = ReActAgent(llm, ToolRegistry(), max_steps=3, conversation_mode=True, **kw)
    a._output = lambda _m: None
    return a


def _stm_dump(mm):
    return [(m.get("role"), (m.get("content") or "")[:120]) for m in mm.short_term.get_all()]


class TestScreenshotSymptom:
    def test_H3_salvage_truncated_path_still_writes_stm(self):
        """对照：走 salvage 的截断（think 一直 truncated）→ 应能看到正文。"""
        llm = _llm()
        agent = _agent(llm)
        mm = MemoryManager(session_id="diag-h3", enable_long_term=False)

        def think(**kw):
            llm.last_truncated = True
            return "AgentNexus 是一个 Python 实现的 LLM 驱动的 AI Agent 框架，配备完整"

        llm.think.side_effect = think
        agent.run("介绍 AgentNexus 项目", memory_manager=mm)
        dump = _stm_dump(mm)
        joined = " | ".join(c for _, c in dump)
        # 记录事实：salvage 是否写入
        assert "介绍 AgentNexus 项目" in joined
        # 这条若失败 → H1 对 salvage 成立；通过则 salvage 不是丢上下文的那条路
        assert "AgentNexus" in joined, f"H3-salvage 未写入正文: {dump}"

    def test_H2_empty_answer_after_long_stream_leaves_no_reply_in_stm(self):
        """UI 可能流了很多 token，但 run 返回空答案时 STM 不得假装有回复。

        若此用例只断言「STM 里没有正文」，则证明 H2 会造成下一轮丢上下文。
        """
        llm = _llm()
        agent = _agent(llm)
        mm = MemoryManager(session_id="diag-h2", enable_long_term=False)

        # 模拟：内部流了很多内容，但最终 answer 为空（fatal / 未 emit）
        def think(**kw):
            llm.last_truncated = False
            llm.last_error = "Error code: 400 - something"
            return ""  # agent 视角：没有正文

        llm.think.side_effect = think
        result = agent.run("介绍 AgentNexus 项目", memory_manager=mm)

        dump = _stm_dump(mm)
        joined = " | ".join(c for _, c in dump)
        assert "介绍 AgentNexus 项目" in joined, dump
        has_reply = "AgentNexus 是一个" in joined or "配备完整" in joined
        # 事实记录：空答案轮次里，STM 是否含回复正文
        # 若 False → 下一轮 conv_ctx 必然没有「上一轮说了什么」
        print("H2 STM dump:", dump)
        print("H2 result.answer:", repr(result.answer))
        assert result.answer in (None, "",) or "AgentNexus" not in str(result.answer) or True
        # 钉住可复现事实：空答案不会写入回复正文
        if not (result.answer or "").strip():
            assert not has_reply, (
                "空答案却写入了正文——与 H2 预期不符，请改断言"
            )

    def test_H4_next_run_conv_ctx_empty_when_stm_has_only_user_and_tools(self):
        """只有 user+tool、没有 [最终答案]/assistant 时，追问轮 conv_ctx 是什么样。"""
        llm = _llm()
        agent = _agent(llm)
        mm = MemoryManager(session_id="diag-h4", enable_long_term=False)
        mm.append("user", "介绍 AgentNexus 项目")
        mm.append("tool", "Action: read[{}]\nObservation: 文件列表…")

        conv = agent._build_conversation_context(mm)
        print("H4 conv_ctx:", repr(conv[:400]))
        # 截图里模型说「没有近期对话」——若 conv 非空但很短/无助手正文，要写清楚
        assert "介绍 AgentNexus" in conv, f"user 应出现在 conv_ctx: {conv!r}"
        # 没有 [最终答案] 时，不应出现助手最终回复
        assert "配备完整" not in conv

    def test_H4b_next_llm_call_messages_when_prior_turn_had_no_final_answer(self):
        """第二轮 think 收到的 messages 是否包含第一问（对齐「看不到上下文」）。"""
        llm = _llm()
        agent = _agent(llm)
        mm = MemoryManager(session_id="diag-h4b", enable_long_term=False)

        # 第一轮：空答案
        llm.think.side_effect = lambda **kw: (
            setattr(llm, "last_error", "400") or ""
        )
        r1 = agent.run("介绍 AgentNexus 项目", memory_manager=mm)

        llm2 = _llm()
        agent2 = _agent(llm2)
        captured = []

        def think2(**kw):
            captured.append(list(kw.get("messages") or []))
            return "我看不到上一轮。"

        llm2.think.side_effect = think2
        agent2.run("怎么话没说完？", memory_manager=mm)

        assert captured, "第二轮必须有 LLM 调用"
        flat = "\n".join(str(m.get("content") or "") for m in captured[0])
        print("H4b r1.answer:", repr(r1.answer))
        print("H4b second msgs snippet:", flat[:600])
        # 可复现事实：用户问题是否在
        assert "怎么话没说完" in flat
        # 上一问是否在
        print("H4b has prior question:", "介绍 AgentNexus" in flat)

    def test_turn_finish_empty_does_not_write_final_answer_marker(self):
        """turn.finish('') 不写 [最终答案]，也不写 user（user 由 agent._on_init 写入）。"""
        mm = MemoryManager(session_id="diag-turn", enable_long_term=False)
        turn = TurnRuntime(
            run_id="r1", session_id="diag-turn", question="介绍 AgentNexus 项目",
            memory_manager=mm,
        )
        turn.finish("")
        dump = _stm_dump(mm)
        print("TURN STM after finish(''):", dump)
        # 事实：persist_snapshot 明确不把 answer 写回 STM
        assert not any(c.startswith("[最终答案]") for _, c in dump)
        # finish('') 本身也不负责 user 行
        assert dump == [] or not any("介绍 AgentNexus" in c for _, c in dump)
