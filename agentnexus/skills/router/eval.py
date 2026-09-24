"""Deterministic skill-router Hit@k evaluation and parameter sweep.

Zero-LLM: scores gold (query → skill_id) pairs with SkillRecommender.rank only.
Use for tuning min_score / margin / max_candidates without model calls.

CLI:
    python -m agentnexus.skills.router.eval path/to/gold.jsonl
    python -m agentnexus.skills.router.eval path/to/gold.jsonl --sweep
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class GoldItem:
    query: str
    skill_id: str  # qualified id, e.g. default/docx


@dataclass
class HitReport:
    n: int
    hit_at_1: float
    hit_at_3: float
    hit_at_5: float
    mrr: float
    misses: list[tuple[str, str, tuple[str, ...]]]  # query, gold, top ids

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "hit_at_1": round(self.hit_at_1, 4),
            "hit_at_3": round(self.hit_at_3, 4),
            "hit_at_5": round(self.hit_at_5, 4),
            "mrr": round(self.mrr, 4),
            "misses": len(self.misses),
        }


def load_gold(path: str | Path) -> list[GoldItem]:
    """Load gold JSONL lines: {"query": "...", "skill_id": "ns/id"}."""
    items: list[GoldItem] = []
    text = Path(path).read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        data = json.loads(line)
        query = str(data.get("query") or "").strip()
        skill_id = str(data.get("skill_id") or "").strip()
        if not query or not skill_id:
            raise ValueError(f"{path}:{line_no}: need query and skill_id")
        items.append(GoldItem(query=query, skill_id=skill_id))
    if not items:
        raise ValueError(f"{path}: empty gold set")
    return items


def evaluate_hits(
    gold: Iterable[GoldItem],
    ranked_ids_per_query: Iterable[list[str]],
) -> HitReport:
    """Score precomputed rankings. Independent of SkillService for pure unit tests."""
    hits1 = hits3 = hits5 = 0
    rr_sum = 0.0
    misses: list[tuple[str, str, tuple[str, ...]]] = []
    n = 0
    for item, ranked in zip(gold, ranked_ids_per_query, strict=True):
        n += 1
        top = list(ranked)
        try:
            rank = top.index(item.skill_id) + 1
        except ValueError:
            rank = 0
        if rank == 1:
            hits1 += 1
        if 1 <= rank <= 3:
            hits3 += 1
        if 1 <= rank <= 5:
            hits5 += 1
        if rank > 0:
            rr_sum += 1.0 / rank
        else:
            misses.append((item.query, item.skill_id, tuple(top[:5])))
    if n == 0:
        return HitReport(0, 0.0, 0.0, 0.0, 0.0, [])
    return HitReport(
        n=n,
        hit_at_1=hits1 / n,
        hit_at_3=hits3 / n,
        hit_at_5=hits5 / n,
        mrr=rr_sum / n,
        misses=misses,
    )


def evaluate_on_entries(
    gold: list[GoldItem],
    entries: list[Any],
    *,
    min_score: float = 2.0,
    margin: float = 0.75,
    max_candidates: int = 8,
    use_embeddings: bool = False,
) -> HitReport:
    from agentnexus.skills.router.decide import SkillRecommender

    router = SkillRecommender(
        min_score=min_score,
        margin=margin,
        max_candidates=max_candidates,
        use_embeddings=use_embeddings,
    )
    ranked_lists: list[list[str]] = []
    for item in gold:
        routes = router.rank(item.query, entries)
        ranked_lists.append([r.entry.qualified_id for r in routes])
    return evaluate_hits(gold, ranked_lists)


def sweep_params(
    gold: list[GoldItem],
    entries: list[Any],
    *,
    min_scores: tuple[float, ...] = (1.0, 2.0, 3.0),
    margins: tuple[float, ...] = (0.5, 0.75, 1.25),
    max_candidates: tuple[int, ...] = (5, 8),
    use_embeddings: bool = False,
) -> list[dict[str, Any]]:
    """Grid-search router knobs; sort by Hit@1 then MRR."""
    results: list[dict[str, Any]] = []
    for min_score in min_scores:
        for margin in margins:
            for max_k in max_candidates:
                report = evaluate_on_entries(
                    gold,
                    entries,
                    min_score=min_score,
                    margin=margin,
                    max_candidates=max_k,
                    use_embeddings=use_embeddings,
                )
                row = {
                    "min_score": min_score,
                    "margin": margin,
                    "max_candidates": max_k,
                    **report.as_dict(),
                }
                results.append(row)
    results.sort(key=lambda r: (-r["hit_at_1"], -r["mrr"], r["min_score"], r["margin"]))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Skill router Hit@k eval (no LLM)")
    parser.add_argument("gold", help="JSONL gold file: query, skill_id")
    parser.add_argument(
        "--skills-root",
        action="append",
        default=[],
        help="Skill root to discover entries (repeatable). Default: AGENTNEXUS_HOME/skills",
    )
    parser.add_argument("--sweep", action="store_true", help="Grid-search min_score/margin/max_k")
    parser.add_argument("--embeddings", action="store_true", help="Enable semantic embeddings")
    args = parser.parse_args(argv)

    gold = load_gold(args.gold)

    from agentnexus.skills.registry import SkillRegistry

    if args.skills_root:
        registry = SkillRegistry(args.skills_root)
    else:
        from agentnexus.core.config import get_settings

        registry = SkillRegistry.from_settings(get_settings())
    entries = [e for e in registry.discover() if e.source_kind == "skill"]
    if not entries:
        print("No skills discovered — pass --skills-root")
        return 2

    if args.sweep:
        rows = sweep_params(gold, entries, use_embeddings=args.embeddings)
        for row in rows[:15]:
            print(json.dumps(row, ensure_ascii=False))
        print(f"# swept {len(rows)} configs; top by hit_at_1,mrr shown")
    else:
        report = evaluate_on_entries(gold, entries, use_embeddings=args.embeddings)
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        for query, gold_id, top in report.misses[:10]:
            print(f"MISS gold={gold_id} top={list(top)} :: {query}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
