"""Conversational memory benchmark framework.

Backends (nexus = product memory system, naive = full-context baseline) are
driven over LOCOMO/LongMemEval-shaped suites; answers scored by char-F1 with
an optional LLM judge. Companion to the deterministic probe suite in
``agentnexus.evaluation.memory_eval`` (``nexus eval memory``): probes check
invariants, this framework measures end-to-end QA quality.
"""

from agentnexus.eval.memory.backend import MemoryBackend, NaiveBackend, NexusBackend
from agentnexus.eval.memory.dataset import builtin_suite_path, load_builtin_suite, load_suite
from agentnexus.eval.memory.runner import MemoryBenchRunner
from agentnexus.eval.memory.schema import (
    QA_TYPES,
    BenchQuestion,
    BenchReport,
    Conversation,
    QuestionResult,
    Turn,
)
from agentnexus.eval.memory.scoring import char_f1

__all__ = [
    "BenchQuestion",
    "BenchReport",
    "Conversation",
    "MemoryBackend",
    "MemoryBenchRunner",
    "NaiveBackend",
    "NexusBackend",
    "QA_TYPES",
    "QuestionResult",
    "Turn",
    "builtin_suite_path",
    "char_f1",
    "load_builtin_suite",
    "load_suite",
]
