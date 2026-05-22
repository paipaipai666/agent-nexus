import random
import time
from dataclasses import dataclass, field

from agentnexus.core.judge_llm import get_judge_llm
from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import load_prompt
from agentnexus.rag.chroma_client import delete_collection, search
from agentnexus.rag.ingestion import ChunkStrategy, chunk_text
from agentnexus.rag.retriever import HybridRetriever, build_knowledge_base


@dataclass
class EvalSample:
    question: str
    ground_truth: str
    reference_contexts: list[str] = field(default_factory=list)


@dataclass
class EvalRun:
    label: str
    strategy: ChunkStrategy
    chunk_size: int
    use_hybrid: bool
    faithfulness: float = 0.0
    answer_relevancy: float = 0.0
    context_precision: float = 0.0
    context_recall: float = 0.0
    context_relevancy: float = 0.0
    avg_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    # Bootstrap confidence intervals (Change 3)
    faithfulness_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    answer_relevancy_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    context_precision_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    context_recall_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    context_relevancy_ci: tuple[float, float] = field(default_factory=lambda: (0.0, 0.0))
    # Negative sample rejection rate (Change 4)
    rejection_rate: float = 0.0


EVAL_GENERATE_PROMPT = load_prompt("eval_generate")
EVAL_FAITHFULNESS_PROMPT = load_prompt("eval_faithfulness")
EVAL_RELEVANCY_PROMPT = load_prompt("eval_relevancy")
EVAL_PRECISION_PROMPT = load_prompt("eval_precision")
EVAL_RECALL_PROMPT = load_prompt("eval_recall")

# Keywords for detecting refusal in negative samples (Change 4)
_REFUSAL_KEYWORDS = [
    "无法", "没有找到", "不能", "不确定", "不包含",
    "i don't know", "not found", "cannot", "unable",
    "无可奉告", "无法回答", "暂无", "无相关信息",
]


