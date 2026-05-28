"""Integration tests for memory compaction pipeline: STM growth → snip → microcompact → projection."""
from unittest.mock import MagicMock, patch

from agentnexus.memory.manager import MemoryManager
from agentnexus.memory.short_term import ShortTermMemory


class TestMemoryCompactionIntegration:
    """Full compaction pipeline: append → snip → microcompact → build_projection."""

    def _make_mgr(self, temp_agentnexus_home, ctx_max=128000):
        mock_embed = MagicMock()
        mock_embed.encode.return_value.tolist.return_value = [0.1] * 384
        mock_ltm = MagicMock()
        mock_ltm.search.return_value = []
        mock_ltm.write_counter = 0

        with patch("agentnexus.memory.manager.get_embedding_model", return_value=mock_embed):
            mgr = MemoryManager.__new__(MemoryManager)
            mgr.session_id = "compact_test"
            mgr.short_term = ShortTermMemory()
            mgr.long_term = mock_ltm
            mgr._llm = MagicMock()
            mgr._embed_model = mock_embed
            mgr._enable_long_term = True
            mgr._ctx_max = ctx_max
            mgr._compact_threshold = ctx_max - 8000
            mgr._compact_failures = 0
            mgr._circuit_open = False
            mgr._microcompacts_since_open = 0
            mgr._compacting = False
            mgr._snip_freed_tokens = 0
            mgr._recent_reads = []
            mgr._last_api_call_ts = 0.0
            mgr._on_compact = None
            mgr._on_after_compact = None
            mgr._settings = MagicMock()
            mgr._settings.snip_enabled = True
            mgr._settings.time_microcompact_interval = 0
            mgr._settings.autocompact_buffer_tokens = 8000
            mgr._settings.transcript_enabled = False
            mgr._settings.post_compact_max_files = 0
            mgr._settings.offload_enabled = False
            mgr._settings.large_result_threshold = 10000
            return mgr

    def test_append_grows_stm(self, temp_agentnexus_home):
        mgr = self._make_mgr(temp_agentnexus_home)
        for i in range(20):
            mgr.append("user", f"message {i}")
        msgs = mgr.short_term.get_all()
        assert len(msgs) == 20
        assert msgs[0]["content"] == "message 0"
        assert msgs[-1]["content"] == "message 19"

    def test_snip_reduces_message_count(self, temp_agentnexus_home):
        mgr = self._make_mgr(temp_agentnexus_home)
        for i in range(30):
            mgr.short_term.append("user", f"msg {i}")
        removed = mgr.snip(keep_recent=5)
        assert removed > 0
        remaining = mgr.short_term.get_all()
        assert len(remaining) < 30
        assert len(remaining) == 6

    def test_microcompact_cleans_old_tool_results(self, temp_agentnexus_home):
        mgr = self._make_mgr(temp_agentnexus_home)
        for i in range(10):
            mgr.short_term.append(
                "tool", f"Action: read[file=f{i}.py]\nObservation: content of file {i}"
            )
        mgr.microcompact()
        msgs = mgr.short_term.get_all()
        cleared = [m for m in msgs if "工具结果已清理" in m.get("content", "")]
        kept = [m for m in msgs if "content of file" in m.get("content", "")]
        assert len(cleared) == 5
        assert len(kept) == 5

    def test_microcompact_preserves_non_recoverable_tools(self, temp_agentnexus_home):
        mgr = self._make_mgr(temp_agentnexus_home)
        mgr.short_term.append("tool", "Action: python_repl[code=print(1)]\nObservation: 1")
        mgr.short_term.append("tool", "Action: memory_save[key=test]\nObservation: saved")
        mgr.microcompact()
        msgs = mgr.short_term.get_all()
        assert all("工具結果已清理" not in m.get("content", "") for m in msgs)

    def test_build_projection_triggers_at_high_ratio(self, temp_agentnexus_home, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 118000,
        )
        mgr = self._make_mgr(temp_agentnexus_home, ctx_max=128000)
        mgr.short_term.append("assistant", "x" * 3000)
        for i in range(4):
            mgr.short_term.append("user", f"recent {i}")

        messages = [{"role": m["role"], "content": m["content"]} for m in mgr.short_term.get_all()]
        result = mgr.build_projection(messages)
        assert "投影截断" in result[0]["content"]

    def test_build_projection_noop_when_under_threshold(self, temp_agentnexus_home, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 50000,
        )
        mgr = self._make_mgr(temp_agentnexus_home, ctx_max=128000)
        mgr.short_term.append("user", "hello")
        messages = [{"role": "user", "content": "hello"}]
        result = mgr.build_projection(messages)
        assert result is messages

    def test_snip_then_microcompact_pipeline(self, temp_agentnexus_home):
        mgr = self._make_mgr(temp_agentnexus_home)
        for i in range(15):
            mgr.short_term.append("user", f"user msg {i}")
        for i in range(10):
            mgr.short_term.append(
                "tool", f"Action: read[file=f{i}.py]\nObservation: file content {i}"
            )
        total_before = len(mgr.short_term.get_all())
        assert total_before == 25

        removed = mgr.snip(keep_recent=5)
        assert removed > 0

        mgr.microcompact()
        msgs = mgr.short_term.get_all()
        assert len(msgs) < total_before

    def test_full_compaction_with_mock_llm(self, temp_agentnexus_home, monkeypatch):
        call_count = {"n": 0}

        def fake_estimate(self):
            call_count["n"] += 1
            return 125000 if call_count["n"] <= 1 else 50000

        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            fake_estimate,
        )
        mgr = self._make_mgr(temp_agentnexus_home, ctx_max=128000)
        for i in range(20):
            mgr.short_term.append("user", f"msg {i}")
            mgr.short_term.append("assistant", f"reply {i}")

        mgr._llm.think.return_value = "<summary>会话摘要：用户讨论了多个话题。</summary>"

        saved = mgr.maybe_compact()
        assert saved > 0
        msgs = mgr.short_term.get_all()
        assert len(msgs) < 40
        assert any("会话摘要" in m.get("content", "") for m in msgs)
