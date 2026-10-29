"""Suite loading: JSONL conversations + the built-in sample suite.

JSONL line format (one conversation per line, UTF-8):

    {
      "id": "conv-1",
      "turns": [{"role": "user", "content": "..."}, ...],
      "questions": [
        {"id": "q1", "question": "...", "answer": "...", "qa_type": "single_hop"}
      ]
    }

`qa_type` must be one of schema.QA_TYPES. Converted LOCOMO / LongMemEval
exports should target this shape; no bespoke importer lives here.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from agentnexus.eval.memory.schema import QA_TYPES, BenchQuestion, Conversation, Turn


def _parse_conversation(obj: dict, line_no: int) -> Conversation:
    try:
        conv = Conversation(
            id=str(obj["id"]),
            turns=[Turn(role=t["role"], content=t["content"]) for t in obj["turns"]],
            questions=[
                BenchQuestion(
                    id=str(q["id"]),
                    question=q["question"],
                    answer=q["answer"],
                    qa_type=q.get("qa_type", "single_hop"),
                    evidence=[str(e) for e in q.get("evidence", [])],
                )
                for q in obj.get("questions", [])
            ],
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"line {line_no}: malformed conversation: {e}") from e
    bad_types = {q.qa_type for q in conv.questions} - set(QA_TYPES)
    if bad_types:
        raise ValueError(
            f"line {line_no}: unknown qa_type(s) {sorted(bad_types)}, "
            f"expected subset of {list(QA_TYPES)}"
        )
    bad_roles = {t.role for t in conv.turns} - {"user", "assistant"}
    if bad_roles:
        raise ValueError(f"line {line_no}: unknown role(s) {sorted(bad_roles)}")
    return conv


def load_suite(path: str | Path) -> list[Conversation]:
    """Load a suite from JSONL (one conversation per line)."""
    conversations: list[Conversation] = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            conversations.append(_parse_conversation(json.loads(line), line_no))
    if not conversations:
        raise ValueError(f"{path}: empty suite")
    return conversations


def builtin_suite_path() -> Path:
    return Path(resources.files("agentnexus.eval.memory") / "data" / "sample_suite.jsonl")


def load_builtin_suite() -> list[Conversation]:
    return load_suite(builtin_suite_path())
