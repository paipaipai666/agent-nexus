"""Tests for message display order in historical sessions.

Root cause: the answer was stored TWICE:
1. re_act_agent.py:633 — append("system", "[最终答案] ...")
2. turn.py:132 — append("assistant", record.answer)  ← duplicate, no prefix

The duplicate "assistant" entry displays as thinking in history,
making it look like "answer before thinking".

Fix: remove the duplicate storage in turn.py. The agent already
stores the answer with [最终答案] prefix.
"""

import pytest


# Simulate the frontend transformation logic from ChatPage.tsx loadAndDisplayMessages
def transform_messages(stm: list[dict]) -> list[dict]:
    """Python port of the TypeScript transformation logic for testing."""
    transformed = []
    pending_tools = []
    idx = 0

    def flush_pending_tools():
        nonlocal idx
        for t in pending_tools:
            t["id"] = f"h-{idx}"
            idx += 1
            transformed.append(t)
        pending_tools.clear()

    for m in stm:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()

        if role == "system" and content.startswith("[上下文已裁剪]"):
            continue
        if role == "system" and content.startswith("[恢复文件]"):
            continue

        if role == "system" and content.startswith("[最终答案]"):
            answer = content.replace("[最终答案]", "").strip()
            if answer:
                flush_pending_tools()
                transformed.append({"id": f"h-{idx}", "role": "assistant", "content": answer})
                idx += 1
            continue

        if role == "system" and content.startswith("[会话摘要]"):
            flush_pending_tools()
            transformed.append({"id": f"h-{idx}", "role": "system", "content": content})
            idx += 1
            continue

        if role == "user":
            flush_pending_tools()
            transformed.append({"id": f"h-{idx}", "role": "user", "content": content})
            idx += 1
            continue

        if role == "tool":
            pending_tools.append({"id": "", "role": "tool", "content": content})
            continue

        if role == "assistant":
            flush_pending_tools()
            transformed.append({"id": f"h-{idx}", "role": "system", "content": content})
            idx += 1
            continue

        if role == "system" and content:
            flush_pending_tools()
            transformed.append({"id": f"h-{idx}", "role": "system", "content": content})
            idx += 1

    flush_pending_tools()
    return transformed


class TestCorrectStorageOrder:
    """After fix: only agent stores answer, no duplicate from turn service."""

    def test_single_turn_correct_order(self):
        """User → thinking → [最终答案] should display as user → system(thinking) → assistant(answer)."""
        messages = [
            {"role": "user", "content": "什么是 Python?"},
            {"role": "assistant", "content": "让我思考一下 Python 的定义..."},
            {"role": "system", "content": "[最终答案] Python 是一种编程语言。"},
        ]
        result = transform_messages(messages)

        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "system"
        assert result[1]["content"] == "让我思考一下 Python 的定义..."
        assert result[2]["role"] == "assistant"
        assert result[2]["content"] == "Python 是一种编程语言。"

    def test_multi_turn_correct_order(self):
        """Multiple turns should each have thinking before answer."""
        messages = [
            {"role": "user", "content": "问题1"},
            {"role": "assistant", "content": "思考1"},
            {"role": "system", "content": "[最终答案] 答案1"},
            {"role": "user", "content": "问题2"},
            {"role": "assistant", "content": "思考2"},
            {"role": "system", "content": "[最终答案] 答案2"},
        ]
        result = transform_messages(messages)

        answers = [m for m in result if m["role"] == "assistant"]
        assert len(answers) == 2
        assert answers[0]["content"] == "答案1"
        assert answers[1]["content"] == "答案2"

    def test_thinking_with_tool_calls(self):
        """Thinking → tool → thinking → answer should work correctly."""
        messages = [
            {"role": "user", "content": "搜索 Python"},
            {"role": "assistant", "content": "我需要搜索..."},
            {"role": "tool", "content": "搜索结果: Python 是..."},
            {"role": "assistant", "content": "根据搜索结果..."},
            {"role": "system", "content": "[最终答案] Python 是一种编程语言。"},
        ]
        result = transform_messages(messages)

        assert result[-1]["role"] == "assistant"
        assert result[-1]["content"] == "Python 是一种编程语言。"
        assert result[-2]["role"] == "system"
        assert result[-2]["content"] == "根据搜索结果..."


class TestDuplicateAnswerBug:
    """Document the bug: when turn service stores answer as assistant (no prefix),
    it shows as thinking after the real answer."""

    def test_duplicate_answer_shows_as_thinking(self):
        """If answer is stored twice (once with prefix, once without),
        the duplicate appears as thinking after the answer."""
        messages = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "思考"},
            {"role": "system", "content": "[最终答案] 答案"},
            {"role": "assistant", "content": "答案"},  # duplicate from turn service
        ]
        result = transform_messages(messages)

        # The duplicate "assistant" becomes "system" (thinking)
        assert len(result) == 4
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "system"      # thinking
        assert result[2]["role"] == "assistant"    # answer
        assert result[3]["role"] == "system"       # duplicate displayed as thinking!
        assert result[3]["content"] == "答案"

        # This is the bug: the duplicate makes it look like thinking comes after answer
        # After fix (removing duplicate), this scenario won't occur

    def test_no_duplicate_after_fix(self):
        """After fix: no duplicate answer in stored messages."""
        # This is what the stored messages should look like after fix
        messages = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "思考"},
            {"role": "system", "content": "[最终答案] 答案"},
            # No duplicate assistant entry
        ]
        result = transform_messages(messages)

        assert len(result) == 3
        assert result[-1]["role"] == "assistant"
        assert result[-1]["content"] == "答案"
        # No extra thinking after answer
