"""Memory benchmark schema — conversational memory QA suites.

One suite = N conversations. Each conversation is a flat turn list (the
multi-session structure is implicit in the dialogue) plus probing questions.
This is the LOCOMO / LongMemEval data shape, so converted public benchmarks
drop in as plain JSONL without further glue.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Question taxonomy, aligned with LongMemEval's core abilities plus the
# short-term axes (distractor filtering / state tracking) our probe research
# flagged as uncovered:
#   single_hop        — one fact, stated once
#   multi_session     — must combine facts from separated turns/sessions
#   knowledge_update  — a later turn supersedes an earlier fact; answer = current
#   temporal          — question about the earlier (superseded) state
#   distractor_filter — answer must ignore planted irrelevant content
#   abstention        — answer is not in the conversation; system should say so
#   open_domain       — needs world knowledge beyond the conversation (LOCOMO)
QA_TYPES = (
    "single_hop",
    "multi_session",
    "knowledge_update",
    "temporal",
    "distractor_filter",
    "abstention",
    "open_domain",
)


@dataclass
class Turn:
    role: str           # "user" | "assistant"
    content: str


@dataclass
class BenchQuestion:
    id: str
    question: str
    answer: str                 # gold short phrase (abstention: e.g. "无法确定")
    qa_type: str = "single_hop"
    # Source-location labels from the public benchmarks (LoCoMo dia_ids like
    # "D1:3", LongMemEval session ids). Unused by the current metrics — kept
    # for the day LTM writes carry provenance and true evidence-Recall@k
    # becomes computable.
    evidence: list[str] = field(default_factory=list)


# qa_types where "gold appears in the retrieved context" is not a valid
# expectation: abstention gold is a canonical phrase, open-domain gold needs
# world knowledge beyond the dialogue.
NO_RETRIEVAL_CHECK = ("abstention", "open_domain")


@dataclass
class Conversation:
    id: str
    turns: list[Turn]
    questions: list[BenchQuestion] = field(default_factory=list)


@dataclass
class QuestionResult:
    question_id: str
    conversation_id: str
    qa_type: str
    question: str
    gold: str
    answer: str = ""
    f1: float = 0.0
    judge_correct: bool | None = None
    prompt_chars: int = 0
    # Retrieval-level (model-free) measurement: the memory context the backend
    # actually injected for this question, and whether the gold answer is
    # covered by it. None when the qa_type makes coverage meaningless.
    context_chars: int = 0
    retrieval_hit: bool | None = None
    error: str = ""


@dataclass
class BenchReport:
    backend: str
    dataset: str
    results: list[QuestionResult] = field(default_factory=list)
    total_prompt_chars: int = 0

    def by_type(self, qa_type: str) -> list[QuestionResult]:
        return [r for r in self.results if r.qa_type == qa_type]

    def type_f1(self, qa_type: str) -> float:
        rs = self.by_type(qa_type)
        return sum(r.f1 for r in rs) / len(rs) if rs else 0.0

    def type_retrieval_rate(self, qa_type: str) -> float | None:
        """Gold-coverage rate over questions where the check applies."""
        checked = [r.retrieval_hit for r in self.by_type(qa_type) if r.retrieval_hit is not None]
        if not checked:
            return None
        return sum(1 for h in checked if h) / len(checked)

    @property
    def retrieval_hit_rate(self) -> float | None:
        checked = [r.retrieval_hit for r in self.results if r.retrieval_hit is not None]
        if not checked:
            return None
        return sum(1 for h in checked if h) / len(checked)

    @property
    def overall_f1(self) -> float:
        return sum(r.f1 for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def judge_accuracy(self) -> float | None:
        judged = [r for r in self.results if r.judge_correct is not None]
        if not judged:
            return None
        return sum(1 for r in judged if r.judge_correct) / len(judged)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.error)

    def summary(self) -> str:
        hit_txt = (
            f", retrieval hit = {self.retrieval_hit_rate:.3f}"
            if self.retrieval_hit_rate is not None else ""
        )
        lines = [
            f"记忆基准评测 [{self.backend}] {self.dataset}: "
            f"{len(self.results)} 题, overall F1 = {self.overall_f1:.3f}{hit_txt}",
        ]
        for t in QA_TYPES:
            rs = self.by_type(t)
            if not rs:
                continue
            judged = [r for r in rs if r.judge_correct is not None]
            judge_txt = ""
            if judged:
                acc = sum(1 for r in judged if r.judge_correct) / len(judged)
                judge_txt = f" judge {acc:.2f}"
            retr_txt = ""
            rate = self.type_retrieval_rate(t)
            if rate is not None:
                retr_txt = f" retr {rate:.2f}"
            lines.append(
                f"  {t:<18} n={len(rs):<3} F1 {self.type_f1(t):.3f}{retr_txt}{judge_txt}"
            )
        if self.error_count:
            lines.append(f"  errors: {self.error_count}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "dataset": self.dataset,
            "overall_f1": round(self.overall_f1, 4),
            "judge_accuracy": (
                round(self.judge_accuracy, 4) if self.judge_accuracy is not None else None
            ),
            "retrieval_hit_rate": (
                round(self.retrieval_hit_rate, 4)
                if self.retrieval_hit_rate is not None else None
            ),
            "total_prompt_chars": self.total_prompt_chars,
            "error_count": self.error_count,
            "by_type": {
                t: {
                    "n": len(self.by_type(t)),
                    "f1": round(self.type_f1(t), 4),
                    "retrieval_hit_rate": (
                        round(r, 4) if (r := self.type_retrieval_rate(t)) is not None else None
                    ),
                }
                for t in QA_TYPES if self.by_type(t)
            },
            "results": [
                {
                    "question_id": r.question_id,
                    "conversation_id": r.conversation_id,
                    "qa_type": r.qa_type,
                    "question": r.question,
                    "gold": r.gold,
                    "answer": r.answer,
                    "f1": round(r.f1, 4),
                    "judge_correct": r.judge_correct,
                    "prompt_chars": r.prompt_chars,
                    "context_chars": r.context_chars,
                    "retrieval_hit": r.retrieval_hit,
                    "error": r.error,
                }
                for r in self.results
            ],
        }
