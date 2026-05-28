import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentnexus.memory.manager import MemoryManager


def _make_manager(home, enable_ltm=False):
    mock_llm = MagicMock()
    with patch("agentnexus.memory.manager.get_embedding_model", return_value=MagicMock()):
        return MemoryManager(session_id="s1", llm=mock_llm, enable_long_term=enable_ltm)


def _append_msgs(mm, role, content):
    with patch("agentnexus.core.hooks.get_hook_manager", return_value=MagicMock()):
        mm.append(role, content)


class TestMemoryManagerInit:

    def test_creates_stm(self, temp_agentnexus_home):
        mock_llm = MagicMock()
        mock_llm.think.return_value = "摘要"
        with patch("agentnexus.memory.manager.get_embedding_model", return_value=MagicMock()):
            mm = MemoryManager(session_id="s1", llm=mock_llm, enable_long_term=False)
        assert mm.short_term is not None
        assert mm.long_term is None

    def test_creates_ltm_when_enabled(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home, enable_ltm=True)
        assert mm.long_term is not None

    def test_no_ltm_when_disabled(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home, enable_ltm=False)
        assert mm.long_term is None


class TestEstimateStmTokens:

    def test_returns_int(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home)
        tokens = mm.estimate_stm_tokens()
        assert isinstance(tokens, int)
        assert tokens >= 0

    def test_grows_with_content(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home)
        before = mm.estimate_stm_tokens()
        mm.short_term.append("user", "这是一段比较长的中文内容用来测试token增长")
        after = mm.estimate_stm_tokens()
        assert after > before


class TestAppend:

    def test_append_adds_to_stm(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home)
        _append_msgs(mm, "user", "hello")
        _append_msgs(mm, "assistant", "world")
        msgs = mm.short_term.get_all()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"


class TestSnip:

    def test_snip_returns_zero_when_few_messages(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home)
        for i in range(5):
            _append_msgs(mm, "user", f"msg{i}")
        removed = mm.snip(keep_recent=10)
        assert removed == 0

    def test_snip_removes_old_messages(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home)
        for i in range(20):
            _append_msgs(mm, "user", f"msg{i}")
        removed = mm.snip(keep_recent=5)
        assert removed > 0
        msgs = mm.short_term.get_all()
        assert len(msgs) <= 6 + 1  # boundary marker + keep_recent


class TestMicrocompactTimeBased:

    def test_returns_false_when_interval_not_elapsed(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home)
        mm._last_api_call_ts = time.time()
        result = mm.microcompact_time_based(interval=9999)
        assert result is False

    def test_returns_false_when_no_api_call(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home)
        mm._last_api_call_ts = 0.0
        result = mm.microcompact_time_based(interval=1)
        assert result is False


class TestHasNewMemories:

    def test_false_initially(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home)
        assert mm.has_new_memories() is False

    def test_false_without_ltm(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home)
        mm._last_write_count = 0
        assert mm.has_new_memories() is False

    def test_true_after_write_counter_increases(self, temp_agentnexus_home):
        mock_ltm = MagicMock()
        mock_ltm.write_counter = 5
        mm = _make_manager(temp_agentnexus_home)
        mm.long_term = mock_ltm
        mm._last_write_count = 3
        assert mm.has_new_memories() is True


class TestBridgeRead:

    def test_tracks_files(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home)
        mm.bridge_read("/path/to/file.py", "print('hello')")
        assert len(mm._recent_reads) == 1
        assert mm._recent_reads[0][0] == "/path/to/file.py"

    def test_caps_at_20(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home)
        for i in range(25):
            mm.bridge_read(f"/file_{i}.py", f"content {i}")
        assert len(mm._recent_reads) == 20
        assert mm._recent_reads[0][0] == "/file_5.py"


class TestWriteTranscript:

    def test_creates_jsonl_file(self, temp_agentnexus_home):
        mm = _make_manager(temp_agentnexus_home)
        mm.short_term.append("user", "hello")
        mm.short_term.append("assistant", "world")

        mm._write_transcript()

        transcript_dir = Path(mm._transcript_dir)
        jsonl_files = list(transcript_dir.glob("*.jsonl"))
        assert len(jsonl_files) == 1
        assert "test_session" not in jsonl_files[0].name  # session_id is "s1"
        assert "s1" in jsonl_files[0].name

        raw = jsonl_files[0].read_text(encoding="utf-8").strip()
        lines = [json.loads(line) for line in raw.split("\n")]
        assert len(lines) == 2
        assert lines[0]["role"] == "user"
        assert lines[1]["role"] == "assistant"
