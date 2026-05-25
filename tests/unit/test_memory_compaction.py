import time
from unittest.mock import MagicMock

from agentnexus.memory.manager import (
    MemoryManager,
    _contains_pii,
    _extract_xml_tag,
    _parse_tool_message,
)
from agentnexus.memory.short_term import ShortTermMemory


class TestParseToolMessage:

    def test_parses_tool_name_and_params(self):
        tool, params = _parse_tool_message("Action: read[file=a.py]")
        assert tool == "read"
        assert params == "file=a.py"

    def test_parses_hyphenated_tool_names(self):
        tool, params = _parse_tool_message("Action: web_search[query=hello world]")
        assert tool == "web_search"
        assert params == "query=hello world"

    def test_returns_none_for_non_matching_content(self):
        tool, params = _parse_tool_message("Hello world")
        assert tool is None
        assert params is None

    def test_returns_none_for_empty_string(self):
        tool, params = _parse_tool_message("")
        assert tool is None
        assert params is None


class TestExtractXmlTag:

    def test_extracts_content_from_tag(self):
        result = _extract_xml_tag("<summary>摘要内容</summary>", "summary")
        assert result == "摘要内容"

    def test_strips_whitespace(self):
        result = _extract_xml_tag("<summary>  内容  </summary>", "summary")
        assert result == "内容"

    def test_handles_multiline_content(self):
        result = _extract_xml_tag("<summary>\n行1\n行2\n</summary>", "summary")
        assert result == "行1\n行2"

    def test_case_insensitive(self):
        result = _extract_xml_tag("<SUMMARY>内容</SUMMARY>", "summary")
        assert result == "内容"

    def test_returns_none_when_tag_not_found(self):
        result = _extract_xml_tag("Hello world", "summary")
        assert result is None

    def test_returns_none_for_empty_text(self):
        result = _extract_xml_tag("", "summary")
        assert result is None

    def test_different_tag_names(self):
        result = _extract_xml_tag("<thinking>思考过程</thinking>", "thinking")
        assert result == "思考过程"


class TestContainsPII:

    def test_matches_email_address(self):
        assert _contains_pii("contact me at user@example.com")

    def test_matches_phone_number(self):
        assert _contains_pii("call 13800138000")

    def test_matches_api_key(self):
        key = "sk-abcdefghijklmnopqrstuvwxyz1234567890abcd"
        assert _contains_pii(key)

    def test_matches_credit_card_number(self):
        assert _contains_pii("card 4111111111111111")

    def test_no_pii_returns_false(self):
        assert not _contains_pii("Hello, how are you?")

    def test_empty_string_returns_false(self):
        assert not _contains_pii("")

    def test_short_number_does_not_match(self):
        assert not _contains_pii("number 12345")


class TestSnipInManager:

    def _make_mgr(self):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.short_term = ShortTermMemory()
        mgr._settings = MagicMock()
        mgr._settings.snip_enabled = True
        mgr._settings.time_microcompact_interval = 0
        mgr._snip_freed_tokens = 0
        mgr._on_compact = None
        mgr._on_after_compact = None
        return mgr

    def test_removes_oldest_messages_beyond_keep_recent_plus_4(self):
        mgr = self._make_mgr()
        for i in range(20):
            mgr.short_term.append("user", f"msg{i}")
        removed = mgr.snip(keep_recent=5)
        assert removed == 15
        msgs = mgr.short_term.get_all()
        assert len(msgs) == 6

    def test_returns_0_when_under_threshold(self):
        mgr = self._make_mgr()
        for i in range(10):
            mgr.short_term.append("user", f"msg{i}")
        removed = mgr.snip(keep_recent=10)
        assert removed == 0

    def test_returns_0_when_snip_disabled(self):
        mgr = self._make_mgr()
        mgr._settings.snip_enabled = False
        for i in range(20):
            mgr.short_term.append("user", f"msg{i}")
        removed = mgr.snip(keep_recent=5)
        assert removed == 0

    def test_updates_snip_freed_tokens(self, monkeypatch):
        call_count = [0]

        def fake_estimate(self):
            call_count[0] += 1
            return 1000 if call_count[0] == 1 else 100

        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            fake_estimate,
        )
        mgr = self._make_mgr()
        for i in range(20):
            mgr.short_term.append("user", f"msg{i}")
        mgr.snip(keep_recent=5)
        assert mgr._snip_freed_tokens == 900


