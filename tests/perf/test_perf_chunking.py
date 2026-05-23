"""Performance: chunking strategies — fixed, recursive, semantic."""
from __future__ import annotations

import pytest

from agentnexus.rag.chunking import (
    ChunkStrategy,
    _fixed_window_split,
    _recursive_split,
    _semantic_split,
    chunk_structured_document,
    chunk_text,
)

FIXED_100KB_P95_MAX_MS = 20
RECURSIVE_100KB_P95_MAX_MS = 100
SEMANTIC_100KB_P95_MAX_MS = 150
FIXED_EXTREME_OVERLAP_P95_MAX_MS = 50
CHUNK_STRUCTURED_50_SECTIONS_P95_MAX_MS = 200


def _make_text(char_count: int) -> str:
    """Generate CJK-heavy realistic text approximating a technical document."""
    paragraphs = []
    total = 0
    while total < char_count:
        p = (
            "文档分块（Chunking）是 RAG pipeline 的关键预处理步骤。固定窗口分块（Fixed Window）按字符数"
            "切分，简单但可能切断语义单元。递归分块（Recursive Splitting）使用分层分隔符（段落→句子→词）"
            "逐步切分。语义分块（Semantic Chunking）基于嵌入相似度或自然段落边界，能最好地保留语义完整性。"
            "分块大小直接影响检索粒度和上下文完整性，过小会导致信息碎片化，过大则引入噪声降低 Precision。"
        )
        paragraphs.append(p)
        total += len(p)
    return "\n\n".join(paragraphs)[:char_count]


@pytest.mark.perf
def test_fixed_window_50kb(benchmark):
    text = _make_text(50000)
    result = benchmark(_fixed_window_split, text, 512, 50)
    assert isinstance(result, list)
    assert len(result) > 0


@pytest.mark.perf
def test_fixed_window_100kb(benchmark):
    text = _make_text(100000)
    result = benchmark.pedantic(_fixed_window_split, args=(text, 512, 50), rounds=5)
    assert isinstance(result, list)
    assert len(result) > 0
    data = benchmark.stats.stats.sorted_data
    p95 = data[int(len(data) * 0.95)] * 1000 if len(data) >= 20 else data[-1] * 1000
    assert p95 < FIXED_100KB_P95_MAX_MS, f"Fixed 100KB p95={p95:.0f}ms > {FIXED_100KB_P95_MAX_MS}ms"


@pytest.mark.perf
def test_fixed_window_extreme_overlap(benchmark):
    text = _make_text(100000)
    result = benchmark.pedantic(_fixed_window_split, args=(text, 512, 500), rounds=5)
    assert isinstance(result, list)
    assert len(result) > 0
    data = benchmark.stats.stats.sorted_data
    p95 = data[int(len(data) * 0.95)] * 1000 if len(data) >= 20 else data[-1] * 1000
    max_ms = FIXED_EXTREME_OVERLAP_P95_MAX_MS
    assert p95 < max_ms, f"Extreme overlap p95={p95:.0f}ms > {max_ms}ms"


@pytest.mark.perf
def test_recursive_split_50kb(benchmark, perf_env):
    text = _make_text(50000)
    result = benchmark(_recursive_split, text, 512, 50)
    assert isinstance(result, list)
    assert len(result) > 0


@pytest.mark.perf
def test_recursive_split_100kb(benchmark, perf_env):
    text = _make_text(100000)
    result = benchmark.pedantic(_recursive_split, args=(text, 512, 50), rounds=5)
    assert isinstance(result, list)
    assert len(result) > 0
    data = benchmark.stats.stats.sorted_data
    p95 = data[int(len(data) * 0.95)] * 1000 if len(data) >= 20 else data[-1] * 1000
    assert p95 < RECURSIVE_100KB_P95_MAX_MS, f"Recursive 100KB p95={p95:.0f}ms > {RECURSIVE_100KB_P95_MAX_MS}ms"


@pytest.mark.perf
def test_semantic_split_50kb(benchmark, perf_env):
    text = _make_text(50000)
    result = benchmark(_semantic_split, text, 512, 50)
    assert isinstance(result, list)
    assert len(result) > 0


@pytest.mark.perf
def test_semantic_split_100kb(benchmark, perf_env):
    text = _make_text(100000)
    result = benchmark.pedantic(_semantic_split, args=(text, 512, 50), rounds=5)
    assert isinstance(result, list)
    assert len(result) > 0
    data = benchmark.stats.stats.sorted_data
    p95 = data[int(len(data) * 0.95)] * 1000 if len(data) >= 20 else data[-1] * 1000
    assert p95 < SEMANTIC_100KB_P95_MAX_MS, f"Semantic 100KB p95={p95:.0f}ms > {SEMANTIC_100KB_P95_MAX_MS}ms"


@pytest.mark.perf
def test_chunk_text_strategy_comparison():
    text = _make_text(100000)
    fixed_chunks = chunk_text(text, ChunkStrategy.FIXED, 512, 50)
    recursive_chunks = chunk_text(text, ChunkStrategy.RECURSIVE, 512, 50)
    semantic_chunks = chunk_text(text, ChunkStrategy.SEMANTIC, 512, 50)
    assert len(fixed_chunks) > 0
    assert len(recursive_chunks) > 0
    assert len(semantic_chunks) > 0
    total_chars_fixed = sum(len(c) for c in fixed_chunks)
    total_chars_recursive = sum(len(c) for c in recursive_chunks)
    total_chars_semantic = sum(len(c) for c in semantic_chunks)
    assert total_chars_fixed > 0
    assert total_chars_recursive > 0
    assert total_chars_semantic > 0


@pytest.mark.perf
def test_chunk_text_fixed_throughput(benchmark, perf_env):
    text = _make_text(50000)

    def _run():
        for _ in range(100):
            chunk_text(text, ChunkStrategy.FIXED, 512, 50)

    benchmark(_run)


@pytest.mark.perf
def test_chunk_structured_document_50_sections(benchmark, perf_env):
    from agentnexus.rag.models import DocumentSection, SourceDocument

    doc = SourceDocument(
        document_id="perf_doc_v1",
        kb_id="perf_kb",
        source_id="perf_src",
        source_uri="/perf/doc.md",
        document_version="v1",
        content="placeholder",
        metadata={"format": "markdown"},
    )
    sections = []
    for i in range(50):
        text = _make_text(1000)
        sec = DocumentSection.create(
            document_version="v1",
            section_index=i,
            raw_text=text,
            indexed_text=text,
            metadata={"format": "markdown", "heading_path": [f"Section {i}"]},
        )
        sections.append(sec)
    doc.sections = sections

    result = benchmark.pedantic(
        chunk_structured_document,
        args=(doc, ChunkStrategy.RECURSIVE, 512, 50),
        rounds=5,
    )
    assert isinstance(result, list)
    assert len(result) >= 50
    data = benchmark.stats.stats.sorted_data
    p95 = data[int(len(data) * 0.95)] * 1000 if len(data) >= 20 else data[-1] * 1000
    max_ms = CHUNK_STRUCTURED_50_SECTIONS_P95_MAX_MS
    assert p95 < max_ms, f"Structured doc p95={p95:.0f}ms > {max_ms}ms"
