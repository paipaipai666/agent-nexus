"""P0-3: Multi-turn conversation context regression test.

Simulates user "modify here → refactor there" multi-turn interaction,
verifying that context from earlier turns is preserved and accessible
in later turns.
"""
from unittest.mock import MagicMock, patch

from agentnexus.memory.manager import MemoryManager
from agentnexus.memory.short_term import ShortTermMemory


class TestMultiTurnContext:
    """Multi-turn conversation: context must survive across turns."""

    def _make_mgr(self, temp_agentnexus_home, mock_embed=None, mock_ltm=None):
        if mock_embed is None:
            mock_embed = MagicMock()
            mock_embed.encode.return_value.tolist.return_value = [0.1] * 384

        if mock_ltm is None:
            mock_ltm = MagicMock()
            mock_ltm.search.return_value = []
            mock_ltm.write_counter = 0

        with patch("agentnexus.memory.manager.get_embedding_model", return_value=mock_embed):
            mgr = MemoryManager.__new__(MemoryManager)
            mgr.session_id = "multi_turn_test"
            mgr.short_term = ShortTermMemory()
            mgr.long_term = mock_ltm
            mgr._llm = MagicMock()
            mgr._embed_model = mock_embed
            mgr._enable_long_term = True
            mgr._ctx_max = 8000
            return mgr

    def test_earlier_context_available_in_later_turn(self, temp_agentnexus_home):
        """Information from turn 1 appears in turn 2's LTM query."""
        mock_ltm = MagicMock()
        mock_ltm.write_counter = 0
        # Turn 2 search returns memory extracted from turn 1
        mock_ltm.search.return_value = [
            {"category": "user_preference", "_score": 0.8, "content": "用户要求使用Python"},
        ]

        mock_embed = MagicMock()
        mock_embed.encode.return_value.tolist.return_value = [0.1] * 384

        mgr = self._make_mgr(temp_agentnexus_home, mock_embed, mock_ltm)

        # Turn 1
        mgr.short_term.append("user", "用Python实现一个计算器")
        mgr.short_term.append("assistant", "好的，以下是Python计算器实现...")

        # Simulate conclude from turn 1
        mgr._llm.think.return_value = '```json\n{"user_preference": [{"content": "用户要求使用Python"}]}\n```'
        mgr.conclude("用Python实现一个计算器", "好的，以下是Python计算器实现...")

        # Turn 2: init_session should include turn 1's memory
        context = mgr.init_session("添加GUI界面")

        assert "Python" in context
        assert "计算器" in context or "用户" in context

    def test_stm_accumulates_across_turns(self, temp_agentnexus_home):
        """Short-term memory accumulates user/assistant messages."""
        mgr = self._make_mgr(temp_agentnexus_home)

        # Turn 1
        mgr.short_term.append("user", "写一个排序算法")
        mgr.short_term.append("assistant", "这是快速排序实现")

        # Turn 2
        mgr.short_term.append("user", "改成降序排列")
        mgr.short_term.append("assistant", "已修改为降序")

        messages = mgr.short_term.get_all()
        contents = [m["content"] for m in messages]

        assert "写一个排序算法" in contents
        assert "改成降序排列" in contents

    def test_conclude_does_not_lose_prior_context(self, temp_agentnexus_home):
        """Conclude in turn N does not corrupt turn N-1 memory."""
        mock_ltm = MagicMock()
        mock_ltm.search.return_value = []
        mock_ltm.write_counter = 0
        mock_embed = MagicMock()
        mock_embed.encode.return_value.tolist.return_value = [0.1] * 384

        mgr = self._make_mgr(temp_agentnexus_home, mock_embed, mock_ltm)

        mgr.short_term.append("user", "定义User模型")
        mgr.short_term.append("assistant", "class User(Base): ...")
        mgr._llm.think.return_value = '```json\n{"entity_fact": [{"content": "定义了User模型"}]}\n```'
        mgr.conclude("定义User模型", "class User(Base): ...")
        turn1_save_count = mock_ltm.save.call_count

        # Turn 2
        mgr.short_term.append("user", "添加email字段")
        mgr.short_term.append("assistant", "email = Column(String)")
        mgr._llm.think.return_value = '```json\n{"entity_fact": [{"content": "添加了email字段"}]}\n```'
        mgr.conclude("添加email字段", "email = Column(String)")
        turn2_save_count = mock_ltm.save.call_count

        assert turn2_save_count > turn1_save_count
        all_save_calls = mock_ltm.save.call_args_list
        contents = [c[1]["content"] for c in all_save_calls]
        assert "定义了User模型" in contents
        assert "添加了email字段" in contents

    def test_multi_turn_with_ltm_init_session(self, temp_agentnexus_home):
        """Multiple LTM-recalled contexts merge correctly."""
        mock_ltm = MagicMock()
        mock_ltm.write_counter = 0
        mock_ltm.search.side_effect = [
            # Turn 1: no prior memory
            [],
            # Turn 2: memory from turn 1
            [{"category": "entity_fact", "_score": 0.7, "content": "用户有一个Flask项目"}],
            # Turn 3: memory from turns 1+2
            [
                {"category": "entity_fact", "_score": 0.7, "content": "用户有一个Flask项目"},
                {"category": "user_preference", "_score": 0.8, "content": "使用SQLite数据库"},
            ],
        ]
        mock_embed = MagicMock()
        mock_embed.encode.return_value.tolist.return_value = [0.1] * 384

        with patch("agentnexus.memory.manager.get_embedding_model", return_value=mock_embed):
            mgr = MemoryManager.__new__(MemoryManager)
            mgr.session_id = "multi_turn"
            mgr.short_term = ShortTermMemory()
            mgr.long_term = mock_ltm
            mgr._llm = MagicMock()
            mgr._embed_model = mock_embed
            mgr._enable_long_term = True
            mgr._llm.think.return_value = '{}'

            # Turn 1: no prior context
            ctx1 = mgr.init_session("项目结构")
            assert ctx1 == "" or "Flask" not in ctx1

            # Turn 2: should see turn 1's memory
            ctx2 = mgr.init_session("添加用户认证")
            assert "Flask" in ctx2

            # Turn 3: should see both memories
            ctx3 = mgr.init_session("部署方案")
            assert "Flask" in ctx3
            assert "SQLite" in ctx3 or "数据库" in ctx3

    def test_long_conversation_does_not_drop_early_context(self, temp_agentnexus_home):
        """A 10-turn conversation still preserves early context."""
        mock_ltm = MagicMock()
        mock_ltm.write_counter = 0
        mock_ltm.search.return_value = []
        mock_embed = MagicMock()
        mock_embed.encode.return_value.tolist.return_value = [0.1] * 384

        mgr = self._make_mgr(temp_agentnexus_home, mock_embed, mock_ltm)
        mgr._llm.think.return_value = '{}'

        for i in range(10):
            mgr.short_term.append("user", f"Turn {i} question")
            mgr.short_term.append("assistant", f"Turn {i} answer")

        messages = mgr.short_term.get_all()
        assert len(messages) == 20  # 10 turns × 2 messages
        assert messages[0]["content"] == "Turn 0 question"

    def test_build_projection_after_multi_turn(self, temp_agentnexus_home):
        """build_projection does not truncate recent context after many turns."""
        mock_embed = MagicMock()
        mock_embed.encode.return_value.tolist.return_value = [0.1] * 384
        mock_ltm = MagicMock()
        mock_ltm.search.return_value = []
        mock_ltm.write_counter = 0

        mgr = self._make_mgr(temp_agentnexus_home, mock_embed, mock_ltm)

        for i in range(8):
            mgr.short_term.append("user", f"Q{i}: what is Python?")
            mgr.short_term.append("assistant", f"A{i}: Python is...")
            mgr.mark_api_call()

        projection = mgr.build_projection(mgr.short_term.get_all())
        content_all = " ".join(m.get("content", "") for m in projection)
        assert "Q7:" in content_all
        assert "Q0:" in content_all
