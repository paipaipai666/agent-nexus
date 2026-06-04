"""Eval CLI command registration and compatibility exports."""

from __future__ import annotations

from importlib.util import find_spec

# NOTE: The old `from agentnexus.cli import eval` import was broken.
# Commands are now registered via the eval/ subpackage.
# This file only exports compatibility symbols.

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
