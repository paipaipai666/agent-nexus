from unittest.mock import MagicMock, patch

from agentnexus.memory.manager import MemoryManager
from agentnexus.memory.short_term import ShortTermMemory


class TestInitSessionWithContext:

    def test_init_session_uses_question_with_summary(self, temp_agentnexus_home):
        stm = ShortTermMemory()
        stm.append("user", "用python实现一个快速排序算法")
        stm.append("assistant", "def quicksort(arr): ...")
        stm.compact("用户要求实现快速排序")

        mock_embed = MagicMock()
        mock_embed.encode.return_value = MagicMock(tolists=MagicMock(return_value=[[0.1] * 384]))
        mock_embed.encode.return_value.tolist.return_value = [0.1] * 384

        mock_ltm = MagicMock()
        mock_ltm.search.return_value = []

        with patch("agentnexus.memory.manager.get_embedding_model", return_value=mock_embed):
            mgr = MemoryManager.__new__(MemoryManager)
            mgr.session_id = "test"
            mgr.short_term = stm
            mgr.long_term = mock_ltm
            mgr._llm = MagicMock()
            mgr._embed_model = mock_embed
            mgr._enable_long_term = True

            mgr.init_session("讲解一下")

            call_args = mock_embed.encode.call_args
            query_text = call_args[0][0]
            # Question is always in the query; summary is prepended when available
            assert "讲解一下" in query_text
            assert "快速排序" in query_text

    def test_init_session_falls_back_to_question_without_stm(self, temp_agentnexus_home):
        stm = ShortTermMemory()

        mock_embed = MagicMock()
        mock_embed.encode.return_value.tolist.return_value = [0.1] * 384

        mock_ltm = MagicMock()
        mock_ltm.search.return_value = []

        with patch("agentnexus.memory.manager.get_embedding_model", return_value=mock_embed):
            mgr = MemoryManager.__new__(MemoryManager)
            mgr.session_id = "test"
            mgr.short_term = stm
            mgr.long_term = mock_ltm
            mgr._llm = MagicMock()
            mgr._embed_model = mock_embed
            mgr._enable_long_term = True

            mgr.init_session("讲解一下")

            call_args = mock_embed.encode.call_args
            query_text = call_args[0][0]
            assert query_text == "讲解一下"

    def test_init_session_returns_empty_without_ltm(self):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.long_term = None
        mgr.short_term = ShortTermMemory()
        result = mgr.init_session("test")
        assert result == ""


class TestReActAgentConversationMode:

    def test_conversation_mode_default_false(self):
        from agentnexus.agents.re_act_agent import ReActAgent
        agent = ReActAgent.__new__(ReActAgent)
        assert not hasattr(agent, 'conversation_mode') or agent.conversation_mode is False

    def test_conversation_mode_true(self):
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.tools.tool_executor import ToolExecutor
        mock_llm = MagicMock()
        executor = ToolExecutor()
        agent = ReActAgent(mock_llm, executor, conversation_mode=True)
        assert agent.conversation_mode is True

    def test_conversation_mode_false_creates_new_local_history(self):
        """In non-conversation mode, history is a local variable re-created each run."""
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.tools.tool_executor import ToolExecutor
        mock_llm = MagicMock()
        mock_llm.think.return_value = "done"
        executor = ToolExecutor()
        agent = ReActAgent(mock_llm, executor, conversation_mode=False)
        # Should not raise — history is now local, not self.history
        result = agent.run("test question")
        assert result == "done"

    def test_build_conversation_context_empty_stm(self):
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.tools.tool_executor import ToolExecutor
        mock_llm = MagicMock()
        executor = ToolExecutor()
        agent = ReActAgent(mock_llm, executor, conversation_mode=True)

        mock_mm = MagicMock()
        mock_mm.short_term = ShortTermMemory()
        result = agent._build_conversation_context(mock_mm)
        assert result == ""

    def test_build_conversation_context_with_messages(self):
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.tools.tool_executor import ToolExecutor
        mock_llm = MagicMock()
        executor = ToolExecutor()
        agent = ReActAgent(mock_llm, executor, conversation_mode=True)

        stm = ShortTermMemory()
        stm.append("user", "用python实现快速排序")
        stm.append("assistant", "def quicksort(arr): ...")

        mock_mm = MagicMock()
        mock_mm.short_term = stm
        result = agent._build_conversation_context(mock_mm)
        assert "近期对话" in result
        assert "快速排序" in result
        assert "用户" in result
        assert "助手" in result

    def test_build_conversation_context_truncates_long_content(self):
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.tools.tool_executor import ToolExecutor
        mock_llm = MagicMock()
        executor = ToolExecutor()
        agent = ReActAgent(mock_llm, executor, conversation_mode=True)

        stm = ShortTermMemory()
        stm.append("user", "x" * 1000)

        mock_mm = MagicMock()
        mock_mm.short_term = stm
        result = agent._build_conversation_context(mock_mm)
        for line in result.split("\n"):
            if line.startswith("用户:"):
                assert len(line) <= 510

    def test_build_conversation_context_limits_to_six_messages(self):
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.tools.tool_executor import ToolExecutor
        mock_llm = MagicMock()
        executor = ToolExecutor()
        agent = ReActAgent(mock_llm, executor, conversation_mode=True)

        stm = ShortTermMemory()
        for i in range(10):
            stm.append("user" if i % 2 == 0 else "assistant", f"msg{i}")

        mock_mm = MagicMock()
        mock_mm.short_term = stm
        result = agent._build_conversation_context(mock_mm)
        user_lines = [line for line in result.split("\n") if line.startswith("用户:") or line.startswith("助手:")]
        assert len(user_lines) <= 6

    def test_build_conversation_context_with_summary(self):
        """When STM has a summary, it should be shown prominently."""
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.tools.tool_executor import ToolExecutor
        mock_llm = MagicMock()
        executor = ToolExecutor()
        agent = ReActAgent(mock_llm, executor, conversation_mode=True)

        stm = ShortTermMemory()
        stm.append("user", "写一个排序算法")
        stm.append("assistant", "def quicksort(arr): ...")
        stm.compact("用户要求实现快速排序，已完成基本实现。")

        mock_mm = MagicMock()
        mock_mm.short_term = stm
        result = agent._build_conversation_context(mock_mm)
        assert "对话历史摘要" in result
        assert "快速排序" in result
        assert "最近对话" in result

    def test_build_conversation_context_no_summary_fallback(self):
        """Without summary, should show recent messages directly."""
        from agentnexus.agents.re_act_agent import ReActAgent
        from agentnexus.tools.tool_executor import ToolExecutor
        mock_llm = MagicMock()
        executor = ToolExecutor()
        agent = ReActAgent(mock_llm, executor, conversation_mode=True)

        stm = ShortTermMemory()
        stm.append("user", "测试消息")

        mock_mm = MagicMock()
        mock_mm.short_term = stm
        result = agent._build_conversation_context(mock_mm)
        assert "近期对话" in result
        assert "对话历史摘要" not in result

    def test_get_summary_method(self):
        """ShortTermMemory.get_summary() should return the compacted summary."""
        stm = ShortTermMemory()
        assert stm.get_summary() == ""
        stm.compact("这是测试摘要")
        assert stm.get_summary() == "这是测试摘要"
