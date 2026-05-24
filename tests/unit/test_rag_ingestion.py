from pathlib import Path

from agentnexus.rag.ingestion import (
    ChunkStrategy,
    chunk_text,
    clean_text,
    ingest,
    ingest_document,
    load_structured_document,
)


class FakePdfPage:
    def __init__(self, text: str):
        self._text = text

    def get_text(self, mode: str, textpage=None):
        assert mode == "text"
        del textpage
        return self._text


class FakePdfDocument:
    def __init__(self, pages: list[FakePdfPage]):
        self._pages = pages

    def __iter__(self):
        return iter(self._pages)

    def close(self):
        return None


class TestRagIngestion:
    def test_markdown_loader_preserves_headings_and_code_blocks(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "guide.md"
        path.write_text(
            "# API Guide\n\n"
            "Intro paragraph.\n\n"
            "## Install\n\n"
            "Use `pip install agentnexus`.\n\n"
            "```python\n"
            "print(\"hello\")\n"
            "```\n",
            encoding="utf-8",
        )

        document = load_structured_document(str(path))

        assert document.metadata["format"] == "markdown"
        assert document.sections[0].metadata["heading_path"] == ["API Guide"]
        assert document.sections[1].metadata["section_title"] == "Install"
        assert 'print("hello")' in document.raw_text

        artifacts = ingest_document(str(path), chunk_size=120, chunk_overlap=10)

        assert artifacts.document.source_id.startswith("src_")
        assert artifacts.document.document_version.startswith("doc_")
        assert any(chunk.metadata.get("section_title") == "Install" for chunk in artifacts.chunks)
        code_chunk = next(chunk for chunk in artifacts.chunks if 'print("hello")' in chunk.raw_text)
        assert "API Guide" in code_chunk.indexed_text

    def test_semantic_ingest_keeps_code_block_intact(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "semantic.md"
        path.write_text(
            "# Guide\n\n"
            "Intro paragraph.\n\n"
            "```python\n"
            "print(\"hello\")\n"
            "print(\"world\")\n"
            "```\n\n"
            "- item one\n"
            "- item two\n",
            encoding="utf-8",
        )

        artifacts = ingest_document(
            str(path),
            strategy=ChunkStrategy.SEMANTIC,
            chunk_size=80,
            chunk_overlap=0,
        )

        assert any(
            'print("hello")' in chunk.raw_text and 'print("world")' in chunk.raw_text
            for chunk in artifacts.chunks
        )

    def test_txt_normalization_and_legacy_chunking_remain_available(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "notes.txt"
        path.write_text("ＡＢＣ\x00\n\n\nline two  \n", encoding="utf-8")

        assert clean_text("ＡＢＣ\x00\n\n\nline two  \n") == "ABC\n\nline two"

        document = load_structured_document(str(path))
        chunks = chunk_text(document.indexed_text, strategy=ChunkStrategy.FIXED, chunk_size=6, chunk_overlap=2)

        assert document.metadata["format"] == "text"
        assert document.indexed_text == "ABC\n\nline two"
        assert chunks
        assert all(isinstance(chunk, str) for chunk in chunks)

    def test_pdf_loader_keeps_page_metadata(self, monkeypatch):
        from agentnexus.rag import loaders

        fake_pdf = FakePdfDocument(
            [
                FakePdfPage("Page one overview\n"),
                FakePdfPage("Page two details\n"),
            ]
        )
        monkeypatch.setattr(loaders.fitz, "open", lambda _: fake_pdf)

        document = load_structured_document("manual.pdf")
        artifacts = ingest_document("manual.pdf", chunk_size=80, chunk_overlap=0)

        assert document.metadata["format"] == "pdf"
        assert document.metadata["page_count"] == 2
        assert [section.metadata["page_number"] for section in document.sections] == [1, 2]
        assert any(chunk.metadata.get("page_number") == 1 for chunk in artifacts.chunks)
        assert any(chunk.metadata.get("page_number") == 2 for chunk in artifacts.chunks)

    def test_legacy_ingest_returns_plain_chunk_texts(self, temp_agentnexus_home: Path):
        path = temp_agentnexus_home / "legacy.md"
        path.write_text("# Overview\n\nLegacy API body.\n", encoding="utf-8")

        chunks = ingest(str(path), chunk_size=80, chunk_overlap=0)

        assert chunks
        assert all(isinstance(chunk, str) for chunk in chunks)
        assert any("Overview" in chunk for chunk in chunks)

    def test_pdf_loader_uses_ocr_fallback_when_text_empty(self, monkeypatch):
        from agentnexus.rag import loaders

        fake_pdf = FakePdfDocument([FakePdfPage("")])
        monkeypatch.setattr(loaders.fitz, "open", lambda _: fake_pdf)
        monkeypatch.setattr(loaders, "_extract_pdf_page_text_with_ocr", lambda page: "OCR text")

        document = load_structured_document("scan.pdf")

        assert document.metadata["ocr_fallback_used"] is True
        assert document.sections[0].metadata["ocr_fallback_used"] is True
        assert "OCR text" in document.sections[0].raw_text

    def test_contextual_ingest_uses_dual_channel_texts(self, temp_agentnexus_home: Path):
        from agentnexus.rag import ingestion as ingestion_mod

        path = temp_agentnexus_home / "contextual.md"
        path.write_text("# Install\n\nRun setup first.\n", encoding="utf-8")

        class FakeLLM:
            def think(self, messages, temperature=0, silent=True):
                prompt = messages[0]["content"]
                if "适合检索的摘要" in prompt:
                    return "retrieval summary"
                return "generation summary"

        artifacts = ingestion_mod.ingest_document(
            str(path),
            chunk_size=120,
            chunk_overlap=0,
            enable_contextual=True,
            llm_client=FakeLLM(),
        )

        assert artifacts.chunks
        chunk = artifacts.chunks[0]
        assert chunk.indexed_text.startswith("retrieval summary")
        assert chunk.sparse_text.startswith("retrieval summary")
        assert chunk.text.startswith("generation summary")
        assert chunk.metadata["retrieval_text"].startswith("retrieval summary")
        assert chunk.metadata["generation_text"].startswith("generation summary")
