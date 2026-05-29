import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from agentnexus.core.judge_llm import get_judge_llm
from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import load_prompt
from agentnexus.rag.ingestion import ChunkStrategy, chunk_text, ingest_document
from agentnexus.rag.retriever import HybridRetriever, build_knowledge_base, result_display_text
from agentnexus.storage.chroma import delete_collection, search


@dataclass
class EvalSample:
    question: str
    ground_truth: str
    reference_contexts: list[str] = field(default_factory=list)


@dataclass
class EvalRun:
    def check_passed(self, thresholds: dict[str, float] | None = None) -> bool:
        t = thresholds or DEFAULT_RAG_THRESHOLDS
        return (
            self.faithfulness >= t.get("faithfulness", 0.0)
            and self.answer_relevancy >= t.get("answer_relevancy", 0.0)
            and self.answer_correctness >= t.get("answer_correctness", 0.0)
            and self.hit_rate >= t.get("hit_rate", 0.0)
            and self.mrr >= t.get("mrr", 0.0)
            and self.rejection_rate >= t.get("rejection_rate", 0.0)
        )
    label: str
    strategy: ChunkStrategy
    chunk_size: int
    use_hybrid: bool
    faithfulness: float = 0.0
    answer_relevancy: float = 0.0       # 新: 回答是否切题（Judge LLM，不依赖 ground_truth）
    answer_correctness: float = 0.0     # 原 answer_relevancy (与 ground_truth 比较)
    context_precision: float = 0.0
    context_recall: float = 0.0
    context_relevancy: float = 0.0
    hit_rate: float = 0.0              # 检索命中率@k
    mrr: float = 0.0                   # 平均倒数排名@k
    avg_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    faithfulness_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    answer_relevancy_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    answer_correctness_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    context_precision_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    context_recall_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    context_relevancy_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    hit_rate_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    mrr_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    rejection_rate: float = 0.0


EVAL_GENERATE_PROMPT = load_prompt("eval_generate")
EVAL_FAITHFULNESS_PROMPT = load_prompt("eval_faithfulness")
EVAL_CORRECTNESS_PROMPT = load_prompt("eval_correctness")
EVAL_ANSWER_RELEVANCY_PROMPT = load_prompt("eval_answer_relevancy")
EVAL_PRECISION_PROMPT = load_prompt("eval_precision")
EVAL_RECALL_PROMPT = load_prompt("eval_recall")

# Keywords for detecting refusal in negative samples (Change 4)
_REFUSAL_KEYWORDS = [
    "无法", "没有找到", "不能", "不确定", "不包含",
    "i don't know", "not found", "cannot", "unable",
    "无可奉告", "无法回答", "暂无", "无相关信息",
]

# ── Default thresholds for CI gating ──
DEFAULT_RAG_THRESHOLDS: dict[str, float] = {
    "faithfulness": 0.80,
    "answer_relevancy": 0.75,
    "answer_correctness": 0.70,
    "context_precision": 0.70,
    "context_recall": 0.70,
    "context_relevancy": 0.60,
    "hit_rate": 0.85,
    "mrr": 0.70,
    "rejection_rate": 0.75,
}


