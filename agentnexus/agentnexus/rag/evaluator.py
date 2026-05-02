import time
from dataclasses import dataclass, field

from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import load_prompt
from agentnexus.rag.ingestion import chunk_text, ChunkStrategy
from agentnexus.rag.chroma_client import (
    get_embedding_model, insert_documents, search, delete_collection,
)
from agentnexus.rag.retriever import HybridRetriever


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
    avg_latency_ms: float = 0.0


EVAL_GENERATE_PROMPT = load_prompt("eval_generate")
EVAL_FAITHFULNESS_PROMPT = load_prompt("eval_faithfulness")
EVAL_RELEVANCY_PROMPT = load_prompt("eval_relevancy")


class RAGEvaluator:
    def __init__(self, documents: list[str], samples: list[EvalSample]):
        self._docs = documents
        self._samples = samples
        self._llm = AgentLLM()

    def run_combination(
        self,
        strategy: ChunkStrategy,
        chunk_size: int,
        chunk_overlap: int,
        use_hybrid: bool,
    ) -> EvalRun:
        label = f"{strategy.value}-{chunk_size}-{'hybrid' if use_hybrid else 'dense'}"
        run = EvalRun(label=label, strategy=strategy, chunk_size=chunk_size, use_hybrid=use_hybrid)

        chunks = self._chunk_all(strategy, chunk_size, chunk_overlap)
        model = get_embedding_model()

        delete_collection()
        insert_documents(chunks)

        retriever = HybridRetriever()
        if use_hybrid:
            retriever.build_bm25(chunks)

        faithfulness_scores = []
        relevancy_scores = []
        precision_scores = []
        recall_scores = []
        latencies = []

        for sample in self._samples:
            t0 = time.perf_counter()
            retrieved = self._retrieve(sample.question, model, retriever, use_hybrid)
            latencies.append((time.perf_counter() - t0) * 1000)

            if not retrieved:
                continue

            answer = self._generate_answer(sample.question, retrieved)

            faithfulness_scores.append(self._score_faithfulness(answer, retrieved))
            relevancy_scores.append(self._score_relevancy(sample.question, answer, sample.ground_truth))
            precision_scores.append(self._score_precision(sample, retrieved))
            recall_scores.append(self._score_recall(sample, retrieved))

        run.faithfulness = _safe_mean(faithfulness_scores)
        run.answer_relevancy = _safe_mean(relevancy_scores)
        run.context_precision = _safe_mean(precision_scores)
        run.context_recall = _safe_mean(recall_scores)
        run.avg_latency_ms = _safe_mean(latencies)
        return run

    def _chunk_all(self, strategy: ChunkStrategy, size: int, overlap: int) -> list[str]:
        full_text = "\n\n".join(self._docs)
        chunks = chunk_text(full_text, strategy=strategy, chunk_size=size, chunk_overlap=overlap)
        if not chunks:
            return self._docs
        return chunks

    def _retrieve(self, query, model, retriever, use_hybrid, top_k: int = 3, min_score: float = 0.3):
        if not use_hybrid:
            return [r["text"] for r in search(query, limit=top_k)]
        vec = model.encode(query, normalize_embeddings=True).tolist()
        dense_results = search(query, limit=top_k * 2)
        dense = [(i, r["score"]) for i, r in enumerate(dense_results)]
        results = retriever.search(query, dense, top_k=top_k, min_score=min_score)
        return [r.text for r in results]

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

    def _score_precision(self, sample: EvalSample, retrieved: list[str]) -> float:
        if not sample.reference_contexts or not retrieved:
            return 0.0
        relevant = set(sample.reference_contexts)
        hits = sum(1 for r in retrieved if any(ref in r for ref in relevant))
        return hits / len(retrieved) if retrieved else 0.0

    def _score_recall(self, sample: EvalSample, retrieved: list[str]) -> float:
        if not sample.reference_contexts or not retrieved:
            return 0.0
        relevant = set(sample.reference_contexts)
        hits = sum(1 for ref in relevant if any(ref in r for r in retrieved))
        return hits / len(relevant) if relevant else 0.0


def _parse_score(text: str | None) -> float:
    if not text:
        return 0.0
    import re
    m = re.search(r"(\d+\.?\d*)", text.strip())
    if m:
        return max(0.0, min(1.0, float(m.group(1))))
    return 0.0


def _safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
