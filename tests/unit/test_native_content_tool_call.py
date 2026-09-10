"""P0 回归：NATIVE 策略下模型把工具调用 JSON 写进正文（last_tool_calls 为空）。

真实故障（fine-grained 评测 PLAN-004 / E2E-101 复现）：
DeepSeek-V4-Flash 偶发不走 native tool_calls 通道，而是在 content 里输出
{"thought": ..., "tool": "get_weather", "params": {...}}。FSM 的
_on_receive_native 在 pending_tool_calls 为空时把正文直接当最终答案，
用户看到一坨原始 JSON，工具从未执行。

期望行为：正文可解析为工具调用且工具存在 → 还原为真实调用继续执行；
显式 {"answer": ...} → 提取答案文本；其余正文原样作为答案。
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
    llm.capabilities.supports_tool_calling = True  # NATIVE 策略
    llm.capabilities.supports_json_mode = True
    llm.capabilities.supports_json_schema = False
    llm.capabilities.supports_parallel_tool_calls = False
    llm.capabilities.thinking_effort = "none"
    llm.think.return_value = ""
    return llm


def _make_agent():
    llm = _make_llm()
    te = ToolRegistry()
    calls: list[dict] = []

    def weather(**kw):
        calls.append(kw)
        return {"city": kw.get("city"), "weather": "暴雪", "temp": "-5"}

    te.register_tool("get_weather", "查天气", weather)
    agent = ReActAgent(llm, te, max_steps=5)
    return agent, llm, calls


class TestNativeContentToolCallRecovery:
    """正文里的工具调用 JSON 必须被还原执行，而不是当答案返回。"""

    def test_tool_call_json_in_content_executes_tool(self):
        agent, llm, calls = _make_agent()
        round_no = [0]

        def mock_think(**kw):
            round_no[0] += 1
            llm.last_tool_calls = []
            if round_no[0] == 1:
                # 通道漏接：工具调用写进了正文
                return '{"thought": "需要查询天气", "tool": "get_weather", "params": {"city": "杭州"}}'
            return "杭州今天暴雪，气温-5°C，不适合穿短袖。"

        llm.think.side_effect = mock_think
        result = agent.run("杭州今天多少度，适合穿短袖吗？")

        assert calls == [{"city": "杭州"}], "正文里的工具调用必须被还原执行"
        assert result.answer == "杭州今天暴雪，气温-5°C，不适合穿短袖。"
        assert '{"tool"' not in (result.answer or ""), "答案不得是原始工具调用 JSON"

    def test_answer_json_in_content_is_unwrapped(self):
        agent, llm, calls = _make_agent()

        def mock_think(**kw):
            llm.last_tool_calls = []
            return '{"thought": "直接回答即可", "answer": "北京今天晴，26°C。"}'

        llm.think.side_effect = mock_think
        result = agent.run("北京今天天气怎么样")

        assert result.answer == "北京今天晴，26°C。"
        assert calls == []

    def test_plain_text_content_unchanged(self):
        agent, llm, calls = _make_agent()

        def mock_think(**kw):
            llm.last_tool_calls = []
            return "1+1 等于 2，这是基础算术。"

        llm.think.side_effect = mock_think
        result = agent.run("1+1等于几")

        assert result.answer == "1+1 等于 2，这是基础算术。"
        assert calls == []

    def test_unknown_tool_json_stays_answer(self):
        """正文 JSON 指向不存在的工具 → 不得伪造执行，原样返回。"""
        agent, llm, calls = _make_agent()

        def mock_think(**kw):
            llm.last_tool_calls = []
            return '{"tool": "nonexistent_tool", "params": {"x": 1}}'

        llm.think.side_effect = mock_think
        result = agent.run("随便问点什么")

        assert calls == []
        assert "nonexistent_tool" in (result.answer or "")

    def test_repeated_tool_json_bounded_by_max_steps(self):
        """模型每轮都重复正文工具调用 → 不得死循环，受 max_steps 约束。"""
        agent, llm, calls = _make_agent()

        def mock_think(**kw):
            llm.last_tool_calls = []
            return '{"thought": "再查一次", "tool": "get_weather", "params": {"city": "杭州"}}'

        llm.think.side_effect = mock_think
        result = agent.run("杭州天气")

        assert len(calls) <= 5, f"恢复执行必须受 max_steps 约束，实际 {len(calls)} 次"
        assert result is not None
        assert result.answer is not None, "达到步数上限也必须给出兜底答案"

    def test_native_tool_call_loop_bounded_by_max_steps(self):
        """正常 native 通道的无限工具循环同样受 max_steps 约束。

        回归：max_steps 检查此前只在 _on_init（step=0）执行，是死代码。
        """
        agent, llm, calls = _make_agent()

        def mock_think(**kw):
            llm.last_tool_calls = [{"name": "get_weather", "arguments": {"city": "杭州"}}]
            return "查一下"

        llm.think.side_effect = mock_think
        result = agent.run("杭州天气")

        assert 0 < len(calls) <= 5, f"工具循环必须受 max_steps 约束，实际 {len(calls)} 次"
        assert result.answer is not None

    def test_single_key_json_answer_not_mangled(self):
        """普通单键 JSON 答案（如 {"温度": "26°C"}）不是协议 JSON，不得拆解。"""
        agent, llm, calls = _make_agent()

        def mock_think(**kw):
            llm.last_tool_calls = []
            return '{"温度": "26°C", "建议": "适合外出"}'

        llm.think.side_effect = mock_think
        result = agent.run("把结果以 JSON 给我")

        assert result.answer == '{"温度": "26°C", "建议": "适合外出"}'
        assert calls == []
