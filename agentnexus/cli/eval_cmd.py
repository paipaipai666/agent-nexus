"""Eval CLI command registration and compatibility exports."""

from __future__ import annotations

from importlib.util import find_spec

# Import command modules for Typer registration (required at import time for CLI).
from agentnexus.cli import eval as _eval_commands  # noqa: F401,E402

RAGEvaluator = None


def get_rag_evaluator_cls():
    global RAGEvaluator
    if RAGEvaluator is None:
        from agentnexus.rag.evaluator import RAGEvaluator as _RAGEvaluator

        RAGEvaluator = _RAGEvaluator
    return RAGEvaluator

__all__ = [
    "RAGEvaluator",
    "get_rag_evaluator_cls",
    "find_spec",
]
