"""Offline tests for the conversational memory benchmark framework.

No LLM: backends run against a scripted fake generator, so CI exercises the
full ingest → answer → score → report path for free.
"""

from __future__ import annotations

import json

import pytest

from agentnexus.eval.memory.backend import NaiveBackend
from agentnexus.eval.memory.dataset import load_builtin_suite, load_suite
from agentnexus.eval.memory.runner import MemoryBenchRunner
from agentnexus.eval.memory.schema import Turn
from agentnexus.eval.memory.scoring import char_f1, gold_coverage


class FakeLLM:
    """Answers from a canned fact table, echoes nothing else."""

    def __init__(self, facts: dict[str, str]):
        self.facts = facts
        self.calls: list[list[dict]] = []

    def think(self, messages, silent=True):
        self.calls.append(messages)
        question = messages[-1]["content"]
        return self.facts.get(question, "无法确定")


# ── scoring ──────────────────────────────────────────────────────────

def test_char_f1_exact_and_partial():
    assert char_f1("PostgreSQL", "PostgreSQL") == 1.0
    assert char_f1("使用 PostgreSQL 数据库", "PostgreSQL") > 0.5
    assert char_f1("苹果", "香蕉") == 0.0          # disjoint char multisets
    assert char_f1("", "PostgreSQL") == 0.0


def test_char_f1_punctuation_and_case_insensitive():
    assert char_f1("ruff 和 mypy！", "ruff和mypy") == 1.0


# ── retrieval-level (model-free) coverage ────────────────────────────

def test_gold_coverage_substring_with_normalization():
    assert gold_coverage("PostgreSQL", "数据库选型是 PostgreSQL，排除 SQLite。")
    assert gold_coverage("3.11", "之前的版本是 3.11")          # temporal old value
    assert not gold_coverage("3.12", "之前的版本是 3.11")
    assert not gold_coverage("", "anything")                    # empty gold
    assert not gold_coverage("FastAPI", "")                     # empty context


# ── dataset ──────────────────────────────────────────────────────────

def test_builtin_suite_wellformed():
    suite = load_builtin_suite()
    assert len(suite) == 3
    total_q = sum(len(c.questions) for c in suite)
    assert total_q == 15
    types = {q.qa_type for c in suite for q in c.questions}
    assert types == {
        "single_hop", "multi_session", "knowledge_update",
        "temporal", "distractor_filter", "abstention",
    }
    for c in suite:
        assert all(t.role in ("user", "assistant") for t in c.turns)
        assert all(q.answer and q.question for q in c.questions)


def test_load_suite_roundtrip(tmp_path):
    # Build via dict → JSONL to exercise the real parse path.
    line = json.dumps({
        "id": "c1",
        "turns": [{"role": "user", "content": "记住部署端口是 8080"}],
        "questions": [{"id": "q1", "question": "端口？", "answer": "8080"}],
    }, ensure_ascii=False)
    path = tmp_path / "suite.jsonl"
    path.write_text(line + "\n", encoding="utf-8")
    suite = load_suite(path)
    assert len(suite) == 1
    assert suite[0].questions[0].qa_type == "single_hop"  # default type


def test_load_suite_rejects_bad_qa_type(tmp_path):
    line = json.dumps({
        "id": "c1",
        "turns": [{"role": "user", "content": "x"}],
        "questions": [{"id": "q1", "question": "q", "answer": "a", "qa_type": "nonsense"}],
    }, ensure_ascii=False)
    path = tmp_path / "bad.jsonl"
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="qa_type"):
        load_suite(path)


# ── runner end-to-end (fake backend, no LLM) ─────────────────────────

def _fake_backend(facts: dict[str, str]) -> NaiveBackend:
    return NaiveBackend(FakeLLM(facts))


def test_runner_scores_and_aggregates():
    suite = load_builtin_suite()
    facts = {
        "这个项目后端用什么框架？": "FastAPI",
        "项目当前使用的 Python 版本是多少？": "3.12",
        "项目部署在哪一家云厂商上？": "无法确定",
    }
    backend = _fake_backend(facts)
    report = MemoryBenchRunner(backend, dataset_name="sample").run(suite)

    assert report.backend == "naive"
    assert len(report.results) == 15
    assert report.error_count == 0

    by_id = {r.question_id: r for r in report.results}
    assert by_id["sw-q1"].f1 == 1.0            # exact fact
    assert by_id["sw-q3"].f1 == 1.0            # knowledge_update answered current
    assert by_id["sw-q5"].f1 == 1.0            # abstention gold "无法确定"
    assert by_id["sw-q4"].f1 == 0.0            # wrong "无法确定" vs gold "3.11": no char overlap
    # FakeLLM only knows 3 facts → 1 of 6 single_hop questions answered.
    assert report.type_f1("single_hop") == pytest.approx(1 / 6)

    # Retrieval-level: NaiveBackend's context IS the transcript, so golds
    # stated in the dialogue are covered regardless of the fake LLM's knowledge.
    assert by_id["sw-q1"].retrieval_hit is True     # FastAPI in transcript
    assert by_id["sw-q2"].retrieval_hit is True     # PostgreSQL in transcript
    assert by_id["sw-q3"].retrieval_hit is True     # 3.12 in transcript
    assert by_id["sw-q4"].retrieval_hit is True     # 3.11 still in transcript
    assert by_id["sw-q5"].retrieval_hit is None     # abstention: check not applicable
    checked = [r for r in report.results if r.retrieval_hit is not None]
    assert report.retrieval_hit_rate == pytest.approx(
        sum(r.retrieval_hit for r in checked) / len(checked))
    assert report.type_retrieval_rate("abstention") is None

    payload = report.to_dict()
    json.dumps(payload, ensure_ascii=False)    # must serialize
    assert payload["by_type"]["single_hop"]["n"] == 6
    assert payload["retrieval_hit_rate"] is not None
    assert "retrieval_hit" in payload["results"][0]
    assert "context_chars" in payload["results"][0]


def test_runner_ingest_error_captured():
    class ExplodingBackend(NaiveBackend):
        def ingest(self, turns):
            raise RuntimeError("boom")

    report = MemoryBenchRunner(ExplodingBackend(FakeLLM({}))).run(
        load_builtin_suite()[:1])
    assert report.error_count == 5             # all questions of conv 1 flagged
    assert all("boom" in r.error for r in report.results)


def test_runner_limit():
    backend = _fake_backend({})
    report = MemoryBenchRunner(backend).run(load_builtin_suite(), limit=1)
    assert {r.conversation_id for r in report.results} == {"shopmind-webshop"}


def test_naive_backend_budget_keeps_newest():
    turns = [Turn(role="user", content=f"turn {i:03d} " + "x" * 40) for i in range(20)]
    backend = NaiveBackend(FakeLLM({}), max_turn_chars=500)
    backend.ingest(turns)
    assert "turn 019" in backend._transcript
    assert "turn 000" not in backend._transcript