class RAGEvaluator:
    def __init__(self, documents: list[str], samples: list[EvalSample]):
        self._docs = documents
        self._samples = samples
        self._llm = AgentLLM()
        self._judge_llm = get_judge_llm()

    def run_combination(
        self,
        strategy: ChunkStrategy,
        chunk_size: int,
        chunk_overlap: int,
        use_hybrid: bool,
        _token_budget: int | None = None,
    ) -> EvalRun:
        label = f"{strategy.value}-{chunk_size}-{'hybrid' if use_hybrid else 'dense'}"
        run = EvalRun(label=label, strategy=strategy, chunk_size=chunk_size, use_hybrid=use_hybrid)

        chunks = self._chunk_all(strategy, chunk_size, chunk_overlap)

        delete_collection(namespace="eval")
        build_knowledge_base(chunks, load_reranker=False, namespace="eval")

        retriever = HybridRetriever(namespace="eval")
        retriever.rebuild_from_catalog()

        # ── Change 4: Split positive / negative samples ──
        positive_samples = [
            s for s in self._samples
            if s.ground_truth and s.reference_contexts
        ]
        negative_samples = [
            s for s in self._samples
            if not s.ground_truth or not s.reference_contexts
        ]

        faithfulness_scores = []
        relevancy_scores = []
        precision_scores = []
        recall_scores = []
        context_relevancy_scores = []
        latencies = []

        # ── Evaluate positive samples ──
        for sample in positive_samples:
            # Change 2: dynamic token budget
            if _token_budget is not None and _token_budget > 0:
                max_tokens = _token_budget
            else:
                max_tokens = max(len(sample.question) * 5, 100)

            t0 = time.perf_counter()
            retrieved = self._retrieve(sample.question, retriever, use_hybrid, max_tokens=max_tokens)
            latencies.append((time.perf_counter() - t0) * 1000)

            if not retrieved:
                continue

            answer = self._generate_answer(sample.question, retrieved)

            faithfulness_scores.append(self._score_faithfulness(answer, retrieved))
            relevancy_scores.append(self._score_relevancy(sample.question, answer, sample.ground_truth))
            precision_scores.append(self._score_precision(sample, retrieved))
            recall_scores.append(self._score_recall(sample, retrieved))
            context_relevancy_scores.append(self._score_context_relevancy(sample.question, retrieved))

        # ── Evaluate negative samples (rejection rate) ──
        correct_refusals = 0
        for sample in negative_samples:
            if _token_budget is not None and _token_budget > 0:
                max_tokens = _token_budget
            else:
                max_tokens = max(len(sample.question) * 5, 100)

            retrieved = self._retrieve(sample.question, retriever, use_hybrid, max_tokens=max_tokens)
            if not retrieved:
                # No chunks retrieved → treat as correct refusal (nothing to answer from)
                correct_refusals += 1
                continue

            answer = self._generate_answer(sample.question, retrieved)
            if _is_refusal(answer):
                correct_refusals += 1

        # ── Compute means ──
        run.faithfulness = _safe_mean(faithfulness_scores)
        run.answer_relevancy = _safe_mean(relevancy_scores)
        run.context_precision = _safe_mean(precision_scores)
        run.context_recall = _safe_mean(recall_scores)
        run.context_relevancy = _safe_mean(context_relevancy_scores)
        run.avg_latency_ms = _safe_mean(latencies)
        sorted_lat = sorted(latencies)
        if sorted_lat:
            run.p50_latency_ms = _percentile(sorted_lat, 50)
            run.p95_latency_ms = _percentile(sorted_lat, 95)
            run.p99_latency_ms = _percentile(sorted_lat, 99)

        # ── Change 3: Bootstrap confidence intervals ──
        run.faithfulness_ci = _bootstrap_ci(faithfulness_scores)
        run.answer_relevancy_ci = _bootstrap_ci(relevancy_scores)
        run.context_precision_ci = _bootstrap_ci(precision_scores)
        run.context_recall_ci = _bootstrap_ci(recall_scores)
        run.context_relevancy_ci = _bootstrap_ci(context_relevancy_scores)

        # ── Change 4: Rejection rate for negative samples ──
        if negative_samples:
            run.rejection_rate = correct_refusals / len(negative_samples)

        return run

    def _chunk_all(self, strategy: ChunkStrategy, size: int, overlap: int) -> list[str]:
        full_text = "\n\n".join(self._docs)
        chunks = chunk_text(full_text, strategy=strategy, chunk_size=size, chunk_overlap=overlap)
        if not chunks:
            return self._docs
        return chunks

    def _retrieve(self, query, retriever, use_hybrid, max_tokens: int, min_score: float = 0.3):
        """Retrieve chunks with dynamic token budget (Change 2).

        Fetches up to 10 candidates, then accumulates until token budget is reached.
        """
        if not use_hybrid:
            candidates = [r["text"] for r in search(query, limit=10)]
            return _fit_token_budget(candidates, max_tokens)
        dense_results = search(query, limit=20)
        dense = [(r["id"], r["score"]) for r in dense_results]
        results = retriever.search(query, dense, top_k=10, min_score=min_score)
        return _fit_token_budget([r.text for r in results], max_tokens)

    def _generate_answer(self, question: str, contexts: list[str]) -> str:
        ctx = "\n---\n".join(contexts)
        prompt = EVAL_GENERATE_PROMPT.format(context=ctx, question=question)
        return self._llm.think([{"role": "user", "content": prompt}]) or ""

    def _score_faithfulness(self, answer: str, contexts: list[str]) -> float:
        ctx = "\n".join(contexts)
        prompt = EVAL_FAITHFULNESS_PROMPT.format(context=ctx, answer=answer)
        return _parse_score(self._llm.think([{"role": "user", "content": prompt}]))

    def _score_relevancy(self, question: str, answer: str, ground_truth: str) -> float:
        prompt = EVAL_RELEVANCY_PROMPT.format(question=question, ground_truth=ground_truth, answer=answer)
        return _parse_score(self._llm.think([{"role": "user", "content": prompt}]))

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

    def _score_recall(self, sample: EvalSample, retrieved: list[str]) -> float:
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
                result = self._judge_llm.think([{"role": "user", "content": prompt}])
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
