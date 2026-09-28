"""Regression tests: the /api/knowledge/search memory-growth fixes.

Background (observed on a live `nexus serve`): process private bytes grew to
36.8 GB during subagent-heavy runs. Root-cause chain that these tests pin:

(1) `kb_service.search_kb` built a FRESH HybridRetriever on every call —
    all chunk texts into RAM + a full BM25 rebuild — instead of reusing the
    process-wide singleton that the `kb_search` agent tool uses;
(2) `load_reranker()` constructed a brand-new ~2.3 GB fp32 CrossEncoder per
    retriever; in the incident environment the tokenizer step then failed on
    a HuggingFace Hub round-trip (offline/GFW), discarding the allocation —
    every search re-loaded and re-discarded the model (probe:
    experiments/probe_wave_repro.py showed concurrent waves ratcheting
    private memory by GBs);
(3) failures retried on EVERY call (no negative caching), so a bad
    network/config turned each search into a multi-GB churn.

Fixed contract: one retriever rebuild + one reranker construction per
process; failed loads are cached for a TTL and load offline from the local
HF snapshot. Before the fix these asserted 2/2/2 (per-call churn); they now
pin 1/1/1.
"""

import sys
import types

import pytest

import agentnexus.rag.embeddings as embeddings_mod
import agentnexus.rag.kb_service as kb_service
import agentnexus.rag.retriever as retriever_mod
from agentnexus.rag.retriever import HybridRetriever, reset_reranker_cache


class _CountingRetriever:
    """Stand-in for HybridRetriever that counts the expensive operations."""

    constructions = 0
    rebuilds = 0
    reranker_loads = 0

    def __init__(self, namespace):
        type(self).constructions += 1
        self.namespace = namespace
        self._chunks = {"chunk-1": object()}  # non-empty so search proceeds
        self._reranker = None

    def rebuild_from_catalog(self):
        type(self).rebuilds += 1

    def load_reranker(self, model_name=None):
        type(self).reranker_loads += 1
        self._reranker = object()

    def search(self, query, dense, top_k, min_score, metadata_filters):
        return [{"chunk_id": "chunk-1", "score": 1.0}]

    def expand_contexts(self, results, view=None):
        return results


@pytest.fixture(autouse=True)
def _isolate_reranker_cache():
    """The reranker cache is process-global state — reset around every test."""
    reset_reranker_cache()
    yield
    reset_reranker_cache()


@pytest.fixture
def counting_retriever(monkeypatch):
    _CountingRetriever.constructions = 0
    _CountingRetriever.rebuilds = 0
    _CountingRetriever.reranker_loads = 0
    monkeypatch.setattr(kb_service, "chroma_search", lambda *a, **k: [{"id": "chunk-1"}])
    # keep the test offline: no LLM query rewrite / multi-query expansion
    monkeypatch.setattr(kb_service, "expand_queries", lambda q: [q])
    # the singleton lives in the retriever module — reset it and route its
    # construction through the counting fake
    monkeypatch.setattr(retriever_mod, "HybridRetriever", _CountingRetriever)
    monkeypatch.setattr(retriever_mod, "_retriever", None)
    yield _CountingRetriever
    reset_reranker_cache()


def test_search_kb_reuses_singleton_retriever_and_reranker(counting_retriever):
    """FIX (1): two API calls share one retriever — one rebuild, no reload."""
    kb_service.search_kb("q1", namespace="default", top_k=3, view="chunk")
    kb_service.search_kb("q2", namespace="default", top_k=3, view="chunk")

    assert counting_retriever.constructions == 1
    assert counting_retriever.rebuilds == 1
    assert counting_retriever.reranker_loads == 1


def test_load_reranker_constructs_cross_encoder_once(monkeypatch):
    """FIX (2): repeated load_reranker calls share one CrossEncoder."""
    constructions = []
    fake_st = types.ModuleType("sentence_transformers")

    class _FakeCrossEncoder:
        def __init__(self, *args, **kwargs):
            constructions.append((args, kwargs))

    fake_st.CrossEncoder = _FakeCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    retriever = HybridRetriever(namespace="default")
    retriever.load_reranker()
    first = retriever._reranker
    retriever.load_reranker()

    assert len(constructions) == 1  # was 2 before the fix
    assert retriever._reranker is first


def test_load_reranker_failure_cached_with_ttl(monkeypatch):
    """FIX (3): a failed load is not retried on every call.

    Without negative caching, a offline/GFW tokenizer timeout re-materialized
    2.3 GB of weights on every kb_search. After a failure, subsequent calls
    within the TTL skip construction entirely; reset_reranker_cache() makes
    it retry.
    """
    constructions = []
    fake_st = types.ModuleType("sentence_transformers")

    class _FailingCrossEncoder:
        def __init__(self, *args, **kwargs):
            constructions.append((args, kwargs))
            raise RuntimeError("simulated tokenizer network timeout")

    fake_st.CrossEncoder = _FailingCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    r = HybridRetriever(namespace="default")
    r.load_reranker()
    r.load_reranker()
    r.load_reranker()
    assert len(constructions) == 1  # failure cached, no churn

    reset_reranker_cache()
    r.load_reranker()
    assert len(constructions) == 2  # explicit reset retries


def test_tool_path_reuses_singleton_retriever(monkeypatch):
    """CONTROL: the agent-tool path (kb_search) reuses ONE process-wide retriever."""
    _CountingRetriever.constructions = 0
    monkeypatch.setattr(retriever_mod, "HybridRetriever", _CountingRetriever)
    monkeypatch.setattr(retriever_mod, "_retriever", None)

    r1 = retriever_mod._get_retriever(namespace="default")
    r2 = retriever_mod._get_retriever(namespace="default")

    assert r1 is r2
    assert _CountingRetriever.constructions == 1


def test_embedding_model_loads_once_under_lock(monkeypatch):
    """CONTROL: embeddings already use the load-once singleton pattern
    (rag/embeddings.py, double-checked lock). The reranker path was missing
    exactly this. Pinned so a future refactor can't regress the pattern.
    """
    constructions = []
    fake_st = types.ModuleType("sentence_transformers")

    class _FakeSentenceTransformer:
        def __init__(self, *args, **kwargs):
            constructions.append((args, kwargs))

        def get_sentence_embedding_dimension(self):
            return 8

    fake_st.SentenceTransformer = _FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    monkeypatch.setattr(embeddings_mod, "_resolve_local_model_path", lambda name: "/fake/local/path")

    embeddings_mod.reset_embedding_model()
    try:
        m1 = embeddings_mod.get_embedding_model(
            device_resolver=lambda: "cpu", runtime_configurer=lambda d: None
        )
        m2 = embeddings_mod.get_embedding_model(
            device_resolver=lambda: "cpu", runtime_configurer=lambda d: None
        )
        assert m1 is m2
        assert len(constructions) == 1  # load-once, even across repeated calls
    finally:
        embeddings_mod.reset_embedding_model()
