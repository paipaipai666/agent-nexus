from unittest.mock import MagicMock

from agentnexus.memory.extraction import (
    _check_conflict,
    _embed_text,
    extract_and_save_memories,
    extract_xml_tag,
    iter_memory_items,
    parse_memory_payload,
)


class TestExtractXmlTag:

    def test_found_tag(self):
        result = extract_xml_tag("<summary>hello world</summary>", "summary")
        assert result == "hello world"

    def test_not_found(self):
        result = extract_xml_tag("no tags here", "summary")
        assert result is None

    def test_nested_tags(self):
        result = extract_xml_tag("<outer><inner>data</inner></outer>", "outer")
        assert result == "<inner>data</inner>"

    def test_case_insensitive(self):
        result = extract_xml_tag("<SUMMARY>content</SUMMARY>", "summary")
        assert result == "content"


class TestParseMemoryPayload:

    def test_valid_json(self):
        data = '{"user_preference": ["likes Python"]}'
        result = parse_memory_payload(data)
        assert result == {"user_preference": ["likes Python"]}

    def test_json_with_fences(self):
        data = '```json\n{"user_preference": ["likes Python"]}\n```'
        result = parse_memory_payload(data)
        assert result == {"user_preference": ["likes Python"]}

    def test_invalid_json_returns_empty(self):
        result = parse_memory_payload("not json at all")
        assert result == {}


class TestIterMemoryItems:

    def test_valid_data_with_multiple_categories(self):
        data = {
            "preference": ["likes Python"],
            "fact": ["uses VSCode"],
            "note": ["prefers dark mode"],
        }
        items = list(iter_memory_items(data))
        assert len(items) == 3
        categories = [cat for cat, _, _, _ in items]
        assert "preference" in categories
        assert "fact" in categories
        assert "note" in categories

    def test_dict_items_with_content(self):
        data = {"fact": [{"content": "some fact"}]}
        items = list(iter_memory_items(data))
        assert len(items) == 1
        assert items[0][2] == "some fact"
        # dict item without context → empty context string
        assert items[0][3] == ""

    def test_dict_items_with_text(self):
        data = {"fact": [{"text": "some fact"}]}
        items = list(iter_memory_items(data))
        assert len(items) == 1
        assert items[0][2] == "some fact"

    def test_dict_items_with_context(self):
        data = {
            "preference": [
                {"content": "用户喜欢周杰伦", "context": "推荐歌曲时用户否定了莫文蔚、选择周杰伦"}
            ]
        }
        items = list(iter_memory_items(data))
        assert len(items) == 1
        category, importance, content, context = items[0]
        assert category == "preference"
        assert content == "用户喜欢周杰伦"
        assert context == "推荐歌曲时用户否定了莫文蔚、选择周杰伦"

    def test_string_items_have_empty_context(self):
        data = {"fact": ["a plain string fact"]}
        items = list(iter_memory_items(data))
        assert len(items) == 1
        assert items[0][2] == "a plain string fact"
        assert items[0][3] == ""

    def test_short_items_skipped(self):
        data = {"preference": ["hi", "long enough item"]}
        items = list(iter_memory_items(data))
        assert len(items) == 1
        assert items[0][2] == "long enough item"

    def test_empty_dict(self):
        items = list(iter_memory_items({}))
        assert items == []


class TestEmbedText:

    def test_content_only_when_context_empty(self):
        assert _embed_text("用户喜欢周杰伦", "") == "用户喜欢周杰伦"

    def test_content_only_when_context_none(self):
        assert _embed_text("用户喜欢周杰伦", None) == "用户喜欢周杰伦"

    def test_concatenates_context(self):
        result = _embed_text("用户喜欢周杰伦", "推荐歌曲时否定了莫文蔚")
        assert result == "用户喜欢周杰伦\n推荐歌曲时否定了莫文蔚"


