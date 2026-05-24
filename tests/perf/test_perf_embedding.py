"""Performance: embedding model — encode latency, batch scaling, cold start."""

from __future__ import annotations

import os
import time

import pytest

EMBED_SINGLE_P95_MAX_MS = 200
EMBED_BATCH_50_P95_MAX_MS = 2000
EMBED_COLD_START_P95_MAX_MS = 14000 if os.name == "nt" else 9000
EMBED_SMALL_TEXT_P95_MAX_MS = 200
EMBED_LARGE_TEXT_P95_MAX_MS = 300


def _time_one(fn, arg):
    start = time.perf_counter()
    fn(arg)
    return time.perf_counter() - start


# ── Cold start ────────────────────────────────────────────────────────


def test_embed_cold_start(perf_env):
    from agentnexus.rag import chroma_client

    chroma_client._reset_chroma_client(reset_model=True)

    start = time.perf_counter()
    model = chroma_client.get_embedding_model()
    elapsed = time.perf_counter() - start
    p95 = elapsed * 1000 * 1.05
    assert p95 < EMBED_COLD_START_P95_MAX_MS, f"Cold start p95={p95:.0f}ms"
    assert model is not None


# ── Single text ───────────────────────────────────────────────────────


def test_embed_single_text(benchmark, perf_env):
    from agentnexus.rag.chroma_client import _embed_texts, get_embedding_model

    get_embedding_model()
    result = benchmark(_embed_texts, ["What is vector search?"])
    assert isinstance(result, list)
    assert len(result) == 1


# ── Batch encoding ────────────────────────────────────────────────────


def test_embed_batch_10(benchmark, perf_env):
    from agentnexus.rag.chroma_client import _embed_texts, get_embedding_model

    get_embedding_model()
    texts = [
        f"Test document number {i} with some meaningful content for embedding performance testing."
        for i in range(10)
    ]
    result = benchmark(_embed_texts, texts)
    assert isinstance(result, list)
    assert len(result) == 10


def test_embed_batch_50(perf_env):
    from agentnexus.rag.chroma_client import _embed_texts, get_embedding_model

    get_embedding_model()
    texts = [
        f"Test document number {i} with some meaningful content for embedding performance testing."
        for i in range(50)
    ]

    start = time.perf_counter()
    result = _embed_texts(texts)
    elapsed = time.perf_counter() - start
    p95 = elapsed * 1000 * 1.05
    assert p95 < EMBED_BATCH_50_P95_MAX_MS, f"Batch 50 p95={p95:.0f}ms"
    assert len(result) == 50


# ── Text length sensitivity ───────────────────────────────────────────


def test_embed_text_length_sensitivity(perf_env):
    from agentnexus.rag.chroma_client import _embed_texts, get_embedding_model

    get_embedding_model()

    short_text = "Short query for testing."
    long_text = short_text * 100

    start = time.perf_counter()
    _embed_texts([short_text])
    short_time = time.perf_counter() - start

    start = time.perf_counter()
    _embed_texts([long_text])
    long_time = time.perf_counter() - start

    ratio = long_time / max(short_time, 0.0001)
    assert ratio < 20.0, f"Text length ratio too high: {ratio:.1f}x"


# ── Batch scaling linearity ───────────────────────────────────────────


def test_embed_batch_scaling_linearity(perf_env):
    from agentnexus.rag.chroma_client import _embed_texts, get_embedding_model

    get_embedding_model()

    texts_1 = ["test"] * 1
    texts_10 = ["test"] * 10
    texts_50 = ["test"] * 50

    t1 = _time_one(_embed_texts, texts_1)
    t10 = _time_one(_embed_texts, texts_10)
    t50 = _time_one(_embed_texts, texts_50)

    ratio_10 = t10 / max(t1, 0.0001)
    ratio_50 = t50 / max(t1, 0.0001)
    assert ratio_10 < 15.0, f"10x scaling too high: {ratio_10:.1f}x"
    assert ratio_50 < 60.0, f"50x scaling too high: {ratio_50:.1f}x"


# ── Determinism ───────────────────────────────────────────────────────


def test_embed_deterministic(perf_env):
    from agentnexus.rag.chroma_client import _embed_texts, get_embedding_model

    get_embedding_model()
    r1 = _embed_texts(["deterministic test"])
    r2 = _embed_texts(["deterministic test"])
    assert r1[0] == pytest.approx(r2[0], abs=1e-5)
