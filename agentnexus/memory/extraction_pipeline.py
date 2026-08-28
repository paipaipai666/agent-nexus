"""Memory extraction pipeline — two-level gate (rules + LLM) and LTM extraction.

Extracted from MemoryManager. Owns the gate circuit breaker and the
extraction decision logic; session-scoped dependencies (LLM, LTM, embed
model) are borrowed from the owning MemoryManager via ``self._mgr``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agentnexus.core.pii import contains_pii as _contains_pii
from agentnexus.core.pii import mask_pii as _mask_pii
from agentnexus.memory.circuit_breaker import CircuitBreaker
from agentnexus.memory.extraction import extract_and_save_memories
from agentnexus.memory.metrics import get_metrics
from agentnexus.prompts import load_prompt

if TYPE_CHECKING:
    from agentnexus.memory.manager import MemoryManager

logger = logging.getLogger(__name__)

GATE_PROMPT = load_prompt("memory_gate")


class MemoryExtractionPipeline:
    """Decides whether a Q&A pair is worth memorizing, then extracts to LTM."""

    # ── Two-level filtering: class constants ────────────────────────
    _SKIP_PATTERNS = frozenset(["怎么", "如何", "帮我", "查一下", "搜索", "运行", "执行"])
    _STRONG_SIGNALS = frozenset(["记住", "我叫", "我的名字", "我喜欢", "我不喜欢", "以后都", "偏好"])

    def __init__(self, mgr: MemoryManager):
        self._mgr = mgr
        # LLM gate circuit breaker (fixed recovery)
        self.gate_circuit = CircuitBreaker(failure_threshold=3, recovery_seconds=20.0)

    def run(self, question: str, answer: str, allow_memory: bool = True) -> None:
        """Extract memories from a concluded Q&A pair (never raises)."""
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()
        hook_mgr.fire(HookType.AFTER_MEMORY_OP, {
            "op": "conclude", "question": question[:200],
            "allow_memory": allow_memory,
        })

        try:
            self._conclude_impl(question, answer, allow_memory)
        except Exception as e:
            logger.warning("LTM extraction failed (non-fatal): %s", e, exc_info=True)

    def should_extract_rules(self, question: str, answer: str) -> str:
        """First-level rule filter. Returns 'yes' / 'no' / 'uncertain'.

        Only filters formatally useless content — does NOT judge importance.
        Strong signals are checked FIRST to avoid killing short but valuable answers
        (e.g. "我叫张三" → answer "张三" is <5 chars but contains strong signal "我叫").
        """
        combined = question + answer
        # Strong signals: always pass, regardless of answer length
        if any(sig in combined for sig in self._STRONG_SIGNALS):
            return "yes"
        # Format-level exclusion: empty / whitespace-only
        if len(answer.strip()) < 5:
            return "no"
        # Transactional + very short: tool echo
        if any(p in question for p in self._SKIP_PATTERNS) and len(answer.strip()) < 50:
            return "no"
        return "uncertain"

    def should_extract(self, question: str, answer: str) -> bool:
        """Two-level filtering: rules first (free), LLM gate second (boundary cases only)."""
        metrics = get_metrics()

        # Level 1: rule filter (0ms, deterministic)
        rule_result = self.should_extract_rules(question, answer)
        if rule_result != "uncertain":
            return rule_result == "yes"

        # Level 2: LLM gate (only for boundary cases)
        gate = self.gate_circuit
        if not gate.should_allow():
            metrics.incr("writes_skipped_gate")
            return False

        try:
            prompt = GATE_PROMPT.format(question=question[:500], answer=answer[:500])
            result = self._mgr._llm.think([{"role": "user", "content": prompt}], silent=True)
            normalized = result.strip().lower().strip('"').strip("'").strip(".")
            if normalized == "yes" or normalized.startswith("yes"):
                gate.record_success()
                return True
            if normalized == "no" or normalized.startswith("no"):
                gate.record_success()
                metrics.incr("writes_skipped_gate")
                return False
            # Format anomaly — not a failure, but counted separately
            logger.warning("Gate returned unexpected format: %s", result[:100])
            gate.record_success()
            metrics.incr("writes_skipped_gate_format_error")
            return False
        except Exception:
            gate.record_failure()
            metrics.incr("writes_skipped_gate_error")
            return False

    def _conclude_impl(self, question: str, answer: str, allow_memory: bool):
        mgr = self._mgr
        if not answer or not mgr.long_term:
            return
        if not allow_memory:
            return
        if not self._mgr._should_extract(question, answer):
            return
        # PII masking on input side (defense in depth — extraction prompt also controls this)
        if _contains_pii(question) or _contains_pii(answer):
            question = _mask_pii(question)
            answer = _mask_pii(answer)
        extract_and_save_memories(
            llm=mgr._llm,
            embed_model=mgr._get_embed_model(),
            long_term=mgr.long_term,
            session_id=mgr.session_id,
            question=question,
            answer=answer,
        )
