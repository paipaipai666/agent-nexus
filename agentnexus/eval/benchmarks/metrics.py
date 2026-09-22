"""TREC-style retrieval metrics, written against the trec_eval conventions.

Zero-dependency on purpose: pytrec_eval wheels are unreliable to install on
Windows/offline mirrors, and BEIR only needs a small metric set. Formulas
follow trec_eval (linear gain for NDCG, binary relevance for MAP), which is
what the BEIR leaderboard numbers are computed with.

Conventions:
  - NDCG@k: linear gain, ideal DCG from sorted relevance levels.
  - MAP: binary relevance (rel >= 1), queries without relevant docs are
    excluded from the average (mirrors BEIR test splits, where every query
    has at least one relevant doc).
  - recall@k / precision@k / hits@k: averaged over queries that have at
    least one relevant document; callers see ``n_queries_with_rels``.
"""

from __future__ import annotations

import math


def _binary_rels(doc_scores: dict[str, int]) -> set[str]:
    return {doc_id for doc_id, rel in doc_scores.items() if rel >= 1}


def ndcg_at_k(ranking: list[str], doc_scores: dict[str, int], k: int) -> float:
    """NDCG@k with linear gain (trec_eval convention)."""
    ideal_levels = sorted((rel for rel in doc_scores.values() if rel > 0), reverse=True)[:k]
    if not ideal_levels:
        return 0.0

    dcg = 0.0
    for rank, doc_id in enumerate(ranking[:k]):
        rel = doc_scores.get(doc_id, 0)
        if rel > 0:
            dcg += rel / math.log2(rank + 2)

    idcg = sum(level / math.log2(rank + 2) for rank, level in enumerate(ideal_levels))
    return dcg / idcg if idcg > 0 else 0.0


def average_precision(ranking: list[str], relevant: set[str]) -> float:
    """AP with binary relevance; 0 when no relevant doc exists."""
    if not relevant:
        return 0.0
    hits = 0
    total = 0.0
    for rank, doc_id in enumerate(ranking):
        if doc_id in relevant:
            hits += 1
            total += hits / (rank + 1)
    return total / len(relevant)


def recall_at_k(ranking: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    retrieved = {doc_id for doc_id in ranking[:k] if doc_id in relevant}
    return len(retrieved) / len(relevant)


def precision_at_k(ranking: list[str], relevant: set[str], k: int) -> float:
    if k <= 0:
        return 0.0
    retrieved = sum(1 for doc_id in ranking[:k] if doc_id in relevant)
    return retrieved / k


def hits_at_k(ranking: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return 1.0 if any(doc_id in relevant for doc_id in ranking[:k]) else 0.0


def score_rankings(
    rankings: dict[str, list[str]],
    qrels: dict[str, dict[str, int]],
    k: int = 10,
    recall_ks: tuple[int, ...] = (100,),
) -> tuple[dict[str, float], int]:
    """Aggregate metrics over all queries.

    Returns (metrics, n_queries_with_rels). Queries with no relevant document
    are skipped in the averages; the count is returned so callers can report
    the denominator explicitly.
    """
    ndcgs: list[float] = []
    aps: list[float] = []
    hits: list[float] = []
    precisions: list[float] = []
    recalls: dict[int, list[float]] = {rk: [] for rk in recall_ks}
    n_with_rels = 0

    for qid, doc_scores in qrels.items():
        ranking = rankings.get(qid, [])
        relevant = _binary_rels(doc_scores)
        if not relevant:
            continue
        n_with_rels += 1
        ndcgs.append(ndcg_at_k(ranking, doc_scores, k))
        aps.append(average_precision(ranking, relevant))
        hits.append(hits_at_k(ranking, relevant, k))
        precisions.append(precision_at_k(ranking, relevant, k))
        for rk in recall_ks:
            recalls[rk].append(recall_at_k(ranking, relevant, rk))

    n = max(n_with_rels, 1)

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    metrics = {
        f"ndcg@{k}": mean(ndcgs),
        "map": mean(aps),
        f"hits@{k}": mean(hits),
        f"precision@{k}": mean(precisions),
    }
    for rk in recall_ks:
        metrics[f"recall@{rk}"] = mean(recalls[rk])
    return metrics, n_with_rels
