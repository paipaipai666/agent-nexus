"""Tests for MCP utility functions: sanitize, content blocks, normalize."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentnexus.tools.mcp_adapter import (
    _content_block_to_text,
    _normalize_tool_result,
    _sanitize_name,
)


class TestSanitizeName:
    def test_sanitize_lowercases(self):
        assert _sanitize_name("HelloWorld") == "helloworld"

    def test_sanitize_replaces_non_alnum(self):
        assert _sanitize_name("foo bar!baz@123") == "foo_bar_baz_123"

    def test_sanitize_strips_leading_trailing_underscores(self):
        assert _sanitize_name("__hello__") == "hello"

    def test_sanitize_empty_falls_back(self):
        assert _sanitize_name("") == "tool"

    def test_sanitize_all_special_chars(self):
        assert _sanitize_name("!!!___!!!") == "tool"


class TestContentBlockToText:
    def test_text_block_returns_text(self):
        block = SimpleNamespace(text="hello")
        assert _content_block_to_text(block) == "hello"

    def test_resource_text_block(self):
        block = SimpleNamespace(text=None, resource=SimpleNamespace(text="resource text"))
        assert _content_block_to_text(block) == "resource text"

    def test_resource_blob_block(self):
        block = SimpleNamespace(
            text=None, resource=SimpleNamespace(text=None, blob=b"binary", mimeType="image/png")
        )
        assert _content_block_to_text(block) == "[embedded resource: image/png]"

    def test_resource_blob_no_mime(self):
        block = SimpleNamespace(
            text=None, resource=SimpleNamespace(text=None, blob=b"data", mimeType=None, mime_type="application/pdf")
        )
        assert _content_block_to_text(block) == "[embedded resource: application/pdf]"

    def test_resource_uri_only(self):
        res = SimpleNamespace(text=None, blob=None, uri="https://example.com/resource", mimeType=None, mime_type=None)
        block = SimpleNamespace(text=None, resource=res)
        assert _content_block_to_text(block) == "[embedded resource] https://example.com/resource"

    def test_binary_content_with_mime(self):
        block = SimpleNamespace(text=None, resource=None, mimeType="audio/wav", data=b"...")
        assert _content_block_to_text(block) == "[binary content: audio/wav]"

    def test_binary_content_mime_alt(self):
        block = SimpleNamespace(text=None, resource=None, mimeType=None, mime_type="video/mp4", data=b"...")
        assert _content_block_to_text(block) == "[binary content: video/mp4]"

    def test_model_dump_fallback(self):
        block = MagicMock()
        block.text = None
        block.resource = None
        block.mimeType = None
        block.mime_type = None
        block.data = None
        block.model_dump.return_value = {"key": "value"}
        result = _content_block_to_text(block)
        assert '"key"' in result

    def test_str_fallback(self):
        block = object()
        result = _content_block_to_text(block)
        assert result == str(block)


class TestNormalizeToolResult:
    def test_simple_text_content(self):
        result = SimpleNamespace(content=[SimpleNamespace(text="line1"), SimpleNamespace(text="line2")],
                                  isError=False, structuredContent=None)
        assert _normalize_tool_result(result) == "line1\nline2"

    def test_structured_content_takes_priority(self):
        result = SimpleNamespace(
            structuredContent={"summary": "structured"},
            structured_content=None,
            content=[SimpleNamespace(text="text content")],
            isError=False,
        )
        text = _normalize_tool_result(result)
        assert "summary" in text
        assert "structured" in text

    def test_structured_content_fallback(self):
        result = SimpleNamespace(
            structuredContent=None,
            structured_content={"key": "val"},
            content=[],
            isError=False,
        )
        text = _normalize_tool_result(result)
        assert "key" in text

    def test_no_content_fallback(self):
        result = SimpleNamespace(content=[], structuredContent=None, structured_content=None, isError=False)
        assert _normalize_tool_result(result) == "[mcp] 工具未返回文本内容"

    def test_resource_content(self):
        block = SimpleNamespace(text=None, resource=SimpleNamespace(text="resource text"))
        result = SimpleNamespace(content=[block], structuredContent=None, structured_content=None, isError=False)
        assert _normalize_tool_result(result) == "resource text"


class TestContentBlockToTextEdgeCases:
    def test_mixed_content_types(self):
        """Multiple content blocks with different types."""
        blocks = [
            SimpleNamespace(text="text block"),
            SimpleNamespace(
                text=None, resource=SimpleNamespace(text="resource text")
            ),
            SimpleNamespace(
                text=None, resource=SimpleNamespace(text=None, blob=b"img", mimeType="image/png")
            ),
        ]
        from agentnexus.tools.mcp_adapter import _normalize_tool_result

        result = SimpleNamespace(
            content=blocks,
            structuredContent=None,
            isError=False,
        )
        text = _normalize_tool_result(result)
        assert "text block" in text
        assert "resource text" in text
        assert "[embedded resource: image/png]" in text

    def test_structured_content_with_text_mixed(self):
        """Both structuredContent and content blocks should appear."""
        from agentnexus.tools.mcp_adapter import _normalize_tool_result

        result = SimpleNamespace(
            structuredContent={"summary": "structured data"},
            content=[SimpleNamespace(text="text block")],
            isError=False,
        )
        text = _normalize_tool_result(result)
        assert "summary" in text
        assert "text block" in text

    def test_resource_blob_unknown_mime(self):
        """Blob with no mime should use 'unknown'."""
        block = SimpleNamespace(
            text=None,
            resource=SimpleNamespace(text=None, blob=b"\x00\x01", mimeType=None, mime_type=None),
        )
        result = _content_block_to_text(block)
        assert result == "[embedded resource: unknown]"

    def test_resource_no_text_no_blob_no_uri(self):
        """Resource with none of text/blob/uri should fall through."""
        block = SimpleNamespace(
            text=None,
            resource=SimpleNamespace(text=None, blob=None, uri=None, mimeType=None, mime_type=None),
        )
        result = _content_block_to_text(block)
        assert result == str(block)

    def test_binary_data_without_mime_falls_through(self):
        """data without mimeType should not be classified as binary."""
        block = SimpleNamespace(text=None, resource=None, mimeType=None, mime_type=None, data=b"raw")
        result = _content_block_to_text(block)
        assert isinstance(result, str)
        # Falls through to str() fallback; binary markers not present
        assert "[binary content:" not in result
        assert "[embedded resource:" not in result

    def test_model_dump_exception_falls_to_str(self):
        """If model_dump raises, fall back to str()."""
        block = MagicMock()
        block.text = None
        block.resource = None
        block.mimeType = None
        block.mime_type = None
        block.data = None
        block.model_dump.side_effect = ValueError("oops")
        result = _content_block_to_text(block)
        assert isinstance(result, str)

    def test_complex_mcp_result_with_binary_content(self):
        """MCP result containing binary content blocks."""
        from agentnexus.tools.mcp_adapter import _normalize_tool_result

        result = SimpleNamespace(
            content=[
                SimpleNamespace(text="stdout line 1"),
                SimpleNamespace(
                    text=None, resource=None,
                    mimeType="application/octet-stream", data=b"\xff\xfe",
                ),
            ],
            structuredContent=None,
            isError=False,
        )
        text = _normalize_tool_result(result)
        assert "stdout line 1" in text
        assert "[binary content: application/octet-stream]" in text
