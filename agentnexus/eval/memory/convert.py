"""Public-benchmark converters: official LoCoMo / LongMemEval data → suite JSONL.

Sources (fetched once, cached under the shared benchmark cache):

- ``locomo``        — LoCoMo10 (Maharana et al., ACL 2024), CC BY-NC 4.0.
                      Official: https://github.com/snap-research/locomo
                      Fetched via the hf-mirror copy of ``data/locomo10.json``
                      (raw.githubusercontent is unreachable from CN networks).
- ``longmemeval-s`` — LongMemEval-S cleaned (Wu et al., ICLR 2025), 500 instances.
                      Official: https://github.com/xiaowu0162/LongMemEval
                      Fetched from hf-mirror (xiaowu0162/longmemeval-cleaned).

License note: LoCoMo is CC BY-NC 4.0 — research/internal evaluation only.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from agentnexus.eval.memory.schema import BenchQuestion, Conversation, Turn

logger = logging.getLogger(__name__)

# abstention gold for LoCoMo adversarial questions: the question presupposes
# something the conversation does not support — the scored behavior is saying
# "not determinable", so we canonicalize the gold phrase.
ABSTAIN_GOLD = "无法确定"

SOURCES: dict[str, dict[str, str]] = {
    "locomo": {
        "url": "https://hf-mirror.com/datasets/KimmoZZZ/locomo/resolve/main/locomo10.json",
        "filename": "locomo10.json",
    },
    "longmemeval-s": {
        "url": "https://hf-mirror.com/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json",
        "filename": "longmemeval_s_cleaned.json",
    },
}

# LoCoMo qa.category ints → our qa_type (counts per the ACL 2024 paper:
# 4=single-hop 841, 1=multi-hop 282, 2=temporal 321, 3=open-domain 96,
# 5=adversarial 446).
_LOCOMO_CATEGORY_MAP = {
    4: "single_hop",
    1: "multi_session",
    2: "temporal",
    3: "open_domain",
    5: "abstention",
}

# LongMemEval question_type values → our qa_type.
_LONGMEMEVAL_TYPE_MAP = {
    "single-session-user": "single_hop",
    "single-session-assistant": "single_hop",
    "single-session-preference": "single_hop",
    "multi-session": "multi_session",
    "knowledge-update": "knowledge_update",
    "temporal-reasoning": "temporal",
    "abstention": "abstention",
}


def cache_root() -> Path:
    """Shared benchmark cache, same override convention as the BEIR track."""
    override = os.environ.get("AGENTNEXUS_BENCHMARK_CACHE")
    base = Path(override) if override else Path.home() / ".cache" / "agentnexus" / "benchmarks"
    return base / "memory"


def ensure_source(source: str, offline: bool = False) -> Path:
    """Return the cached raw file for a source, downloading it on first use."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}, choose from {sorted(SOURCES)}")
    spec = SOURCES[source]
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / spec["filename"]
    if target.exists() and target.stat().st_size > 1_000:
        return target
    if offline:
        raise FileNotFoundError(
            f"{source} raw file missing: {target}. "
            f"Download {spec['url']} into the cache dir, or re-run without --offline."
        )
    logger.info("Downloading %s ...", spec["url"])
    import httpx

    resp = httpx.get(spec["url"], timeout=900, follow_redirects=True)
    resp.raise_for_status()
    target.write_bytes(resp.content)
    return target


def convert_locomo(raw: list[dict]) -> list[Conversation]:
    """LoCoMo10 → conversations. speaker_a → user, speaker_b → assistant."""
    conversations: list[Conversation] = []
    for sample in raw:
        conv = sample["conversation"]
        speaker_a = conv.get("speaker_a", "")
        session_idx = sorted(
            (int(k.split("_")[1]) for k in conv if k.startswith("session_")
             and k.removeprefix("session_").isdigit()),
        )
        turns: list[Turn] = []
        for i in session_idx:
            for t in conv.get(f"session_{i}", []):
                turns.append(Turn(
                    role="user" if t.get("speaker") == speaker_a else "assistant",
                    content=t.get("text", ""),
                ))

        questions: list[BenchQuestion] = []
        for j, q in enumerate(sample.get("qa", [])):
            qa_type = _LOCOMO_CATEGORY_MAP.get(q["category"])
            if qa_type is None:
                continue
            if qa_type == "abstention":
                gold = ABSTAIN_GOLD          # adversarial_answer is the trap, not gold
            else:
                gold = str(q.get("answer", "")).strip()
                if not gold:
                    continue
            questions.append(BenchQuestion(
                id=f"{sample['sample_id']}-q{j}",
                question=str(q["question"]).strip(),
                answer=gold,
                qa_type=qa_type,
                evidence=[str(e) for e in q.get("evidence", [])],
            ))
        conversations.append(Conversation(
            id=str(sample["sample_id"]), turns=turns, questions=questions,
        ))
    return conversations


def convert_longmemeval(raw: list[dict]) -> list[Conversation]:
    """LongMemEval-S (cleaned) → conversations."""
    conversations: list[Conversation] = []
    for i, sample in enumerate(raw):
        turns: list[Turn] = []
        for session in sample.get("haystack_sessions", []):
            for msg in session:
                role = "user" if msg.get("role") == "user" else "assistant"
                turns.append(Turn(role=role, content=msg.get("content", "")))

        qa_type = _LONGMEMEVAL_TYPE_MAP.get(sample.get("question_type"))
        answer = str(sample.get("answer", "")).strip()
        if qa_type is None or not answer:
            continue
        conversations.append(Conversation(
            id=str(sample.get("question_id", f"lme-{i}")),
            turns=turns,
            questions=[BenchQuestion(
                id=str(sample.get("question_id", f"lme-{i}")),
                question=str(sample.get("question", "")).strip(),
                answer=ABSTAIN_GOLD if qa_type == "abstention" else answer,
                qa_type=qa_type,
                evidence=[str(s) for s in sample.get("answer_session_ids", [])],
            )],
        ))
    return conversations


_CONVERTERS = {
    "locomo": convert_locomo,
    "longmemeval-s": convert_longmemeval,
}


def load_and_convert(source: str, offline: bool = False) -> list[Conversation]:
    """Download (if needed) + convert a public source to conversations."""
    path = ensure_source(source, offline=offline)
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _CONVERTERS[source](raw)


def write_suite(conversations: list[Conversation], out_path: str | Path) -> Path:
    """Serialize conversations to suite JSONL (one conversation per line)."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for conv in conversations:
            f.write(json.dumps({
                "id": conv.id,
                "turns": [{"role": t.role, "content": t.content} for t in conv.turns],
                "questions": [
                    {"id": q.id, "question": q.question, "answer": q.answer,
                     "qa_type": q.qa_type, "evidence": q.evidence}
                    for q in conv.questions
                ],
            }, ensure_ascii=False) + "\n")
    return path
