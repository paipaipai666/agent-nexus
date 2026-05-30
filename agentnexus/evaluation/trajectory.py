"""Trajectory Evaluator — deterministic trace quality assessment.

Reads JSONL trace files and applies rule-based checks (no LLM-as-Judge):
  1. duplicate_calls   — same tool + same params called >=3 times in a row
  2. repeated_failures — same tool fails >=3 times without switching
  3. loop_detection    — excessive LLM steps (>=8) suggests stuck loop
  4. error_cascade     — >=3 consecutive LLM errors
  5. step_efficiency   — total steps vs tool calls ratio
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

        tool_spans = [s for s in spans if s.get("name") == "tool"]
        llm_spans = [s for s in spans if s.get("name") == "llm"]

        # 1. Duplicate call detection
        self._check_duplicate_calls(tool_spans, report)

        # 2. Repeated failures
        self._check_repeated_failures(tool_spans, report)

        # 3. Loop detection (excessive steps)
        self._check_loops(llm_spans, report)

        # 4. Error cascade
        self._check_error_cascade(llm_spans, report)

        # 5. Step efficiency
        self._check_step_efficiency(llm_spans, tool_spans, report)

        report.score = max(0, report.score)
        return report

    # ── individual checks ─────────────────────────────────────────

    def _check_duplicate_calls(self, tool_spans: list[dict], report: TrajectoryReport):
        """Detect same tool + same input called >=3 times consecutively."""
        for i in range(len(tool_spans) - 2):
            a, b, c = tool_spans[i], tool_spans[i + 1], tool_spans[i + 2]
            a_name = (a.get("input", {}) or {}).get("tool_name", "")
            b_name = (b.get("input", {}) or {}).get("tool_name", "")
            c_name = (c.get("input", {}) or {}).get("tool_name", "")
            if a_name and a_name == b_name == c_name:
                a_params = str((a.get("input", {}) or {}).get("params", ""))[:100]
                b_params = str((b.get("input", {}) or {}).get("params", ""))[:100]
                c_params = str((c.get("input", {}) or {}).get("params", ""))[:100]
                if a_params == b_params == c_params:
                    report.score -= 1.5
                    report.issues.append(TrajectoryIssue(
                        check="duplicate_calls",
                        severity="error",
                        detail=f"Tool '{a_name}' called 3+ times with identical params: {a_params[:60]}",
                    ))
                    return  # report once per trace

    def _check_repeated_failures(self, tool_spans: list[dict], report: TrajectoryReport):
        """Detect same tool failing >=3 times without switching."""
        failures: list[str] = []
        for s in tool_spans:
            meta = s.get("metadata", {}) or {}
            name = (s.get("input", {}) or {}).get("tool_name", "")
            if meta.get("status") == "error" and name:
                failures.append(name)
            else:
                failures.clear()

        if len(failures) >= 3:
            report.score -= 1.5
            report.issues.append(TrajectoryIssue(
                check="repeated_failures",
                severity="error",
                detail=f"Tool '{failures[0]}' failed {len(failures)} times consecutively",
            ))

    def _check_loops(self, llm_spans: list[dict], report: TrajectoryReport):
        """Flag if agent made >=8 LLM calls (possible stuck loop)."""
        if len(llm_spans) >= 8:
            report.score -= 2.0
            report.issues.append(TrajectoryIssue(
                check="loop_detection",
                severity="error",
                detail=f"Agent made {len(llm_spans)} LLM calls (>=8 suggests stuck loop)",
                evidence=f"llm_span_count={len(llm_spans)}",
            ))

    def _check_error_cascade(self, llm_spans: list[dict], report: TrajectoryReport):
        """Detect >=3 consecutive LLM errors."""
        consecutive_errors = 0
        for s in llm_spans:
            meta = s.get("metadata", {}) or {}
            if meta.get("status") == "error":
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    report.score -= 1.5
                    report.issues.append(TrajectoryIssue(
                        check="error_cascade",
                        severity="error",
                        detail=f"{consecutive_errors} consecutive LLM errors",
                    ))
                    return
            else:
                consecutive_errors = 0

    def _check_step_efficiency(self, llm_spans: list[dict], tool_spans: list[dict],
                               report: TrajectoryReport):
        """Check if agent used excessive LLM calls relative to tool calls."""
        llm_count = len(llm_spans)
        tool_count = len(tool_spans)
        if tool_count > 0 and llm_count > tool_count * 3:
            report.score -= 1.0
            report.issues.append(TrajectoryIssue(
                check="step_efficiency",
                severity="warning",
                detail=f"Low tool/LLM ratio: {tool_count} tools in {llm_count} LLM calls",
            ))
