"""Tests for agentnexus.rag.chunking."""

from agentnexus.rag.chunking import (
    ChunkStrategy,
    _fixed_window_split,
    _prepend_prefix,
    _section_body,
    _section_prefix,
    chunk_text,
    chunk_structured_document,
)
from agentnexus.rag.models import ChunkRecord, DocumentSection, SourceDocument


class TestChunkText:
    def test_empty_text_returns_empty(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_fixed_strategy_small_text(self):
        result = chunk_text("hello world", ChunkStrategy.FIXED, 100, 0)
        assert result == ["hello world"]

    def test_fixed_strategy_splits_long_text(self):
        text = "a" * 1000
        result = chunk_text(text, ChunkStrategy.FIXED, 100, 10)
        assert len(result) > 1
        assert all(len(c) <= 100 for c in result)

    def test_recursive_strategy_returns_chunks(self):
        text = "word " * 500
        result = chunk_text(text, ChunkStrategy.RECURSIVE, 100, 10)
        assert len(result) >= 1
        assert all(isinstance(c, str) for c in result)

    def test_semantic_strategy_returns_chunks(self):
        text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        result = chunk_text(text, ChunkStrategy.SEMANTIC, 50, 0)
        assert len(result) >= 1
        assert all(isinstance(c, str) for c in result)

    def test_invalid_strategy_raises(self):
        try:
            chunk_text("text", "unknown", 100, 0)
            assert False, "should have raised"
        except ValueError:
            pass


class TestFixedWindowSplit:
    def test_shorter_than_chunk_returns_single(self):
        assert _fixed_window_split("hello", 100, 0) == ["hello"]

    def test_empty_text_after_strip_returns_empty(self):
        assert _fixed_window_split("   ", 100, 0) == []
        assert _fixed_window_split("", 100, 0) == []

    def test_correct_chunk_count(self):
        text = "a" * 300
        chunks = _fixed_window_split(text, 100, 0)
        assert len(chunks) == 3

    def test_with_overlap(self):
        text = "a" * 300
        chunks = _fixed_window_split(text, 100, 20)
        assert len(chunks) == 4  # step=80, 300/80 = 3.75 -> 4


class TestPrependPrefix:
    def test_prefix_and_body(self):
        assert _prepend_prefix("Guide", "body") == "Guide\n\nbody"

    def test_prefix_only(self):
        assert _prepend_prefix("Guide", "") == "Guide"

    def test_body_only(self):
        assert _prepend_prefix("", "body") == "body"

    def test_both_empty(self):
        assert _prepend_prefix("", "") == ""

    def test_strips_whitespace(self):
        assert _prepend_prefix("  Guide  ", "  body  ") == "Guide\n\nbody"


class TestSectionPrefix:
    def test_markdown_with_heading_path(self):
        section = DocumentSection.create(
            "v1",
            section_index=0,
            raw_text="body",
            indexed_text="body",
            sparse_text="body",
            metadata={"format": "markdown", "heading_path": ["Guide", "Install"]},
        )
        assert _section_prefix(section) == "Guide\nInstall"

    def test_markdown_no_heading_path(self):
        section = DocumentSection.create(
            "v1",
            section_index=0,
            raw_text="body",
            indexed_text="body",
            sparse_text="body",
            metadata={"format": "markdown"},
        )
        assert _section_prefix(section) == ""

    def test_pdf_with_page_number(self):
        section = DocumentSection.create(
            "v1",
            section_index=0,
            raw_text="body",
            indexed_text="body",
            sparse_text="body",
            metadata={"format": "pdf"},
            page_number=3,
        )
        assert _section_prefix(section) == "Page 3"

    def test_pdf_no_page_number(self):
        section = DocumentSection.create(
            "v1",
            section_index=0,
            raw_text="body",
            indexed_text="body",
            sparse_text="body",
            metadata={"format": "pdf"},
        )
        assert _section_prefix(section) == ""

    def test_unknown_format(self):
        section = DocumentSection.create(
            "v1",
            section_index=0,
            raw_text="body",
            indexed_text="body",
            sparse_text="body",
            metadata={"format": "text"},
        )
        assert _section_prefix(section) == ""


class TestSectionBody:
    def test_markdown_uses_raw_or_indexed(self):
        section = DocumentSection.create(
            "v1",
            section_index=0,
            raw_text="raw body",
            indexed_text="indexed body",
            sparse_text="sparse",
            metadata={"format": "markdown"},
        )
        assert _section_body(section) == "raw body"

    def test_pdf_uses_raw_or_indexed(self):
        section = DocumentSection.create(
            "v1",
            section_index=0,
            raw_text="pdf raw",
            indexed_text="pdf indexed",
            sparse_text="sparse",
            metadata={"format": "pdf"},
        )
        assert _section_body(section) == "pdf raw"

    def test_other_format_uses_indexed(self):
        section = DocumentSection.create(
            "v1",
            section_index=0,
            raw_text="raw text",
            indexed_text="indexed text",
            sparse_text="sparse",
            metadata={"format": "text"},
        )
        assert _section_body(section) == "indexed text"


class TestChunkStructuredDocument:
    def test_with_sections(self):
        doc = SourceDocument.create(
            source_uri="test.md",
            raw_text="# H1\n\nbody text",
            metadata={"format": "markdown"},
            indexed_text="indexed",
            sparse_text="sparse",
        )
        doc.sections = [
            DocumentSection.create(
                "v1",
                section_index=0,
                raw_text="body text",
                indexed_text="body text",
                sparse_text="body text",
                metadata={"format": "markdown", "heading_path": ["Title"]},
            )
        ]
        chunks = chunk_structured_document(doc, ChunkStrategy.FIXED, 500, 0)
        assert len(chunks) >= 1
        assert all(isinstance(c, ChunkRecord) for c in chunks)

    def test_fallback_creates_section_from_document(self):
        doc = SourceDocument.create(
            source_uri="test.txt",
            raw_text="plain text body here",
            metadata={"format": "text"},
            indexed_text="plain text body here",
            sparse_text="plain text body here",
        )
        chunks = chunk_structured_document(doc, ChunkStrategy.FIXED, 500, 0)
        assert len(chunks) >= 1

    def test_empty_parts_fallback_to_indexed_text(self):
        doc = SourceDocument.create(
            source_uri="test.md",
            raw_text="short",
            metadata={"format": "markdown"},
            indexed_text="falls back to indexed",
            sparse_text="falls back to indexed",
        )
        doc.sections = [
            DocumentSection.create(
                "v1",
                section_index=0,
                raw_text="   ",
                indexed_text="falls back to indexed",
                sparse_text="falls back to indexed",
                metadata={"format": "markdown"},
            )
        ]
        chunks = chunk_structured_document(doc, ChunkStrategy.FIXED, 500, 0)
        assert len(chunks) == 1
