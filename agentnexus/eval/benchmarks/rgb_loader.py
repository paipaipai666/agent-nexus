"""RGB benchmark loader — Chen et al., AAAI 2024 "Benchmarking LLMs in RAG".

Official repo: https://github.com/chen700564/RGB (data is JSONL, one query per
line: query / acceptable-answer groups / positive docs / negative docs).

Evaluation protocol (from the official evalue.py): for each query, sample
``passage_num`` documents where ``ceil(passage_num * noise_rate)`` are negative
and the rest positive, mix them, retrieve from that per-query corpus, generate
with the official instruction template, then judge. We keep passage_num=5 to
stay comparable with the paper.

Cache layout mirrors BEIR: ``~/.cache/agentnexus/benchmarks/rgb/*.json``.
"""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

RGB_REPO_RAW = "https://raw.githubusercontent.com/chen700564/RGB/master"
RGB_LICENSE_NOTE = (
    "RGB (chen700564/RGB) is distributed for research evaluation; "
    "derived from HotpotQA / DuReader checklists. Cite the AAAI 2024 paper."
)

RGB_REJECTION_PHRASES_ZH = ("文档信息不足", "无法基于提供的文档", "事实性错误")
RGB_REJECTION_PHRASES_EN = (
    "i can not answer",
    "cannot answer",
    "insufficient information",
    "factual errors",
    "cannot answer the question",
)


def rgb_cache_root() -> Path:
    import os

    override = os.environ.get("AGENTNEXUS_BENCHMARK_CACHE")
    base = Path(override) if override else Path.home() / ".cache" / "agentnexus" / "benchmarks"
    return base / "rgb"


@dataclass
class RGBQuery:
    query_id: str
    query: str
    answer_groups: list[list[str]]  # acceptable-answer groups (any group match = correct)
    positive: list[str]
    negative: list[str]
    task: str = "noise"  # noise | rejection
    language: str = "zh"  # zh | en
    sampled_docs: list[str] = field(default_factory=list)

    @property
    def noise_rate(self) -> float:
        total = len(self.positive) + len(self.negative)
        return len(self.negative) / total if total else 0.0

    def build_corpus(self, passage_num: int = 5, seed: int = 13) -> list[str]:
        """Official processdata sampling: ceil(n*noise) negatives, rest positives."""
        rng = random.Random(seed + hash(self.query_id) % 10_000)
        neg_num = min(math.ceil(passage_num * self.noise_rate), len(self.negative))
        pos_num = min(passage_num - neg_num, len(self.positive))
        pos_docs = rng.sample(self.positive, pos_num)
        neg_docs = rng.sample(self.negative, neg_num)
        docs = pos_docs + neg_docs
        labels = [True] * len(pos_docs) + [False] * len(neg_docs)
        paired = list(zip(docs, labels, strict=True))
        rng.shuffle(paired)
        self.sampled_docs = [doc for doc, _ in paired]
        self.sampled_labels = [label for _, label in paired]
        return self.sampled_docs

    sampled_labels: list[bool] = field(default_factory=list)

    def ground_truth_text(self) -> str:
        return " ; ".join(" / ".join(group) for group in self.answer_groups)

    def expects_rejection(self) -> bool:
        return self.task == "rejection"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"RGB data not found: {path}. Download: {RGB_REPO_RAW}/data/{path.name}"
        )
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _ensure_downloaded(root: Path, names: list[str]) -> None:
    import httpx

    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        target = root / name
        if target.exists() and target.stat().st_size > 1000:
            continue
        url = f"{RGB_REPO_RAW}/data/{name}"
        logger.info("Downloading %s ...", url)
        resp = httpx.get(url, timeout=300, follow_redirects=True)
        resp.raise_for_status()
        target.write_bytes(resp.content)


def load_rgb_queries(
    language: str = "zh",
    task: str = "noise",
    offline: bool = False,
) -> list[RGBQuery]:
    """Load RGB queries. task: 'noise' (main set) or 'rejection' (_refine set)."""
    suffix = "_refine" if task == "rejection" else ""
    filename = f"{language}{suffix}.json"
    root = rgb_cache_root()
    if not offline:
        _ensure_downloaded(root, [filename])
    rows = _load_jsonl(root / filename)
    queries = []
    for row in rows:
        answers = row.get("answer") or []
        groups = [group if isinstance(group, list) else [str(group)] for group in answers]
        queries.append(
            RGBQuery(
                query_id=str(row["id"]),
                query=str(row["query"]),
                answer_groups=groups,
                positive=[str(doc) for doc in row.get("positive") or []],
                negative=[str(doc) for doc in row.get("negative") or []],
                task=task,
                language=language,
            )
        )
    logger.info("RGB %s/%s: %d queries", language, task, len(queries))
    return queries


def rgb_generation_prompt(query: RGBQuery, docs: list[str], reinforce_refusal: bool = False) -> tuple[str, str]:
    """Official RGB system instruction + user template, per language.

    ``reinforce_refusal`` appends an explicit refusal-discipline clause — the
    system-level lever our RGB A/B tests: same corpus, same model, only the
    assembled prompt changes, so accuracy deltas attribute to the pipeline.
    """
    joined = "\n".join(f"Document {i + 1}: {doc}" for i, doc in enumerate(docs))
    if query.language == "zh":
        system = (
            "你是一个准确和可靠的人工智能助手，能够借助外部文档回答问题，请注意外部文档可能存在噪声事实性错误。"
            "如果文档中的信息包含了正确答案，你将进行准确的回答。"
            "如果文档中的信息不包含答案，你将生成“文档信息不足，因此我无法基于提供的文档回答该问题。”。"
            "如果部分文档中存在与事实不一致的错误，请先生成“提供文档的文档存在事实性错误。”，并生成正确答案。"
        )
        if reinforce_refusal:
            system += (
                "\n再次强调：当文档中确实没有问题的答案时，你必须只输出“文档信息不足，因此我无法基于提供的文档回答该问题。”，"
                "不要根据常识、推测或文档外的知识作答。"
            )
        user = f"文档：\n{joined} \n\n问题：\n{query.query}"
    else:
        system = (
            "You are an accurate and reliable AI assistant that can answer questions with the help of external documents. "
            "Please note that external documents may contain noisy or factually incorrect information. "
            "If the information in the document contains the correct answer, you will give an accurate answer. "
            "If the information in the document does not contain the answer, you will generate "
            "'I can not answer the question because of the insufficient information in documents.'. "
            "If there are inconsistencies with the facts in some of the documents, please generate the response "
            "'There are factual errors in the provided documents.' and provide the correct answer."
        )
        if reinforce_refusal:
            system += (
                "\nReiterate: when the documents genuinely do not contain the answer, you must output only "
                "'I can not answer the question because of the insufficient information in documents.' "
                "Do NOT answer from common knowledge, speculation, or knowledge outside the documents."
            )
        user = f"Document:\n{joined} \n\nQuestion:\n{query.query}"
    return system, user


def is_rejection_output(answer: str, language: str) -> bool:
    lowered = (answer or "").strip().lower()
    phrases = RGB_REJECTION_PHRASES_ZH if language == "zh" else RGB_REJECTION_PHRASES_EN
    return any(phrase in lowered for phrase in phrases)
