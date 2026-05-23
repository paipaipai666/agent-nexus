"""Tests for CallingStrategy selection and robust JSON parsing."""
from agentnexus.agents.re_act_agent import AgentStep, CallingStrategy, ReActAgent


class TestCallingStrategyEnum:
    def test_all_strategies_defined(self):
        assert hasattr(CallingStrategy, "NATIVE_TOOLS")
        assert hasattr(CallingStrategy, "JSON_MODE")
        assert hasattr(CallingStrategy, "PROMPT_JSON")
        assert hasattr(CallingStrategy, "PLAIN_TEXT")


class TestAgentStep:
    def test_default_values(self):
        step = AgentStep(step_id=1)
        assert step.step_id == 1
        assert step.strategy_used == CallingStrategy.NATIVE_TOOLS
        assert step.reasoning_content == ""
        assert step.tool_calls == []

    def test_with_tool_calls(self):
        step = AgentStep(
            step_id=2,
            strategy_used=CallingStrategy.NATIVE_TOOLS,
            tool_calls=[{"name": "search", "arguments": {"q": "test"}}],
            tool_outputs=[{"tool": "search", "output": "result"}],
        )
        assert len(step.tool_calls) == 1
        assert step.tool_outputs[0]["tool"] == "search"


class TestRobustJsonParse:
    def test_standard_tool_call(self):
        result = ReActAgent._robust_json_parse(
            '{"tool": "web_search", "params": {"query": "test"}}'
        )
        assert result["type"] == "tool_call"
        assert result["tool"] == "web_search"

    def test_standard_answer(self):
        result = ReActAgent._robust_json_parse('{"answer": "hello"}')
        assert result["type"] == "answer"

    def test_strips_markdown_fence(self):
        result = ReActAgent._robust_json_parse(
            '```json\n{"answer": "hello"}\n```'
        )
        assert result["type"] == "answer"
        assert result["text"] == "hello"

    def test_strips_markdown_fence_no_lang(self):
        result = ReActAgent._robust_json_parse(
            '```\n{"answer": "world"}\n```'
        )
        assert result["type"] == "answer"

    def test_fixes_trailing_comma(self):
        result = ReActAgent._robust_json_parse(
            '{"tool": "search", "params": {"q": "test",},}'
        )
        assert result["type"] == "tool_call"

    def test_extra_text_after_brace(self):
        # _try_fix_json handles this via brace matching
        result = ReActAgent._robust_json_parse(
            '{"answer": "done"} More text here...'
        )
        assert result["type"] == "answer"

    def test_empty_string(self):
        result = ReActAgent._robust_json_parse("")
        assert result["type"] == "error"

    def test_plain_text_non_json(self):
        result = ReActAgent._robust_json_parse("this is not json at all")
        assert result["type"] == "error"

    def test_markdown_with_extra_text(self):
        result = ReActAgent._robust_json_parse(
            'Some preamble\n```json\n{"answer": "yes"}\n```\nSome postamble'
        )
        assert result["type"] == "answer"
        assert result["text"] == "yes"


class TestVisibleThoughtSelection:
    def test_prefers_reasoning_content_over_plain_text(self):
        result = ReActAgent._select_visible_thought(
            response_text="先搜索官方文档",
            reasoning_text="I should search the official docs before answering.",
        )
        assert result == "I should search the official docs before answering."

    def test_falls_back_to_plain_text_when_reasoning_missing(self):
        result = ReActAgent._select_visible_thought(
            response_text="先搜索官方文档",
            reasoning_text="",
        )
        assert result == "先搜索官方文档"

    def test_extracts_thought_from_json_when_reasoning_missing(self):
        result = ReActAgent._select_visible_thought(
            response_text='{"thought": "Need latest info first.", "tool": "web_search", "params": {"query": "test"}}',
            reasoning_text="",
        )
        assert result == "Need latest info first."

    def test_does_not_surface_raw_json_without_thought(self):
        result = ReActAgent._select_visible_thought(
            response_text='{"tool": "web_search", "params": {"query": "test"}}',
            reasoning_text="",
        )
        assert result == ""


class TestClassifyParsed:
    def test_tool_call(self):
        result = ReActAgent._classify_parsed({"tool": "search", "params": {"q": "x"}})
        assert result["type"] == "tool_call"

    def test_answer(self):
        result = ReActAgent._classify_parsed({"answer": "42"})
        assert result["type"] == "answer"

    def test_single_key_fallback(self):
        result = ReActAgent._classify_parsed({"summary": "ok"})
        assert result["type"] == "answer"
        assert result["text"] == "ok"

    def test_unknown_object(self):
        result = ReActAgent._classify_parsed({"foo": 1, "bar": 2})
        assert result["type"] == "error"
