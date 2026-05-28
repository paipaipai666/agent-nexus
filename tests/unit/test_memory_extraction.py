from unittest.mock import MagicMock

from agentnexus.memory.extraction import (
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
            "user_preference": ["likes Python"],
            "entity_fact": ["uses VSCode"],
            "conclusion": ["prefers dark mode"],
        }
        items = list(iter_memory_items(data))
        assert len(items) == 3
        categories = [cat for cat, _, _ in items]
        assert "user_preference" in categories
        assert "entity_fact" in categories
        assert "conclusion" in categories

    def test_dict_items_with_content(self):
        data = {"entity_fact": [{"content": "some fact"}]}
        items = list(iter_memory_items(data))
        assert len(items) == 1
        assert items[0][2] == "some fact"

    def test_dict_items_with_text(self):
        data = {"entity_fact": [{"text": "some fact"}]}
        items = list(iter_memory_items(data))
        assert len(items) == 1
        assert items[0][2] == "some fact"

    def test_short_items_skipped(self):
        data = {"user_preference": ["hi", "long enough item"]}
        items = list(iter_memory_items(data))
        assert len(items) == 1
        assert items[0][2] == "long enough item"

    def test_empty_dict(self):
        items = list(iter_memory_items({}))
        assert items == []


class TestExtractAndSaveMemories:

    def test_mock_llm_returns_json_and_save_called(self):
        llm = MagicMock()
        llm.think.return_value = '{"user_preference": ["likes Python"]}'
        embed_model = MagicMock()
        embed_model.encode.return_value.tolist.return_value = [0.1, 0.2]
        long_term = MagicMock()

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
        assert call_kwargs["category"] == "user_preference"
