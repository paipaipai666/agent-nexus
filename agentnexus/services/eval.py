"""Evaluation service facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class EvalService:
    def __init__(self, settings: Any):
        self.settings = settings

    def run_rag_eval(self, *args: Any, **kwargs: Any) -> Any:
        from agentnexus.rag.evaluator import RAGEvaluator

        return RAGEvaluator(*args, **kwargs)

    def list_reports(self) -> list[Path]:
        traces_dir = Path(getattr(self.settings, "traces_dir", ""))
        if not traces_dir.exists():
            return []
        return sorted(traces_dir.glob("*.jsonl"))

    def compare_reports(self, left: str | Path, right: str | Path) -> dict[str, str]:
        return {"left": str(left), "right": str(right)}

