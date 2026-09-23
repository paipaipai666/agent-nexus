"""Scoring for memory-bench answers.

Primary metric is char-level (multiset) F1, which is language-fair for
Chinese gold phrases and matches the token-F1 convention of LOCOMO /
LongMemEval. `--judge` adds an LLM correctness verdict on top.
"""

from __future__ import annotations

import re
from collections import Counter

_PUNCT = re.compile(r"[\s，。！？；：、（）()【】\[\]《》<>\"'\"'.,!?;:·—\-_/\\|]+")

# Judge prompt shared with the memory probe suite (memory_eval.py) — one
# convention for "does the answer contain the gold fact" across the repo.
JUDGE_PROMPT = (
    "判断回答是否正确。\n问题: {question}\n正确答案应包含: {gold}\n"
    "模型回答: {answer}\n只回答 \"正确\" 或 \"错误\"。"
)


def _norm(text: str) -> list[str]:
    """Character multiset after lowercasing and stripping punctuation/spaces."""
    return list(_PUNCT.sub("", text.lower()))


def char_f1(prediction: str, gold: str) -> float:
    """Multiset char F1 in [0, 1]. Empty prediction scores 0."""
    pred_counts = Counter(_norm(prediction))
    gold_counts = Counter(_norm(gold))
    if not pred_counts or not gold_counts:
        return 0.0
    overlap = sum((pred_counts & gold_counts).values())
    if overlap == 0:
        return 0.0
    precision = overlap / sum(pred_counts.values())
    recall = overlap / sum(gold_counts.values())
    return 2 * precision * recall / (precision + recall)


def _norm_text(text: str) -> str:
    return "".join(_norm(text))


def gold_coverage(gold: str, context: str) -> bool:
    """True when the normalized gold appears in the normalized context.

    Retrieval-level, model-free: "could any reader produce the gold from the
    memory context this backend injected" (LongMemEval answerability check).
    """
    norm_gold = _norm_text(gold)
    return bool(norm_gold) and norm_gold in _norm_text(context)


def judge_correct(judge_llm, question: str, gold: str, answer: str) -> bool:
    """LLM verdict; returns True unless the judge explicitly says 错误."""
    verdict = judge_llm.think(
        [{"role": "user", "content": JUDGE_PROMPT.format(
            question=question, gold=gold, answer=answer)}],
        silent=True,
    )
    return "错误" not in verdict
