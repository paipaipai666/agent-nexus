"""Tests for MCP SDK object and result normalization helpers."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentnexus.tools.mcp_result import (
    content_block_to_text,
    dump_sdk_object,
    get_sdk_attr,
    json_text,
    normalize_prompt_result,
    normalize_resource_result,
    normalize_tool_result,
    sanitize_name,
)

# ---------------------------------------------------------------------------
# get_sdk_attr
# ---------------------------------------------------------------------------

class TestGetSdkAttr:
    """Tests for the get_sdk_attr helper."""

    def test_first_attr_found_returns_it(self):
        obj = SimpleNamespace(foo="hello", bar="world")
        assert get_sdk_attr(obj, "foo", "bar") == "hello"

    def test_skips_none_and_returns_first_non_none(self):
        obj = SimpleNamespace(foo=None, bar="world")
        assert get_sdk_attr(obj, "foo", "bar") == "world"

    def test_all_none_returns_default(self):
        obj = SimpleNamespace(foo=None, bar=None)
        assert get_sdk_attr(obj, "foo", "bar") is None

    def test_no_matching_attrs_returns_default(self):
        obj = SimpleNamespace(baz="x")
        assert get_sdk_attr(obj, "foo", "bar") is None

    def test_default_value_customizable(self):
        obj = SimpleNamespace()
        assert get_sdk_attr(obj, "missing", default="fallback") == "fallback"

    def test_default_value_none_when_not_specified(self):
        obj = SimpleNamespace()
        assert get_sdk_attr(obj, "missing") is None


# ---------------------------------------------------------------------------
# sanitize_name
# ---------------------------------------------------------------------------

class TestSanitizeName:
    """Tests for the sanitize_name helper."""

    def test_normal_string_lowercase_and_underscore(self):
        assert sanitize_name("MyTool") == "mytool"

    def test_spaces_replaced_with_underscores(self):
        assert sanitize_name("my tool name") == "my_tool_name"

    def test_empty_string_returns_tool(self):
        assert sanitize_name("") == "tool"

    def test_none_returns_tool(self):
        assert sanitize_name(None) == "tool"

    def test_only_special_chars_returns_tool(self):
        assert sanitize_name("@#$%") == "tool"

    def test_mixed_chars_sanitized(self):
        result = sanitize_name("my-tool@v2")
        assert result == "my_tool_v2"
        assert "__" not in result

    def test_leading_trailing_underscores_stripped(self):
        assert sanitize_name("_hello_") == "hello"

    def test_multiple_consecutive_special_chars_single_underscore(self):
        assert sanitize_name("a@@@b") == "a_b"

    def test_preserves_digits_and_underscores(self):
        assert sanitize_name("tool_123") == "tool_123"


# ---------------------------------------------------------------------------
# json_text
# ---------------------------------------------------------------------------

class TestJsonText:
    """Tests for the json_text helper."""

    def test_simple_dict(self):
        result = json_text({"key": "value"})
        assert json.loads(result) == {"key": "value"}

    def test_non_serializable_falls_back_to_str(self):
        obj = object()
        result = json_text({"obj": obj})
        assert "object" in result

    def test_chinese_characters_preserved(self):
        data = {"msg": "你好世界"}
        result = json_text(data)
        assert "你好世界" in result
        assert json.loads(result) == data


# ---------------------------------------------------------------------------
# dump_sdk_object
# ---------------------------------------------------------------------------

class TestDumpSdkObject:
    """Tests for the dump_sdk_object helper."""

    def test_pydantic_model_model_dump_json(self):
        model = MagicMock()
        model.model_dump.return_value = {"name": "test"}
        result = dump_sdk_object(model)
        model.model_dump.assert_called_once_with(mode="json")
        assert result == {"name": "test"}

    def test_pydantic_model_typeerror_fallback(self):
        model = MagicMock()
        model.model_dump.side_effect = [TypeError("unsupported"), {"name": "test"}]
        result = dump_sdk_object(model)
        assert result == {"name": "test"}
        assert model.model_dump.call_count == 2

    def test_dict_input_shallow_copy(self):
        original = {"a": 1, "b": 2}
        result = dump_sdk_object(original)
        assert result == original
        assert result is not original

    def test_object_with_recognized_attrs(self):
        obj = SimpleNamespace(name="res", uri="file:///x", description="a resource")
        result = dump_sdk_object(obj)
        assert result["name"] == "res"
        assert result["uri"] == "file:///x"
        assert result["description"] == "a resource"

    def test_object_with_no_recognized_attrs(self):
        obj = SimpleNamespace(unknown="something")
        result = dump_sdk_object(obj)
        assert result == {"value": str(obj)}

    def test_object_with_mime_type_attr(self):
        obj = SimpleNamespace(mimeType="text/plain")
        result = dump_sdk_object(obj)
        assert result["mimeType"] == "text/plain"

    def test_object_with_arguments_attr(self):
        obj = SimpleNamespace(arguments=[{"name": "x"}])
        result = dump_sdk_object(obj)
        assert result["arguments"] == [{"name": "x"}]


# ---------------------------------------------------------------------------
# content_block_to_text
# ---------------------------------------------------------------------------

class TestContentBlockToText:
    """Tests for the content_block_to_text helper."""

    def test_text_block(self):
        block = SimpleNamespace(text="hello world")
        assert content_block_to_text(block) == "hello world"

    def test_resource_with_text(self):
        resource = SimpleNamespace(text="resource text")
        block = SimpleNamespace(text=None, resource=resource)
        assert content_block_to_text(block) == "resource text"

    def test_resource_with_blob(self):
        resource = SimpleNamespace(text=None, blob=b"\x00\x01", mimeType="image/png")
        block = SimpleNamespace(text=None, resource=resource)
        assert content_block_to_text(block) == "[embedded resource: image/png]"

    def test_resource_with_blob_snake_case_mime(self):
        resource = SimpleNamespace(text=None, blob=b"\x00", mime_type="application/pdf")
        block = SimpleNamespace(text=None, resource=resource)
        assert content_block_to_text(block) == "[embedded resource: application/pdf]"

    def test_resource_with_uri(self):
        resource = SimpleNamespace(text=None, blob=None, uri="file:///doc.txt")
        block = SimpleNamespace(text=None, resource=resource)
        assert content_block_to_text(block) == "[embedded resource] file:///doc.txt"

    def test_binary_content(self):
        block = SimpleNamespace(
            text=None, resource=None,
            mimeType="application/octet-stream", data=b"\x00",
        )
        assert content_block_to_text(block) == "[binary content: application/octet-stream]"

    def test_binary_content_snake_case(self):
        block = SimpleNamespace(
            text=None, resource=None,
            mime_type="image/gif", data=b"\x00",
        )
        assert content_block_to_text(block) == "[binary content: image/gif]"

    def test_block_with_model_dump(self):
        block = MagicMock()
        block.text = None
        block.resource = None
        del block.mimeType
        del block.mime_type
        del block.data
        block.model_dump.return_value = {"type": "custom", "value": 42}
        result = content_block_to_text(block)
        parsed = json.loads(result)
        assert parsed["type"] == "custom"

    def test_unknown_block_returns_str(self):
        block = SimpleNamespace(text=None, resource=None)
        result = content_block_to_text(block)
        assert "namespace" in result


# ---------------------------------------------------------------------------
# normalize_tool_result
# ---------------------------------------------------------------------------

class TestNormalizeToolResult:
    """Tests for the normalize_tool_result helper."""

    def test_with_content_blocks_joined(self):
        block1 = SimpleNamespace(text="part1")
        block2 = SimpleNamespace(text="part2")
        result_obj = SimpleNamespace(content=[block1, block2], structuredContent=None)
        assert normalize_tool_result(result_obj) == "part1\npart2"

    def test_empty_result_returns_fallback(self):
        result_obj = SimpleNamespace(content=[], structuredContent=None)
        assert normalize_tool_result(result_obj) == "[mcp] 工具未返回文本内容"

    def test_with_structured_content(self):
        result_obj = SimpleNamespace(
            content=[],
            structuredContent={"answer": 42},
        )
        result = normalize_tool_result(result_obj)
        assert '"answer": 42' in result

    def test_no_content_attr_returns_fallback(self):
        result_obj = SimpleNamespace()
        assert normalize_tool_result(result_obj) == "[mcp] 工具未返回文本内容"


# ---------------------------------------------------------------------------
# normalize_resource_result
# ---------------------------------------------------------------------------

class TestNormalizeResourceResult:
    """Tests for the normalize_resource_result helper."""

    def test_with_content_blocks(self):
        block = SimpleNamespace(text="resource data")
        result_obj = SimpleNamespace(contents=[block])
        assert normalize_resource_result(result_obj) == "resource data"

    def test_empty_returns_fallback(self):
        result_obj = SimpleNamespace(contents=[])
        assert normalize_resource_result(result_obj) == "[mcp] 资源未返回文本内容"

    def test_no_content_but_has_model_dump(self):
        result_obj = MagicMock()
        result_obj.contents = []
        del result_obj.content
        result_obj.model_dump.return_value = {"data": "value"}
        result = normalize_resource_result(result_obj)
        parsed = json.loads(result)
        assert parsed["data"] == "value"

    def test_no_content_no_model_dump_returns_fallback(self):
        result_obj = SimpleNamespace()
        assert normalize_resource_result(result_obj) == "[mcp] 资源未返回文本内容"


# ---------------------------------------------------------------------------
# normalize_prompt_result
# ---------------------------------------------------------------------------

class TestNormalizePromptResult:
    """Tests for the normalize_prompt_result helper."""

    def test_with_messages_dumps_each(self):
        msg1 = SimpleNamespace(role="user", content="hi")
        msg2 = SimpleNamespace(role="assistant", content="hello")
        result_obj = SimpleNamespace(messages=[msg1, msg2])
        result = normalize_prompt_result(result_obj)
        parsed = json.loads(result)
        assert len(parsed) == 2
        # SimpleNamespace has no recognized attrs for dump_sdk_object,
        # so each message is dumped as {"value": str(msg)}
        assert "value" in parsed[0]

    def test_no_messages_has_model_dump(self):
        result_obj = MagicMock()
        result_obj.messages = []
        result_obj.model_dump.return_value = {"prompt": "test"}
        result = normalize_prompt_result(result_obj)
        parsed = json.loads(result)
        assert parsed["prompt"] == "test"

    def test_no_messages_no_model_dump_returns_str(self):
        result_obj = SimpleNamespace()
        result = normalize_prompt_result(result_obj)
        assert "namespace" in result
