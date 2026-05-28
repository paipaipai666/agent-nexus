"""Integration tests for multi-turn conversation with long-term memory (LTM)."""
from unittest.mock import MagicMock, patch

from agentnexus.memory.manager import MemoryManager
from agentnexus.memory.short_term import ShortTermMemory


class TestMultiTurnLTM:
    """Multi-turn conversation lifecycle: init_session → append → conclude → new session with LTM."""

    def _make_mgr(self, temp_agentnexus_home, mock_ltm=None):
        mock_embed = MagicMock()
        mock_embed.encode.return_value.tolist.return_value = [0.1] * 384

        if mock_ltm is None:
            mock_ltm = MagicMock()
            mock_ltm.search.return_value = []
            mock_ltm.write_counter = 0

        with patch("agentnexus.memory.manager.get_embedding_model", return_value=mock_embed):
            mgr = MemoryManager.__new__(MemoryManager)
            mgr.session_id = "ltm_test"
            mgr.short_term = ShortTermMemory()
            mgr.long_term = mock_ltm
            mgr._llm = MagicMock()
            mgr._embed_model = mock_embed
            mgr._enable_long_term = True
            mgr._ctx_max = 128000
            mgr._compact_threshold = 120000
            mgr._compact_failures = 0
            mgr._circuit_open = False
            mgr._microcompacts_since_open = 0
            mgr._compacting = False
            mgr._snip_freed_tokens = 0
            mgr._recent_reads = []
            mgr._last_api_call_ts = 0.0
            mgr._last_write_count = 0
            mgr._on_compact = None
            mgr._on_after_compact = None
            mgr._settings = MagicMock()
            mgr._settings.snip_enabled = False
            mgr._settings.time_microcompact_interval = 0
            mgr._settings.autocompact_buffer_tokens = 8000
            mgr._settings.transcript_enabled = False
            mgr._settings.post_compact_max_files = 0
            mgr._settings.offload_enabled = False
            mgr._settings.large_result_threshold = 10000
            return mgr

    def test_init_session_returns_empty_without_ltm(self, temp_agentnexus_home):
        mgr = self._make_mgr(temp_agentnexus_home)
        mgr.long_term = None
        ctx = mgr.init_session("hello")
        assert ctx == ""

    def test_init_session_returns_empty_when_no_memories(self, temp_agentnexus_home):
        mock_ltm = MagicMock()
        mock_ltm.search.return_value = []
        mock_ltm.write_counter = 0
        mgr = self._make_mgr(temp_agentnexus_home, mock_ltm)
        ctx = mgr.init_session("hello")
        assert ctx == ""

    def test_init_session_returns_ltm_context(self, temp_agentnexus_home):
        mock_ltm = MagicMock()
        mock_ltm.write_counter = 0
        mock_ltm.search.return_value = [
            {"category": "user_preference", "_score": 0.8, "content": "用户喜欢Python"},
        ]
        mgr = self._make_mgr(temp_agentnexus_home, mock_ltm)
        ctx = mgr.init_session("写代码")
        assert "Python" in ctx
        assert "偏好" in ctx

    def test_append_adds_messages_to_stm(self, temp_agentnexus_home):
        mgr = self._make_mgr(temp_agentnexus_home)
        mgr.append("user", "你好")
        mgr.append("assistant", "你好！有什么可以帮你的？")
        msgs = mgr.short_term.get_all()
        assert len(msgs) == 2
        assert msgs[0]["content"] == "你好"
        assert msgs[1]["content"] == "你好！有什么可以帮你的？"

    def test_conclude_extracts_memories(self, temp_agentnexus_home):
        mock_ltm = MagicMock()
        mock_ltm.write_counter = 0
        mock_ltm.search.return_value = []
        mock_embed = MagicMock()
        mock_embed.encode.return_value.tolist.return_value = [0.1] * 384

        mgr = self._make_mgr(temp_agentnexus_home, mock_ltm)
        mgr._embed_model = mock_embed
        mgr._llm.think.return_value = (
            '```json\n{"user_preference": [{"content": "用户喜欢简洁回答"}]}\n```'
        )

        mgr.conclude("你好", "你好！")

        mock_ltm.save.assert_called()
        call_kwargs = mock_ltm.save.call_args
        assert call_kwargs[1]["content"] == "用户喜欢简洁回答"
        assert call_kwargs[1]["category"] == "user_preference"

    def test_new_session_retrieves_ltm_from_previous(self, temp_agentnexus_home):
        mock_ltm = MagicMock()
        mock_ltm.write_counter = 0
        mock_embed = MagicMock()
        mock_embed.encode.return_value.tolist.return_value = [0.1] * 384

        mock_ltm.search.side_effect = [
            [],
            [{"category": "user_preference", "_score": 0.8, "content": "用户喜欢Python"}],
        ]

        mgr = self._make_mgr(temp_agentnexus_home, mock_ltm)
        mgr._embed_model = mock_embed
        mgr._llm.think.return_value = (
            '```json\n{"user_preference": [{"content": "用户喜欢Python"}]}\n```'
        )

        ctx1 = mgr.init_session("写一个排序算法")
        assert ctx1 == ""

        mgr.append("user", "写一个排序算法")
        mgr.append("assistant", "好的，这是快速排序...")
        mgr.conclude("写一个排序算法", "好的，这是快速排序...")

        ctx2 = mgr.init_session("改成降序")
        assert "Python" in ctx2

    def test_multi_turn_accumulates_memories(self, temp_agentnexus_home):
        mock_ltm = MagicMock()
        mock_ltm.write_counter = 0
        mock_embed = MagicMock()
        mock_embed.encode.return_value.tolist.return_value = [0.1] * 384

        mock_ltm.search.side_effect = [
            [],
            [{"category": "entity_fact", "_score": 0.7, "content": "用户有Flask项目"}],
            [
                {"category": "entity_fact", "_score": 0.7, "content": "用户有Flask项目"},
                {"category": "user_preference", "_score": 0.8, "content": "使用SQLite数据库"},
            ],
        ]

        mgr = self._make_mgr(temp_agentnexus_home, mock_ltm)
        mgr._embed_model = mock_embed
        mgr._llm.think.return_value = '{}'

        ctx1 = mgr.init_session("项目结构")
        assert ctx1 == "" or "Flask" not in ctx1

        mgr.short_term.append("user", "项目结构")
        mgr.short_term.append("assistant", "Flask项目结构如下...")
        mgr._llm.think.return_value = (
            '```json\n{"entity_fact": [{"content": "用户有Flask项目"}]}\n```'
        )
        mgr.conclude("项目结构", "Flask项目结构如下...")

        ctx2 = mgr.init_session("添加用户认证")
        assert "Flask" in ctx2

        mgr.short_term.append("user", "添加用户认证")
        mgr.short_term.append("assistant", "使用Flask-Login...")
        mgr._llm.think.return_value = (
            '```json\n{"user_preference": [{"content": "使用SQLite数据库"}]}\n```'
        )
        mgr.conclude("添加用户认证", "使用Flask-Login...")

        ctx3 = mgr.init_session("部署方案")
        assert "Flask" in ctx3
        assert "SQLite" in ctx3 or "数据库" in ctx3

    def test_conclude_does_not_corrupt_prior_stm(self, temp_agentnexus_home):
        mock_ltm = MagicMock()
        mock_ltm.search.return_value = []
        mock_ltm.write_counter = 0

        mgr = self._make_mgr(temp_agentnexus_home, mock_ltm)
        mgr._llm.think.return_value = '{}'

        mgr.short_term.append("user", "第一轮问题")
        mgr.short_term.append("assistant", "第一轮回答")
        mgr.conclude("第一轮问题", "第一轮回答")

        msgs_after_conclude = mgr.short_term.get_all()
        contents = [m["content"] for m in msgs_after_conclude]
        assert "第一轮问题" in contents
        assert "第一轮回答" in contents

        mgr.short_term.append("user", "第二轮问题")
        mgr.short_term.append("assistant", "第二轮回答")
        mgr.conclude("第二轮问题", "第二轮回答")

        final_msgs = mgr.short_term.get_all()
        final_contents = [m["content"] for m in final_msgs]
        assert "第一轮问题" in final_contents
        assert "第二轮问题" in final_contents

    def test_has_new_memories_after_conclude(self, temp_agentnexus_home):
        mock_ltm = MagicMock()
        mock_ltm.search.return_value = []
        mock_ltm.write_counter = 0

        mgr = self._make_mgr(temp_agentnexus_home, mock_ltm)
        mgr._llm.think.return_value = (
            '```json\n{"entity_fact": [{"content": "新记忆内容"}]}\n```'
        )

        mgr.init_session("test")
        assert not mgr.has_new_memories()

        mock_ltm.write_counter = 1
        assert mgr.has_new_memories()
