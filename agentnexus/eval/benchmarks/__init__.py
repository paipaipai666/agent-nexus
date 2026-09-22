"""Public benchmarks for the RAG stack (BEIR/MTEB-style, document-level).

This package is the second evaluation track, separate from the built-in
60-question chunked-knowledge-base track (agentnexus.rag.evaluator):

- built-in track: chunk-strategy grid over a small inline corpus, LLM-judged
  generation quality — for tuning the RAG pipeline.
- benchmark track (this package): standard IR corpora with qrels, exact
  doc-id matching, NDCG@10/MAP/Recall — for comparing against public
  leaderboards and detecting retriever regressions. No LLM calls.

Entry points::

    from agentnexus.eval.benchmarks import list_suites, get_suite, run_retrieval
"""

from .runner import run_retrieval
from .schema import DENSE, HYBRID, RETRIEVAL_MODES, BenchmarkData, BenchmarkDoc, BenchmarkQuery, DatasetResult
from .suites import get_suite, list_suites, load_suite_datasets

__all__ = [
    "BenchmarkData",
    "BenchmarkDoc",
    "BenchmarkQuery",
    "DENSE",
    "DatasetResult",
    "HYBRID",
    "RETRIEVAL_MODES",
    "get_suite",
    "list_suites",
    "load_suite_datasets",
    "run_retrieval",
]
