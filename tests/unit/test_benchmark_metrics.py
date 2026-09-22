"""Hand-checked metric values plus a cross-check against ir_measures.

The hand cases pin the trec_eval conventions (linear NDCG gain, binary MAP
relevance, skip queries without relevant docs). The ir_measures test is an
importorskip guard: it only runs where the optional package is installed.
"""

import math

import pytest

from agentnexus.eval.benchmarks import metrics

# qrels: q1 -> {d1:1, d2:2, d3:1}; q2 has no relevant docs.
QRELS = {
    "q1": {"d1": 1, "d2": 2, "d3": 1},
    "q2": {},
}
RANKING_Q1 = ["d2", "d4", "d1"]
RANKING_Q2 = ["d9"]

LOG2_3 = math.log2(3)


def test_ndcg_linear_gain_multilevel():
    # DCG@3 = 2/log2(2) + 0 (d4) + 1/log2(4) = 2.5
    # IDCG@3 = 2/log2(2) + 1/log2(3) + 1/log2(4)
    idcg = 2.0 + 1.0 / LOG2_3 + 0.5
    assert metrics.ndcg_at_k(RANKING_Q1, QRELS["q1"], 3) == pytest.approx(2.5 / idcg)
    # d2 (rel 2) is ranked first -> perfect at k=1
    assert metrics.ndcg_at_k(RANKING_Q1, QRELS["q1"], 1) == pytest.approx(1.0)


def test_map_binary_relevance():
    # hits: rank1 (1/1), rank3 (2/3); 3 relevant docs total
    assert metrics.average_precision(RANKING_Q1, {"d1", "d2", "d3"}) == pytest.approx((1.0 + 2.0 / 3.0) / 3.0)
    assert metrics.average_precision([], {"d1"}) == 0.0


def test_recall_precision_hits():
    relevant = {"d1", "d2", "d3"}
    assert metrics.recall_at_k(RANKING_Q1, relevant, 2) == pytest.approx(1.0 / 3.0)
    assert metrics.precision_at_k(RANKING_Q1, relevant, 2) == pytest.approx(1.0 / 2.0)
    assert metrics.hits_at_k(RANKING_Q1, relevant, 1) == 1.0
    assert metrics.hits_at_k(RANKING_Q1, relevant, 1) == 1.0  # d2 first
    assert metrics.hits_at_k(["d9"], relevant, 10) == 0.0


def test_score_rankings_skips_queries_without_rels():
    rankings = {"q1": RANKING_Q1, "q2": RANKING_Q2}
    metric_map, n_with_rels = metrics.score_rankings(rankings, QRELS, k=3, recall_ks=(4,))
    assert n_with_rels == 1
    # q1-only averages: ndcg@3 and recall@4 against 3 relevant docs
    assert metric_map["ndcg@3"] == pytest.approx(metrics.ndcg_at_k(RANKING_Q1, QRELS["q1"], 3))
    assert metric_map["recall@4"] == pytest.approx(metrics.recall_at_k(RANKING_Q1, {"d1", "d2", "d3"}, 4))
    assert metric_map["map"] == pytest.approx((1.0 + 2.0 / 3.0) / 3.0)


def test_ir_measures_cross_check():
    """Our formulas must match pytrec_eval/ir_measures per-query values."""
    ir_measures = pytest.importorskip("ir_measures")
    from ir_measures import Qrel, ScoredDoc

    qrels = [Qrel("q1", "d2", 2), Qrel("q1", "d1", 1), Qrel("q1", "d3", 1), Qrel("q2", "d9", 1)]
    run = [
        ScoredDoc("q1", "d2", 0.9), ScoredDoc("q1", "d4", 0.8), ScoredDoc("q1", "d1", 0.7),
        ScoredDoc("q2", "d9", 0.9), ScoredDoc("q2", "d1", 0.1),
    ]
    theirs = {
        str(measure): value
        for measure, value in ir_measures.calc_aggregate(
            [ir_measures.nDCG@10, ir_measures.MAP, ir_measures.Recall@100], qrels, run
        ).items()
    }

    ours, n = metrics.score_rankings(
        {"q1": ["d2", "d4", "d1"], "q2": ["d9", "d1"]},
        {"q1": {"d2": 2, "d1": 1, "d3": 1}, "q2": {"d9": 1}},
        k=10,
    )
    assert n == 2
    assert ours["ndcg@10"] == pytest.approx(theirs["nDCG@10"], abs=1e-9)
    assert ours["map"] == pytest.approx(theirs["AP"], abs=1e-9)
    assert ours["recall@100"] == pytest.approx(theirs["R@100"], abs=1e-9)
