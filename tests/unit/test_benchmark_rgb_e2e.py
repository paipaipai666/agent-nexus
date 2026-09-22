"""Tests for RGB loader, rate limiter, and e2e runner."""

import json
import time

import pytest

from agentnexus.eval.benchmarks import e2e, ratelimit
from agentnexus.eval.benchmarks.rgb_loader import (
    RGBQuery,
    is_rejection_output,
    load_rgb_queries,
    rgb_generation_prompt,
)


@pytest.fixture
def rgb_cache(tmp_path, monkeypatch):
    cache = tmp_path / "rgb-cache"
    (cache / "rgb").mkdir(parents=True)
    monkeypatch.setenv("AGENTNEXUS_BENCHMARK_CACHE", str(cache))
    rows = [
        {
            "id": 0,
            "query": "query one",
            "answer": [["ans1", "ans1-alt"]],
            "positive": [f"pos doc {i} about alpha" for i in range(3)],
            "negative": [f"neg doc {i} about beta" for i in range(3)],
        },
        {
            "id": 1,
            "query": "query two",
            "answer": [["ans2"]],
            "positive": ["only positive doc"],
            "negative": [],
        },
    ]
    (cache / "rgb" / "zh.json").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
    )
    return cache


def test_load_rgb_queries(rgb_cache):
    queries = load_rgb_queries("zh", "noise", offline=True)
    assert len(queries) == 2
    q0, q1 = queries
    assert q0.task == "noise" and q0.language == "zh"
    assert q0.answer_groups == [["ans1", "ans1-alt"]]
    assert q0.noise_rate == pytest.approx(0.5)
    assert q1.noise_rate == 0.0
    assert q1.ground_truth_text() == "ans2"


def test_build_corpus_sampling(rgb_cache):
    (query,) = [q for q in load_rgb_queries("zh", "noise", offline=True) if q.query_id == "0"]
    docs = query.build_corpus(passage_num=5, seed=7)
    # noise_rate=0.5 -> neg_num = ceil(5*0.5) = 3, pos_num = min(5-3, 3) = 2
    assert len(docs) == 5
    assert sum(d in query.negative for d in docs) == 3
    assert sum(d in query.positive for d in docs) == 2


def test_missing_file_offline_raises(rgb_cache):
    with pytest.raises(FileNotFoundError, match="Download"):
        load_rgb_queries("zh", "rejection", offline=True)


def test_generation_prompt_and_rejection_rules(rgb_cache):
    (query,) = [q for q in load_rgb_queries("zh", "noise", offline=True) if q.query_id == "0"]
    system, user = rgb_generation_prompt(query, ["d1", "d2"])
    assert "外部文档" in system and "噪声" in system
    assert "d1" in user and query.query in user

    assert is_rejection_output("文档信息不足，因此我无法基于提供的文档回答该问题。", "zh")
    assert is_rejection_output("I can not answer the question because of the insufficient information in documents.", "en")
    assert not is_rejection_output("答案是 2022 年 1 月 2 日。", "zh")


