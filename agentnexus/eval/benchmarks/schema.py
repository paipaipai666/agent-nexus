"""Benchmark evaluation schemas — standard IR shape, decoupled from EvalSample.

The built-in 60-question track (EvalSample / reference_contexts) is a chunked
knowledge-base track. Public benchmarks (BEIR/MTEB and friends) instead speak
corpus + queries + qrels keyed by document id. These types are the pivot
between a benchmark's raw layout and our retriever primitives.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BenchmarkDoc:
    doc_id: str
    title: str
    text: str

    @property
    def indexed_text(self) -> str:
        """BEIR convention: index title and text together."""
        title = (self.title or "").strip()
        text = (self.text or "").strip()
        return f"{title} {text}".strip() if title else text


@dataclass(frozen=True)
class BenchmarkQuery:
    query_id: str
    text: str


@dataclass
class BenchmarkData:
    suite: str
    dataset: str
    docs: list[BenchmarkDoc]
    queries: list[BenchmarkQuery]
    qrels: dict[str, dict[str, int]]
    license_note: str = ""
    # e2e extras: gold answers and null-query markers (MultiHop-RAG track)
    answers: dict[str, str] = field(default_factory=dict)
    null_query_ids: set[str] = field(default_factory=set)

    def relevant_counts(self) -> dict[str, int]:
        return {qid: len(doc_scores) for qid, doc_scores in self.qrels.items()}


# Retrieval modes. ``dense`` matches the official BEIR leaderboard pipeline
# (single dense retriever, no reranker); ``hybrid`` is this project's own
# RRF(dense, BM25) stack and is reported alongside, never mixed into the
# leaderboard-comparable number.
DENSE = "dense"
HYBRID = "hybrid"
RETRIEVAL_MODES = (DENSE, HYBRID)


@dataclass
class DatasetResult:
    suite: str
    dataset: str
    mode: str
    embedding_model: str
    n_docs: int
    n_queries: int
    n_queries_with_rels: int
    depth: int
    metrics: dict[str, float]
    elapsed_s: float
    license_note: str = ""
    data_path: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class SuiteReport:
    suite: str
    run_at: str
    results: list[DatasetResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "suite": self.suite,
            "run_at": self.run_at,
            "kind": "retrieval-benchmark",
            "results": [
                {
                    "dataset": r.dataset,
                    "mode": r.mode,
                    "embedding_model": r.embedding_model,
                    "n_docs": r.n_docs,
                    "n_queries": r.n_queries,
                    "n_queries_with_rels": r.n_queries_with_rels,
                    "depth": r.depth,
                    "metrics": r.metrics,
                    "elapsed_s": round(r.elapsed_s, 2),
                    "license_note": r.license_note,
                    "data_path": r.data_path,
                    "notes": r.notes,
                }
                for r in self.results
            ],
        }
