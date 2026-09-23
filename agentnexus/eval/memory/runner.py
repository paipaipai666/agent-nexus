"""Benchmark runner: drive a backend over a suite, score, aggregate."""

from __future__ import annotations

import logging

from agentnexus.eval.memory.schema import (
    NO_RETRIEVAL_CHECK,
    BenchReport,
    Conversation,
    QuestionResult,
)
from agentnexus.eval.memory.scoring import char_f1, gold_coverage, judge_correct

logger = logging.getLogger(__name__)


class MemoryBenchRunner:
    """Run a MemoryBackend over a conversation suite and build a BenchReport.

    ``judge_llm`` (optional) adds an LLM correctness verdict per question on
    top of char-F1. The generator LLM is owned by the backend, matching the
    memory_eval.py convention (production model under test).
    """

    def __init__(self, backend, judge_llm=None, dataset_name: str = "sample"):
        self.backend = backend
        self.judge = judge_llm
        self.dataset_name = dataset_name

    def run(self, conversations: list[Conversation],
            limit: int = 0) -> BenchReport:
        report = BenchReport(backend=self.backend.name, dataset=self.dataset_name)
        selected = conversations[:limit] if limit > 0 else conversations
        try:
            for i, conv in enumerate(selected, 1):
                logger.info(
                    "memory-bench: conversation %d/%d %s (%d turns, %d questions)",
                    i, len(selected), conv.id, len(conv.turns), len(conv.questions))
                self._run_conversation(conv, report)
        finally:
            self.backend.close()
        return report

    def _run_conversation(self, conv: Conversation, report: BenchReport) -> None:
        self.backend.reset()
        try:
            self.backend.ingest(conv.turns)
            logger.info("memory-bench: %s ingest done", conv.id)
        except Exception as e:
            logger.exception("ingest failed: %s", conv.id)
            for q in conv.questions:
                report.results.append(QuestionResult(
                    question_id=q.id, conversation_id=conv.id, qa_type=q.qa_type,
                    question=q.question, gold=q.answer, error=f"ingest: {e}",
                ))
            return

        for j, q in enumerate(conv.questions, 1):
            logger.info("memory-bench: %s question %d/%d %s",
                        conv.id, j, len(conv.questions), q.id)
            result = QuestionResult(
                question_id=q.id, conversation_id=conv.id, qa_type=q.qa_type,
                question=q.question, gold=q.answer,
            )
            try:
                result.answer = self.backend.answer(q.question)
                result.prompt_chars = self.backend.prompt_chars()
                result.f1 = char_f1(result.answer, q.answer)
                context = self.backend.last_context()
                result.context_chars = len(context)
                if q.qa_type not in NO_RETRIEVAL_CHECK:
                    result.retrieval_hit = gold_coverage(q.answer, context)
                if self.judge is not None:
                    result.judge_correct = judge_correct(
                        self.judge, q.question, q.answer, result.answer)
            except Exception as e:
                logger.exception("question failed: %s", q.id)
                result.error = f"{type(e).__name__}: {e}"
            report.total_prompt_chars += result.prompt_chars
            report.results.append(result)