class RAGEvaluator:
    def __init__(self, documents: list[str], samples: list[EvalSample]):
        self._docs = documents
        self._samples = samples
        self._llm = AgentLLM()
        self._judge_llm = get_judge_llm()

    def _think_timeout(self, llm, messages, timeout=0):
        """LLM call with timeout protection. timeout<=0 means no timeout."""
        if timeout <= 0:
            return llm.think(messages)
        result = [None]
        error = [None]
        def _target():
            try:
                result[0] = llm.think(messages)
            except Exception as e:
                error[0] = e
        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            return None
        if error[0] is not None:
            raise error[0]
        return result[0]

    def run_combination(
        self,
        strategy: ChunkStrategy,
        chunk_size: int,
        chunk_overlap: int,
        use_hybrid: bool,
        _token_budget: int | None = None,
        top_k: int = 10,
        max_workers: int = 1,
        verbose: bool = False,
        call_timeout: int = 0,
    ) -> EvalRun:
        label = f"{strategy.value}-{chunk_size}-{'hybrid' if use_hybrid else 'dense'}"
        run = EvalRun(label=label, strategy=strategy, chunk_size=chunk_size, use_hybrid=use_hybrid)

        def _log(msg: str):
            if verbose:
                import sys
                print(msg, file=sys.stderr, flush=True)

        t_total = time.perf_counter()

        _log(f"    [1/4] 分块中 (strategy={strategy.value}, size={chunk_size}, overlap={chunk_overlap})...")
        t0 = time.perf_counter()
        chunks = self._chunk_all(strategy, chunk_size, chunk_overlap)
        _log(f"    [1/4] 分块完成: {len(chunks)} 个 chunk ({time.perf_counter() - t0:.1f}s)")

        _log("    [2/4] 构建索引 (ChromaDB + BM25)...")
        t0 = time.perf_counter()
        delete_collection(namespace="eval")
        build_knowledge_base(chunks, load_reranker=False, namespace="eval")
        retriever = HybridRetriever(namespace="eval")
        retriever.rebuild_from_catalog()
        _log(f"    [2/4] 索引完成 ({time.perf_counter() - t0:.1f}s)")

        positive_samples = [
            s for s in self._samples
            if s.ground_truth and s.reference_contexts
        ]
        negative_samples = [
            s for s in self._samples
            if not s.ground_truth or not s.reference_contexts
        ]

        effective_workers = max(1, max_workers)
        token_budget = _token_budget

        def _token_max(sample):
            if token_budget is not None and token_budget > 0:
                return token_budget
            return max(len(sample.question) * 5, 100)

        def _eval_positive(sample):
            max_tokens = _token_max(sample)
            t0 = time.perf_counter()
            full_ranked, truncated = self._retrieve(
                sample.question, retriever, use_hybrid,
                max_tokens=max_tokens, top_k=top_k,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            if not full_ranked and not truncated:
                return None
            ret_precision, ret_hit, ret_mrr = self._score_retrieval_ranked(
                sample, full_ranked, top_k=top_k, _timeout=call_timeout,
            )
            recall = 0.0
            context_relevancy = 0.0
            if truncated:
                recall = self._score_recall(sample, truncated, _timeout=call_timeout)
                context_relevancy = self._score_context_relevancy(sample.question, truncated)
            answer = self._generate_answer(sample.question, truncated, _timeout=call_timeout)
            faithfulness = self._score_faithfulness(answer, truncated, _timeout=call_timeout)
            answer_relevancy = self._score_answer_relevancy(sample.question, answer, _timeout=call_timeout)
            correctness = self._score_correctness(sample.question, answer, sample.ground_truth, _timeout=call_timeout)
            return {
                "faithfulness": faithfulness,
                "correctness": correctness,
                "answer_relevancy": answer_relevancy,
                "precision": ret_precision,
                "recall": recall,
                "context_relevancy": context_relevancy,
                "hit_rate": ret_hit,
                "mrr": ret_mrr,
                "latency_ms": latency_ms,
            }

        def _eval_negative(sample):
            max_tokens = _token_max(sample)
            _, retrieved = self._retrieve(
                sample.question, retriever, use_hybrid,
                max_tokens=max_tokens, top_k=top_k,
            )
            if not retrieved:
                return True
            answer = self._generate_answer(sample.question, retrieved, _timeout=call_timeout)
            return _is_refusal(answer)

        faithfulness_scores = []
        correctness_scores = []
        answer_relevancy_scores = []
        precision_scores = []
        recall_scores = []
        context_relevancy_scores = []
        hit_rates = []
        mrrs = []
        latencies = []

        _log(f"    [3/4] 评估正样本 ({len(positive_samples)} 个, workers={effective_workers})...")
        t_eval = time.perf_counter()

        if effective_workers <= 1:
            for idx, sample in enumerate(positive_samples, 1):
                _log(f"      正样本 {idx}/{len(positive_samples)}: {sample.question[:40]}...")
                result = _eval_positive(sample)
                if result is None:
                    _log(f"      正样本 {idx}: 跳过 (无检索结果)")
                    continue
                _log(f"      正样本 {idx}: faith={result['faithfulness']:.2f} "
                     f"hit={result['hit_rate']:.0f} lat={result['latency_ms']:.0f}ms")
                faithfulness_scores.append(result["faithfulness"])
                correctness_scores.append(result["correctness"])
                answer_relevancy_scores.append(result["answer_relevancy"])
                precision_scores.append(result["precision"])
                hit_rates.append(result["hit_rate"])
                mrrs.append(result["mrr"])
                recall_scores.append(result["recall"])
                context_relevancy_scores.append(result["context_relevancy"])
                latencies.append(result["latency_ms"])
        else:
            done_count = 0
            with ThreadPoolExecutor(max_workers=effective_workers) as pool:
                futures = {pool.submit(_eval_positive, s): s for s in positive_samples}
                for future in as_completed(futures):
                    done_count += 1
                    result = future.result()
                    if result is None:
                        _log(f"      正样本 {done_count}/{len(positive_samples)}: 跳过")
                        continue
                    _log(f"      正样本 {done_count}/{len(positive_samples)}: "
                         f"faith={result['faithfulness']:.2f} hit={result['hit_rate']:.0f} "
                         f"lat={result['latency_ms']:.0f}ms")
                    faithfulness_scores.append(result["faithfulness"])
                    correctness_scores.append(result["correctness"])
                    answer_relevancy_scores.append(result["answer_relevancy"])
                    precision_scores.append(result["precision"])
                    hit_rates.append(result["hit_rate"])
                    mrrs.append(result["mrr"])
                    recall_scores.append(result["recall"])
                    context_relevancy_scores.append(result["context_relevancy"])
                    latencies.append(result["latency_ms"])

        _log(f"    [3/4] 正样本完成 ({time.perf_counter() - t_eval:.1f}s)")

        _log(f"    [4/4] 评估负样本 ({len(negative_samples)} 个)...")
        t_neg = time.perf_counter()
        correct_refusals = 0
        if effective_workers <= 1:
            for idx, sample in enumerate(negative_samples, 1):
                _log(f"      负样本 {idx}/{len(negative_samples)}: {sample.question[:40]}...")
                if _eval_negative(sample):
                    correct_refusals += 1
                    _log(f"      负样本 {idx}: 正确拒绝")
                else:
                    _log(f"      负样本 {idx}: 未拒绝")
        else:
            with ThreadPoolExecutor(max_workers=effective_workers) as pool:
                futures_list = [pool.submit(_eval_negative, s) for s in negative_samples]
                for idx, future in enumerate(as_completed(futures_list), 1):
                    if future.result():
                        correct_refusals += 1
                        _log(f"      负样本 {idx}/{len(negative_samples)}: 正确拒绝")
                    else:
                        _log(f"      负样本 {idx}/{len(negative_samples)}: 未拒绝")

        _log(f"    [4/4] 负样本完成 ({time.perf_counter() - t_neg:.1f}s)")

        # Compute means
        run.faithfulness = _safe_mean(faithfulness_scores)
        run.answer_relevancy = _safe_mean(answer_relevancy_scores)
        run.answer_correctness = _safe_mean(correctness_scores)
        run.context_precision = _safe_mean(precision_scores)
        run.context_recall = _safe_mean(recall_scores)
        run.context_relevancy = _safe_mean(context_relevancy_scores)
        run.hit_rate = _safe_mean(hit_rates)
        run.mrr = _safe_mean(mrrs)
        run.avg_latency_ms = _safe_mean(latencies)
        sorted_lat = sorted(latencies)
        if sorted_lat:
            run.p50_latency_ms = _percentile(sorted_lat, 50)
            run.p95_latency_ms = _percentile(sorted_lat, 95)
            run.p99_latency_ms = _percentile(sorted_lat, 99)

        run.faithfulness_ci = _bootstrap_ci(faithfulness_scores)
        run.answer_relevancy_ci = _bootstrap_ci(answer_relevancy_scores)
        run.answer_correctness_ci = _bootstrap_ci(correctness_scores)
        run.context_precision_ci = _bootstrap_ci(precision_scores)
        run.context_recall_ci = _bootstrap_ci(recall_scores)
        run.context_relevancy_ci = _bootstrap_ci(context_relevancy_scores)
        run.hit_rate_ci = _bootstrap_ci(hit_rates)
        run.mrr_ci = _bootstrap_ci(mrrs)

        if negative_samples:
            run.rejection_rate = correct_refusals / len(negative_samples)

        total_elapsed = time.perf_counter() - t_total
        _log(f"    总耗时: {total_elapsed:.1f}s (faithfulness={run.faithfulness:.3f})")

        return run
    def _chunk_all(self, strategy: ChunkStrategy, size: int, overlap: int) -> list[str]:
        if self._docs and all(self._looks_like_file_path(item) for item in self._docs):
            chunks: list[str] = []
            for path in self._docs:
                artifacts = ingest_document(
                    path,
                    strategy=strategy,
                    chunk_size=size,
                    chunk_overlap=overlap,
                )
                chunks.extend(artifacts.legacy_chunks())
            return chunks or self._docs

        full_text = "\n\n".join(self._docs)
        chunks = chunk_text(full_text, strategy=strategy, chunk_size=size, chunk_overlap=overlap)
        if not chunks:
            return self._docs
        return chunks

    @staticmethod
    def _looks_like_file_path(value: str) -> bool:
        candidate = Path(value)
        return candidate.suffix.lower() in {".pdf", ".md", ".txt"} and candidate.exists()

    def _retrieve(
        self, query, retriever, use_hybrid, max_tokens: int,
        min_score: float = 0.3, top_k: int = 10,
    ) -> tuple[list[str], list[str]]:
        """Retrieve chunks.

        Returns (full_ranked, truncated):
          - full_ranked: top_k candidate chunks before token budget fitting
            (used for hit_rate / MRR computation)
          - truncated: chunks after token budget fitting
            (used for generation + precision / recall)
        """
        if not use_hybrid:
            candidates = [r["text"] for r in search(query, limit=top_k, namespace="eval")]
            return candidates, _fit_token_budget(candidates, max_tokens)
        dense_results = search(query, limit=top_k * 2, namespace="eval")
        dense = [(r["id"], r["score"]) for r in dense_results]
        results = retriever.search(query, dense, top_k=top_k, min_score=min_score)
        full_ranked = [r.text for r in results]
        expanded = retriever.expand_contexts(results) if hasattr(retriever, "expand_contexts") else results
        expanded_contexts = [result_display_text(r) for r in expanded]
        return full_ranked, _fit_token_budget(expanded_contexts, max_tokens)

    def _generate_answer(self, question: str, contexts: list[str], _timeout: int = 0) -> str:
        ctx = "\n---\n".join(contexts)
        prompt = EVAL_GENERATE_PROMPT.format(context=ctx, question=question)
        return self._think_timeout(self._llm, [{"role": "user", "content": prompt}], _timeout) or ""

    def _score_faithfulness(self, answer: str, contexts: list[str], _timeout: int = 0) -> float:
        ctx = "\n".join(contexts)
        prompt = EVAL_FAITHFULNESS_PROMPT.format(context=ctx, answer=answer)
        return _parse_score(self._think_timeout(self._judge_llm, [{"role": "user", "content": prompt}], _timeout))

    def _score_correctness(self, question: str, answer: str, ground_truth: str, _timeout: int = 0) -> float:
        prompt = EVAL_CORRECTNESS_PROMPT.format(question=question, ground_truth=ground_truth, answer=answer)
        return _parse_score(self._think_timeout(self._judge_llm, [{"role": "user", "content": prompt}], _timeout))

    def _score_answer_relevancy(self, question: str, answer: str, _timeout: int = 0) -> float:
        """Judge whether the answer addresses the question (no ground truth needed)."""
        if not answer:
            return 0.0
        prompt = EVAL_ANSWER_RELEVANCY_PROMPT.format(question=question, answer=answer)
        return _parse_score(self._think_timeout(self._judge_llm, [{"role": "user", "content": prompt}], _timeout))

    def _score_retrieval_ranked(
        self, sample: EvalSample, full_ranked: list[str], top_k: int = 10, _timeout: int = 0,
    ) -> tuple[float, float, float]:
        """Compute context_precision, hit_rate@k, and MRR@k in one LLM pass.

        For each chunk in full_ranked[:k], asks Judge LLM whether it is relevant.
        Returns (avg_precision, hit_rate, mrr).
        """
        if not full_ranked:
            return (0.0, 0.0, 0.0)
        candidates = full_ranked[:top_k] if top_k > 0 else full_ranked
        relevant_flags: list[bool] = []
        for chunk in candidates:
            prompt = EVAL_PRECISION_PROMPT.format(chunk=chunk, question=sample.question)
            try:
                result = self._think_timeout(self._judge_llm, [{"role": "user", "content": prompt}], _timeout)
            except Exception:
                result = None
            score = _parse_score(result)
            relevant_flags.append(score >= 0.5)

        total = len(candidates)
        n_relevant = sum(relevant_flags)
        precision = n_relevant / total if total > 0 else 0.0

        hit = 1.0 if n_relevant > 0 else 0.0

        first_rank = next((i + 1 for i, flag in enumerate(relevant_flags) if flag), None)
        mrr = 1.0 / first_rank if first_rank else 0.0

        return (precision, hit, mrr)

    # ── Change 1: LLM-based precision & recall ──

    def _score_precision(self, sample: EvalSample, retrieved: list[str]) -> float:
        """LLM-based context precision: what fraction of retrieved chunks are relevant.

        For each retrieved chunk, asks Judge whether it helps answer the question.
        """
        if not retrieved:
            return 0.0
        scores = []
        for chunk in retrieved:
            prompt = EVAL_PRECISION_PROMPT.format(chunk=chunk, question=sample.question)
            try:
                result = self._judge_llm.think([{"role": "user", "content": prompt}])
            except Exception:
                result = None
            scores.append(_parse_score(result))
        return sum(scores) / len(scores) if scores else 0.0

    def _score_precision_keyword(self, sample: EvalSample, retrieved: list[str]) -> float:
        """Legacy keyword-matching precision (kept for reference)."""
        if not sample.reference_contexts or not retrieved:
            return 0.0
        relevant = set(sample.reference_contexts)
        hits = sum(1 for r in retrieved if any(ref in r for ref in relevant))
        return hits / len(retrieved) if retrieved else 0.0

    def _score_recall(self, sample: EvalSample, retrieved: list[str], _timeout: int = 0) -> float:
        """LLM-based context recall: what fraction of reference info is covered.

        For each reference passage, asks Judge whether its information is
        covered by the retrieved chunks.
        """
        if not sample.reference_contexts or not retrieved:
            return 0.0
        scores = []
        retrieved_text = "\n---\n".join(retrieved)
        for reference in sample.reference_contexts:
            prompt = EVAL_RECALL_PROMPT.format(reference=reference, retrieved=retrieved_text)
            try:
                result = self._think_timeout(self._judge_llm, [{"role": "user", "content": prompt}], _timeout)
            except Exception:
                result = None
            scores.append(_parse_score(result))
        return sum(scores) / len(scores) if scores else 0.0

    def _score_recall_keyword(self, sample: EvalSample, retrieved: list[str]) -> float:
        """Legacy keyword-matching recall (kept for reference)."""
        if not sample.reference_contexts or not retrieved:
            return 0.0
        relevant = set(sample.reference_contexts)
        hits = sum(1 for ref in relevant if any(ref in r for r in retrieved))
        return hits / len(relevant) if relevant else 0.0

    def _score_context_relevancy(self, query: str, retrieved: list[str]) -> float:
        """Score what fraction of retrieved chunks are relevant to the query.

        Uses keyword overlap between query and each chunk. Threshold: >0.85 target.
        """
        if not retrieved:
            return 0.0
        query_terms = set(query.lower().split())
        if not query_terms:
            return 0.0
        scores = []
        for chunk in retrieved:
            chunk_lower = chunk.lower()
            matched = sum(1 for t in query_terms if t in chunk_lower)
            scores.append(matched / len(query_terms))
        return sum(scores) / len(scores) if scores else 0.0


# ── Module-level helpers ──


def _parse_score(text: str | None) -> float:
    if not text:
        return 0.0
    import re
    m = re.search(r"(\d+\.?\d*)", text.strip())
    if m:
        return max(0.0, min(1.0, float(m.group(1))))
    return 0.0


def _percentile(sorted_values: list[float], pct: int) -> float:
    idx = max(0, int(len(sorted_values) * pct / 100) - 1)
    return round(sorted_values[idx], 1)


def _safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _bootstrap_ci(scores: list[float], n_resamples: int = 1000, ci: float = 0.95) -> tuple[float, float]:
    """Compute bootstrap confidence interval for a list of scores (Change 3).

    Resamples with replacement n_resamples times, computes mean of each resample,
    then returns (lower_percentile, upper_percentile) based on the CI level.

    If fewer than 3 scores, returns (mean, mean).
    """
    if len(scores) < 3:
        m = _safe_mean(scores)
        return (m, m)

    lower_pct = ((1.0 - ci) / 2.0) * 100
    upper_pct = (1.0 - (1.0 - ci) / 2.0) * 100

    n = len(scores)
    means = []
    rng = random.Random(42)  # deterministic seed for reproducibility
    for _ in range(n_resamples):
        sample = [scores[rng.randint(0, n - 1)] for _ in range(n)]
        means.append(sum(sample) / n)

    means.sort()
    lo_idx = max(0, int(len(means) * lower_pct / 100))
    hi_idx = min(len(means) - 1, int(len(means) * upper_pct / 100))
    return (round(means[lo_idx], 4), round(means[hi_idx], 4))


def _fit_token_budget(chunks: list[str], max_tokens: int) -> list[str]:
    """Accumulate chunks until approximate token count reaches max_tokens (Change 2).

    Token estimation for Chinese: len(chunk) // 2 (approx).
    """
    if not chunks:
        return []
    result = []
    used = 0
    for chunk in chunks:
        cost = len(chunk) // 2
        if used + cost > max_tokens and result:
            break
        result.append(chunk)
        used += cost
    return result


def _is_refusal(answer: str) -> bool:
    """Check if the answer indicates the LLM could not / refused to answer (Change 4)."""
    if not answer or len(answer.strip()) < 10:
        return True
    answer_lower = answer.lower()
    for kw in _REFUSAL_KEYWORDS:
        if kw.lower() in answer_lower:
            return True
    return False
