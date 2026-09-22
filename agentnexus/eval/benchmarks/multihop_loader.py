"""MultiHop-RAG loader (yixuantt/MultiHopRAG, HF revision).

The HF distribution ships a 609-article evidence-connected corpus (not the
full 255k news crawl from the paper) + 2556 multi-hop queries. Evidence links
carry the article ``url``, which we use as the doc id — exact qrels matching
with zero text-overlap guessing.

Question types: inference / comparison / temporal (positive) and null_query
(negative — evidence list is empty, the retrieval analogue of RGB rejection).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .schema import BenchmarkData, BenchmarkDoc, BenchmarkQuery

logger = logging.getLogger(__name__)

MULTIHOP_LICENSE_NOTE = (
    "MultiHop-RAG (yixuantt, ODC-BY) built on ~255k TIMES/NYT-derived news "
    "articles; HF distribution carries the 609-article evidence-connected "
    "corpus. Cite arXiv:2401.15391."
)

_QUESTION_TYPES = ("inference_query", "comparison_query", "temporal_query", "null_query")


def multihop_cache_root() -> Path:
    import os

    override = os.environ.get("AGENTNEXUS_BENCHMARK_CACHE")
    base = Path(override) if override else Path.home() / ".cache" / "agentnexus" / "benchmarks"
    return base / "multihop"


def _ensure_downloaded(root: Path, offline: bool) -> None:
    import httpx

    root.mkdir(parents=True, exist_ok=True)
    files = {
        "corpus.json": "https://hf-mirror.com/datasets/yixuantt/MultiHopRAG/resolve/main/corpus.json",
        "MultiHopRAG.json": "https://hf-mirror.com/datasets/yixuantt/MultiHopRAG/resolve/main/MultiHopRAG.json",
    }
    for name, url in files.items():
        target = root / name
        if target.exists() and target.stat().st_size > 100_000:
            continue
        if offline:
            raise FileNotFoundError(f"MultiHopRAG file missing: {target}")
        logger.info("Downloading %s ...", url)
        resp = httpx.get(url, timeout=600, follow_redirects=True)
        resp.raise_for_status()
        target.write_bytes(resp.content)


def load_multihop(spec=None, offline: bool = False) -> BenchmarkData:
    """Corpus + queries + evidence qrels. Null queries get empty qrels.

    ``spec`` is accepted-and-ignored: suite loaders are called uniformly as
    ``loader(ref.spec, offline=...)`` and this dataset has no sub-specs.
    """
    root = multihop_cache_root()
    _ensure_downloaded(root, offline=offline)

    corpus_rows = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
    query_rows = json.loads((root / "MultiHopRAG.json").read_text(encoding="utf-8"))

    docs = [
        BenchmarkDoc(
            doc_id=str(row.get("url") or f"doc-{i}"),
            title=str(row.get("title") or ""),
            text=str(row.get("body") or ""),
        )
        for i, row in enumerate(corpus_rows)
    ]

    queries: list[BenchmarkQuery] = []
    qrels: dict[str, dict[str, int]] = {}
    answers: dict[str, str] = {}
    null_query_ids: set[str] = set()
    for i, row in enumerate(query_rows):
        qid = str(i)
        queries.append(BenchmarkQuery(query_id=qid, text=str(row.get("query") or "")))
        answers[qid] = str(row.get("answer") or "")
        if row.get("question_type") == "null_query":
            null_query_ids.add(qid)
        evidence = row.get("evidence_list") or []
        qrels[qid] = {}
        for ev in evidence:
            url = str(ev.get("url") or "")
            if url:
                qrels[qid][url] = 1

    n_null = sum(1 for r in query_rows if r.get("question_type") == "null_query")
    logger.info(
        "MultiHopRAG: %d docs, %d queries (null=%d), %d evidence links",
        len(docs), len(queries), n_null, sum(len(v) for v in qrels.values()),
    )
    return BenchmarkData(
        suite="multihop",
        dataset="MultiHopRAG",
        docs=docs,
        queries=queries,
        qrels=qrels,
        license_note=MULTIHOP_LICENSE_NOTE,
        answers=answers,
        null_query_ids=null_query_ids,
    )
