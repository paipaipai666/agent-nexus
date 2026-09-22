"""End-to-end RGB evaluation: per-query corpus -> retrieve -> generate -> judge.

Protocol follows the official RGB release (instruction template, passage_num=5,
noise_rate sampled per query), with our hybrid retriever in the loop:

- retrieval: BenchmarkIndex over the query's 5-doc corpus (temp ChromaDB, never
  the user's main store), top_k ranking feeds the generator;
- generation: official RGB system+user template via AgentLLM (any configured
  provider/model, e.g. agnes/agnes-3.0-flash);
- judging: rejection tasks use the official keyword rules (no LLM call);
  noise tasks reuse the evaluator's correctness judge prompt, >= 0.5 = correct.

All LLM calls flow through one RateLimiter — Agnes free tier shares its RPM
pool across keys, so generation and judging serialize against the same bucket.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from agentnexus.prompts import load_prompt

from .ratelimit import RateLimiter, call_with_retry
from .rgb_loader import RGBQuery, is_rejection_output, rgb_generation_prompt
from .runner import BenchmarkIndex
from .schema import DENSE, RETRIEVAL_MODES, BenchmarkData, BenchmarkDoc, BenchmarkQuery

logger = logging.getLogger(__name__)

EVAL_CORRECTNESS_PROMPT = load_prompt("eval_correctness")

NOISE_BUCKETS = ((0.0, 0.5), (0.5, 0.8), (0.8, 1.01))


@dataclass
class E2ERecord:
    query_id: str
    task: str
    language: str
    noise_rate: float
    query: str
    generation: str
    correct: bool | None  # None = generated but not yet judged (generate-only mode)
    judge_score: float | None  # None for rule-judged rejection tasks or pending
    latency_s: float
    error: str = ""
    # retrieval attribution: how many of the top-k contexts were positive docs
    retrieval_pos_hits: int | None = None
    retrieval_pos_total: int | None = None


@dataclass
class E2ESummary:
    task: str
    language: str
    model: str
    judge_model: str
    n_total: int
    n_correct: int
    n_errors: int
    accuracy: float
    by_noise_bucket: dict[str, float] = field(default_factory=dict)
    avg_latency_s: float = 0.0
    records: list[E2ERecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "language": self.language,
            "model": self.model,
            "judge_model": self.judge_model,
            "n_total": self.n_total,
            "n_correct": self.n_correct,
            "n_errors": self.n_errors,
            "accuracy": round(self.accuracy, 4),
            "by_noise_bucket": {k: round(v, 4) for k, v in self.by_noise_bucket.items()},
            "avg_latency_s": round(self.avg_latency_s, 2),
        }


def _parse_score(text: str | None) -> float:
    from agentnexus.rag.evaluator import _parse_score as _eval_parse

    return _eval_parse(text)


def judge_noise_answer(
    query: RGBQuery,
    generation: str,
    judge,
    limiter: RateLimiter,
) -> tuple[bool, float, str]:
    """Score one noise-task generation against its ground truth.

    Returns (correct, score, error). Reused by the e2e loop and by
    ``rejudge_report`` so a stored generation can be re-judged later —
    e.g. cross-checking judges off-peak without re-running generation.
    """
    if not (generation or "").strip():
        return False, 0.0, "empty generation"
    prompt = EVAL_CORRECTNESS_PROMPT.format(
        question=query.query,
        ground_truth=query.ground_truth_text(),
        answer=generation,
    )
    raw = call_with_retry(
        lambda: judge.think([{"role": "user", "content": prompt}], silent=True, thinking=False),
        limiter,
    )
    if not (raw or "").strip():
        return False, 0.0, "empty judge response after retries"
    score = _parse_score(raw)
    return score >= 0.5, score, ""


def _make_llm(model_selector: str):
    from agentnexus.core.config import get_settings
    from agentnexus.core.llm import AgentLLM

    settings = get_settings()
    if model_selector:
        found = settings.find_model(model_selector)
        if found is None:
            raise ValueError(f"Unknown model selector '{model_selector}' (expected 'provider/model')")
        provider, entry = found
        return AgentLLM(
            model=entry.model_id,
            apiKey=provider.api_key.get_secret_value(),
            baseUrl=provider.base_url,
            timeout=provider.timeout,
        )
    # fall back to the active task model
    profile = settings.get_active_llm_profile()
    model_id, base_url, api_key, timeout = profile
    return AgentLLM(model=model_id, apiKey=api_key.get_secret_value(), baseUrl=base_url, timeout=timeout)


def _process_rgb_query(
    query: RGBQuery,
    *,
    generator,
    judge,
    limiter: RateLimiter,
    passage_num: int,
    top_k: int,
    retrieval_mode: str = DENSE,
    reinforce_refusal: bool = False,
) -> E2ERecord:
    """Retrieve -> generate -> judge one query. Runs in a worker thread."""
    t0 = time.perf_counter()
    error = ""
    generation = ""
    judge_score: float | None = None
    correct: bool | None = None  # None until judged (rules, LLM, or later rejudge)
    pos_hits: int | None = None
    pos_total: int | None = None
    try:
        docs = query.build_corpus(passage_num=passage_num)
        labels = list(getattr(query, "sampled_labels", []))
        pos_total = sum(labels)
        data = BenchmarkData(
            suite="rgb",
            dataset=f"q{query.query_id}",
            docs=[BenchmarkDoc(doc_id=str(i), title="", text=doc) for i, doc in enumerate(docs)],
            queries=[BenchmarkQuery(query_id=query.query_id, text=query.query)],
            qrels={},
        )
        index = BenchmarkIndex(namespace=f"rgb-q-{query.query_id}")
        index.build(data)
        ranked = index.retrieve_all([query.query], mode=retrieval_mode, depth=min(top_k, len(docs)))
        top_ids = [doc_id for doc_id in ranked[0] if int(doc_id) < len(docs)]
        contexts = [docs[int(doc_id)] for doc_id in top_ids]
        if labels:
            pos_hits = sum(1 for doc_id in top_ids if labels[int(doc_id)])
        index.clear()

        system, user = rgb_generation_prompt(query, contexts, reinforce_refusal=reinforce_refusal)
        generation = call_with_retry(
            lambda: generator.think(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                silent=True,
            ),
            limiter,
        )
        if not (generation or "").strip():
            error = "empty generation after retries"
        elif query.expects_rejection():
            correct = is_rejection_output(generation, query.language)
        elif judge is not None:
            correct, judge_score, judge_error = judge_noise_answer(query, generation, judge, limiter)
            if judge_error:
                error = judge_error
        # else generate-only: correct/judge_score stay None for rejudge
    except Exception as exc:  # per-query isolation: one bad query must not kill the run
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("RGB query %s failed: %s", query.query_id, error)

    return E2ERecord(
        query_id=query.query_id,
        task=query.task,
        language=query.language,
        noise_rate=query.noise_rate,
        query=query.query,
        generation=generation,
        correct=correct,
        judge_score=judge_score,
        latency_s=time.perf_counter() - t0,
        error=error,
        retrieval_pos_hits=pos_hits,
        retrieval_pos_total=pos_total,
    )


def run_rgb_e2e(
    queries: list[RGBQuery],
    model_selector: str = "",
    passage_num: int = 5,
    top_k: int = 5,
    limit: int = 0,
    rpm: float = 18.0,
    judge_enabled: bool = True,
    retrieval_mode: str = DENSE,
    reinforce_refusal: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> E2ESummary:
    """Evaluate RGB queries end to end. Returns per-query records + accuracy.

    With ``judge_enabled=False`` (generate-only mode) noise-task records are
    generated and stored with correct/judge_score left None — judging is
    deferred to a later ``run-rgb -j`` pass, e.g. when a cheaper/free judge
    window opens. Rejection tasks stay rule-judged either way (no LLM cost).
    ``retrieval_mode`` selects the production stack: ``dense`` (leaderboard
    style) or ``hybrid`` (RRF over dense+BM25, this project's own retriever).
    """
    if not queries:
        raise ValueError("no queries to evaluate")
    if retrieval_mode not in RETRIEVAL_MODES:
        raise ValueError(f"Unknown retrieval mode '{retrieval_mode}'. Choose from {RETRIEVAL_MODES}")
    language, task = queries[0].language, queries[0].task
    if limit and limit > 0:
        queries = queries[:limit]

    generator = _make_llm(model_selector)
    judge = None
    judge_name = ""
    if judge_enabled:
        from agentnexus.core.judge_llm import get_judge_llm

        judge = get_judge_llm()
        judge_name = f"{getattr(judge, 'model', 'judge')}"
    limiter = RateLimiter(rpm)

    # One throwaway ChromaDB for the whole run; per-query collections inside it.
    import shutil
    import tempfile
    from pathlib import Path

    from agentnexus.core.config import get_settings
    from agentnexus.storage.chroma import reset_storage_client

    settings = get_settings()
    original_persist_dir = settings.chroma_persist_dir
    tmp_dir = Path(tempfile.mkdtemp(prefix="agentnexus-rgb-"))
    settings.chroma_persist_dir = str(tmp_dir)
    reset_storage_client()

    records: list[E2ERecord] = []
    started = time.perf_counter()

    def _process_one(query: RGBQuery) -> E2ERecord:
        return _process_rgb_query(
            query, generator=generator, judge=judge, limiter=limiter,
            passage_num=passage_num, top_k=top_k, retrieval_mode=retrieval_mode,
            reinforce_refusal=reinforce_refusal,
        )

    # Concurrent workers hide high per-call latency (free-tier APIs often run
    # 10-20s/call at peak); the shared RateLimiter still caps the aggregate
    # at rpm, so workers only fill the pipe, never exceed the quota.
    workers = max(1, min(6, round(rpm / 3))) if rpm else 1
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for position, record in enumerate(pool.map(_process_one, queries), 1):
                records.append(record)
                if progress:
                    progress(position, len(queries))
    finally:
        settings.chroma_persist_dir = original_persist_dir
        reset_storage_client()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    n_total = len(records)
    n_errors = sum(1 for r in records if r.error)
    n_correct = sum(1 for r in records if r.correct and not r.error)

    by_bucket: dict[str, list[bool]] = {}
    for record in records:
        if record.error or record.task != "noise" or record.correct is None:
            continue
        for low, high in NOISE_BUCKETS:
            if low <= record.noise_rate < high:
                by_bucket.setdefault(f"noise[{low:.1f}-{high:.1f})", []).append(record.correct)
                break

    return E2ESummary(
        task=task,
        language=language,
        model=getattr(generator, "model", model_selector or "active"),
        judge_model=judge_name,
        n_total=n_total,
        n_correct=n_correct,
        n_errors=n_errors,
        accuracy=n_correct / n_total if n_total else 0.0,
        by_noise_bucket={
            bucket: (sum(flags) / len(flags)) for bucket, flags in by_bucket.items() if flags
        },
        avg_latency_s=(time.perf_counter() - started) / max(n_total, 1),
        records=records,
    )


MULTIHOP_REFUSAL_PHRASES = (
    "i can not answer",
    "cannot answer",
    "insufficient information",
    "do not contain",
)


def multihop_generation_prompt(query: str, contexts: list[str]) -> tuple[str, str]:
    """Simple RAG prompt; null queries are expected to trigger the refusal line."""
    joined = "\n".join(f"Document {i + 1}: {doc}" for i, doc in enumerate(contexts))
    system = (
        "You are an accurate assistant. Answer the question based only on the given documents. "
        "If the documents do not contain the answer, reply exactly: "
        "'I can not answer the question because of the insufficient information in documents.'"
    )
    return system, f"Document:\n{joined} \n\nQuestion:\n{query}"


@dataclass
class MultiHopRecord:
    query_id: str
    query: str
    answer: str
    is_null: bool
    generation: str
    correct: bool | None
    judge_score: float | None
    latency_s: float
    error: str = ""


@dataclass
class MultiHopSummary:
    n_total: int
    n_null: int
    n_correct_pos: int
    n_pos: int
    n_correct_null: int
    accuracy_pos: float
    accuracy_null: float
    accuracy_all: float
    n_errors: int
    model: str = ""
    judge_model: str = ""
    records: list[MultiHopRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "n_total": self.n_total,
            "n_null": self.n_null,
            "n_pos": self.n_pos,
            "n_errors": self.n_errors,
            "accuracy_pos": round(self.accuracy_pos, 4),
            "accuracy_null": round(self.accuracy_null, 4),
            "accuracy_all": round(self.accuracy_all, 4),
            "model": self.model,
            "judge_model": self.judge_model,
        }


def run_multihop_e2e(
    data: BenchmarkData,
    answers: dict[str, str],
    is_null: dict[str, bool],
    model_selector: str = "",
    top_k: int = 5,
    limit: int = 0,
    rpm: float = 18.0,
    judge_enabled: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> MultiHopSummary:
    """MultiHop-RAG end-to-end: one shared index, retrieve -> generate -> judge.

    Positive queries are LLM-judged against the gold answer (>=0.5 = correct);
    null queries are rule-judged on the refusal phrase (no judge LLM cost).
    """
    import shutil
    import tempfile

    from agentnexus.core.config import get_settings
    from agentnexus.core.judge_llm import get_judge_llm
    from agentnexus.storage.chroma import reset_storage_client

    generator = _make_llm(model_selector)
    judge = get_judge_llm() if judge_enabled else None
    judge_name = f"{getattr(judge, 'model', '')}" if judge else ""
    limiter = RateLimiter(rpm)

    settings = get_settings()
    original_persist_dir = settings.chroma_persist_dir
    tmp_dir = Path(tempfile.mkdtemp(prefix="agentnexus-multihop-e2e-"))
    settings.chroma_persist_dir = str(tmp_dir)

    queries = data.queries[:limit] if limit else data.queries
    records: list[MultiHopRecord] = []
    started = time.perf_counter()
    try:
        reset_storage_client()
        index = BenchmarkIndex(namespace="multihop-e2e")
        index.build(data)
        workers = max(1, min(6, round(rpm / 3))) if rpm else 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            for query in queries:
                futures.append(pool.submit(
                    _process_multihop_query, query, index, generator, judge, limiter,
                    top_k, answers.get(query.query_id, ""), is_null.get(query.query_id, False),
                ))
            for position, fut in enumerate(futures, 1):
                records.append(fut.result())
                if progress:
                    progress(position, len(futures))
    finally:
        settings.chroma_persist_dir = original_persist_dir
        reset_storage_client()
        shutil.rmtree(tmp_dir, ignore_errors=True)

    n_pos = [r for r in records if not r.is_null]
    n_null = [r for r in records if r.is_null]
    c_pos = sum(1 for r in n_pos if r.correct and not r.error)
    c_null = sum(1 for r in n_null if r.correct and not r.error)
    n_errors = sum(1 for r in records if r.error)
    return MultiHopSummary(
        n_total=len(records),
        n_null=len(n_null),
        n_correct_pos=c_pos,
        n_pos=len(n_pos),
        n_correct_null=c_null,
        accuracy_pos=c_pos / max(len(n_pos), 1),
        accuracy_null=c_null / max(len(n_null), 1),
        accuracy_all=(c_pos + c_null) / max(len(records) - n_errors, 1),
        n_errors=n_errors,
        model=getattr(generator, "model", model_selector or "active"),
        judge_model=judge_name,
        records=records,
    )


def _process_multihop_query(query, index, generator, judge, limiter, top_k, gold_answer, is_null) -> MultiHopRecord:
    t0 = time.perf_counter()
    error = ""
    generation = ""
    judge_score: float | None = None
    correct: bool | None = None
    try:
        ranked = index.retrieve_all([query.text], mode="dense", depth=top_k)[0]
        contexts = index.contexts_for(ranked[:top_k])
        system, user = multihop_generation_prompt(query.text, contexts)
        generation = call_with_retry(
            lambda: generator.think(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                silent=True,
            ),
            limiter,
        )
        if not (generation or "").strip():
            error = "empty generation after retries"
        elif is_null:
            lowered = generation.strip().lower()
            correct = any(p in lowered for p in MULTIHOP_REFUSAL_PHRASES)
        elif judge is not None:
            fake_query = RGBQuery(
                query_id=query.query_id, query=query.text,
                answer_groups=[[gold_answer]] if gold_answer else [],
                positive=[], negative=[], task="noise", language="en",
            )
            correct, judge_score, judge_error = judge_noise_answer(fake_query, generation, judge, limiter)
            if judge_error:
                error = judge_error
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("MultiHop query %s failed: %s", query.query_id, error)
    return MultiHopRecord(
        query_id=query.query_id, query=query.text, answer=gold_answer, is_null=is_null,
        generation=generation, correct=correct, judge_score=judge_score,
        latency_s=time.perf_counter() - t0, error=error,
    )


def rejudge_report(
    report_path,
    rpm: float = 12.0,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Re-judge stored generations from a previous run-rgb report.

    Decouples the two LLM roles: generation can run when the free tier is
    idle, judging can happen later (or with a different judge) against the
    exact same answers. Only noise-task records are LLM-judged — rejection
    records use keyword rules and are carried over unchanged.

    Returns a dict with the new scores, accuracy, and per-record agreement
    with the original judge's verdicts.
    """
    import json as _json
    from pathlib import Path as _Path

    from agentnexus.core.judge_llm import get_judge_llm

    from .rgb_loader import load_rgb_queries

    path = _Path(report_path)
    payload = _json.loads(path.read_text(encoding="utf-8"))
    if payload.get("kind") != "rgb-e2e":
        raise ValueError(f"{path} is not an rgb-e2e report (kind={payload.get('kind')!r})")

    summary_meta = payload["summary"]
    language, task = summary_meta["language"], summary_meta["task"]
    original_judge = summary_meta.get("judge_model", "unknown")
    records = payload["records"]

    if task == "rejection":
        raise ValueError("rejection-task reports are rule-judged; nothing to re-judge with an LLM")

    queries = {q.query_id: q for q in load_rgb_queries(language, task, offline=True)}
    judge = get_judge_llm()
    limiter = RateLimiter(rpm)
    judge_name = f"{getattr(judge, 'model', 'judge')}"

    def _rejudge_one(record: dict) -> dict:
        query = queries.get(str(record["query_id"]))
        if query is None:
            record["rejudge_error"] = "query not found in dataset"
            return record
        correct, score, error = judge_noise_answer(query, record.get("generation", ""), judge, limiter)
        record["judge_score_new"] = score
        record["correct_new"] = correct
        record["rejudge_error"] = error
        return record

    # Concurrent like the generation loop: shared limiter caps the aggregate,
    # workers hide per-call latency (thinking-mode judges run seconds each).
    workers = max(1, min(6, round(rpm / 10))) if rpm else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for position, record in enumerate(pool.map(_rejudge_one, records), 1):
            if progress:
                progress(position, len(records))

    comparable = 0
    agree = 0
    n_correct = 0
    n_errors = 0
    by_bucket: dict[str, list[bool]] = {}
    for record in records:
        if record.get("rejudge_error"):
            n_errors += 1
        else:
            n_correct += bool(record.get("correct_new"))
            # generate-only reports store correct=None — nothing to compare against
            if record.get("correct") is not None:
                comparable += 1
                agree += int(bool(record["correct"]) == bool(record["correct_new"]))
            noise_rate = record.get("noise_rate")
            if isinstance(noise_rate, (int, float)):
                for low, high in NOISE_BUCKETS:
                    if low <= noise_rate < high:
                        by_bucket.setdefault(f"noise[{low:.1f}-{high:.1f})", []).append(bool(record.get("correct_new")))
                        break

    n_total = len(records)
    return {
        "kind": "rgb-e2e-rejudge",
        "source_report": str(path),
        "language": language,
        "task": task,
        "original_judge": original_judge,
        "new_judge": judge_name,
        "n_total": n_total,
        "n_correct_new": n_correct,
        "n_errors": n_errors,
        "accuracy_new": n_correct / n_total if n_total else 0.0,
        "by_noise_bucket_new": {
            bucket: sum(flags) / len(flags) for bucket, flags in by_bucket.items() if flags
        },
        "agreement": (agree / comparable) if comparable else None,
        "records": records,
    }