class TestMicroCompact:

    def _make_mgr(self):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.short_term = ShortTermMemory()
        mgr._settings = MagicMock()
        mgr._on_compact = None
        return mgr

    def test_clears_recoverable_tool_results(self):
        mgr = self._make_mgr()
        for i in range(8):
            mgr.short_term.append(
                "tool", f"Action: read[file=f{i}.py]\nObservation: content {i}"
            )
        mgr.microcompact()
        msgs = mgr.short_term.get_all()
        cleared = [m for m in msgs if "工具结果已清理" in m.get("content", "")]
        kept = [m for m in msgs if "content" in m.get("content", "")]
        assert len(cleared) == 3
        assert len(kept) == 5

    def test_preserves_non_recoverable_tool_results(self):
        mgr = self._make_mgr()
        mgr.short_term.append("tool", "Action: python_repl[code=print(1)]\nObservation: 1")
        mgr.short_term.append("tool", "Action: memory_save[key=test]\nObservation: saved")
        mgr.microcompact()
        msgs = mgr.short_term.get_all()
        assert all("工具结果已清理" not in m.get("content", "") for m in msgs)

    def test_truncates_long_assistant_messages(self):
        mgr = self._make_mgr()
        long_content = "x" * 3000
        mgr.short_term.append("assistant", long_content)
        mgr.microcompact()
        msgs = mgr.short_term.get_all()
        assert len(msgs) == 1
        assert "截断" in msgs[0]["content"]
        assert len(msgs[0]["content"]) < len(long_content)

    def test_keeps_last_5_tool_results_intact(self):
        mgr = self._make_mgr()
        for i in range(10):
            mgr.short_term.append(
                "tool", f"Action: read[file=f{i}.py]\nObservation: content of file {i}"
            )
        mgr.microcompact()
        msgs = mgr.short_term.get_all()
        kept = [m for m in msgs if "content of file" in m.get("content", "")]
        assert len(kept) == 5
        assert "file 9" in kept[-1]["content"]

    def test_noop_when_no_messages(self):
        mgr = self._make_mgr()
        mgr.microcompact()
        assert len(mgr.short_term.get_all()) == 0


class TestMicroCompactTimeBased:

    def _make_mgr(self):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.short_term = ShortTermMemory()
        mgr._settings = MagicMock()
        mgr._settings.time_microcompact_interval = 60
        mgr._last_api_call_ts = 0.0
        mgr._on_compact = None
        mgr._on_after_compact = None
        return mgr

    def test_triggers_when_elapsed_exceeds_interval(self):
        mgr = self._make_mgr()
        mgr._last_api_call_ts = time.time() - 120
        mgr.short_term.append("assistant", "x" * 3000)
        result = mgr.microcompact_time_based(interval=60)
        assert result is True

    def test_noop_when_interval_not_elapsed(self):
        mgr = self._make_mgr()
        mgr._last_api_call_ts = time.time() - 10
        result = mgr.microcompact_time_based(interval=60)
        assert result is False

    def test_returns_false_when_no_previous_api_call(self):
        mgr = self._make_mgr()
        mgr._last_api_call_ts = 0.0
        result = mgr.microcompact_time_based(interval=60)
        assert result is False

    def test_returns_false_when_no_messages_exist(self):
        mgr = self._make_mgr()
        mgr._last_api_call_ts = time.time() - 120
        result = mgr.microcompact_time_based(interval=60)
        assert result is False

    def test_triggers_with_default_interval_from_settings(self):
        mgr = self._make_mgr()
        mgr._settings.time_microcompact_interval = 30
        mgr._last_api_call_ts = time.time() - 60
        mgr.short_term.append("assistant", "x" * 3000)
        result = mgr.microcompact_time_based()
        assert result is True


