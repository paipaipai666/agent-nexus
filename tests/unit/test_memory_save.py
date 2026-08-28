"""Tests for agentnexus.tools.memory_save."""

from unittest.mock import MagicMock, patch

from agentnexus.tools.memory_save import _VALID_CATEGORIES, memory_save


class TestMemorySave:
    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_save_success(self, mock_get_emb, mock_get_ltm):
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1, 0.2]
        mock_get_emb.return_value = mock_model
        result = memory_save("用户喜欢Python编程", category="preference", importance=0.8)
        assert "已保存" in result
        assert "preference" in result
        mock_get_ltm.return_value.save.assert_called_once()
    def test_content_too_short(self):
        result = memory_save("hi")
        assert "至少需要5个字符" in result

    def test_content_empty(self):
        result = memory_save("")
        assert "至少需要5个字符" in result

    def test_invalid_category(self):
        result = memory_save("this is a long enough fact", category="invalid")
        assert "无效分类" in result
        assert "invalid" in result
        assert "fact" in result
        assert "preference" in result
        assert "note" in result

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_importance_clamping(self, mock_get_emb, mock_get_ltm):
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model

        memory_save("Very important fact", importance=1.5)
        mock_get_ltm.return_value.save.assert_called_with(
            session_id="agent_written",
            content="Very important fact",
            category="fact",
            importance=1.0,
            embedding=[0.1],
        )

        memory_save("Not important fact", importance=-0.5)
        mock_get_ltm.return_value.save.assert_called_with(
            session_id="agent_written",
            content="Not important fact",
            category="fact",
            importance=0.0,
            embedding=[0.1],
        )

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_embedding_failure(self, mock_get_emb, mock_get_ltm):
        mock_model = MagicMock()
        mock_model.encode.side_effect = Exception("model error")
        mock_get_emb.return_value = mock_model
        result = memory_save("Important fact to remember", importance=0.9)
        assert "已保存" in result
        mock_get_ltm.return_value.save.assert_called_once()
        _, kwargs = mock_get_ltm.return_value.save.call_args
        assert kwargs["embedding"] == []

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_all_valid_categories(self, mock_get_emb, mock_get_ltm):
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model
        for cat in _VALID_CATEGORIES:
            result = memory_save(f"A memory with category {cat}", category=cat)
            assert "已保存" in result
            assert cat in result

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_context_passthrough_and_concat_embedding(self, mock_get_emb, mock_get_ltm):
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model

        memory_save(
            "用户喜欢周杰伦",
            category="preference",
            importance=0.9,
            context="推荐歌曲时用户否定了莫文蔚、选择周杰伦",
        )

        # D: embedding input is content + context concatenated
        assert mock_model.encode.call_args[0][0] == (
            "用户喜欢周杰伦\n推荐歌曲时用户否定了莫文蔚、选择周杰伦"
        )
        # A: context lands in metadata
        _, kwargs = mock_get_ltm.return_value.save.call_args
        assert kwargs["metadata"] == {"context": "推荐歌曲时用户否定了莫文蔚、选择周杰伦"}

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_no_context_omits_metadata(self, mock_get_emb, mock_get_ltm):
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1]
        mock_get_emb.return_value = mock_model

        memory_save("A fact without context", category="fact", importance=0.7)

        _, kwargs = mock_get_ltm.return_value.save.call_args
        # metadata kwarg must be absent to keep existing precise assertions stable
        assert "metadata" not in kwargs
        # D: embedding input is content only
        assert mock_model.encode.call_args[0][0] == "A fact without context"
