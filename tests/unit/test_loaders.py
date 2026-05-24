"""Tests for agentnexus.rag.loaders."""

from pathlib import Path

import pytest

from agentnexus.rag.loaders import (
    _compose_indexed_text,
    _is_code_fence,
    _normalize_common,
    _split_markdown_sections,
    clean_text,
    load_document,
    load_structured_document,
)


class TestCleanText:
    def test_normalizes_fullwidth_chars(self):
        result = clean_text("ＡＢＣ")
        assert result == "ABC"

    def test_removes_control_chars(self):
        result = clean_text("\x00hello\x01\x02world\x7f")
        assert result == "helloworld"

    def test_normalizes_newlines(self):
        result = clean_text("line1\r\nline2\rline3")
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result

    def test_collapses_excessive_newlines(self):
        result = clean_text("aaa\n\n\n\n\nbbb")
        assert result == "aaa\n\nbbb"

    def test_single_whitespace_lines_removed(self):
        result = clean_text("aaa\n   \nbbb")
        assert result == "aaa\n\nbbb"

    def test_keeps_short_lines_with_punctuation(self):
        result = clean_text("aaa\n好。\nbbb")
        assert "好。" in result

    def test_keeps_short_lines_with_quotes(self):
        result = clean_text('aaa\n"hi"\nbbb')
        assert '"hi"' in result

    def test_removes_short_lines_without_punctuation(self):
        result = clean_text("hello\na\nworld")
        assert "hello\nworld" in result

    def test_removes_cjk_lines_without_punctuation(self):
        result = clean_text("项目\n简介")
        assert result == ""

    def test_strips_final_result(self):
        result = clean_text("  hello world  ")
        assert result == "hello world"


class TestNormalizeCommon:
    def test_fullwidth_to_ascii(self):
        result = _normalize_common("０１２ＡＢＣａｂｃ")
        assert result == "012ABCabc"

    def test_control_chars_removed(self):
        result = _normalize_common("\x00\x01\x02hello\x7f")
        assert result == "hello"

    def test_newlines_normalized(self):
        result = _normalize_common("a\r\nb\rc")
        assert result == "a\nb\nc"


class TestLoadDocument:
    def test_delegates_to_structured_and_returns_raw_text(self, tmp_path: Path):
        path = tmp_path / "hello.txt"
        path.write_text("hello world", encoding="utf-8")
        result = load_document(str(path))
        assert result == "hello world"


class TestLoadStructuredDocument:
    def test_unsupported_extension_raises(self):
        with pytest.raises(ValueError, match="不支持的文件格式"):
            load_structured_document("file.csv")

    def test_txt_loader(self, tmp_path: Path):
        path = tmp_path / "test.txt"
        path.write_text("some text content", encoding="utf-8")
        doc = load_structured_document(str(path))
        assert doc.metadata["format"] == "text"
        assert doc.raw_text == "some text content"

    def test_md_loader(self, tmp_path: Path):
        path = tmp_path / "doc.md"
        path.write_text("# Title\n\nBody", encoding="utf-8")
        doc = load_structured_document(str(path))
        assert doc.metadata["format"] == "markdown"

    def test_pdf_loader_missing_file_raises(self):
        with pytest.raises(Exception):
            load_structured_document("nonexistent.pdf")


class TestSplitMarkdownSections:
    def test_basic_heading_split(self, tmp_path: Path):
        path = tmp_path / "doc.md"
        path.write_text("# H1\n\ncontent1\n\n## H2\n\ncontent2", encoding="utf-8")
        doc = load_structured_document(str(path))
        assert len(doc.sections) >= 2
        assert doc.sections[0].metadata["section_title"] == "H1"

    def test_no_headings_fallback(self, tmp_path: Path):
        path = tmp_path / "plain.md"
        path.write_text("Just a plain paragraph\n\nNo headings here.", encoding="utf-8")
        doc = load_structured_document(str(path))
        assert len(doc.sections) >= 1

    def test_empty_text_returns_empty(self):
        result = _split_markdown_sections("", "untitled")
        assert result == []

    def test_blank_text_returns_empty(self):
        result = _split_markdown_sections("   \n\n  ", "untitled")
        assert result == []

    def test_code_block_respected(self):
        text = "# Title\n\n```\n# This is not a heading\n```\n\nBody text"
        result = _split_markdown_sections(text, "doc")
        assert len(result) >= 1


class TestComposeIndexedText:
    def test_with_heading_path(self):
        meta = {"heading_path": ["Guide", "Install"]}
        assert _compose_indexed_text(meta, "body") == "Guide\nInstall\n\nbody"

    def test_no_heading_path_uses_section_title(self):
        meta = {"section_title": "Overview"}
        assert _compose_indexed_text(meta, "body") == "Overview\n\nbody"

    def test_no_heading_path_no_title(self):
        meta = {}
        assert _compose_indexed_text(meta, "body") == "body"

    def test_prefix_only_no_body(self):
        meta = {"heading_path": ["Title"]}
        assert _compose_indexed_text(meta, "") == "Title"

    def test_no_prefix_no_body(self):
        meta = {}
        assert _compose_indexed_text(meta, "") == ""


class TestIsCodeFence:
    def test_backtick_fence(self):
        assert _is_code_fence("```python") is True

    def test_tilde_fence(self):
        assert _is_code_fence("~~~") is True

    def test_plain_line(self):
        assert _is_code_fence("hello") is False

    def test_empty(self):
        assert _is_code_fence("") is False
