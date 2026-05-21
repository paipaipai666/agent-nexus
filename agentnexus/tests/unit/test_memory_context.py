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


class TestBuildProjection:

    def _make_mgr(self, stm, ctx_max=128000, buffer_tokens=8000):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.short_term = stm
        mgr._ctx_max = ctx_max
        mgr._settings = MagicMock()
        mgr._settings.autocompact_buffer_tokens = buffer_tokens
        return mgr

    def test_projection_noop_under_threshold(self):
        stm = ShortTermMemory()
        for i in range(10):
            stm.append("user", f"msg{i}")
        mgr = self._make_mgr(stm, ctx_max=128000)
        messages = [{"role": m["role"], "content": m["content"]} for m in stm.get_all()]
        result = mgr.build_projection(messages)
        assert len(result) == len(messages)

    def test_projection_mild_truncates_long(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 120000,  # 93.75% of 128k
        )
        stm = ShortTermMemory()
        stm.append("system", "system prompt")
        long_content = "x" * 3000
        stm.append("assistant", long_content)
        stm.append("user", "short")
        stm.append("user", "recent1")
        stm.append("user", "recent2")
        stm.append("user", "recent3")
        stm.append("user", "recent4")

        mgr = self._make_mgr(stm)
        messages = [{"role": m["role"], "content": m["content"]} for m in stm.get_all()]
        result = mgr.build_projection(messages)
        # Last 4 should be intact
        assert result[-4]["content"] == "recent1"
        # Long assistant should be truncated (not last 4)
        assert "投影截断" in result[1]["content"]
        assert len(result[1]["content"]) < len(long_content)

    def test_projection_aggressive_clears_tools(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 125000,  # 97.6% of 128k
        )
        stm = ShortTermMemory()
        stm.append("system", "system msg")
        stm.append("assistant", "some response")
        stm.append("tool", "Action: read[file=a.py]\nObservation: file content here")
        stm.append("assistant", "final recent message")
        stm.append("user", "recent2")
        stm.append("user", "recent3")

        mgr = self._make_mgr(stm)
        messages = [{"role": m["role"], "content": m["content"]} for m in stm.get_all()]
        result = mgr.build_projection(messages)
        # Tool result should be cleared
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "投影清除" in tool_msgs[0]["content"]
        # Boundary marker should be present
        system_msgs = [m for m in result if m["role"] == "system"]
        assert any("上下文投影" in m["content"] for m in system_msgs)
        # Last 3 should be intact
        assert result[-1]["content"] == "recent3"

    def test_projection_non_destructive(self, monkeypatch):
        monkeypatch.setattr(
            "agentnexus.memory.short_term.ShortTermMemory.estimate_tokens",
            lambda self: 125000,
        )
        stm = ShortTermMemory()
        stm.append("system", "system msg")
        stm.append("assistant", "long " * 500)
        stm.append("user", "recent1")
        stm.append("user", "recent2")
        stm.append("user", "recent3")

        mgr = self._make_mgr(stm)
        original = [{"role": m["role"], "content": m["content"]} for m in stm.get_all()]
        original_copy = [dict(m) for m in original]
        mgr.build_projection(original)
        for i, orig in enumerate(original_copy):
            assert original[i]["content"] == orig["content"]


class TestCompactFull:
    def test_compact_full_auto(self):
        stm = ShortTermMemory()
        for i in range(20):
            stm.append("user", f"msg{i}")
        stm.compact_full("摘要内容", message_count=20, is_auto=True)
        msgs = stm.get_all()
        assert len(msgs) == 1
        assert "本会话是从之前一次因上下文耗尽而中断的对话延续过来的" in msgs[0]["content"]
        assert "摘要内容" in msgs[0]["content"]

    def test_compact_full_manual(self):
        stm = ShortTermMemory()
        for i in range(20):
            stm.append("user", f"msg{i}")
        stm.compact_full("摘要内容", message_count=20, is_auto=False)
        msgs = stm.get_all()
        assert len(msgs) == 1
        assert "对话已被手动压缩" in msgs[0]["content"]

    def test_compact_full_no_original_retained(self):
        stm = ShortTermMemory()
        stm.append("user", "important message")
        stm.append("assistant", "critical decision")
        stm.compact_full("summary", is_auto=True)
        msgs = stm.get_all()
        assert len(msgs) == 1
        for msg in msgs:
            assert "important message" not in msg["content"]


class TestMicroCompactKeepLast:
    def test_keeps_last_5_recoverable(self):
        from agentnexus.memory.manager import MemoryManager, _parse_tool_message, _RECOVERABLE_TOOLS

        mgr = MemoryManager.__new__(MemoryManager)
        mgr.short_term = ShortTermMemory()
        mgr._settings = MagicMock()
        mgr._settings.time_microcompact_interval = 0
        mgr._settings.snip_enabled = False

        for i in range(10):
            mgr.short_term.append("tool",
                f"Action: read[file=file{i}.py]\nObservation: content of file {i}")
        mgr.microcompact()
        msgs = mgr.short_term.get_all()
        cleared = [m for m in msgs if "工具结果已清理" in m.get("content", "")]
        kept = [m for m in msgs if "content of file" in m.get("content", "")]
        assert len(cleared) == 5
        assert len(kept) == 5
        assert "file 9" in kept[-1]["content"]


class TestBridgeRead:
    def test_bridge_read_tracks_files(self, tmp_path):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.short_term = ShortTermMemory()
        mgr._settings = MagicMock()
        mgr._recent_reads = []
        mgr._settings.post_compact_max_files = 5
        mgr._settings.post_compact_token_per_file = 5000
        mgr._settings.post_compact_token_budget = 50000
        mgr._on_compact = None
        mgr._transcript_dir = str(tmp_path)
        mgr._settings.transcript_enabled = False
        mgr.session_id = "test"

        mgr.bridge_read("/tmp/test.py", "print('hello')")
        mgr.bridge_read("/tmp/test2.py", "x = 1")
        assert len(mgr._recent_reads) == 2
        assert mgr._recent_reads[0][0] == "/tmp/test.py"

    def test_bridge_read_deduplication(self):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.short_term = ShortTermMemory()
        mgr._settings = MagicMock()
        mgr._recent_reads = []
        mgr._settings.post_compact_max_files = 3
        mgr._settings.post_compact_token_per_file = 5000
        mgr._settings.post_compact_token_budget = 50000
        mgr._transcript_dir = "."
        mgr._settings.transcript_enabled = False
        mgr.session_id = "test"

        for _ in range(3):
            mgr.bridge_read("/tmp/a.py", "a")
        mgr.bridge_read("/tmp/b.py", "b")
        assert len(mgr._recent_reads) == 4


class TestTranscriptBackup:
    def test_transcript_writes_file(self, tmp_path):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.short_term = ShortTermMemory()
        mgr.short_term.append("user", "hello")
        mgr.short_term.append("assistant", "world")
        mgr.session_id = "test"
        mgr._transcript_dir = str(tmp_path)
        mgr._settings = MagicMock()
        mgr._settings.transcript_enabled = True
        mgr._on_compact = None

        mgr._write_transcript()
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "hello" in content
        assert "world" in content

    def test_transcript_disabled_skips(self, tmp_path):
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.short_term = ShortTermMemory()
        mgr.short_term.append("user", "hello")
        mgr.session_id = "test"
        mgr._transcript_dir = str(tmp_path)
        mgr._settings = MagicMock()
        mgr._settings.transcript_enabled = False

        mgr._write_transcript()
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 0