class TestOffloadLargeResult:

    def test_writes_content_to_disk(self, tmp_path):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.session_id = "test-session"
        mgr._offload_dir = str(tmp_path)
        content = "A" * 2000
        result = mgr._offload_large_result(content)
        assert "工具结果已缓存" in result
        assert str(tmp_path) in result
        files = list(tmp_path.glob("*.txt"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8") == content

    def test_returns_stub_with_preview(self, tmp_path):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.session_id = "test"
        mgr._offload_dir = str(tmp_path)
        long_content = "Hello " * 200
        result = mgr._offload_large_result(long_content)
        assert "预览(前500字符)" in result
        assert long_content[:500] in result


class TestMaybeCompactCircuitBreaker:

    def _make_mgr(self):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.short_term = ShortTermMemory()
        mgr._llm = MagicMock()
        mgr._embed_model = MagicMock()
        mgr._settings = MagicMock()
        mgr._settings.snip_enabled = False
        mgr._settings.time_microcompact_interval = 0
        mgr._settings.autocompact_buffer_tokens = 8000
        mgr._settings.transcript_enabled = False
        mgr._settings.post_compact_max_files = 0
        mgr._ctx_max = 128000
        mgr._compact_threshold = 120000
        mgr._compact_failures = 0
        mgr._circuit_open = False
        mgr._microcompacts_since_open = 0
        mgr._compacting = False
        mgr._snip_freed_tokens = 0
        mgr._recent_reads = []
        mgr._last_api_call_ts = 0.0
        mgr._on_compact = None
        mgr._on_after_compact = None
        mgr._transcript_dir = "/tmp"
        mgr.session_id = "test"
        return mgr

    def test_circuit_opens_after_3_consecutive_failures(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 125000,
        )
        mgr = self._make_mgr()
        for i in range(10):
            mgr.short_term.append("user", f"msg{i}")
        mgr._llm.think.return_value = ""
        for _ in range(3):
            mgr.maybe_compact()
        assert mgr._circuit_open is True
        assert mgr._compact_failures == 3

    def test_circuit_open_only_microcompact_runs(self):
        mgr = self._make_mgr()
        mgr._circuit_open = True
        mgr._microcompacts_since_open = 0
        for i in range(10):
            mgr.short_term.append(
                "tool", f"Action: read[file=f{i}.py]\nObservation: content of file {i}"
            )
        result = mgr.maybe_compact()
        assert result == 0
        assert mgr._microcompacts_since_open == 1
        msgs = mgr.short_term.get_all()
        cleared = [m for m in msgs if "工具结果已清理" in m.get("content", "")]
        assert len(cleared) == 5

    def test_circuit_resets_after_5_successful_microcompacts(self):
        mgr = self._make_mgr()
        mgr._circuit_open = True
        mgr._compact_failures = 3
        mgr._microcompacts_since_open = 4
        mgr.maybe_compact()
        assert mgr._circuit_open is False
        assert mgr._compact_failures == 0
        assert mgr._microcompacts_since_open == 0


class TestBuildProjection:

    def _make_mgr(self, stm, ctx_max=128000):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.short_term = stm
        mgr._ctx_max = ctx_max
        mgr._settings = MagicMock()
        mgr._settings.autocompact_buffer_tokens = 8000
        return mgr

    def test_returns_same_messages_when_tokens_under_90_percent(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 100000,
        )
        stm = ShortTermMemory()
        stm.append("user", "hello")
        mgr = self._make_mgr(stm)
        messages = [{"role": "user", "content": "hello"}]
        result = mgr.build_projection(messages)
        assert result is messages

    def test_returns_projected_at_90_percent_threshold(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 118000,
        )
        stm = ShortTermMemory()
        stm.append("assistant", "x" * 3000)
        stm.append("user", "r1")
        stm.append("user", "r2")
        stm.append("user", "r3")
        stm.append("user", "r4")
        mgr = self._make_mgr(stm)
        messages = [{"role": m["role"], "content": m["content"]} for m in stm.get_all()]
        result = mgr.build_projection(messages)
        assert "投影截断" in result[0]["content"]

    def test_returns_aggressive_at_95_percent_threshold(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 125000,
        )
        stm = ShortTermMemory()
        stm.append("tool", "Action: read[file=a.py]\nObservation: content")
        stm.append("user", "r1")
        stm.append("user", "r2")
        stm.append("user", "r3")
        mgr = self._make_mgr(stm)
        messages = [{"role": m["role"], "content": m["content"]} for m in stm.get_all()]
        result = mgr.build_projection(messages)
        assert "投影清除" in result[0]["content"]


class TestProjectMild:

    def _make_mgr(self):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr._settings = MagicMock()
        return mgr

    def test_truncates_long_assistant_messages(self):
        mgr = self._make_mgr()
        messages = [
            {"role": "assistant", "content": "x" * 2000},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
            {"role": "user", "content": "r4"},
        ]
        result = mgr._project_mild(messages)
        assert "投影截断" in result[0]["content"]
        assert len(result[0]["content"]) < 2000

    def test_truncates_long_tool_messages(self):
        mgr = self._make_mgr()
        messages = [
            {"role": "tool", "content": "Action: read[file=a.py]\n" + "x" * 2000},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
            {"role": "user", "content": "r4"},
        ]
        result = mgr._project_mild(messages)
        assert "投影截断" in result[0]["content"]

    def test_keeps_recent_4_messages_intact(self):
        mgr = self._make_mgr()
        messages = [
            {"role": "assistant", "content": "x" * 2000},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
            {"role": "user", "content": "r4"},
        ]
        result = mgr._project_mild(messages)
        assert result[-4]["content"] == "r1"
        assert result[-3]["content"] == "r2"
        assert result[-2]["content"] == "r3"
        assert result[-1]["content"] == "r4"

    def test_preserves_long_user_messages_as_is(self):
        mgr = self._make_mgr()
        long_user = "x" * 2000
        messages = [
            {"role": "user", "content": long_user},
            {"role": "user", "content": "short"},
        ]
        result = mgr._project_mild(messages)
        assert result[0]["content"] == long_user
        assert result[1]["content"] == "short"


class TestProjectAggressive:

    def _make_mgr(self):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr._settings = MagicMock()
        return mgr

    def test_clears_recoverable_tool_results(self):
        mgr = self._make_mgr()
        messages = [
            {"role": "tool", "content": "Action: read[file=a.py]\nObservation: content"},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
        ]
        result = mgr._project_aggressive(messages)
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "投影清除" in tool_msgs[0]["content"]

    def test_truncates_all_assistant_messages_over_1000(self):
        mgr = self._make_mgr()
        messages = [
            {"role": "assistant", "content": "x" * 2000},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
        ]
        result = mgr._project_aggressive(messages)
        assert "投影压缩" in result[0]["content"]

    def test_keeps_recent_3_messages_intact(self):
        mgr = self._make_mgr()
        messages = [
            {"role": "assistant", "content": "old and long " * 100},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
        ]
        result = mgr._project_aggressive(messages)
        assert result[-3]["content"] == "r1"
        assert result[-2]["content"] == "r2"
        assert result[-1]["content"] == "r3"

    def test_inserts_boundary_marker_once_before_recent(self):
        mgr = self._make_mgr()
        messages = [
            {"role": "user", "content": "old1"},
            {"role": "user", "content": "old2"},
            {"role": "user", "content": "r1"},
            {"role": "user", "content": "r2"},
            {"role": "user", "content": "r3"},
        ]
        result = mgr._project_aggressive(messages)
        boundaries = [m for m in result if m["role"] == "system" and "上下文投影" in m["content"]]
        assert len(boundaries) == 1
        boundary_idx = next(
            i for i, m in enumerate(result) if "上下文投影" in m.get("content", "")
        )
        first_recent_idx = len(messages) - 3
        assert boundary_idx == first_recent_idx

    def test_inserts_boundary_at_start_when_all_messages_recent(self):
        mgr = self._make_mgr()
        messages = [{"role": "user", "content": "r1"}]
        result = mgr._project_aggressive(messages)
        boundaries = [m for m in result if m["role"] == "system" and "上下文投影" in m["content"]]
        assert len(boundaries) == 1
        assert result[0]["role"] == "system"
        assert "上下文投影" in result[0]["content"]

    def test_preserves_non_recoverable_tool_results(self):
        mgr = self._make_mgr()
        messages = [
            {"role": "tool", "content": "Action: python_repl[code=print(1)]\nObservation: 1"},
            {"role": "user", "content": "recent"},
        ]
        result = mgr._project_aggressive(messages)
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "投影清除" not in tool_msgs[0]["content"]
