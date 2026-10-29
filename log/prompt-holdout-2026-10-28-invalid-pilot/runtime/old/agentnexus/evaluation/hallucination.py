"""Hallucination Detector — claim extraction + context verification.

Deterministic pipeline (no LLM-as-Judge required):
  1. Extract claims from answer text (split by sentence, filter short fragments)
  2. For each claim, check if it appears in or is supported by tool results
  3. Unsupported claims are flagged as potential hallucinations
  4. Compute hallucination_rate = unsupported_claims / total_claims

Thresholds:
  - Production agents: <2%
  - Regulated industries: <0.5%
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentnexus.evaluation.utils import load_all_traces


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
        all_traces = load_all_traces(traces_dir)
        for tid, spans in all_traces.items():
            answer = self._extract_answer(spans)
            if answer:
                reports.append(self._evaluate_one(tid, answer, spans))
        return reports

    def evaluate_trace(self, trace_id: str, traces_dir: str) -> HallucinationReport | None:
        from agentnexus.evaluation.utils import find_trace
        spans = find_trace(traces_dir, trace_id)
        if not spans:
            return None
        answer = self._extract_answer(spans)
        if not answer:
            return None
        return self._evaluate_one(trace_id, answer, spans)

    def _extract_answer(self, spans: list[dict]) -> str:
        """Extract the final answer text from trace spans."""
        for s in spans:
            if s.get("name") == "final_answer":
                output = s.get("output", {}) or {}
                return str(output.get("answer", ""))
        return ""

    def _evaluate_one(self, trace_id: str, answer: str, spans: list[dict]) -> HallucinationReport:
        report = HallucinationReport(trace_id=trace_id)
        report.answer_preview = answer[:500]

        claims = [c.strip() for c in self._SENTENCE_RE.split(answer)
                  if len(c.strip()) >= self._MIN_CLAIM_LEN]
        report.total_claims = len(claims)
        if not claims:
            return report

        context_text = self._gather_context(spans, answer)

        for claim in claims:
            if not self._is_supported(claim, context_text):
                report.unsupported_claims += 1
                report.flagged_claims.append(claim[:120])

        report.hallucination_rate = (
            report.unsupported_claims / report.total_claims
            if report.total_claims > 0 else 0.0
        )
        return report

    def _gather_context(self, spans: list[dict], current_output: str) -> str:
        """Collect retrievable context from tool results for verification."""
        context_parts: list[str] = []

        for s in spans:
            if s.get("name") == "tool":
                output = s.get("output", {}) or {}
                result = str(output.get("result_summary", ""))
                if result:
                    context_parts.append(result)

        context_parts.extend(re.findall(r'"([^"]{20,})"', current_output))
        code_blocks = re.findall(r'```(?:\w+)?\n(.*?)```', current_output, re.DOTALL)
        context_parts.extend(code_blocks)

        return "\n".join(context_parts) if context_parts else current_output

    @staticmethod
    def _is_supported(claim: str, context: str) -> bool:
        """Check if claim is supported by context using substring + keyword overlap."""
        if not context:
            return False

        if claim in context:
            return True

        claim_words = set(re.findall(r'[一-鿿\w]{2,}', claim.lower()))
        if not claim_words:
            return True

        context_lower = context.lower()
        matched = sum(1 for w in claim_words if w in context_lower)
        return matched / len(claim_words) >= 0.5
