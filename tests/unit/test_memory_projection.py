from agentnexus.memory.projection import (
    build_projection,
    microcompact_messages,
    project_aggressive,
    project_mild,
)


def _parse_tool(content):
    return "test_tool", content


def _is_recoverable(name):
    return name == "test_tool"


class TestProjectMild:

    def test_short_messages_unchanged(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = project_mild(messages)
        assert result == messages

    def test_long_assistant_message_truncated(self):
        messages = [
            {"role": "assistant", "content": "x" * 2000},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
            {"role": "user", "content": "r4"},
        ]
        result = project_mild(messages)
        assert "投影截断" in result[0]["content"]
        assert result[0]["content"].startswith("x" * 500)
        assert result[0]["content"].endswith("x" * 500)

    def test_long_tool_message_truncated(self):
        messages = [
            {"role": "tool", "content": "x" * 2000},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
            {"role": "user", "content": "r4"},
        ]
        result = project_mild(messages)
        assert "投影截断" in result[0]["content"]

    def test_last_4_kept_intact(self):
        messages = [
            {"role": "assistant", "content": "x" * 2000},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
            {"role": "user", "content": "r4"},
        ]
        result = project_mild(messages)
        assert result[-4]["content"] == "r1"
        assert result[-3]["content"] == "r2"
        assert result[-2]["content"] == "r3"
        assert result[-1]["content"] == "r4"

    def test_user_messages_never_truncated(self):
        long_user = "x" * 2000
        messages = [
            {"role": "user", "content": long_user},
            {"role": "user", "content": "short"},
        ]
        result = project_mild(messages)
        assert result[0]["content"] == long_user


class TestProjectAggressive:

    def test_boundary_message_inserted(self):
        messages = [
            {"role": "user", "content": "old1"},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
        ]
        result = project_aggressive(
            messages,
            parse_tool_message=_parse_tool,
            is_recoverable_tool=_is_recoverable,
        )
        boundaries = [m for m in result if m["role"] == "system" and "上下文投影" in m["content"]]
        assert len(boundaries) == 1

    def test_recoverable_tool_cleared(self):
        messages = [
            {"role": "tool", "content": "result data"},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
        ]
        result = project_aggressive(
            messages,
            parse_tool_message=_parse_tool,
            is_recoverable_tool=_is_recoverable,
        )
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "工具结果已投影清除" in tool_msgs[0]["content"]

    def test_non_recoverable_tool_kept(self):
        def _not_recoverable(name):
            return False

        messages = [
            {"role": "tool", "content": "important result"},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
        ]
        result = project_aggressive(
            messages,
            parse_tool_message=_parse_tool,
            is_recoverable_tool=_not_recoverable,
        )
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert tool_msgs[0]["content"] == "important result"

    def test_last_3_intact(self):
        messages = [
            {"role": "user", "content": "old1"},
            {"role": "user", "content": "old2"},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
        ]
        result = project_aggressive(
            messages,
            parse_tool_message=_parse_tool,
            is_recoverable_tool=_is_recoverable,
        )
        assert result[-3]["content"] == "r1"
        assert result[-2]["content"] == "r2"
        assert result[-1]["content"] == "r3"

    def test_long_assistant_truncated(self):
        messages = [
            {"role": "assistant", "content": "x" * 2000},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
        ]
        result = project_aggressive(
            messages,
            parse_tool_message=_parse_tool,
            is_recoverable_tool=_is_recoverable,
        )
        assistant_msgs = [m for m in result if m["role"] == "assistant"]
        assert "投影压缩" in assistant_msgs[0]["content"]


class TestBuildProjection:

    def test_ratio_below_090_returns_unchanged(self):
        messages = [{"role": "user", "content": "hello"}]
        result = build_projection(
            messages,
            token_count=800,
            ctx_max=1000,
            parse_tool_message=_parse_tool,
            is_recoverable_tool=_is_recoverable,
        )
        assert result is messages

    def test_ratio_092_triggers_mild(self):
        messages = [
            {"role": "assistant", "content": "x" * 2000},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
            {"role": "user", "content": "r4"},
        ]
        result = build_projection(
            messages,
            token_count=920,
            ctx_max=1000,
            parse_tool_message=_parse_tool,
            is_recoverable_tool=_is_recoverable,
        )
        assert "投影截断" in result[0]["content"]

    def test_ratio_096_triggers_aggressive(self):
        messages = [
            {"role": "tool", "content": "some result"},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
        ]
        result = build_projection(
            messages,
            token_count=960,
            ctx_max=1000,
            parse_tool_message=_parse_tool,
            is_recoverable_tool=_is_recoverable,
        )
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert "工具结果已投影清除" in tool_msgs[0]["content"]


class TestMicrocompactMessages:

    def test_clears_old_recoverable_tools_keeps_last_5(self):
        messages = [
            {"role": "tool", "content": f"result {i}"} for i in range(8)
        ]
        result, cleaned = microcompact_messages(
            messages,
            parse_tool_message=_parse_tool,
            is_recoverable_tool=_is_recoverable,
        )
        cleared = [m for m in result if "工具结果已清理" in m["content"]]
        kept = [m for m in result if "result" in m["content"] and "已清理" not in m["content"]]
        assert len(cleared) == 3
        assert len(kept) == 5
        assert cleaned is True

    def test_truncates_long_assistant(self):
        messages = [
            {"role": "assistant", "content": "x" * 3000},
        ]
        result, cleaned = microcompact_messages(
            messages,
            parse_tool_message=_parse_tool,
            is_recoverable_tool=_is_recoverable,
        )
        assert "截断" in result[0]["content"]
        assert len(result[0]["content"]) < 3000
        assert cleaned is True

    def test_returns_cleaned_true_when_changes_made(self):
        messages = [
            {"role": "assistant", "content": "x" * 3000},
        ]
        _, cleaned = microcompact_messages(
            messages,
            parse_tool_message=_parse_tool,
            is_recoverable_tool=_is_recoverable,
        )
        assert cleaned is True

    def test_no_changes_returns_cleaned_false(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "short reply"},
        ]
        result, cleaned = microcompact_messages(
            messages,
            parse_tool_message=_parse_tool,
            is_recoverable_tool=_is_recoverable,
        )
        assert cleaned is False
        assert result == messages
