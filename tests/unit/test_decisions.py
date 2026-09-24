"""Unit tests for agentnexus.agents.decisions (FSM redesign Step 1).

These pin down the decision logic that ReActAgent handlers now delegate to.
Step 1 must be behavior-identical: every branch asserted here mirrors the
pre-rewrite branching of _on_receive_native / retry_gate / _select_strategy.
"""
from unittest.mock import MagicMock

from agentnexus.agents import decisions
from agentnexus.agents.react_types import CallingStrategy, RetryReason
from agentnexus.core.capabilities import SessionCapabilityTracker

# ── interpret_native ────────────────────────────────────────────────


def _tools(**over):
    base = {
        "response_text": "",
        "reasoning_text": "",
        "tool_calls": None,
        "truncated": False,
        "streamed": False,
        "tool_exists": lambda name: True,
    }
    base.update(over)
    return decisions.interpret_native(**base)


class TestInterpretNative:
    def test_truncated_with_pending_calls_fails_whole_batch(self):
        d = _tools(tool_calls=[{"name": "x", "arguments": {}}], truncated=True)
        assert d.kind == "fault"
        assert d.reason is RetryReason.TRUNCATED
        assert d.fail_pending_calls is True

    def test_truncated_without_calls_routes_to_retry_gate(self):
        d = _tools(truncated=True)
        assert d.kind == "fault"
        assert d.reason is RetryReason.TRUNCATED
        assert d.fail_pending_calls is False

    def test_tool_calls_without_thought_are_allowed(self):
        # 决策1（2026-09-24 拍板）：允许模型不做可见思考直接调工具
        d = _tools(response_text="", reasoning_text="",
                   tool_calls=[{"name": "x", "arguments": {}}])
        assert d.kind == "tools"
        assert d.thought == ""
        assert d.text == ""

    def test_tool_calls_carry_commentary_text(self):
        # 决策2：边说话边调工具 —— text 随行展示
        d = _tools(response_text="我先查一下这两个文件，然后给你结论。",
                   tool_calls=[{"name": "x", "arguments": {}}])
        assert d.kind == "tools"
        assert d.text == "我先查一下这两个文件，然后给你结论。"
        assert d.thought == "我先查一下这两个文件，然后给你结论。"

    def test_tool_calls_with_text_count_as_thought(self):
        d = _tools(response_text="先看看文件。",
                   tool_calls=[{"name": "x", "arguments": {}}])
        assert d.kind == "tools"
        assert d.thought == "先看看文件。"
        assert d.terminal_answer is None

    def test_bookkeeping_batch_stashes_terminal_answer(self):
        text = "好，我已经把待办更新完了。这是一个满足长度要求的最终总结文本，应当走 fast path。"
        d = _tools(response_text=text, tool_calls=[{"name": "todo_add", "arguments": {}}])
        assert d.kind == "tools"
        assert d.terminal_answer == text

    def test_non_bookkeeping_batch_does_not_stash(self):
        text = "好，我已经把待办更新完了。这是一个满足长度要求的最终总结文本，应当走 fast path。"
        d = _tools(response_text=text, tool_calls=[{"name": "file_read", "arguments": {}}])
        assert d.terminal_answer is None

    def test_short_terminal_text_does_not_stash(self):
        d = _tools(response_text="我先更新下待办。",
                   tool_calls=[{"name": "todo_add", "arguments": {}}])
        assert d.terminal_answer is None

    def test_empty_response_and_empty_reasoning_is_no_tools_no_text(self):
        d = _tools()
        assert d.kind == "fault"
        assert d.no_tools_no_text is True

    def test_recovered_protocol_json_tool_call(self):
        d = _tools(response_text='{"tool": "file_read", "params": {"path": "a"}}')
        assert d.kind == "tools"
        assert d.recovered_protocol_json is True
        assert d.tool_calls[0]["name"] == "file_read"
        assert d.tool_calls[0]["arguments"] == {"path": "a"}

    def test_recovered_protocol_json_tool_ignored_if_tool_missing(self):
        d = _tools(response_text='{"tool": "nope", "params": {}}',
                   tool_exists=lambda name: False)
        assert d.kind == "answer"

    def test_recovered_protocol_json_answer(self):
        d = _tools(response_text='{"answer": "42"}')
        assert d.kind == "answer"
        assert d.text == "42"
        assert d.recovered_protocol_json is True

    def test_plain_data_json_is_answered_as_text(self):
        d = _tools(response_text='{"温度": "26°C"}')
        assert d.kind == "answer"
        assert d.text == '{"温度": "26°C"}'
        assert d.recovered_protocol_json is False

    def test_answer_with_reasoning_not_streamed_displays_thought(self):
        d = _tools(response_text="答案是 42。", reasoning_text="逐步算一下")
        assert d.kind == "answer"
        assert d.display_thought == "逐步算一下"
        assert d.text == "答案是 42。"

    def test_answer_streamed_persists_reasoning_instead(self):
        d = _tools(response_text="答案是 42。", reasoning_text="逐步算一下",
                   streamed=True)
        assert d.persist_reasoning == "逐步算一下"
        assert d.display_thought == ""

    def test_answer_with_thought_marker_splits_display(self):
        d = _tools(response_text="Thought: 先算一下。最终答案: 42")
        assert d.kind == "answer"
        assert d.display_thought == "先算一下。"
        assert d.text == "42"