def test_rate_limiter_pacing(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    limiter = ratelimit.RateLimiter(rate_per_minute=120)  # 0.5s interval
    for _ in range(3):
        limiter.acquire()
    assert len(sleeps) == 2  # first call free, then paced
    # sleep is stubbed so the clock never advances: waits accumulate 0.5, 1.0
    assert sleeps[0] == pytest.approx(0.5, abs=0.05)
    assert sleeps[1] == pytest.approx(1.0, abs=0.05)


def test_call_with_retry_success_and_backoff(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    limiter = ratelimit.RateLimiter(rate_per_minute=60000)

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            return ""
        return "ok"

    assert ratelimit.call_with_retry(flaky, limiter) == "ok"
    assert calls["n"] == 3

    def always_bad():
        raise RuntimeError("boom")

    assert ratelimit.call_with_retry(always_bad, limiter, max_retries=2) == ""


class _FakeLLM:
    def __init__(self, model="fake", script=None):
        self.model = model
        self._script = script or (lambda q, docs: "答案是 X")
        self.calls = 0

    def think(self, messages, silent=True, **kwargs):
        self.calls += 1
        user = messages[-1]["content"]
        return self._script(user, messages)


def _patch_e2e_env(monkeypatch, tmp_path, script=None):
    """Wire fake generator/judge/embeddings + isolated chroma for e2e tests."""
    from agentnexus.core.config import get_settings
    from agentnexus.storage.chroma import reset_storage_client

    settings = get_settings()
    monkeypatch.setattr(settings, "chroma_persist_dir", str(tmp_path / "chroma"))
    reset_storage_client()
    # retry backoff must not slow tests down
    monkeypatch.setattr(ratelimit.time, "sleep", lambda s: None)

    import hashlib
    import math

    class _FakeEmbeddingModel:
        def encode(self, inputs, normalize_embeddings=True):
            single = isinstance(inputs, str)
            texts = [inputs] if single else list(inputs)
            vectors = []
            for text in texts:
                digest = hashlib.sha256(text.strip().lower().encode()).digest()
                vec = [(b / 127.5) - 1.0 for b in digest[:64]]
                norm = math.sqrt(sum(v * v for v in vec)) or 1.0
                vectors.append([v / norm for v in vec])
            return vectors[0] if single else vectors

    monkeypatch.setattr("agentnexus.rag.embeddings.get_embedding_model", lambda: _FakeEmbeddingModel())
    monkeypatch.setattr(
        "agentnexus.rag.embeddings.embed_texts",
        lambda texts: _FakeEmbeddingModel().encode(list(texts)),
    )

    generator = _FakeLLM("fake-gen", script)
    judge = _FakeLLM("fake-judge", script=lambda u, m: "0.95")  # always correct
    monkeypatch.setattr(e2e, "_make_llm", lambda selector: generator)
    monkeypatch.setattr("agentnexus.core.judge_llm.get_judge_llm", lambda: judge)
    return generator, judge


def _mk_query(task="noise", language="zh", query_id="q1"):
    return RGBQuery(
        query_id=query_id,
        query="什么语言主要在英国使用?",
        answer_groups=[["英语", "英文"]],
        positive=["英国的主要语言是英语。"],
        negative=["法国的主要语言是法语。"],
        task=task,
        language=language,
    )


def test_e2e_noise_task_judged_correct(monkeypatch, tmp_path):
    _patch_e2e_env(monkeypatch, tmp_path)
    summary = e2e.run_rgb_e2e([_mk_query()], rpm=60000)
    assert summary.n_total == 1 and summary.n_errors == 0
    assert summary.records[0].correct is True
    assert summary.records[0].judge_score == pytest.approx(0.95)
    assert summary.accuracy == pytest.approx(1.0)


def test_e2e_rejection_task_rule_judged(monkeypatch, tmp_path):
    def reject_script(user, messages):
        return "文档信息不足，因此我无法基于提供的文档回答该问题。"

    _patch_e2e_env(monkeypatch, tmp_path, script=reject_script)
    summary = e2e.run_rgb_e2e([_mk_query(task="rejection")], rpm=60000)
    assert summary.records[0].judge_score is None  # rule-based, no LLM judge
    assert summary.records[0].correct is True


def test_e2e_generate_only_defers_judging(monkeypatch, tmp_path):
    _, judge = _patch_e2e_env(monkeypatch, tmp_path)
    summary = e2e.run_rgb_e2e([_mk_query()], rpm=60000, judge_enabled=False)
    record = summary.records[0]
    assert record.generation  # generation still ran and was stored
    assert record.correct is None  # not judged — deferred to a later rejudge pass
    assert record.judge_score is None
    assert judge.calls == 0  # zero judge LLM calls
    assert summary.n_correct == 0 and summary.accuracy == 0.0


def test_reinforce_refusal_appends_discipline_clause():
    from agentnexus.eval.benchmarks.rgb_loader import rgb_generation_prompt

    query = _mk_query(task="rejection")
    base_system, _ = rgb_generation_prompt(query, ["d1"])
    reinforced_system, _ = rgb_generation_prompt(query, ["d1"], reinforce_refusal=True)
    assert len(reinforced_system) > len(base_system)
    assert "不要根据常识" in reinforced_system

    en_query = _mk_query(task="rejection", language="en")
    en_base, _ = rgb_generation_prompt(en_query, ["d1"])
    en_reinforced, _ = rgb_generation_prompt(en_query, ["d1"], reinforce_refusal=True)
    assert "Do NOT answer from common knowledge" in en_reinforced
    assert en_base != en_reinforced


def test_e2e_per_query_error_isolated(monkeypatch, tmp_path):
    _patch_e2e_env(monkeypatch, tmp_path)

    def boom_script(user, messages):
        raise RuntimeError("provider down")

    monkeypatch.setattr(e2e, "_make_llm", lambda selector: _FakeLLM("gen", boom_script))
    queries = [_mk_query(), _mk_query(query_id="q2")]
    summary = e2e.run_rgb_e2e(queries, rpm=60000)
    assert summary.n_total == 2 and summary.n_errors == 2 and summary.accuracy == 0.0


def test_rejudge_report_scores_and_agrees(monkeypatch, tmp_path, rgb_cache):
    """Stored generations can be re-judged later without re-running them."""
    import json as _json

    from agentnexus.eval.benchmarks.e2e import rejudge_report

    _patch_e2e_env(monkeypatch, tmp_path)  # provides fake judge returning 0.95

    report_path = tmp_path / "report.json"
    report_path.write_text(_json.dumps({
        "kind": "rgb-e2e",
        "summary": {"language": "zh", "task": "noise", "judge_model": "old-judge"},
        "records": [
            {"query_id": "0", "generation": "答案是 12 人", "correct": True, "judge_score": 1.0},
            {"query_id": "1", "generation": "答案是 12 人", "correct": False, "judge_score": 0.0},
            {"query_id": "999", "generation": "orphan", "correct": True},  # not in dataset
        ],
    }, ensure_ascii=False), encoding="utf-8")

    result = rejudge_report(report_path, rpm=60000)

    assert result["original_judge"] == "old-judge"
    assert result["new_judge"] == "fake-judge"
    # fake judge always scores 0.95 -> all found records correct
    assert result["records"][0]["judge_score_new"] == pytest.approx(0.95)
    assert result["records"][0]["correct_new"] is True
    assert result["records"][1]["correct_new"] is True  # flipped from original
    assert result["records"][2]["rejudge_error"] == "query not found in dataset"
    assert result["n_errors"] == 1
    assert result["agreement"] == pytest.approx(0.5)  # 1 of 2 comparable agreed


def test_rejudge_rejects_non_rgb_and_rejection_reports(tmp_path):
    import json as _json

    from agentnexus.eval.benchmarks.e2e import rejudge_report

    bad = tmp_path / "bad.json"
    bad.write_text(_json.dumps({"kind": "something-else", "summary": {}, "records": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="not an rgb-e2e report"):
        rejudge_report(bad)

    rej = tmp_path / "rej.json"
    rej.write_text(_json.dumps({
        "kind": "rgb-e2e",
        "summary": {"language": "zh", "task": "rejection"},
        "records": [],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="nothing to re-judge"):
        rejudge_report(rej)
