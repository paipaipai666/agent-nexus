"""Performance: ingestion pipeline — document loading, text cleaning, chunking."""
from __future__ import annotations

from pathlib import Path

import pytest

CLEAN_TEXT_500KB_P95_MAX_MS = 500
INGEST_MD_50KB_P95_MAX_MS = 200
INGEST_MD_100KB_P95_MAX_MS = 400


@pytest.fixture
def perf_docs_dir(perf_env) -> Path:
    """Create sample .md and .txt files for ingestion perf tests."""
    d = perf_env / "docs"
    d.mkdir(parents=True, exist_ok=True)

    # 50KB markdown file with 30 headings
    md_lines = []
    for i in range(30):
        md_lines.append(f"## Section {i}\n\n")
        chunk = (
            f"This is section {i} of the performance test document. " * 20
        )
        md_lines.append(chunk + "\n\n")
    md_content = "".join(md_lines)
    (d / "perf_50kb.md").write_text(md_content[:50000], encoding="utf-8")

    # 100KB markdown file with 60 headings
    md_lines2 = []
    for i in range(60):
        md_lines2.append(f"### Subsection {i}\n\n")
        chunk = (
            f"Performance testing content for subsection {i}. " * 20
        )
        md_lines2.append(chunk + "\n\n")
    md_content2 = "".join(md_lines2)
    (d / "perf_100kb.md").write_text(md_content2[:100000], encoding="utf-8")

    # Plain text file (no headings)
    txt_lines = [f"Plain text line {i} with some content for testing.\n" for i in range(1000)]
    (d / "perf_plain.txt").write_text("".join(txt_lines), encoding="utf-8")

    return d


def test_clean_text_small(benchmark):
    from agentnexus.rag.ingestion import clean_text
    text = "Hello, 世界！\n\n" * 100
    result = benchmark(clean_text, text)
    assert isinstance(result, str)
    assert len(result) > 0


def test_clean_text_500kb(benchmark):
    from agentnexus.rag.ingestion import clean_text
    text = "这是一个测试文档。\n\n" * 10000  # ~200KB
    text += "Full-width test：ｈｅｌｌｏ\n" * 10000  # ~350KB
    result = benchmark(clean_text, text)
    assert isinstance(result, str)
    assert len(result) > 0


def test_load_markdown_50kb(benchmark, perf_env, perf_docs_dir):
    from agentnexus.rag.loaders import load_structured_document
    path = str(perf_docs_dir / "perf_50kb.md")
    result = benchmark(load_structured_document, path)
    assert result is not None
    assert len(result.sections) > 0


def test_load_markdown_100kb(benchmark, perf_env, perf_docs_dir):
    from agentnexus.rag.loaders import load_structured_document
    path = str(perf_docs_dir / "perf_100kb.md")
    result = benchmark(load_structured_document, path)
    assert result is not None
    assert len(result.sections) > 0


def test_load_plain_text(benchmark, perf_env, perf_docs_dir):
    from agentnexus.rag.loaders import load_structured_document
    path = str(perf_docs_dir / "perf_plain.txt")
    result = benchmark(load_structured_document, path)
    assert result is not None
    assert len(result.sections) > 0


def test_ingest_markdown_50kb(benchmark, perf_env, perf_docs_dir):
    from agentnexus.rag.chunking import ChunkStrategy
    from agentnexus.rag.ingestion import ingest_document
    path = str(perf_docs_dir / "perf_50kb.md")
    result = benchmark(ingest_document, path, ChunkStrategy.RECURSIVE, 512)
    assert result is not None
    assert hasattr(result, "chunks")
    assert len(result.chunks) > 0


def test_ingest_plain_text(benchmark, perf_env, perf_docs_dir):
    from agentnexus.rag.chunking import ChunkStrategy
    from agentnexus.rag.ingestion import ingest_document
    path = str(perf_docs_dir / "perf_plain.txt")
    result = benchmark(ingest_document, path, ChunkStrategy.RECURSIVE, 512)
    assert result is not None
    assert hasattr(result, "chunks")
    assert len(result.chunks) > 0


def test_chunk_structured_document_per_section(benchmark, perf_env, perf_docs_dir):
    from agentnexus.rag.chunking import ChunkStrategy, chunk_structured_document
    from agentnexus.rag.loaders import load_structured_document
    path = str(perf_docs_dir / "perf_100kb.md")
    doc = load_structured_document(path)
    result = benchmark(chunk_structured_document, doc, ChunkStrategy.RECURSIVE, 512, 50)
    assert len(result) > 0