# ── select_visible_thought / split ──────────────────────────────────


class TestThoughtHelpers:
    def test_reasoning_wins_over_text(self):
        assert decisions.select_visible_thought("正文", "推理") == "推理"

    def test_no_marker_returns_full_text(self):
        assert decisions.select_visible_thought("整段都是可见文本", "") == "整段都是可见文本"

    def test_json_protocol_text_returns_empty(self):
        assert decisions.select_visible_thought('{"tool": "a", "params": {}}', "") == ""

    def test_marker_body_without_answer_marker(self):
        assert decisions.select_visible_thought("Thought: 分析一下", "") == "分析一下"

    def test_split_marker_with_final_answer(self):
        thought, answer = decisions.split_native_thought_answer(
            "Thought: 分析一下。最终答案: 42")
        assert thought == "分析一下。"
        assert answer == "42"

    def test_split_plain_text_is_all_answer(self):
        thought, answer = decisions.split_native_thought_answer("普通答案")
        assert thought == ""
        assert answer == "普通答案"


# ── JSON protocol path ──────────────────────────────────────────────


class TestJsonProtocol:
    def test_empty_response_is_fault(self):
        d = decisions.interpret_json(response_text="", truncated=False)
        assert d.kind == "fault"
        assert d.reason is RetryReason.EMPTY_RESPONSE

    def test_truncated_is_fault(self):
        d = decisions.interpret_json(response_text="abc", truncated=True)
        assert d.kind == "fault"
        assert d.reason is RetryReason.TRUNCATED
        assert d.detail == "finish_reason=length"

    def test_parse_error_is_fault(self):
        d = decisions.interpret_json(response_text="definitely not json", truncated=False)
        assert d.kind == "fault"
        assert d.reason is RetryReason.PARSE_ERROR

    def test_tool_call_payload_is_tools(self):
        d = decisions.interpret_json(
            response_text='{"tool": "file_read", "params": {"path": "a"}}',
            truncated=False)
        assert d.kind == "tools"
        assert d.tool_calls[0]["name"] == "file_read"

    def test_answer_payload_is_answer(self):
        d = decisions.interpret_json(response_text='{"answer": "42"}', truncated=False)
        assert d.kind == "answer"
        assert d.text == "42"

    def test_single_key_json_is_answer_value(self):
        # json_helpers.classify_parsed：单键字典取值为答案（如 {"温度": "26°C"}）
        d = decisions.interpret_json(response_text='{"weird": 1}', truncated=False)
        assert d.kind == "answer"
        assert d.text == "1"

    def test_multi_key_unknown_payload_is_parse_fault(self):
        # json_helpers.classify_parsed 对无 tool/answer 键的多键字典返回 type=error
        d = decisions.interpret_json(response_text='{"a": 1, "b": 2}', truncated=False)
        assert d.kind == "fault"
        assert d.reason is RetryReason.PARSE_ERROR


# ── recover ──────────────────────────────────────────────────────────


class TestRecover:
    def test_retries_left_when_budget_remains(self):
        assert decisions.retry_gate_decision(
            json_retries=1, max_json_retries=2,
            strategy=CallingStrategy.JSON_MODE) == "round"

    def test_degrade_when_exhausted_in_json_mode(self):
        assert decisions.retry_gate_decision(
            json_retries=2, max_json_retries=2,
            strategy=CallingStrategy.JSON_MODE) == "degrade"

    def test_salvage_when_exhausted_in_prompt_json(self):
        assert decisions.retry_gate_decision(
            json_retries=2, max_json_retries=2,
            strategy=CallingStrategy.PROMPT_JSON) == "salvage"

    def test_after_retry_round_degrades_json_mode(self):
        assert decisions.after_retry_round_decision(
            json_retries=2, max_json_retries=2,
            strategy=CallingStrategy.JSON_MODE) == "degrade"

    def test_after_retry_round_continues_when_budget_left(self):
        assert decisions.after_retry_round_decision(
            json_retries=1, max_json_retries=2,
            strategy=CallingStrategy.JSON_MODE) == "continue"

    def test_after_retry_round_continues_in_prompt_json(self):
        # PROMPT_JSON 即使重试耗尽也不降级（与原实现对齐）
        assert decisions.after_retry_round_decision(
            json_retries=5, max_json_retries=2,
            strategy=CallingStrategy.PROMPT_JSON) == "continue"


class TestSelectStrategy:
    def _caps(self, tool=True, json_mode=True):
        caps = MagicMock()
        caps.supports_tool_calling = tool
        caps.supports_json_mode = json_mode
        return caps

    def test_native_first(self):
        assert decisions.select_strategy(SessionCapabilityTracker(),
                                         self._caps()) is CallingStrategy.NATIVE_TOOLS

    def test_json_when_tool_blocked(self):
        tracker = SessionCapabilityTracker()
        tracker.mark_failed("tool_calling")
        assert decisions.select_strategy(tracker, self._caps()) is CallingStrategy.JSON_MODE

    def test_prompt_json_when_both_blocked(self):
        tracker = SessionCapabilityTracker()
        tracker.mark_failed("tool_calling")
        tracker.mark_failed("json_mode")
        assert decisions.select_strategy(tracker, self._caps()) is CallingStrategy.PROMPT_JSON
