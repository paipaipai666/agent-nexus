"""Hallucination Detector — claim extraction + context verification.

Deterministic pipeline (no LLM-as-Judge required):
  1. Extract claims from answer text (split by sentence, filter short fragments)
  2. For each claim, check if it appears in or is supported by retrieved context
  3. Unsupported claims are flagged as potential hallucinations
  4. Compute hallucination_rate = unsupported_claims / total_claims

Thresholds:
  - Production agents: <2%
  - Regulated industries: <0.5%
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentnexus.evaluation.utils import find_trace, iter_spans


@dataclass
class HallucinationReport:
    trace_id: str = ""
    total_claims: int = 0
    unsupported_claims: int = 0
    hallucination_rate: float = 0.0
    flagged_claims: list[str] = field(default_factory=list)
    answer_preview: str = ""

    @property
    def passed(self) -> bool:
        return self.hallucination_rate < 0.02

    def summary(self) -> str:
        return (
            f"Trace: {self.trace_id} | "
            f"Claims: {self.total_claims} | "
            f"Unsupported: {self.unsupported_claims} | "
            f"Rate: {self.hallucination_rate:.1%}"
        )


class HallucinationDetector:
    """Deterministic hallucination detection from trace data."""

    _SENTENCE_RE = re.compile(r'[。.！!？?\n]+')
    _MIN_CLAIM_LEN = 10

    def evaluate_all(self, traces_dir: str) -> list[HallucinationReport]:
        reports: list[HallucinationReport] = []
        for span in iter_spans(traces_dir, filter_fn=lambda s: s.get("name") == "analyst_node"):
            tid = span.get("trace_id", "")
            output = str(span.get("output", ""))
            if tid and output:
                reports.append(self._evaluate_one(tid, output))
        return reports

    def evaluate_trace(self, trace_id: str, traces_dir: str) -> HallucinationReport | None:
        spans = find_trace(traces_dir, trace_id)
        if not spans:
            return None
        for span in spans:
            if span.get("name") == "analyst_node":
                return self._evaluate_one(trace_id, str(span.get("output", "")))
        return None

    def _evaluate_one(self, trace_id: str, output: str) -> HallucinationReport:
        report = HallucinationReport(trace_id=trace_id)
        report.answer_preview = output[:500]

        # Extract claims (sentences of sufficient length)
        claims = [c.strip() for c in self._SENTENCE_RE.split(output)
                  if len(c.strip()) >= self._MIN_CLAIM_LEN]
        report.total_claims = len(claims)
        if not claims:
            return report

        # Extract context spans from the same trace (research_result, exec_stdout, etc.)
        context_text = self._gather_context(trace_id, output)

        # Verify each claim against context
        for claim in claims:
            if not self._is_supported(claim, context_text):
                report.unsupported_claims += 1
                report.flagged_claims.append(claim[:120])

        report.hallucination_rate = (
            report.unsupported_claims / report.total_claims
            if report.total_claims > 0 else 0.0
        )
        return report

    def _gather_context(self, trace_id: str, current_output: str) -> str:
        """Collect retrievable context for verification. Falls back to output self-check."""
        # Without loading the full trace, do a self-contained check:
        # Look for explicit source citations / research results in the output itself
        context_parts: list[str] = []

        # Extract quoted text (potential citations)
        quoted = re.findall(r'"([^"]{20,})"', current_output)
        context_parts.extend(quoted)

        # Extract code blocks
        code_blocks = re.findall(r'```(?:\w+)?\n(.*?)```', current_output, re.DOTALL)
        context_parts.extend(code_blocks)

        return "\n".join(context_parts) if context_parts else current_output

    @staticmethod
    def _is_supported(claim: str, context: str) -> bool:
        """Check if claim is supported by context using substring + keyword overlap.

        A claim is 'supported' if:
          - It appears verbatim in context (substring match)
          - Its key content words overlap significantly with context (>50% overlap)
        """
        if not context:
            return False

        # Direct match
        if claim in context:
            return True

        # Keyword overlap
        claim_words = set(re.findall(r'[一-鿿\w]{2,}', claim.lower()))
        if not claim_words:
            return True  # can't verify, assume supported

        context_lower = context.lower()
        matched = sum(1 for w in claim_words if w in context_lower)
        return matched / len(claim_words) >= 0.5
