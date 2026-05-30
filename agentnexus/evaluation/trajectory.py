"""Trajectory Evaluator — deterministic trace quality assessment.

Reads JSONL trace files and applies 5 rule-based checks (no LLM-as-Judge):
  1. duplicate_calls   — same tool + same params called ≥3 times in a row
  2. tool_appropriateness — code_error without switching to research
  3. loop_detection     — plan_node appears ≥4 times
  4. retry_efficiency   — critic score not improving across retries
  5. plan_adherence     — actual plan_node count vs original plan steps
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentnexus.evaluation.utils import find_trace, load_all_traces, load_trace_spans


@dataclass
class TrajectoryIssue:
    check: str
    severity: str  # "error" | "warning"
    detail: str
    evidence: str = ""


@dataclass
class TrajectoryReport:
    trace_id: str
    total_spans: int
    issues: list[TrajectoryIssue] = field(default_factory=list)
    score: float = 10.0  # starts at 10, deductions for each issue

    @property
    def passed(self) -> bool:
        return self.score >= 6.0

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    def summary(self) -> str:
        lines = [
            f"Trace: {self.trace_id}",
            f"Spans: {self.total_spans}",
            f"Score: {self.score:.0f}/10",
            f"Issues: {self.issue_count}",
        ]
        if self.issues:
            lines.append("")
            for i in self.issues[:5]:
                lines.append(f"  [{i.severity.upper()}] {i.check}: {i.detail}")
        return "\n".join(lines)


class TrajectoryEvaluator:
    """Deterministic rule-based trajectory quality evaluator."""

    def evaluate_file(self, filepath: str) -> list[TrajectoryReport]:
        """Evaluate all traces in a JSONL file."""
        traces = load_trace_spans(filepath)
        return [self._evaluate_one(tid, spans) for tid, spans in traces.items()]

    def evaluate_trace(self, trace_id: str, traces_dir: str) -> TrajectoryReport | None:
        """Evaluate a single trace by ID from a traces directory."""
        spans = find_trace(traces_dir, trace_id)
        if spans:
            return self._evaluate_one(trace_id, spans)
        return None

    def evaluate_all(self, traces_dir: str) -> list[TrajectoryReport]:
        """Evaluate all traces in a traces directory."""
        traces = load_all_traces(traces_dir)
        return [self._evaluate_one(tid, spans) for tid, spans in traces.items()]

    # ── per-trace evaluation ─────────────────────────────────────

    def _evaluate_one(self, trace_id: str, spans: list[dict]) -> TrajectoryReport:
        spans = sorted(spans, key=lambda s: s.get("start_time", 0))
        report = TrajectoryReport(trace_id=trace_id, total_spans=len(spans))

        # Extract named spans and tool calls
        named_spans = [s for s in spans if s.get("name") and s["name"] != "task"]
        tool_calls = [s for s in spans if "tool" in s.get("name", "").lower()
                      or "execute" in s.get("name", "").lower()]

        # 1. Duplicate call detection
        self._check_duplicate_calls(tool_calls, report)

        # 2. Tool appropriateness
        self._check_tool_appropriateness(named_spans, report)

        # 3. Loop detection
        self._check_loops(named_spans, report)

        # 4. Retry efficiency
        self._check_retry_efficiency(named_spans, report)

        # 5. Plan adherence
        self._check_plan_adherence(named_spans, report)

        report.score = max(0, report.score)
        return report