class TestCheckConflict:

    def test_same_scene_contradiction_is_conflict(self):
        llm = MagicMock()
        # same scene, mutually exclusive → conflict
        llm.think.return_value = "矛盾"
        assert _check_conflict(
            llm,
            old_content="用户喜欢莫文蔚",
            new_content="用户不喜欢莫文蔚",
            old_context="聊到歌手偏好",
            new_context="聊到歌手偏好",
        ) is True

    def test_different_scene_is_not_conflict(self):
        llm = MagicMock()
        # conclusions look contradictory but contexts are different scenes
        llm.think.return_value = "不矛盾"
        assert _check_conflict(
            llm,
            old_content="用户喜欢莫文蔚",
            new_content="用户喜欢周杰伦",
            old_context="深夜情感歌场景",
            new_context="推荐歌曲场景，用户否定了莫文蔚、选择周杰伦",
        ) is False

    def test_llm_failure_assumes_no_conflict(self):
        llm = MagicMock()
        llm.think.side_effect = Exception("llm down")
        assert _check_conflict(
            llm, "old", "new", "ctx-old", "ctx-new"
        ) is False


class TestExtractAndSaveMemories:

    def test_mock_llm_returns_json_and_save_called(self):
        llm = MagicMock()
        llm.think.return_value = '{"preference": ["likes Python"]}'
        embed_model = MagicMock()
        embed_model.encode.return_value.tolist.return_value = [0.1, 0.2]
        long_term = MagicMock()
        long_term.search.return_value = []

        extract_and_save_memories(
            llm=llm,
            embed_model=embed_model,
            long_term=long_term,
            session_id="test-session",
            question="What does the user like?",
            answer="Python",
        )

        long_term.save.assert_called_once()
        call_kwargs = long_term.save.call_args[1]
        assert call_kwargs["session_id"] == "test-session"
        assert call_kwargs["content"] == "likes Python"
        assert call_kwargs["category"] == "preference"
        # plain-string item → no context → metadata kwarg omitted entirely
        # (preserves the historical save() call shape for callers/tests that
        # assert against it).
        assert "metadata" not in call_kwargs

    def test_context_passed_through_to_save(self):
        llm = MagicMock()
        llm.think.return_value = (
            '{"preference": [{"content": "用户喜欢周杰伦", '
            '"context": "推荐歌曲时用户否定了莫文蔚、选择周杰伦"}]}'
        )
        embed_model = MagicMock()
        embed_model.encode.return_value.tolist.return_value = [0.1, 0.2]
        long_term = MagicMock()
        long_term.search.return_value = []

        extract_and_save_memories(
            llm=llm,
            embed_model=embed_model,
            long_term=long_term,
            session_id="s1",
            question="推荐一首歌",
            answer="我推荐了晴天",
        )

        long_term.save.assert_called_once()
        call_kwargs = long_term.save.call_args[1]
        assert call_kwargs["content"] == "用户喜欢周杰伦"
        assert call_kwargs["metadata"] == {"context": "推荐歌曲时用户否定了莫文蔚、选择周杰伦"}
        # D: embedding input should be content+context concatenated
        encode_arg = embed_model.encode.call_args[0][0]
        assert encode_arg == "用户喜欢周杰伦\n推荐歌曲时用户否定了莫文蔚、选择周杰伦"

    def test_context_empty_passes_none_metadata(self):
        llm = MagicMock()
        llm.think.return_value = '{"fact": [{"content": "uses VSCode"}]}'
        embed_model = MagicMock()
        embed_model.encode.return_value.tolist.return_value = [0.1]
        long_term = MagicMock()
        long_term.search.return_value = []

        extract_and_save_memories(
            llm=llm, embed_model=embed_model, long_term=long_term,
            session_id="s1", question="q", answer="a",
        )

        call_kwargs = long_term.save.call_args[1]
        assert "metadata" not in call_kwargs
        # D: no context → embedding input is content only
        assert embed_model.encode.call_args[0][0] == "uses VSCode"
