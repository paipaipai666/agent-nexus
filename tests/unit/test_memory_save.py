"""Tests for agentnexus.tools.memory_save."""

from unittest.mock import MagicMock, patch

from agentnexus.tools.memory_save import memory_save


class TestMemorySave:
    def test_short_content(self):
        result = memory_save("hi")
        assert "至少需要5个字符" in result

    def test_invalid_category(self):
        result = memory_save("this is a long enough fact", category="invalid")
        assert "无效分类" in result

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_successful_save(self, mock_embed, mock_ltm):
        mock_embed.return_value.encode.return_value = MagicMock()
        mock_embed.return_value.encode.return_value.tolist.return_value = [0.1, 0.2]
        result = memory_save("The user prefers Python over Java", category="user_preference")
        assert "已保存" in result
        mock_ltm.return_value.save.assert_called_once()

    @patch("agentnexus.tools.memory_save.get_long_term_memory")
    @patch("agentnexus.tools.memory_save.get_embedding_model")
    def test_save_without_embedding_on_error(self, mock_embed, mock_ltm):
        mock_embed.return_value.encode.side_effect = Exception("model error")
        result = memory_save("Important fact to remember", importance=0.9)
        assert "已保存" in result
        mock_ltm.return_value.save.assert_called_once()
