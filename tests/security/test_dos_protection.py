"""Security: Resource exhaustion / DoS protection tests.

Tests that the system handles very large inputs, many messages,
and empty inputs without crashing.
"""

from agentnexus.memory.compaction import is_recoverable_tool, parse_tool_message
from agentnexus.memory.offload import offload_large_result
from agentnexus.memory.projection import (
    microcompact_messages,
    project_aggressive,
    project_mild,
)
from agentnexus.memory.short_term import ShortTermMemory


class TestSTMResourceLimits:
    """Short-term memory handles large inputs gracefully."""

    def test_very_long_content_does_not_crash(self):
        """STM with very long message content does not crash."""
        stm = ShortTermMemory(max_messages=50)
        long_content = "x" * 1_000_000
        stm.append("user", long_content)
        messages = stm.get_all()
        assert len(messages) == 1
        assert len(messages[0]["content"]) == 1_000_000

    def test_many_messages_snip_works(self):
        """STM with many messages, snip reduces count."""
        stm = ShortTermMemory(max_messages=200)
        for i in range(200):
            stm.append("user", f"message {i}")

        assert len(stm.get_all()) == 200
        removed = stm.snip(keep_recent=10)
        assert removed > 0
        assert len(stm.get_all()) <= 11

    def test_snip_with_fewer_messages_than_keep(self):
        """snip with keep_recent > message count does nothing."""
        stm = ShortTermMemory(max_messages=50)
        stm.append("user", "a")
        stm.append("user", "b")
        removed = stm.snip(keep_recent=10)
        assert removed == 0
        assert len(stm.get_all()) == 2

    def test_max_messages_evicts_old(self):
        """STM with max_messages=3 evicts oldest on append."""
        stm = ShortTermMemory(max_messages=3)
        stm.append("user", "a")
        stm.append("user", "b")
        stm.append("user", "c")
        stm.append("user", "d")
        messages = stm.get_all()
        assert len(messages) == 3
        assert messages[0]["content"] == "b"

    def test_compact_with_many_messages(self):
        """compact() with many messages keeps only recent + summary."""
        stm = ShortTermMemory(max_messages=200)
        for i in range(200):
            stm.append("user", f"msg {i}")

        stm.compact("summary of conversation", keep_recent=4)
        messages = stm.get_all()
        assert len(messages) <= 5
        assert "summary" in messages[0]["content"]


class TestProjectionEdgeCases:
    """Projection handles empty and edge-case inputs."""

    def test_project_mild_empty_messages(self):
        """project_mild on empty list returns empty list."""
        assert project_mild([]) == []

    def test_project_mild_single_message(self):
        """project_mild on single message returns it unchanged."""
        messages = [{"role": "user", "content": "hi"}]
        result = project_mild(messages)
        assert len(result) == 1
        assert result[0]["content"] == "hi"

    def test_project_aggressive_empty_messages(self):
        """project_aggressive on empty list returns boundary message."""
        result = project_aggressive(
            [],
            parse_tool_message=parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
        )
        assert len(result) == 1
        assert "投影" in result[0]["content"]

    def test_project_aggressive_single_message(self):
        """project_aggressive on single message adds boundary."""
        messages = [{"role": "user", "content": "hi"}]
        result = project_aggressive(
            messages,
            parse_tool_message=parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
        )
        assert len(result) >= 1

    def test_microcompact_empty_messages(self):
        """microcompact on empty list returns empty list."""
        compacted, cleaned = microcompact_messages(
            [],
            parse_tool_message=parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
        )
        assert compacted == []
        assert cleaned is False

    def test_microcompact_all_user_messages(self):
        """microcompact with only user messages does nothing."""
        messages = [
            {"role": "user", "content": "a" * 5000},
            {"role": "user", "content": "b" * 5000},
        ]
        compacted, cleaned = microcompact_messages(
            messages,
            parse_tool_message=parse_tool_message,
            is_recoverable_tool=is_recoverable_tool,
        )
        assert cleaned is False
        assert len(compacted) == 2


class TestOffloadLargeResult:
    """offload_large_result handles very large content."""

    def test_offload_writes_to_disk(self, tmp_path):
        """Large content is written to disk and stub returned."""
        offload_dir = str(tmp_path / "offload")
        content = "x" * 100_000
        stub = offload_large_result(content, offload_dir, "session1")
        assert "缓存" in stub
        assert "session1" in stub

    def test_offload_with_empty_content(self, tmp_path):
        """Empty content is handled."""
        offload_dir = str(tmp_path / "offload")
        stub = offload_large_result("", offload_dir, "session1")
        assert "缓存" in stub

    def test_offload_with_special_characters(self, tmp_path):
        """Content with special chars is handled."""
        offload_dir = str(tmp_path / "offload")
        content = "test\n\t\x00\r\nunicode: \u4e2d\u6587"
        stub = offload_large_result(content, offload_dir, "session1")
        assert "缓存" in stub
