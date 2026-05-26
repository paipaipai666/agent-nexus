"""Eval CLI command registration and compatibility exports."""

from importlib.util import find_spec

# Import command modules for Typer registration.
from agentnexus.cli import eval as _eval_commands  # noqa: F401,E402
from agentnexus.cli.eval.common import _detect_embedding_device, _fmt_ci, _fmt_pct
from agentnexus.cli.eval.stats import _compute_calibration, _pearson, _spearman
from agentnexus.core.config import get_settings

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
    "get_settings",
    "_compute_calibration",
    "_detect_embedding_device",
    "_fmt_ci",
    "_fmt_pct",
    "_pearson",
    "_spearman",
]
